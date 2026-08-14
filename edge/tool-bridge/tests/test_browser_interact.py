"""browser goto / click / fill 单测 —— 含密码与 secret_ref 的安全约定。

重点不在"点得动", 在**不泄密**: fill 传明文密码要打审计标记, secret_ref 解析
失败不能把引用内容漏进错误信息, text 长得像 secret_ref 要拒。
"""
from __future__ import annotations


import pytest

from catfish_tool_bridge import catfish_tools
from catfish_tool_bridge import catfish_tools_browser


def test_browser_tools_in_native_list() -> None:
    """4 个 catfish_browser_* 都在 CATFISH_NATIVE_TOOLS"""
    names = {t["name"] for t in catfish_tools.CATFISH_NATIVE_TOOLS}
    assert "catfish_browser_goto" in names
    assert "catfish_browser_click" in names
    assert "catfish_browser_fill" in names
    assert "catfish_browser_snapshot" in names


def test_browser_goto_required_url() -> None:
    tool = next(
        t for t in catfish_tools.CATFISH_NATIVE_TOOLS
        if t["name"] == "catfish_browser_goto"
    )
    assert "url" in tool["input_schema"]["required"]


def test_browser_goto_missing_url() -> None:
    result = catfish_tools.browser_goto({})
    assert result["type"] == "error"
    assert "url" in result["error"]


def test_browser_goto_blank_url() -> None:
    result = catfish_tools.browser_goto({"url": "   "})
    assert result["type"] == "error"


def test_browser_goto_no_playwright_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """playwright 没装 → 友好提示装"""
    def fake_import() -> object:
        raise RuntimeError("缺 playwright 包. 装一下...")
    monkeypatch.setattr(catfish_tools_browser, "_import_playwright", fake_import)
    result = catfish_tools.browser_goto({"url": "https://example.com"})
    assert result["type"] == "error"
    assert "playwright" in result["error"]


def test_browser_click_required_selector() -> None:
    """BL-FIX44 (5/11): selector / coordinates 二选一, schema 不再硬 required.
    改测运行时校验 — 两个都没传 → ok=false."""
    tool = next(
        t for t in catfish_tools.CATFISH_NATIVE_TOOLS
        if t["name"] == "catfish_browser_click"
    )
    # schema 层不强制 required (二选一靠运行时), 但属性必须列出
    assert "selector" in tool["input_schema"]["properties"]
    assert "coordinates" in tool["input_schema"]["properties"]
    # 运行时: 都没传 → error
    result = catfish_tools.browser_click({})
    assert result.get("type") == "error" or result.get("ok") is False
    assert "selector" in (result.get("error", "") or "")


def test_browser_click_missing_selector() -> None:
    result = catfish_tools.browser_click({})
    assert result["type"] == "error"
    assert "selector" in result["error"]


def test_browser_fill_required_fields() -> None:
    """v3: 只 selector 必填, text 跟 secret_ref 二选一 (运行时检查)"""
    tool = next(
        t for t in catfish_tools.CATFISH_NATIVE_TOOLS
        if t["name"] == "catfish_browser_fill"
    )
    required = set(tool["input_schema"]["required"])
    assert "selector" in required
    # text 跟 secret_ref 都不在 required (运行时检查二选一)
    assert "text" not in required
    assert "secret_ref" not in required


def test_browser_fill_neither_text_nor_secret_ref_returns_error() -> None:
    """text 和 secret_ref 都没给 → error"""
    result = catfish_tools.browser_fill({"selector": "input#u"})
    assert result["type"] == "error"
    assert "secret_ref" in result["error"] or "text" in result["error"]


def test_browser_fill_text_looks_like_secret_ref_rejected() -> None:
    """员工把 secret_ref 写到 text 字段 → error 提示放对位置"""
    result = catfish_tools.browser_fill({
        "selector": "input#u",
        "text": "keychain://my_pwd",  # 写错位置了
    })
    assert result["type"] == "error"
    assert "secret_ref" in result["error"]


def test_browser_fill_secret_ref_env_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env:// secret_ref 走 env var 拉值, 不进 LLM 上下文 — security_audit=via_secret_ref"""
    monkeypatch.setenv("EIS_PASSWORD", "real_pwd_xyz")

    class FakePage:
        def fill(self, selector, text, timeout):
            # 验证: page.fill 收到的真值是从 env var 拉的, 不是 secret_ref 本身
            assert text == "real_pwd_xyz"

    class FakeContext:
        pages = [FakePage()]

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, url):
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass

    monkeypatch.setattr(catfish_tools_browser, "_import_playwright", lambda: FakePlaywright)

    result = catfish_tools.browser_fill({
        "selector": "input[name='password']",
        "secret_ref": "env://EIS_PASSWORD",
    })
    assert result["type"] == "ok"
    assert result["security_audit"] == "credential_via_secret_ref"
    assert result["secret_ref_used"] == "env://EIS_PASSWORD"
    # filled_chars 应该是真值长度, 不是 secret_ref 长度
    assert result["filled_chars"] == len("real_pwd_xyz")


def test_browser_fill_secret_ref_failure_no_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """secret_ref 解析失败 → error, 不泄漏任何值 (因为没有值)"""
    monkeypatch.delenv("NONEXISTENT_PWD", raising=False)
    result = catfish_tools.browser_fill({
        "selector": "input[name='password']",
        "secret_ref": "env://NONEXISTENT_PWD",
    })
    assert result["type"] == "error"
    assert "secret_ref" in result["error"]
    assert "没设" in result["error"] or "set" in result["error"].lower()


def test_browser_fill_password_allowed_with_audit_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """selector 含 password → **允许填**, 但加 security_audit 标记让员工 IT 事后能查.

    历史 (2026-04-28): 一度拒填 password, 但实测员工日常需要鲶鱼帮登录,
    拒了 = 核心场景废. 改成允许 + audit 标记.
    """
    # mock playwright 让 fill 走通
    class FakePage:
        def fill(self, selector, text, timeout):
            pass

    class FakeContext:
        pages = [FakePage()]

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, url):
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass

    monkeypatch.setattr(catfish_tools_browser, "_import_playwright", lambda: FakePlaywright)

    result = catfish_tools.browser_fill({
        "selector": "input[name='password']",
        "text": "secret123",
    })
    # 关键: type=ok 不再是 error
    assert result["type"] == "ok"
    # 但有 audit 标记
    assert result.get("security_audit") == "credential_field_filled"
    assert "security_note" in result


def test_browser_fill_pwd_keyword_variants_all_marked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """各种密码框命名变体都加 audit 标记"""
    class FakePage:
        def fill(self, selector, text, timeout):
            pass

    class FakeContext:
        pages = [FakePage()]

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, url):
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass

    monkeypatch.setattr(catfish_tools_browser, "_import_playwright", lambda: FakePlaywright)

    for sel in ["input#pwd", "input[name='passwd']", "#user-password"]:
        result = catfish_tools.browser_fill({"selector": sel, "text": "x"})
        assert result["type"] == "ok", f"selector {sel} 应该允许但被拒"
        assert result.get("security_audit") == "credential_field_filled"


def test_browser_fill_non_password_no_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """普通字段 (用户名 / 邮箱 / 内容) 不应该有 security_audit 标记"""
    class FakePage:
        def fill(self, selector, text, timeout):
            pass

    class FakeContext:
        pages = [FakePage()]

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, url):
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass

    monkeypatch.setattr(catfish_tools_browser, "_import_playwright", lambda: FakePlaywright)

    result = catfish_tools.browser_fill({
        "selector": "input[name='username']",
        "text": "alice",
    })
    assert result["type"] == "ok"
    assert "security_audit" not in result
    assert "security_note" not in result


def test_browser_fill_missing_selector() -> None:
    result = catfish_tools.browser_fill({"text": "x"})
    assert result["type"] == "error"


def test_browser_snapshot_no_required_fields() -> None:
    """snapshot 所有字段可选"""
    tool = next(
        t for t in catfish_tools.CATFISH_NATIVE_TOOLS
        if t["name"] == "catfish_browser_snapshot"
    )
    assert tool["input_schema"]["required"] == []


def test_dispatch_browser_goto_routes() -> None:
    result = catfish_tools.dispatch_native("catfish_browser_goto", {})
    assert result["type"] == "error"
    assert "url" in result["error"]


def test_dispatch_browser_click_routes() -> None:
    result = catfish_tools.dispatch_native("catfish_browser_click", {})
    assert result["type"] == "error"
    assert "selector" in result["error"]


def test_dispatch_browser_fill_routes() -> None:
    result = catfish_tools.dispatch_native("catfish_browser_fill", {})
    assert result["type"] == "error"


def test_dispatch_browser_snapshot_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """snapshot 不需要必填参数, 路由直接进去, 没 playwright 则友好报错"""
    def fake_import() -> object:
        raise RuntimeError("缺 playwright 包...")
    monkeypatch.setattr(catfish_tools_browser, "_import_playwright", fake_import)
    result = catfish_tools.dispatch_native("catfish_browser_snapshot", {})
    assert result["type"] == "error"


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
