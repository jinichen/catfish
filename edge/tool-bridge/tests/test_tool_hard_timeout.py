"""_run_with_hard_timeout 单测 —— 浏览器工具的硬超时兜底。

横切关注点: goto / screenshot / snapshot / find_by_text 四条路径都要真的走这层
包装。所以单独成文件, 不塞进任何一族里。
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from catfish_tool_bridge import catfish_tools
from catfish_tool_bridge import catfish_tools_browser


def test_run_with_hard_timeout_normal_case() -> None:
    """正常返回 — 不到 timeout, 直接返结果"""
    def fast_fn(_args: Any) -> Dict[str, Any]:
        return {"type": "ok", "result": "fast"}

    result = catfish_tools_browser._run_with_hard_timeout(fast_fn, {}, hard_timeout_sec=2.0)
    assert result == {"type": "ok", "result": "fast"}


def test_run_with_hard_timeout_hits_timeout() -> None:
    """卡住超过 timeout — 返 error, 不抛"""
    import time as _time
    def slow_fn(_args: Any) -> Dict[str, Any]:
        _time.sleep(5.0)  # 比 timeout 长
        return {"type": "ok", "result": "never reached"}

    result = catfish_tools_browser._run_with_hard_timeout(slow_fn, {}, hard_timeout_sec=0.5)
    assert result["type"] == "error"
    assert "硬超时" in result["error"]
    assert "0.5s" in result["error"]
    # 提示里要有 LLM 下一步建议
    assert "snapshot" in result["error"] or "selector" in result["error"]


def test_run_with_hard_timeout_inner_exception() -> None:
    """inner fn 抛异常 — 异常透传 (不是 timeout 路径)"""
    def boom_fn(_args: Any) -> Dict[str, Any]:
        raise RuntimeError("inner boom")

    import pytest as _pytest
    with _pytest.raises(RuntimeError, match="inner boom"):
        catfish_tools_browser._run_with_hard_timeout(boom_fn, {}, hard_timeout_sec=2.0)


def test_browser_goto_routes_through_hard_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """browser_goto 公开函数走 hard timeout wrapper, 不直接调 _impl"""
    called = {"impl": False}
    def fake_impl(_args: Any) -> Dict[str, Any]:
        called["impl"] = True
        return {"type": "ok", "marker": "via_impl"}
    monkeypatch.setattr(catfish_tools_browser, "_browser_goto_impl", fake_impl)

    result = catfish_tools.browser_goto({"url": "http://x"})
    assert called["impl"] is True
    assert result["marker"] == "via_impl"


def test_browser_screenshot_routes_through_hard_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """browser_screenshot 也走 wrapper (跟 goto / click / fill / snapshot 一起套)"""
    called = {"impl": False}
    def fake_impl(_args: Any) -> Dict[str, Any]:
        called["impl"] = True
        return {"type": "image", "marker": "via_impl"}
    monkeypatch.setattr(catfish_tools_browser, "_browser_screenshot_impl", fake_impl)

    result = catfish_tools.browser_screenshot({})
    assert called["impl"] is True
    assert result["marker"] == "via_impl"


def test_browser_snapshot_routes_through_hard_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """browser_snapshot 走 wrapper"""
    called = {"impl": False}
    def fake_impl(_args: Any) -> Dict[str, Any]:
        called["impl"] = True
        return {"type": "ok", "elements": []}
    monkeypatch.setattr(catfish_tools_browser, "_browser_snapshot_impl", fake_impl)

    catfish_tools.browser_snapshot({})
    assert called["impl"] is True


def test_run_with_hard_timeout_actually_returns_quickly() -> None:
    """BL-FIX11: 硬超时之后 wrapper 真的快速返回 (不被 shutdown(wait=True) 锁住).

    FIX10 用 `with ThreadPoolExecutor()` 退出时默认等线程结束, timeout 等于没用.
    这个 test 检测 wrapper 整体执行时间 < hard_timeout * 2, 防 regression.
    """
    import time as _time
    import threading as _threading

    stop_event = _threading.Event()

    def stuck_fn(_args: Any) -> Dict[str, Any]:
        # 卡 60s, 远超 timeout
        stop_event.wait(timeout=60.0)
        return {"type": "ok", "result": "should not reach"}

    start = _time.monotonic()
    result = catfish_tools_browser._run_with_hard_timeout(stuck_fn, {}, hard_timeout_sec=1.0)
    elapsed = _time.monotonic() - start

    # 关键 assert: wrapper 1s 后必须返, 不能等到 stuck_fn 自己结束 (60s)
    assert elapsed < 3.0, f"wrapper 卡了 {elapsed}s, BL-FIX11 没生效"
    assert result["type"] == "error"
    assert "硬超时" in result["error"]
    # cleanup
    stop_event.set()


def test_find_by_text_routes_through_hard_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """browser_find_by_text 也走 BL-FIX10 wrapper"""
    called = {"impl": False}

    def fake_impl(_args: Any) -> Dict[str, Any]:
        called["impl"] = True
        return {"type": "ok", "element_count": 0, "elements": []}

    monkeypatch.setattr(catfish_tools_browser, "_browser_find_by_text_impl", fake_impl)
    catfish_tools.browser_find_by_text({"text": "test"})
    assert called["impl"] is True


# ── 关于这次拆分 (8/13) ──────────────────────────────────────
#
# 原 tests/test_screenshot.py 1850 行 / 103 个 test, 一个文件装了六件不相干的事。
# 按**夹具依赖**切, 不按行号切: 先用 AST 算出每个 test 引用了哪些模块级 helper,
# 确认两簇 (_FakePage/_patch_connect 与 _FakeBrowserPage/_patch_browser_connect)
# 没有任何 test 同时用到, 才敢让它们各自独立成文件。
#
# 顺带修了 10 处 pyflakes B 类: List / Dict 在注解里用了但从没 import。
# 有 `from __future__ import annotations` 所以运行时不炸 —— 属于"看不见的债",
# 拆文件时每个文件重算 import, 正好清掉。
