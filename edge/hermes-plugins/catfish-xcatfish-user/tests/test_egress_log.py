"""数据外发记录 (egress_log.py): 记什么、不记什么、挂钩后真写进 outbound_log.db。"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import threading
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location("_egress_log_t", Path(__file__).resolve().parents[1] / "egress_log.py")
e = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(e)


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("CATFISH_GATEWAY_URL", "http://10.0.0.5:8999")
    monkeypatch.setenv("CATFISH_EGRESS_LOG", "1")
    for k in ("CATFISH_GATEWAY_INTERNAL_URL", "CATFISH_IDENTITY_URL", "CATFISH_REGISTRY_URL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    e._CENTRAL = None
    yield tmp_path
    e._CENTRAL = None


def test_记录标准():
    gw = "http://10.0.0.5:8999"
    assert e.classify("POST", f"{gw}/v1/chat/completions", 120) == "content"          # 对话 / 蒸馏 内容
    assert e.classify("GET", f"{gw}/api/quota/me", 0) is None                          # 拉配额, 不带内容
    assert e.classify("POST", f"{gw}/v1/chat/completions", 0) is None                  # 没 body
    assert e.classify("POST", "http://10.0.0.5:8998/token", 80) is None                # 登录 / 续 token
    assert e.classify("POST", "http://127.0.0.1:8642/v1/chat/completions", 99) is None  # 本机 hermes
    assert e.classify("GET", "http://localhost:9222/json", 0) is None                  # 本机 Chrome
    assert e.classify("GET", "https://wttr.in/Fuzhou", 0) == "third_party"             # 第三方都记
    assert e.classify("POST", "https://api.parallel.ai/v1/search", 50) == "third_party"
    assert e.classify("POST", "https://ilinkai.weixin.qq.com/ilink/bot/getupdates", 166) is None  # 轮询拉取不记
    assert e.classify("POST", "https://ilinkai.weixin.qq.com/ilink/bot/sendtyping", 600) is None  # 「正在输入」状态, 不带员工内容
    assert e.classify("POST", "https://ilinkai.weixin.qq.com/ilink/bot/sendmessage", 300) == "third_party"


def test_用途和去密钥():
    assert e.purpose("http://x/v1/chat/completions?catfish_source=plugin:memory-wiki-ground") == "知识卡核对"
    assert e.purpose("http://x/v1/chat/completions?catfish_source=plugin:memory-wiki-analysis") == "资料蒸馏"
    assert e.purpose("http://x/v1/chat/completions") == "模型调用"
    assert e.purpose("http://x/v1/chat/completions", "hermes:aux:vision") == "对话 · 看图"
    assert e.purpose("http://x/v1/chat/completions", "hermes:aux:compression") == "对话 · 上下文压缩"
    assert e.purpose("http://x/v1/chat/completions", "hermes:aux:web_extract") == "hermes 辅助"
    assert e.purpose("http://x/v1/chat/completions", "hermes:api_server") == "对话"
    assert e.purpose("https://ilinkai.weixin.qq.com/ilink/bot/getupdates") == "微信"
    assert e.purpose("https://api.tavily.com/search") == "网页搜索"
    assert "abc" not in e.clean_url("https://api.parallel.ai/v1/search?api_key=abc&q=福州")
    assert "q=福州" in e.clean_url("https://api.parallel.ai/v1/search?api_key=abc&q=福州") or "q=%E7" in e.clean_url(
        "https://api.parallel.ai/v1/search?api_key=abc&q=福州")


def test_挂钩后真写进库_只记该记的(_home):
    seen = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            seen.append(self.rfile.read(int(self.headers["content-length"] or 0)))
            self.send_response(200)
            self.send_header("content-length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        do_GET = do_POST  # noqa: N815

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        import httpx

        e.install("hermes")
        e.install("hermes")  # 重复调用无副作用
        e._CENTRAL = {("127.0.0.1", port)}  # 把测试服务器当中央
        e._CENTRAL_AT = 9e18
        httpx.post(f"http://127.0.0.1:{port}/v1/chat/completions?catfish_source=plugin:memory-wiki-ground",
                   json={"messages": [{"role": "user", "content": "原文……"}]})
        httpx.get(f"http://127.0.0.1:{port}/api/quota/me")
        e.flush()
        rows = sqlite3.connect(str(_home / "outbound_log.db")).execute(
            "SELECT method, origin, dest_kind, purpose, request_bytes, status, request_payload_preview FROM outbound_log"
        ).fetchall()
        assert len(rows) == 1
        method, origin, kind, purpose, nbytes, status, preview = rows[0]
        assert (method, origin, kind, purpose, status) == ("POST", "hermes", "central", "知识卡核对", 200)
        assert nbytes > 0 and preview is None  # 内容不在老的 preview 列, 在 outbound_payload
        conn = sqlite3.connect(str(_home / "outbound_log.db"))
        (lid, body_sha, prev, rh), = conn.execute("SELECT id, body_sha256, prev_hash, row_hash FROM outbound_log")
        (blob,), = conn.execute("SELECT body FROM outbound_payload WHERE log_id = ?", (lid,))
        text = zlib.decompress(blob).decode()
        assert "原文……" in text and e._sha(text.encode()) == body_sha
        assert prev == e.GENESIS and len(rh) == 64
        # 请求头带的来源 (hermes 辅助调用) 也认得出做什么
        httpx.post(f"http://127.0.0.1:{port}/v1/chat/completions", headers={"X-Catfish-Source": "hermes:aux:vision"},
                   json={"messages": [{"role": "user", "content": "看图"}]})
        e.flush()
        (p2, prev2), = conn.execute("SELECT purpose, prev_hash FROM outbound_log ORDER BY id DESC LIMIT 1")
        assert p2 == "对话 · 看图" and prev2 == rh
    finally:
        srv.shutdown()
        e._CENTRAL = None
        e.uninstall()
        import httpx as _h
        assert not getattr(_h.Client.send, "_catfish_egress", False)


def test_流式回复_读完才记_下行按实际字节(_home):
    """10/28: hermes 调模型是 SSE, 没有 content-length。原来 send() 一返回就记, 下行永远 0。"""
    import asyncio
    import time as _t

    chunks = [b"data: " + ("x" * 200).encode() + b"\n\n" for _ in range(5)]

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers["content-length"] or 0))
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("transfer-encoding", "chunked")
            self.end_headers()
            for c in chunks:
                _t.sleep(0.05)
                self.wfile.write(b"%x\r\n%s\r\n" % (len(c), c))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    total = sum(len(c) for c in chunks)
    url = f"http://127.0.0.1:{port}/v1/chat/completions"
    try:
        import httpx

        e.install("hermes")
        e._CENTRAL = {("127.0.0.1", port)}
        e._CENTRAL_AT = 9e18
        with httpx.Client() as c:
            with c.stream("POST", url, json={"stream": True}) as r:  # 同步流式
                for _ in r.iter_bytes():
                    pass
            c.post(url, json={"stream": False})  # 非流式但 chunked: 也按实际字节

        async def go():
            async with httpx.AsyncClient() as c:
                async with c.stream("POST", url, json={"stream": True}) as r:  # 异步流式 (OpenAI SDK 走这条)
                    async for _ in r.aiter_bytes():
                        pass
        asyncio.run(go())
        e.flush()
        rows = sqlite3.connect(str(_home / "outbound_log.db")).execute(
            "SELECT response_bytes, ts_request, ts_response FROM outbound_log ORDER BY id").fetchall()
        assert [r[0] for r in rows] == [total, total, total]
        from datetime import datetime
        for _, t0, t1 in rows:  # 完成时刻 = 读完正文, 不是收到响应头 (正文要传 0.25 秒)
            assert (datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds() >= 0.2
    finally:
        srv.shutdown()
        e._CENTRAL = None
        e.uninstall()


def test_aiohttp_没声明长度_收完正文按实际字节记(_home):
    """10/28: 微信 (aiohttp) 原来直接记 content_length or 0 —— chunked 回复下行永远 0。"""
    aiohttp = pytest.importorskip("aiohttp")
    import asyncio
    from aiohttp import web

    async def chunked(request):
        await request.read()
        r = web.StreamResponse()
        r.enable_chunked_encoding()
        await r.prepare(request)
        for _ in range(4):
            await asyncio.sleep(0.05)
            await r.write(b"y" * 300)
        await r.write_eof()
        return r

    async def sized(request):
        await request.read()
        return web.Response(body=b"z" * 34)

    async def empty(request):  # 微信 CDN 上传: 结果在响应头里, 正文是空的
        await request.read()
        return web.Response(status=200, headers={"x-encrypted-param": "p"})

    async def go():
        app = web.Application()
        app.router.add_post("/chunked", chunked)
        app.router.add_post("/sized", sized)
        app.router.add_post("/empty", empty)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        e._CENTRAL = {("127.0.0.1", port)}
        e._CENTRAL_AT = 9e18
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(f"http://127.0.0.1:{port}/chunked", data=b"hi") as r:
                    await r.read()
                async with s.post(f"http://127.0.0.1:{port}/sized", data=b"hi") as r:
                    await r.read()
                async with s.post(f"http://127.0.0.1:{port}/empty", data=b"hi") as r:
                    pass
                async with s.post(f"http://127.0.0.1:{port}/chunked", data=b"hi") as r:
                    pass  # 没读正文就放掉连接: 也要记一条 (只记一次)
        finally:
            await runner.cleanup()

    e.install("hermes")
    try:
        asyncio.run(go())
        e.flush()
        rows = sqlite3.connect(str(_home / "outbound_log.db")).execute(
            "SELECT url, response_bytes, ts_request, ts_response FROM outbound_log ORDER BY id").fetchall()
        got = [(u.rsplit("/", 1)[1], b) for u, b, _, _ in rows]
        assert got[:3] == [("chunked", 1200), ("sized", 34), ("empty", 0)]
        assert len(got) == 4 and got[3][0] == "chunked" and got[3][1] < 1200
        from datetime import datetime
        t0, t1 = rows[0][2], rows[0][3]  # 完成时刻 = 正文收完 (要传 0.2 秒)
        assert (datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds() >= 0.15
    finally:
        e._CENTRAL = None
        e.uninstall()


def test_存内容_去密钥_大消息去重_指纹链(_home):
    big = "很长的系统提示" * 200
    body = {"model": "m", "api_key": "sk-1", "messages": [{"role": "system", "content": big},
                                                          {"role": "user", "content": "第一句"}]}
    stored, blobs = e.stored_body(json.dumps(body).encode())
    assert "sk-1" not in stored and '"api_key":"***"' in stored
    assert len(blobs) == 1 and "第一句" in stored and big not in stored  # 大的系统提示单独存
    assert e.stored_body(b"\xff\xfe\x00binary")[0].startswith("[二进制内容 9 字节, sha256=")
    assert e.stored_body(b"") == ("", {})

    gw = "http://10.0.0.5:8999/v1/chat/completions"
    e.record("POST", gw, 10, 200, body=json.dumps(body).encode())
    body["messages"].append({"role": "user", "content": "第二句"})
    e.record("POST", gw, 10, 200, body=json.dumps(body).encode())
    e.flush()
    conn = sqlite3.connect(str(_home / "outbound_log.db"))
    rows = conn.execute("SELECT * FROM outbound_log ORDER BY id").fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM outbound_log").description]
    rows = [dict(zip(cols, r)) for r in rows]
    assert len(rows) == 2
    assert rows[0]["prev_hash"] == e.GENESIS and rows[1]["prev_hash"] == rows[0]["row_hash"]
    for r in rows:
        assert e.row_hash(r["prev_hash"], r) == r["row_hash"]
    assert conn.execute("SELECT COUNT(*) FROM outbound_blob").fetchone()[0] == 1  # 系统提示只存一份
    # 改一个字段, 指纹就对不上
    tampered = dict(rows[0], url="http://elsewhere/")
    assert e.row_hash(rows[0]["prev_hash"], tampered) != rows[0]["row_hash"]


def test_指纹算法_跟Rust同一个测试向量():
    row = {"ts_request": "2026-10-25T00:00:00+00:00", "ts_response": "2026-10-25T00:00:01+00:00",
           "method": "POST", "url": "http://10.0.0.5:8999/v1/chat/completions", "request_bytes": 12,
           "response_bytes": 0, "status": 200, "error": None, "origin": "hermes", "dest_kind": "central",
           "dest_host": "10.0.0.5", "purpose": "模型调用", "body_sha256": "ab" * 32}
    # transparent_log_store.rs 的 test 钉的是同一个值
    assert e.row_hash("GENESIS", row) == ROW_HASH_VECTOR


ROW_HASH_VECTOR = "1a5e312e9f1f07a8ad5bbf86ac8aa5de5dd594987343375a23bdb33e34fe8fe7"


def test_打码规则_跟Rust同一个测试向量():
    """10/25 第二次: 会话票据 (typing_ticket / context_token / session_id …) 打码, user_id 类只留前后几位。
    transparent_log_redact_tests.rs 钉的是同一份输入、同一个输出。"""
    body = ('{"ilink_user_id":"o9cq807yh8QQuk8jp4IEWb_900RE@im.wechat","typing_ticket":"AAR=","msg":{"context_token":"c1",'
            '"to_user_id":"wxid_abcdefghijkl1234","text":"你好"},"session_id":"s","max_tokens":5,"user_id":"short",'
            '"Authorization":"Bearer x"}')
    stored, _ = e.stored_body(body.encode())
    assert stored == ('{"Authorization":"***","ilink_user_id":"o9cq…00RE@im.wechat","max_tokens":5,"msg":{"context_token":"***",'
                      '"text":"你好","to_user_id":"wxid…1234"},"session_id":"***","typing_ticket":"***","user_id":"***"}')
    assert e.mask_id("o9cq…00RE@im.wechat") == "o9cq…00RE@im.wechat", "再遮一次不变"
    url = e.clean_url("https://x.example/a?ticket=t1&session=s1&q=1")
    assert "t1" not in url and "s1" not in url and "q=1" in url


def test_hermes目录_Windows在LOCALAPPDATA(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr(e.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert e._hermes_home() == tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "h"))
    assert e._hermes_home() == tmp_path / "h"
