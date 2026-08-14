"""browser 截图 + 图片压缩单测。

共用 _FakeLocator → _FakeBrowserPage → _patch_browser_connect 这一簇, 外加
_make_fake_png。压缩那组 (element 保 PNG / viewport 转 JPEG / 大图降采样)
直接吃截图产物, 所以跟截图放一起而不是单独成文件。
"""
from __future__ import annotations

import base64
from typing import Any, Optional

import pytest

from catfish_tool_bridge import catfish_tools
from catfish_tool_bridge import catfish_tools_browser


class _FakeLocator:
    def __init__(self, screenshot_bytes: bytes = b"\x89PNG\r\n\x1a\n", raise_on_wait: Optional[Exception] = None) -> None:
        self._png = screenshot_bytes
        self._raise = raise_on_wait

    def wait_for(self, **_kw: Any) -> None:
        if self._raise is not None:
            raise self._raise

    def screenshot(self, **_kw: Any) -> bytes:
        return self._png


class _FakeBrowserPage:
    """模拟 Page — 给 browser_screenshot 用"""

    def __init__(
        self,
        title: str = "登录页",
        url: str = "http://eis.ffcs.cn/cas/login",
        screenshot_bytes: bytes = b"\x89PNG\r\n\x1a\n" + b"X" * 200,
        screenshot_raise: Optional[Exception] = None,
        title_raise: Optional[Exception] = None,
        locator_factory: Optional[Any] = None,
    ) -> None:
        self._title = title
        self.url = url
        self._screenshot_bytes = screenshot_bytes
        self._screenshot_raise = screenshot_raise
        self._title_raise = title_raise
        self._locator_factory = locator_factory

    def title(self) -> str:
        if self._title_raise is not None:
            raise self._title_raise
        return self._title

    def screenshot(self, **_kw: Any) -> bytes:
        if self._screenshot_raise is not None:
            raise self._screenshot_raise
        return self._screenshot_bytes

    def locator(self, _selector: str) -> Any:
        if self._locator_factory is not None:
            return self._locator_factory()
        return _FakeLocator()


def _patch_browser_connect(monkeypatch: pytest.MonkeyPatch, page: _FakeBrowserPage) -> None:
    """打桩 _import_playwright + _connect_playwright_browser, 注入 fake page."""
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


def test_browser_screenshot_in_native_tools() -> None:
    """catfish_browser_screenshot 必须出现在工具列表里"""
    names = [t["name"] for t in catfish_tools.CATFISH_NATIVE_TOOLS]
    assert "catfish_browser_screenshot" in names


def test_browser_screenshot_no_required_fields() -> None:
    """所有字段可选 (selector / full_page / timeout)"""
    tool = next(
        t for t in catfish_tools.CATFISH_NATIVE_TOOLS
        if t["name"] == "catfish_browser_screenshot"
    )
    assert tool["input_schema"]["required"] == []


def test_browser_screenshot_viewport_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认 (无 selector, full_page=false) → page.screenshot, 返 data_uri.

    BL-FIX17 (5/8): 默认 compress=auto 走 Pillow downscale + JPEG. 这条 test 传
    compress='none' 保留旧行为 (PNG 原图) 验证 base64 round-trip.
    """
    fake_png = b"\x89PNG\r\n\x1a\n" + b"A" * 1000
    page = _FakeBrowserPage(screenshot_bytes=fake_png)
    _patch_browser_connect(monkeypatch, page)

    result = catfish_tools.browser_screenshot({"compress": "none"})
    assert result["type"] == "image"
    assert result["format"] == "png"
    assert result["capture"] == "viewport"
    assert result["data_uri"].startswith("data:image/png;base64,")
    # base64 解出来 == 原始 bytes
    assert base64.b64decode(result["data"]) == fake_png
    assert result["title"] == "登录页"
    assert "eis.ffcs.cn" in result["url"]


def test_browser_screenshot_full_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """full_page=true → capture='full_page'. BL-FIX17 用 compress='none' 跳 Pillow."""
    page = _FakeBrowserPage()
    _patch_browser_connect(monkeypatch, page)

    result = catfish_tools.browser_screenshot({"full_page": True, "compress": "none"})
    assert result["type"] == "image"
    assert result["capture"] == "full_page"


def test_browser_screenshot_with_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    """selector 给了 → 用 locator.screenshot, capture 标记为 element"""
    fake_captcha = b"\x89PNG\r\n\x1a\n" + b"C" * 500
    page = _FakeBrowserPage(
        locator_factory=lambda: _FakeLocator(screenshot_bytes=fake_captcha),
    )
    _patch_browser_connect(monkeypatch, page)

    result = catfish_tools.browser_screenshot({"selector": "#captchaImg"})
    assert result["type"] == "image"
    assert "element[#captchaImg]" in result["capture"]
    assert base64.b64decode(result["data"]) == fake_captcha


def test_browser_screenshot_screenshot_fails_returns_friendly_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """page.screenshot 抛异常 → friendly error, 含 selector / full_page 信息"""
    page = _FakeBrowserPage(
        screenshot_raise=RuntimeError("Target closed"),
    )
    _patch_browser_connect(monkeypatch, page)

    result = catfish_tools.browser_screenshot({"full_page": True})
    assert result["type"] == "error"
    assert "Target closed" in result["error"]
    assert "full_page=True" in result["error"]


def test_browser_screenshot_locator_wait_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """元素截图: locator.wait_for 抛 (元素找不到) → friendly error"""
    page = _FakeBrowserPage(
        locator_factory=lambda: _FakeLocator(raise_on_wait=TimeoutError("element not visible")),
    )
    _patch_browser_connect(monkeypatch, page)

    result = catfish_tools.browser_screenshot({"selector": "#nonexistent"})
    assert result["type"] == "error"
    assert "element not visible" in result["error"]
    assert "#nonexistent" in result["error"]


def test_browser_screenshot_too_large_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """图太大 (>12MB) → 拒回 (防 IPC 撑爆).
    BL-FIX17 (5/8): compress='none' 跳过 Pillow, 直接走 12MB hard cap 检查.
    auto 模式下 Pillow 压缩会先做 800KB target check 报另一种 error, 测不到 12MB cap.
    """
    huge = b"\x89PNG" + b"X" * (15 * 1024 * 1024)  # 15 MB
    page = _FakeBrowserPage(screenshot_bytes=huge)
    _patch_browser_connect(monkeypatch, page)

    result = catfish_tools.browser_screenshot({"compress": "none"})
    assert result["type"] == "error"
    assert "太大" in result["error"]


def test_browser_screenshot_title_fail_friendly_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """page.title() 抛 → 提示 Chrome 标签可能关了"""
    page = _FakeBrowserPage(title_raise=RuntimeError("Target closed"))
    _patch_browser_connect(monkeypatch, page)

    result = catfish_tools.browser_screenshot({})
    assert result["type"] == "error"
    assert "Target closed" in result["error"]
    assert "标签页" in result["error"]


def test_dispatch_browser_screenshot_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    """dispatch_native 能路由 catfish_browser_screenshot → browser_screenshot"""
    def fake_import() -> object:
        raise RuntimeError("缺 playwright")
    monkeypatch.setattr(catfish_tools_browser, "_import_playwright", fake_import)
    result = catfish_tools.dispatch_native("catfish_browser_screenshot", {})
    assert result["type"] == "error"
    assert "playwright" in result["error"]


def _make_fake_png(width: int = 800, height: int = 600, color: tuple = (255, 0, 0)) -> bytes:
    """生成一张真 PNG (用 PIL), 给压缩测试用"""
    from PIL import Image
    import io
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_compress_element_keeps_png(monkeypatch: pytest.MonkeyPatch) -> None:
    """selector 给元素 → 保 PNG 不压"""
    fake_png = _make_fake_png(200, 100)  # 元素一般小

    page = _FakeBrowserPage(
        locator_factory=lambda: _FakeLocator(screenshot_bytes=fake_png),
    )
    _patch_browser_connect(monkeypatch, page)

    result = catfish_tools.browser_screenshot({"selector": "#captchaImg"})
    assert result["type"] == "image"
    assert result["format"] == "png"
    assert result["compress_strategy"] == "element_keep_png"
    assert result["compression_ratio"] == 1.0
    assert result["downscaled"] is False


def test_compress_viewport_uses_jpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    """无 selector + 默认 compress=auto → JPEG"""
    fake_png = _make_fake_png(1920, 1080)  # 大 viewport
    page = _FakeBrowserPage(screenshot_bytes=fake_png)
    _patch_browser_connect(monkeypatch, page)

    result = catfish_tools.browser_screenshot({})
    assert result["type"] == "image"
    assert result["format"] == "jpeg"
    assert result["data_uri"].startswith("data:image/jpeg;base64,")
    assert result["compression_ratio"] > 1.0  # 真压了
    assert result["compress_strategy"] == "viewport_jpeg"


def test_compress_viewport_downscales_large(monkeypatch: pytest.MonkeyPatch) -> None:
    """4K 输入 → downscale 到 max 1280px"""
    fake_png = _make_fake_png(2560, 1440)  # retina 4K
    page = _FakeBrowserPage(screenshot_bytes=fake_png)
    _patch_browser_connect(monkeypatch, page)

    result = catfish_tools.browser_screenshot({})
    assert result["type"] == "image"
    assert result["downscaled"] is True
    assert "1280" in (result["compress_strategy"] + str(result.get("downscaled_to") or ""))


def test_compress_full_page_uses_1600(monkeypatch: pytest.MonkeyPatch) -> None:
    """full_page=true → max 1600px (比 viewport 1280 大)"""
    fake_png = _make_fake_png(2560, 4000)  # 长图
    page = _FakeBrowserPage(screenshot_bytes=fake_png)
    _patch_browser_connect(monkeypatch, page)

    result = catfish_tools.browser_screenshot({"full_page": True})
    assert result["type"] == "image"
    assert result["compress_strategy"] == "fullpage_jpeg"


def test_compress_none_keeps_original(monkeypatch: pytest.MonkeyPatch) -> None:
    """compress='none' → 原 PNG 不压"""
    fake_png = _make_fake_png(1920, 1080)
    page = _FakeBrowserPage(screenshot_bytes=fake_png)
    _patch_browser_connect(monkeypatch, page)

    result = catfish_tools.browser_screenshot({"compress": "none"})
    assert result["type"] == "image"
    assert result["format"] == "png"
    assert result["compress_strategy"] == "none_explicit"
    assert result["compression_ratio"] == 1.0


def test_compress_returns_size_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    """返结果含 size_kb_before / size_kb_after / compression_ratio"""
    fake_png = _make_fake_png(1920, 1080)
    page = _FakeBrowserPage(screenshot_bytes=fake_png)
    _patch_browser_connect(monkeypatch, page)

    result = catfish_tools.browser_screenshot({})
    assert "size_kb_before" in result
    assert "size_kb_after" in result
    assert "compression_ratio" in result
    assert result["size_kb_after"] < result["size_kb_before"]


def test_compress_realistic_size_under_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """realistic 4K viewport 压完 < 800KB target (核心 demo 跑得动)"""
    fake_png = _make_fake_png(2560, 1440, color=(120, 130, 140))  # 灰渐变 PNG 大
    page = _FakeBrowserPage(screenshot_bytes=fake_png)
    _patch_browser_connect(monkeypatch, page)

    result = catfish_tools.browser_screenshot({})
    assert result["type"] == "image"
    # 800 KB cap
    assert result["size_kb_after"] < 800


def test_summary_mentions_format_and_ratio(monkeypatch: pytest.MonkeyPatch) -> None:
    """summary 含 'JPEG' + '压缩 Nx', 让 LLM 看到压了多少"""
    fake_png = _make_fake_png(1920, 1080)
    page = _FakeBrowserPage(screenshot_bytes=fake_png)
    _patch_browser_connect(monkeypatch, page)

    result = catfish_tools.browser_screenshot({})
    assert "JPEG" in result["summary"]
    assert "压缩" in result["summary"]


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
