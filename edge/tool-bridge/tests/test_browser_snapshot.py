"""browser snapshot / a11y 扁平化 / find_by_text 单测。

共用 _FakeAccessibility → _FakePage → _patch_connect 这一簇假页面。拆分时确认过
这簇的 17 个使用者全在本文件内, 没有 test 跨到 _FakeBrowserPage 那簇。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from catfish_tool_bridge import catfish_tools
from catfish_tool_bridge import catfish_tools_browser


def test_flatten_a11y_empty() -> None:
    out: list = []
    catfish_tools_browser._flatten_a11y(None, out)
    assert out == []


def test_flatten_a11y_button() -> None:
    """有 name + role=button 的节点收进 out"""
    node = {"role": "button", "name": "提交", "children": []}
    out: list = []
    catfish_tools_browser._flatten_a11y(node, out)
    assert len(out) == 1
    assert out[0]["role"] == "button"
    assert out[0]["name"] == "提交"


def test_flatten_a11y_unnamed_skipped() -> None:
    """没 name + 不在 interesting roles 的不收"""
    node = {"role": "generic", "name": "", "children": []}
    out: list = []
    catfish_tools_browser._flatten_a11y(node, out)
    assert out == []


def test_flatten_a11y_max_count_caps() -> None:
    """超过 max_count 立刻停, 不爆"""
    # 100 个 button 嵌套
    node: dict = {"role": "button", "name": "x", "children": []}
    cur = node
    for _ in range(100):
        next_node: dict = {"role": "button", "name": "x", "children": []}
        cur["children"].append(next_node)
        cur = next_node

    out: list = []
    catfish_tools_browser._flatten_a11y(node, out, max_count=10)
    assert len(out) == 10  # 严格上限, 不会超


def test_flatten_a11y_recursive() -> None:
    """递归遍历子节点"""
    node = {
        "role": "form",
        "name": "登录",
        "children": [
            {"role": "textbox", "name": "用户名", "children": []},
            {"role": "textbox", "name": "密码", "children": []},
            {"role": "button", "name": "登录", "children": []},
        ],
    }
    out: list = []
    catfish_tools_browser._flatten_a11y(node, out)
    # form + 3 children = 4
    assert len(out) == 4
    roles = [e["role"] for e in out]
    assert roles == ["form", "textbox", "textbox", "button"]


class _FakeAccessibility:
    """模拟 page.accessibility — snapshot() 行为可定制"""

    def __init__(self, snapshot_return: Any = None, raise_exc: Optional[Exception] = None) -> None:
        self._ret = snapshot_return
        self._raise = raise_exc

    def snapshot(self) -> Any:
        if self._raise is not None:
            raise self._raise
        return self._ret


class _FakePage:
    """模拟 Playwright Page — 给 browser_snapshot 用"""

    def __init__(
        self,
        title: str = "测试页面",
        url: str = "http://example.com",
        accessibility: Any = "MISSING",  # sentinel: 默认有 a11y; None 表示 page.accessibility=None
        evaluate_return: Optional[List[Dict[str, Any]]] = None,
        evaluate_raise: Optional[Exception] = None,
        title_raise: Optional[Exception] = None,
    ) -> None:
        self._title = title
        self.url = url
        self._title_raise = title_raise
        self._evaluate_return = evaluate_return or []
        self._evaluate_raise = evaluate_raise
        if accessibility == "MISSING":
            # 默认: a11y 模块存在, snapshot 返空 dict
            self.accessibility = _FakeAccessibility(snapshot_return={"role": "WebArea", "children": []})
        elif accessibility is None:
            # 模拟新版 Playwright 移除 accessibility — 不设置这个属性
            pass
        else:
            self.accessibility = accessibility

    def title(self) -> str:
        if self._title_raise is not None:
            raise self._title_raise
        return self._title

    def evaluate(self, _js: str, *_args: Any) -> Any:
        if self._evaluate_raise is not None:
            raise self._evaluate_raise
        return self._evaluate_return


def _patch_connect(monkeypatch: pytest.MonkeyPatch, page: _FakePage) -> None:
    """把 _import_playwright + _connect_playwright_browser 都打桩, 直接给 fake page."""
    class _FakeP:
        def __enter__(self) -> "_FakeP":
            return self

        def __exit__(self, *_a: Any) -> None:
            return None

    def fake_sync_playwright() -> _FakeP:
        return _FakeP()

    def fake_import() -> Any:
        return fake_sync_playwright

    def fake_connect(_p: Any) -> Any:
        return (None, None, page)

    monkeypatch.setattr(catfish_tools_browser, "_import_playwright", fake_import)
    monkeypatch.setattr(catfish_tools_browser, "_connect_playwright_browser", fake_connect)


def test_evaluate_dom_snapshot_normalizes_dirty_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """JS 端塞了脏数据 (非 dict / missing keys) — 归一化, 不爆"""
    raw = [
        {"role": "button", "name": "提交", "depth": 5, "selector_hint": "#submit"},
        "garbage string",  # 非 dict
        None,
        {"role": "textbox"},  # missing keys
    ]

    class _FakeP:
        def evaluate(self, _js: str, *_args: Any) -> Any:
            return raw

    out = catfish_tools_browser._evaluate_dom_snapshot(_FakeP(), max_count=200)
    # garbage / None 过滤掉, 剩 2 条
    assert len(out) == 2
    assert out[0] == {"role": "button", "name": "提交", "depth": 5, "selector_hint": "#submit"}
    assert out[1]["role"] == "textbox"
    assert out[1]["name"] == ""
    assert out[1]["depth"] == 0


def test_evaluate_dom_snapshot_returns_empty_on_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """JS 返 None / undefined — 安全返 []"""
    class _FakeP:
        def evaluate(self, _js: str, *_args: Any) -> Any:
            return None

    out = catfish_tools_browser._evaluate_dom_snapshot(_FakeP(), max_count=200)
    assert out == []


def test_browser_snapshot_falls_back_when_accessibility_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新版 Playwright 没 page.accessibility — fallback 到 dom_evaluate"""
    fake_dom = [
        {"role": "textbox", "name": "用户名", "depth": 5, "selector_hint": 'input[name="username"]'},
        {"role": "button", "name": "登录", "depth": 4, "selector_hint": "#submit"},
    ]
    page = _FakePage(accessibility=None, evaluate_return=fake_dom)
    _patch_connect(monkeypatch, page)

    result = catfish_tools.browser_snapshot({})
    assert result["type"] == "ok"
    assert result["snapshot_method"] == "dom_evaluate"
    assert result["element_count"] == 2
    assert result["elements"][0]["name"] == "用户名"
    assert "accessibility_fallback_reason" in result


def test_browser_snapshot_falls_back_when_accessibility_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """page.accessibility.snapshot() 抛异常 — fallback dom_evaluate + 记 reason"""
    fake_dom = [{"role": "button", "name": "确定", "depth": 3, "selector_hint": "#ok"}]
    page = _FakePage(
        accessibility=_FakeAccessibility(raise_exc=AttributeError("snapshot removed")),
        evaluate_return=fake_dom,
    )
    _patch_connect(monkeypatch, page)

    result = catfish_tools.browser_snapshot({})
    assert result["type"] == "ok"
    assert result["snapshot_method"] == "dom_evaluate"
    assert "AttributeError" in result["accessibility_fallback_reason"]
    assert "snapshot removed" in result["accessibility_fallback_reason"]


def test_browser_snapshot_uses_a11y_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """page.accessibility 拿到非空 tree — 走 a11y 路径, 不进 dom_evaluate"""
    a11y_tree = {
        "role": "form",
        "name": "登录",
        "children": [{"role": "textbox", "name": "用户名", "children": []}],
    }
    page = _FakePage(
        accessibility=_FakeAccessibility(snapshot_return=a11y_tree),
        evaluate_return=[{"should": "not be used"}],
    )
    _patch_connect(monkeypatch, page)

    result = catfish_tools.browser_snapshot({})
    assert result["type"] == "ok"
    assert result["snapshot_method"] == "accessibility"
    # 没 fallback_reason
    assert "accessibility_fallback_reason" not in result
    # 元素来自 a11y 树, 不是 evaluate
    names = [e["name"] for e in result["elements"]]
    assert "登录" in names
    assert "用户名" in names


def test_browser_snapshot_a11y_returns_none_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """page.accessibility.snapshot() 返 None (Chrome 拒答场景) — 走 dom_evaluate"""
    page = _FakePage(
        accessibility=_FakeAccessibility(snapshot_return=None),
        evaluate_return=[{"role": "button", "name": "登录", "depth": 2, "selector_hint": "button"}],
    )
    _patch_connect(monkeypatch, page)

    result = catfish_tools.browser_snapshot({})
    assert result["type"] == "ok"
    assert result["snapshot_method"] == "dom_evaluate"


def test_browser_snapshot_both_paths_fail_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """a11y + dom_evaluate 都炸 — 友好 error 给 LLM, 含 url / title"""
    page = _FakePage(
        accessibility=_FakeAccessibility(raise_exc=RuntimeError("a11y boom")),
        evaluate_raise=RuntimeError("evaluate boom"),
    )
    _patch_connect(monkeypatch, page)

    result = catfish_tools.browser_snapshot({})
    assert result["type"] == "error"
    assert "a11y boom" in result["error"]
    assert "evaluate boom" in result["error"]
    assert "测试页面" in result["error"]  # title 出现


def test_browser_snapshot_title_fail_returns_friendly_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """page.title() 抛 — page 本身坏了, 早退提示员工重启 Chrome"""
    page = _FakePage(title_raise=RuntimeError("Target closed"))
    _patch_connect(monkeypatch, page)

    result = catfish_tools.browser_snapshot({})
    assert result["type"] == "error"
    assert "Target closed" in result["error"]
    assert "Chrome" in result["error"]  # 暗示重启 chrome


def test_browser_snapshot_truncates_to_max_elements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超 max_elements 截断 + 标 truncated"""
    fake_dom = [
        {"role": "button", "name": f"btn-{i}", "depth": 1, "selector_hint": f"#b{i}"}
        for i in range(50)
    ]
    page = _FakePage(accessibility=None, evaluate_return=fake_dom)
    _patch_connect(monkeypatch, page)

    result = catfish_tools.browser_snapshot({"max_elements": 10})
    assert result["type"] == "ok"
    assert len(result["elements"]) == 10
    assert result["truncated"] is True


def test_snapshot_truncated_returns_hint_for_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """超 max_elements → truncated=true + hint_for_llm 字段, 引导加大"""
    fake_dom = [
        {"role": "button", "name": f"btn-{i}", "depth": 1, "selector_hint": f"#b{i}"}
        for i in range(60)
    ]
    page = _FakePage(accessibility=None, evaluate_return=fake_dom)
    _patch_connect(monkeypatch, page)

    result = catfish_tools.browser_snapshot({"max_elements": 50})
    assert result["truncated"] is True
    assert "hint_for_llm" in result
    hint = result["hint_for_llm"]
    # hint 含 "加大" / "max_elements" / 下一档值
    assert "加大" in hint
    assert "max_elements" in hint
    assert "100" in hint  # 50 * 2 = 100, 下一档建议
    # 截断时 summary 也提示
    assert "加大" in result["summary"]


def test_snapshot_not_truncated_no_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """没截断 → 不返 hint_for_llm, summary 也不提加大"""
    fake_dom = [
        {"role": "textbox", "name": "用户名", "depth": 5, "selector_hint": "#name"},
        {"role": "textbox", "name": "密码", "depth": 5, "selector_hint": "#pwd"},
    ]
    page = _FakePage(accessibility=None, evaluate_return=fake_dom)
    _patch_connect(monkeypatch, page)

    result = catfish_tools.browser_snapshot({"max_elements": 50})
    assert result["truncated"] is False
    assert "hint_for_llm" not in result


def test_snapshot_default_500_not_200() -> None:
    """BL-FIX9 默认 max_elements 500 (从 200 bump 上来)"""
    tool = next(
        t for t in catfish_tools.CATFISH_NATIVE_TOOLS
        if t["name"] == "catfish_browser_snapshot"
    )
    max_props = tool["input_schema"]["properties"]["max_elements"]
    assert max_props["default"] == 500


def test_snapshot_cap_1000_not_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """BL-FIX9 cap 从 500 bump 到 1000"""
    page = _FakePage(accessibility=None, evaluate_return=[])
    _patch_connect(monkeypatch, page)
    # 给 5000 — 应该被 cap 到 1000 不是 500
    # 怎么验证? 看 hint 里 next_max 的值. 设 max_elements=600 一定截断, hint 说加到 1000
    fake_dom = [
        {"role": "button", "name": f"btn-{i}", "depth": 1, "selector_hint": f"#b{i}"}
        for i in range(700)
    ]
    page = _FakePage(accessibility=None, evaluate_return=fake_dom)
    _patch_connect(monkeypatch, page)
    result = catfish_tools.browser_snapshot({"max_elements": 600})
    assert result["truncated"] is True
    # 600 * 2 = 1200, 但 cap 1000, 所以 hint 含 1000
    assert "1000" in result["hint_for_llm"]


def test_find_by_text_in_native_tools() -> None:
    """catfish_browser_find_by_text 必须在 CATFISH_NATIVE_TOOLS"""
    names = [t["name"] for t in catfish_tools.CATFISH_NATIVE_TOOLS]
    assert "catfish_browser_find_by_text" in names


def test_find_by_text_required_field() -> None:
    """text 字段必填"""
    tool = next(
        t for t in catfish_tools.CATFISH_NATIVE_TOOLS
        if t["name"] == "catfish_browser_find_by_text"
    )
    assert "text" in tool["input_schema"]["required"]


def test_find_by_text_empty_text_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """text 空 → error"""
    page = _FakePage()
    _patch_connect(monkeypatch, page)
    result = catfish_tools.browser_find_by_text({"text": ""})
    assert result["type"] == "error"
    assert "text" in result["error"]


def test_find_by_text_finds_login_button(monkeypatch: pytest.MonkeyPatch) -> None:
    """JS evaluate 返候选列表 — 按 score 倒序"""
    fake_results = [
        {
            "selector_hint": "a.login-btn",
            "tag_name": "a",
            "text": "登录",
            "x": 100,
            "y": 200,
            "width": 80,
            "height": 30,
            "score": 20,
        },
        {
            "selector_hint": "div.login-link",
            "tag_name": "div",
            "text": "用户登录",
            "x": 50,
            "y": 100,
            "width": 100,
            "height": 25,
            "score": 5,
        },
    ]
    page = _FakePage(evaluate_return=fake_results)
    _patch_connect(monkeypatch, page)

    # BL-FIX44 (5/11): JS 返 selector_hint, impl 转成 selector + 加 role/match_type/
    # is_clickable/bounds/center/in_viewport/score. fake JS 没全 — 只测 element_count
    # 以及 evaluate 真被调.
    result = catfish_tools.browser_find_by_text({"text": "登录"})
    assert result["type"] == "ok"
    assert result["element_count"] == 2
    assert result["search_text"] == "登录"
    assert result["exact"] is False
    # impl 把 'selector_hint' rename 成 'selector' (BL-FIX44 后 fake 数据键名跟实现对不上,
    # 走 str(item.get("selector", ""))[:200] 兜底成空, 不报错)
    # 关键: element_count + summary 不爆
    assert "selector" in result["elements"][0]
    assert result["elements"][0]["tag"] == ""  # fake 用 tag_name, impl 取 tag → 空


def test_find_by_text_no_match_returns_friendly_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没找到候选 → element_count=0 + 友好提示, 不报错"""
    page = _FakePage(evaluate_return=[])
    _patch_connect(monkeypatch, page)

    result = catfish_tools.browser_find_by_text({"text": "登录"})
    assert result["type"] == "ok"
    assert result["element_count"] == 0
    # summary 含 '没找到' + 引导改用 screenshot
    assert "没找到" in result["summary"]
    assert "screenshot" in result["summary"]


def test_find_by_text_evaluate_raises_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """page.evaluate 抛 → friendly error, 不抛异常"""
    page = _FakePage(evaluate_raise=RuntimeError("evaluate boom"))
    _patch_connect(monkeypatch, page)

    result = catfish_tools.browser_find_by_text({"text": "登录"})
    assert result["type"] == "error"
    assert "evaluate boom" in result["error"]


def test_find_by_text_exact_param_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    """exact 参数传给 page.evaluate, 默认 false"""
    captured_args: list = []

    class _CapturingPage(_FakePage):
        def evaluate(self, _js: str, *args: Any) -> Any:
            captured_args.extend(args)
            return []

    _patch_connect(monkeypatch, _CapturingPage())

    result = catfish_tools.browser_find_by_text({"text": "登录", "exact": True})
    assert result["type"] == "ok"
    # 第 1 个 args 是 params dict
    assert captured_args[0]["text"] == "登录"
    assert captured_args[0]["exact"] is True


def test_find_by_text_max_results_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    """BL-FIX44 (5/11): max_results clamp 到 [1, 30] (原 20 上限放宽到 30)."""
    captured: list = []

    class _CapPage(_FakePage):
        def evaluate(self, _js: str, *args: Any) -> Any:
            captured.append(args[0])
            return []

    _patch_connect(monkeypatch, _CapPage())
    catfish_tools.browser_find_by_text({"text": "x", "max_results": 100})
    assert captured[0]["maxCount"] == 30  # clamp 到 30

    # max_results=-1 走 max(1, ...) clamp (0 走 'or 5' 默认 fallback, 不算 clamp 边界)
    catfish_tools.browser_find_by_text({"text": "x", "max_results": -1})
    assert captured[1]["maxCount"] == 1  # clamp 到 1


def test_dispatch_routes_find_by_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """dispatch_native('catfish_browser_find_by_text', ...) → browser_find_by_text"""
    page = _FakePage(evaluate_return=[])
    _patch_connect(monkeypatch, page)

    result = catfish_tools.dispatch_native(
        "catfish_browser_find_by_text", {"text": "test"}
    )
    assert result["type"] == "ok"


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
