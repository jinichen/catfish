"""BL-LEARN-RECMODE CDP listener v0 骨架单测.

跑法: cd central/llm-gateway && PYTHONPATH=src python -m pytest tests/test_recmode_cdp_listener.py -q

v0 只测骨架: start/stop 接口 + events.jsonl 落档 + meta.json + active_sessions 管理.
真 CDP ws 连接 + 截图 5/26 sprint 真做时填.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from catfish_tool_bridge.recmode import cdp_listener


@pytest.mark.asyncio
async def test_start_stop_roundtrip(tmp_path):
    """v0: start → 加几个 mock events → stop → 看 events.jsonl + meta.json 落档"""
    sess = await cdp_listener.CDPRecordingSession.start(
        session_id="test_001",
        chrome_ws="ws://localhost:9222",
        output_root=tmp_path,
        connect_ws=False,  # 测试不真连
    )

    # mock 几个 events
    sess.state.events.append(cdp_listener.CDPEvent(
        ts=1.0, kind="page_navigated",
        content={"url": "http://eis.ffcs.cn/", "title": "EIS"},
        screenshot_id="kf_0001",
    ))
    sess.state.events.append(cdp_listener.CDPEvent(
        ts=3.5, kind="dom_changed",
        content={"summary": "DOM updated"},
    ))
    sess.state.keyframe_count = 1

    summary = await sess.stop()

    # events.jsonl
    events_path = tmp_path / "test_001" / "events.jsonl"
    assert events_path.exists()
    lines = events_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    e1 = json.loads(lines[0])
    assert e1["kind"] == "page_navigated"
    assert e1["url"] == "http://eis.ffcs.cn/"
    assert e1["screenshot_id"] == "kf_0001"
    e2 = json.loads(lines[1])
    assert e2["kind"] == "dom_changed"
    assert e2["screenshot_id"] is None

    # meta.json
    meta_path = tmp_path / "test_001" / "meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta["session_id"] == "test_001"
    assert meta["events_count"] == 2
    assert meta["keyframes_count"] == 1
    assert "duration_s" in meta

    # summary
    assert summary["events_count"] == 2
    assert summary["output_dir"] == str(tmp_path / "test_001")


@pytest.mark.asyncio
async def test_screenshots_dir_created(tmp_path):
    """start 时自动建 screenshots/ 子目录 (5/26 真做时往里写 PNG)"""
    await cdp_listener.CDPRecordingSession.start(
        session_id="test_002",
        output_root=tmp_path,
        connect_ws=False,
    )
    assert (tmp_path / "test_002" / "screenshots").is_dir()


@pytest.mark.asyncio
async def test_module_level_start_stop(tmp_path, monkeypatch):
    """gateway endpoint 用的 module-level helper"""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))

    info = await cdp_listener.start_recording("rec_a", connect_ws=False)
    assert info["session_id"] == "rec_a"
    assert "rec_a" in cdp_listener.list_active()

    summary = await cdp_listener.stop_recording("rec_a")
    assert summary["session_id"] == "rec_a"
    assert "rec_a" not in cdp_listener.list_active()


@pytest.mark.asyncio
async def test_duplicate_start_rejected(tmp_path, monkeypatch):
    """同 session_id 重复 start → ValueError"""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    await cdp_listener.start_recording("rec_dup", connect_ws=False)
    with pytest.raises(ValueError, match="已在录中"):
        await cdp_listener.start_recording("rec_dup", connect_ws=False)
    # cleanup
    await cdp_listener.stop_recording("rec_dup")


@pytest.mark.asyncio
async def test_stop_unknown_session_rejected(tmp_path, monkeypatch):
    """stop 不存在的 session → ValueError"""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    with pytest.raises(ValueError, match="没在录中"):
        await cdp_listener.stop_recording("rec_nonexistent")


@pytest.mark.asyncio
async def test_resolve_page_ws_url_picks_first_page(monkeypatch):
    """5/15 1:50 修: _resolve_page_ws_url 拉 /json 选 type=page tab 的 webSocketDebuggerUrl"""
    import httpx

    class FakeResp:
        def raise_for_status(self):
            pass
        def json(self):
            return [
                {"type": "background_page", "title": "ext"},
                {"type": "page", "title": "EIS", "webSocketDebuggerUrl": "ws://localhost:9222/devtools/page/ABCD"},
                {"type": "page", "title": "another", "webSocketDebuggerUrl": "ws://localhost:9222/devtools/page/EFGH"},
            ]

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url):
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    url, title = await cdp_listener._resolve_page_ws_url("ws://localhost:9222")
    assert url == "ws://localhost:9222/devtools/page/ABCD"
    assert title == "EIS"


@pytest.mark.asyncio
async def test_resolve_page_ws_url_no_pages_friendly_error(monkeypatch):
    """5/15: Chrome 没打开任何网页 → 友好错"""
    import httpx

    class FakeResp:
        def raise_for_status(self): pass
        def json(self):
            return [{"type": "service_worker"}, {"type": "background_page"}]

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url): return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    with pytest.raises(RuntimeError, match="先在 Chrome 里打开一个网页"):
        await cdp_listener._resolve_page_ws_url("ws://localhost:9222")


@pytest.mark.asyncio
async def test_resolve_page_ws_url_chrome_unreachable(monkeypatch):
    """5/15: Chrome 没起 → 友好错带 curl 提示"""
    import httpx

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    with pytest.raises(RuntimeError, match="Catfish Chrome 没起"):
        await cdp_listener._resolve_page_ws_url("ws://localhost:9222")


@pytest.mark.asyncio
async def test_long_pause_detector_fires(tmp_path, monkeypatch):
    """直接调 _long_pause_detector 看会不会写 long_pause event (压时间).

    完整版 5/26 sprint 才接真 ws + 真背景 task. v0 只验逻辑."""
    sess = await cdp_listener.CDPRecordingSession.start(
        session_id="test_lp", output_root=tmp_path, connect_ws=False,
    )
    # 模拟 last_event_ts 是 5s 前 + 上次 keyframe 也是 5s 前
    sess.state.last_event_ts = time.time() - 5.0
    sess.state.last_keyframe_ts = time.time() - 5.0

    # 手动调一次 detector 体内的逻辑 (不跑 sleep loop)
    now = time.time()
    gap = now - sess.state.last_event_ts
    assert gap >= cdp_listener._LONG_PAUSE_THRESHOLD_S
    # detector 真跑会触发 _capture_screenshot — v0 这是 noop, 测它不挂
    kf_id = await cdp_listener._capture_screenshot(sess.state)
    assert kf_id.startswith("kf_")
    assert sess.state.keyframe_count == 1


# ============================================================
# BL-RECMODE-NO-PROXY-LOCALHOST regression (5/27 鸿波 自己 patch 引的 'os' bug)
# ============================================================
#
# 5/27 加 proxy bypass 时函数内重 import os, Python 把整个 start() scope 的
# os 当 local, 让函数早些行的 os.environ.get 报 UnboundLocalError.
# 加结构性检查 + smoke test 防回归.


def test_no_duplicate_os_import_in_start():
    """BL-RECMODE-NO-PROXY-LOCALHOST regression: start() 函数内不能再 import os.

    踩过的坑: 函数内 `import os` 让 Python 把整个函数 scope 的 os 当 local,
    函数早些行的 os.environ.get 当未初始化变量, 直接 UnboundLocalError.

    这条静态检查放这里防回归 — 任何人改 cdp_listener.py 在函数内加 import os
    就会被这条测试挡住.
    """
    import ast
    import inspect

    src = inspect.getsource(cdp_listener)
    tree = ast.parse(src)

    offenders: list[str] = []
    for node in ast.walk(tree):
        # 只看函数 (async / sync) 内部
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Import):
                    for alias in sub.names:
                        # 允许 import httpx / websockets / 等其他模块, 但 os
                        # 已经在模块顶部 import 过, 函数内再 import 就是 bug
                        if alias.name == "os":
                            offenders.append(
                                f"{node.name} (line {sub.lineno}): import os"
                            )
    assert not offenders, (
        f"start() 等函数内不能再 import os (模块顶部已有). 触发 UnboundLocalError "
        f"风险. 命中: {offenders}"
    )


@pytest.mark.asyncio
async def test_start_uses_output_root_without_unbound_os(tmp_path):
    """BL-RECMODE-NO-PROXY-LOCALHOST regression: start() 走 output_root 分支不挂.

    5/27 那个 'os' bug 在 line 395 `os.environ.get("CATFISH_HOME")` — 进入
    `if output_root is None` 分支才会触发. 这条测试**故意不传 output_root**,
    走默认分支, 确保 os 能 access (没被 shadow 成 local).
    """
    # 显式不传 output_root, 让代码走 env 兜底分支
    # 用 monkeypatch 把 HOME 指 tmp_path, 防真写到员工 ~/.catfish/
    import os as _real_os
    saved_home = _real_os.environ.get("HOME")
    saved_catfish_home = _real_os.environ.get("CATFISH_HOME")
    _real_os.environ["HOME"] = str(tmp_path)
    _real_os.environ.pop("CATFISH_HOME", None)
    try:
        sess = await cdp_listener.CDPRecordingSession.start(
            session_id="test_no_unbound_os",
            chrome_ws="ws://localhost:9222",
            output_root=None,  # 关键: 走默认分支才会撞 'os' bug
            connect_ws=False,
        )
        # 跑到这里说明 os scope 没出问题
        assert sess.state.output_dir.exists()
        await sess.stop()
    finally:
        if saved_home is not None:
            _real_os.environ["HOME"] = saved_home
        if saved_catfish_home is not None:
            _real_os.environ["CATFISH_HOME"] = saved_catfish_home


def test_resolve_page_ws_url_disables_env_proxy():
    """BL-RECMODE-NO-PROXY-LOCALHOST: httpx 必须用 trust_env=False, 防 Clash 拦.

    国内开发环境很可能设 HTTPS_PROXY=http://127.0.0.1:7890 (Clash). httpx
    默认 trust_env=True 让 localhost 也走代理, 代理一关录屏直接挂. 这条静态
    检查防 trust_env=False 被改回去.
    """
    import inspect
    src = inspect.getsource(cdp_listener._resolve_page_ws_url)
    assert "trust_env=False" in src, (
        "_resolve_page_ws_url 必须用 httpx.AsyncClient(trust_env=False), "
        "否则 Clash 类 HTTPS_PROXY env 会让 localhost:9222 走代理失败. "
        "改回去前先看 5/27 BL-RECMODE-NO-PROXY-LOCALHOST 反思."
    )


def test_websockets_connect_disables_proxy():
    """BL-RECMODE-NO-PROXY-LOCALHOST: websockets.connect 也得绕开 HTTPS_PROXY.

    websockets 14+ 读 https_proxy env. 跟 httpx 同理, 防 Clash 一关 RecMode 挂.
    """
    import inspect
    src = inspect.getsource(cdp_listener.CDPRecordingSession.start)
    # 双保险: 要么显式 proxy=None, 要么 env-clear http_proxy / https_proxy
    has_proxy_none = "proxy=None" in src
    has_env_clear = "https_proxy" in src and "environ.pop" in src
    assert has_proxy_none or has_env_clear, (
        "start() 调 websockets.connect 必须绕 proxy env. 没 proxy=None 也没 "
        "env-clear → Clash 一关录屏挂. 见 5/27 BL-RECMODE-NO-PROXY-LOCALHOST."
    )
