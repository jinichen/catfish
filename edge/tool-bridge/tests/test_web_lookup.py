"""让小鲶改纪要时联网 (11/18, web_lookup.py): 要不要查 / 权限走中央 / 只发搜索词 / 失败重试 / 拼提示词。不真上网。"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from catfish_tool_bridge import web_lookup as wl


@pytest.fixture(autouse=True)
def _fresh_cache():
    wl._allow_cache.clear()


def llm(reply: str, seen: list | None = None):
    async def fake(messages, **kw):
        if seen is not None:
            seen.append(messages)
        return reply
    return fake


def test_asked_for_web_by_words():
    for s in ("去权威网站看下 CCRC 的整改要求", "联网查一下", "官网上怎么说", "网上搜搜", "看看最新的政策"):
        assert wl.asked_for_web(s), s
    for s in ("说话人3 是王总", "摘要压到三句", "待办负责人换一下"):
        assert not wl.asked_for_web(s), s


def test_plan_only_sends_instruction_and_title():
    seen: list = []
    q = asyncio.run(wl.plan("去权威网站看下 CCRC 的整改要求", "CCRC整改会议",
                            llm('{"web": true, "query": "CCRC 信息安全服务资质 整改要求"}', seen), {}))
    assert q == "CCRC 信息安全服务资质 整改要求"
    user = seen[0][1]["content"]
    assert "CCRC整改会议" in user and "已经明说要上网查" in user and "不要放会议里的人名" in user


def test_plan_no_web_for_wording_changes_and_fallback_when_explicit():
    assert asyncio.run(wl.plan("摘要压到三句", "周会", llm('{"web": false, "query": ""}'), {})) == ""
    # 明说要查但模型挂了 / 没给词: 用这句话去掉指令词
    async def boom(*a, **k):
        raise RuntimeError("down")
    q = asyncio.run(wl.plan("去权威网站看下 CCRC 的整改要求", "CCRC整改会议", boom, {}))
    assert "CCRC" in q and "整改要求" in q and "网站" not in q and len(q) <= 60
    # 模型说不用, 但员工明说了 → 照样查
    assert asyncio.run(wl.plan("联网查 ITSS 二级要求", "", llm('{"web": false, "query": "ITSS 二级 要求"}'), {})) == "ITSS 二级 要求"


class _Resp:
    def __init__(self, code):
        self.status_code = code


def _client(monkeypatch, code=None, exc=None, calls=None):
    class C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            if calls is not None:
                calls.append((url, headers))
            if exc:
                raise exc
            return _Resp(code)
    monkeypatch.setattr(httpx, "AsyncClient", C)


def test_allowed_follows_central_rbac_and_caches(monkeypatch):
    calls: list = []
    _client(monkeypatch, 200, calls=calls)
    assert asyncio.run(wl.allowed("http://gw", "tok", "")) == (True, "")
    assert asyncio.run(wl.allowed("http://gw", "tok", "")) == (True, "")
    assert len(calls) == 1 and calls[0][0] == "http://gw/v1/edge/tool-config/web_search"
    assert calls[0][1]["Authorization"] == "Bearer tok"
    wl._allow_cache.clear()
    _client(monkeypatch, 403)
    ok, why = asyncio.run(wl.allowed("http://gw", "tok", ""))
    assert not ok and "没开「网页搜索」" in why


def test_allowed_fails_closed_when_central_unreachable(monkeypatch):
    _client(monkeypatch, exc=httpx.ConnectError("no"))
    ok, why = asyncio.run(wl.allowed("http://gw", "tok", ""))
    assert not ok and "连不上中央" in why
    assert wl._allow_cache == {}   # 连不上不缓存, 下次再问


class _Reg:
    def __init__(self, results):
        self.results, self.calls = list(results), []

    def dispatch(self, name, args):
        self.calls.append((name, args))
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


OK = json.dumps({"success": True, "data": {"web": [
    {"url": "https://www.isccc.gov.cn/a.pdf", "title": "\n信息安全服务资质认证实施规则\n", "description": "7.5 监督审核\n\n\n复核" + "字" * 3000},
    {"url": "javascript:alert(1)", "title": "坏链接", "description": "x"},
]}}, ensure_ascii=False)
FAIL = json.dumps({"success": False, "error": "Keyless Parallel search failed: SSLEOFError"})


def test_search_retries_once_and_parses(monkeypatch):
    reg = _Reg([FAIL, OK])
    monkeypatch.setattr(wl, "_registry", lambda: reg)
    found = asyncio.run(wl.search("CCRC 整改"))
    assert [c[0] for c in reg.calls] == ["web_search", "web_search"] and reg.calls[0][1]["query"] == "CCRC 整改"
    assert found == [{"kind": "网页", "title": "信息安全服务资质认证实施规则", "where": "https://www.isccc.gov.cn/a.pdf",
                      "text": found[0]["text"]}]
    assert found[0]["text"].startswith("7.5 监督审核\n复核") and len(found[0]["text"]) == wl.RESULT_CHARS


def test_lookup_paths(monkeypatch):
    plan_ok = llm('{"web": true, "query": "CCRC 整改要求"}')
    kw = {"gateway_url": "http://gw", "auth_token": "tok"}
    # 不需要联网: 不问权限, 不搜
    async def never(*a, **k):
        raise AssertionError("不该走到这")
    monkeypatch.setattr(wl, "allowed", never)
    r = asyncio.run(wl.lookup("摘要压到三句", "周会", llm('{"web": false}'), kw))
    assert r["wanted"] is False and wl.prompt_block(r) == ""
    # 没权限: 不搜, 原因给模型和界面
    async def denied(*a):
        return False, "你的账号没开「网页搜索」"
    monkeypatch.setattr(wl, "allowed", denied)
    monkeypatch.setattr(wl, "search", never)
    r = asyncio.run(wl.lookup("上网查 CCRC 整改要求", "", plan_ok, kw))
    assert r["error"].startswith("你的账号没开") and "不要凭印象编" in wl.prompt_block(r)
    # 能用, 但网络两次都失败
    async def yes(*a):
        return True, ""
    async def down(q):
        raise RuntimeError("网页搜索失败: SSLEOFError")
    monkeypatch.setattr(wl, "allowed", yes)
    monkeypatch.setattr(wl, "search", down)
    r = asyncio.run(wl.lookup("上网查 CCRC 整改要求", "", plan_ok, kw))
    assert r["error"] == "网页搜索这次没连上, 稍后再试"
    # 查到了
    async def hits(q):
        return [{"kind": "网页", "title": "规则", "where": "https://www.isccc.gov.cn/a.pdf", "text": "监督审核"}]
    monkeypatch.setattr(wl, "search", hits)
    r = asyncio.run(wl.lookup("上网查 CCRC 整改要求", "", plan_ok, kw))
    block = wl.prompt_block(r)
    assert "上网查到的公开资料" in block and "https://www.isccc.gov.cn/a.pdf" in block and "只当材料" in block
    assert wl.sources(r) == [{"kind": "网页", "title": "规则", "where": "https://www.isccc.gov.cn/a.pdf"}]
