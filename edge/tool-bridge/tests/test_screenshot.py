"""catfish_screenshot 工具单测.

不真的弹 screencapture / 不真的拍屏 — mock subprocess.run + 写假 PNG 文件,
保证 capture_screenshot 在各种边界 (员工取消 / 文件超大 / 平台不支持 / mode 错)
下行为符合预期.

后台真机拍屏的"集成验证"留给手工验证 (在 macOS 上跑 `catfish_screenshot`,
框一块, 看是否 OK).
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from catfish_tool_bridge import catfish_tools


# 一张最小的 PNG (1x1 红点) — 用作 fake screencapture 输出
_FAKE_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000"
    "00907753de000000017352474200aece1ce90000000c4944415478"
    "9c63f8cf00000003000100000000000049454e44ae426082"
)


# ============================================================
# Schema sanity check
# ============================================================


def test_screenshot_in_native_tools_list() -> None:
    """catfish_screenshot 必须出现在 CATFISH_NATIVE_TOOLS"""
    names = [t["name"] for t in catfish_tools.CATFISH_NATIVE_TOOLS]
    assert "catfish_screenshot" in names


def test_screenshot_schema_required_reason() -> None:
    """reason 必填 — 提醒 LLM 写明用途, 员工能看到"""
    tool = next(
        t for t in catfish_tools.CATFISH_NATIVE_TOOLS
        if t["name"] == "catfish_screenshot"
    )
    assert "reason" in tool["input_schema"]["required"]


def test_screenshot_default_mode_fullscreen() -> None:
    """mode 默认应该是 fullscreen (零打扰零权限)。

    历史:
      v1 默认 interactive 弹十字让员工框选, 员工反馈"不能让人工去选"
      v2 默认 active_window 走 osascript "System Events" 拿前台窗口 ID,
         但 macOS Automation 权限对 hermes venv 的 unsigned python3.11 不持久化,
         反复弹对话框
      v3 (现) 默认 fullscreen, active_window 模式直接删掉 — 0 打扰 0 权限.
         员工触发场景一般是看"屏幕上 X" / 验证码, 主屏前台就是要看的内容.
    """
    tool = next(
        t for t in catfish_tools.CATFISH_NATIVE_TOOLS
        if t["name"] == "catfish_screenshot"
    )
    mode_prop = tool["input_schema"]["properties"]["mode"]
    assert mode_prop["default"] == "fullscreen"
    # 三个 mode 都得在 enum 里 (active_window 已删, 因为它走 osascript 反复弹权限)
    assert set(mode_prop["enum"]) == {
        "interactive",
        "fullscreen",
        "window",
    }
    # 反向: active_window 不能再出现在 enum 里
    assert "active_window" not in mode_prop["enum"]


# ============================================================
# 入参校验
# ============================================================


def test_missing_reason_returns_error() -> None:
    """没传 reason → type=error, 不调系统命令"""
    result = catfish_tools.capture_screenshot({"mode": "interactive"})
    assert result["type"] == "error"
    assert "reason" in result["error"]


def test_blank_reason_returns_error() -> None:
    """reason 是空白字符串 → 也算没传"""
    result = catfish_tools.capture_screenshot({"reason": "   "})
    assert result["type"] == "error"


def test_unknown_mode_returns_error() -> None:
    """mode 不在枚举里 → error"""
    result = catfish_tools.capture_screenshot(
        {"mode": "magic", "reason": "test"}
    )
    assert result["type"] == "error"
    assert "magic" in result["error"]


# ============================================================
# macOS 路径 (主路径) — mock subprocess + 写 fake PNG
# ============================================================


@mock.patch.object(catfish_tools, "platform")
@mock.patch.object(catfish_tools, "shutil")
@mock.patch.object(catfish_tools, "subprocess")
def test_macos_interactive_success(
    mock_sub: mock.MagicMock,
    mock_shutil: mock.MagicMock,
    mock_platform: mock.MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mac + interactive + screencapture 成功 → 返回 base64 + path + meta"""
    mock_platform.system.return_value = "Darwin"
    mock_shutil.which.return_value = "/usr/sbin/screencapture"

    # 假装 subprocess.run 成功执行 — side_effect 写真实文件到 out_path
    def fake_run(cmd: Any, **_: Any) -> Any:
        # cmd 最后一个参数是 out_path
        out_path = Path(cmd[-1])
        out_path.write_bytes(_FAKE_PNG_BYTES)
        return mock.MagicMock(returncode=0, stdout="", stderr="")

    mock_sub.run.side_effect = fake_run
    mock_sub.TimeoutExpired = Exception  # 让 except 子句别 raise

    # 让 tempfile 在 tmp_path 下生成
    # tempfile.gettempdir() 缓存第一次调用结果, setenv TMPDIR 不再生效 →
    # 直接打 catfish_tools.tempfile.gettempdir 强制改路径 (隔离每个测试)
    monkeypatch.setattr(
        catfish_tools.tempfile, "gettempdir", lambda: str(tmp_path)
    )

    result = catfish_tools.capture_screenshot(
        {"mode": "interactive", "reason": "看下报错"}
    )

    assert result["type"] == "image"
    assert result["format"] == "png"
    assert result["mode"] == "interactive"
    assert result["reason"] == "看下报错"
    # base64 解码后应该能拿回原始字节
    assert base64.b64decode(result["data"]) == _FAKE_PNG_BYTES
    assert result["data_uri"].startswith("data:image/png;base64,")
    assert result["size_bytes"] == len(_FAKE_PNG_BYTES)
    # 文件应该真的留在 tmp_path 下
    assert Path(result["path"]).exists()
    # screencapture 命令必须含 -i (interactive)
    cmd_used = mock_sub.run.call_args[0][0]
    assert "-i" in cmd_used
    assert "-x" in cmd_used  # 必须静音(无快门声)


@mock.patch.object(catfish_tools, "platform")
@mock.patch.object(catfish_tools, "shutil")
@mock.patch.object(catfish_tools, "subprocess")
def test_macos_user_cancelled(
    mock_sub: mock.MagicMock,
    mock_shutil: mock.MagicMock,
    mock_platform: mock.MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """员工按 ESC → screencapture 退码 0 但没文件 → type=error 友好提示"""
    mock_platform.system.return_value = "Darwin"
    mock_shutil.which.return_value = "/usr/sbin/screencapture"
    # subprocess.run 成功但啥也没写
    mock_sub.run.return_value = mock.MagicMock(returncode=0)
    mock_sub.TimeoutExpired = Exception

    # tempfile.gettempdir() 缓存第一次调用结果, setenv TMPDIR 不再生效 →
    # 直接打 catfish_tools.tempfile.gettempdir 强制改路径 (隔离每个测试)
    monkeypatch.setattr(
        catfish_tools.tempfile, "gettempdir", lambda: str(tmp_path)
    )

    result = catfish_tools.capture_screenshot(
        {"mode": "interactive", "reason": "test"}
    )
    assert result["type"] == "error"
    assert "取消" in result["error"]


@mock.patch.object(catfish_tools, "platform")
@mock.patch.object(catfish_tools, "shutil")
@mock.patch.object(catfish_tools, "subprocess")
def test_macos_empty_file_treated_as_cancel(
    mock_sub: mock.MagicMock,
    mock_shutil: mock.MagicMock,
    mock_platform: mock.MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """文件存在但 0 字节 (员工框了 0 像素) → 也算取消"""
    mock_platform.system.return_value = "Darwin"
    mock_shutil.which.return_value = "/usr/sbin/screencapture"

    def fake_run(cmd: Any, **_: Any) -> Any:
        Path(cmd[-1]).write_bytes(b"")  # 空文件
        return mock.MagicMock(returncode=0)

    mock_sub.run.side_effect = fake_run
    mock_sub.TimeoutExpired = Exception
    # tempfile.gettempdir() 缓存第一次调用结果, setenv TMPDIR 不再生效 →
    # 直接打 catfish_tools.tempfile.gettempdir 强制改路径 (隔离每个测试)
    monkeypatch.setattr(
        catfish_tools.tempfile, "gettempdir", lambda: str(tmp_path)
    )

    result = catfish_tools.capture_screenshot(
        {"mode": "interactive", "reason": "test"}
    )
    assert result["type"] == "error"
    assert "取消" in result["error"]


@mock.patch.object(catfish_tools, "platform")
@mock.patch.object(catfish_tools, "shutil")
def test_macos_no_screencapture_binary(
    mock_shutil: mock.MagicMock,
    mock_platform: mock.MagicMock,
) -> None:
    """系统残缺没有 screencapture → 友好报错"""
    mock_platform.system.return_value = "Darwin"
    mock_shutil.which.return_value = None  # 模拟找不到 screencapture

    result = catfish_tools.capture_screenshot(
        {"mode": "interactive", "reason": "test"}
    )
    assert result["type"] == "error"
    assert "screencapture" in result["error"]


@mock.patch.object(catfish_tools, "platform")
@mock.patch.object(catfish_tools, "shutil")
@mock.patch.object(catfish_tools, "subprocess")
def test_macos_window_mode_uses_W_flag(
    mock_sub: mock.MagicMock,
    mock_shutil: mock.MagicMock,
    mock_platform: mock.MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mode=window → screencapture 必须带 -W 让员工选窗口"""
    mock_platform.system.return_value = "Darwin"
    mock_shutil.which.return_value = "/usr/sbin/screencapture"

    def fake_run(cmd: Any, **_: Any) -> Any:
        Path(cmd[-1]).write_bytes(_FAKE_PNG_BYTES)
        return mock.MagicMock(returncode=0)

    mock_sub.run.side_effect = fake_run
    mock_sub.TimeoutExpired = Exception
    # tempfile.gettempdir() 缓存第一次调用结果, setenv TMPDIR 不再生效 →
    # 直接打 catfish_tools.tempfile.gettempdir 强制改路径 (隔离每个测试)
    monkeypatch.setattr(
        catfish_tools.tempfile, "gettempdir", lambda: str(tmp_path)
    )

    result = catfish_tools.capture_screenshot(
        {"mode": "window", "reason": "看下 Foxmail 窗口"}
    )
    assert result["type"] == "image"
    cmd_used = mock_sub.run.call_args[0][0]
    assert "-W" in cmd_used  # 窗口选择标志
    assert "-i" in cmd_used  # 也要 interactive (员工点哪个窗口)


@mock.patch.object(catfish_tools, "platform")
@mock.patch.object(catfish_tools, "shutil")
@mock.patch.object(catfish_tools, "subprocess")
def test_macos_fullscreen_mode_no_i_flag(
    mock_sub: mock.MagicMock,
    mock_shutil: mock.MagicMock,
    mock_platform: mock.MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mode=fullscreen → 不带 -i, 直接全屏拍"""
    mock_platform.system.return_value = "Darwin"
    mock_shutil.which.return_value = "/usr/sbin/screencapture"

    def fake_run(cmd: Any, **_: Any) -> Any:
        Path(cmd[-1]).write_bytes(_FAKE_PNG_BYTES)
        return mock.MagicMock(returncode=0)

    mock_sub.run.side_effect = fake_run
    mock_sub.TimeoutExpired = Exception
    # tempfile.gettempdir() 缓存第一次调用结果, setenv TMPDIR 不再生效 →
    # 直接打 catfish_tools.tempfile.gettempdir 强制改路径 (隔离每个测试)
    monkeypatch.setattr(
        catfish_tools.tempfile, "gettempdir", lambda: str(tmp_path)
    )

    result = catfish_tools.capture_screenshot(
        {"mode": "fullscreen", "reason": "员工要求全屏截"}
    )
    assert result["type"] == "image"
    cmd_used = mock_sub.run.call_args[0][0]
    assert "-i" not in cmd_used  # 全屏模式不要 -i


# ============================================================
# active_window 模式已废弃 — 测试它**不存在**, 避免悄悄复活
# ============================================================


def test_active_window_mode_rejected() -> None:
    """active_window 已从 enum 删除, 模型主动传这个值应当报"未知 mode"。

    历史 (2026-04-27): 这个 mode 走 osascript 拿前台窗口 ID, 反复触发 macOS
    Automation 权限弹窗. hermes venv 的 unsigned python3.11 不持久化授权,
    用户每次截图都被打扰. 删了, fullscreen 已够用.
    """
    result = catfish_tools.capture_screenshot(
        {"mode": "active_window", "reason": "test deprecated"}
    )
    assert result["type"] == "error"
    assert "未知 mode" in result["error"] or "active_window" in result["error"]


def test_no_get_frontmost_window_id_func() -> None:
    """_get_frontmost_window_id_macos() 函数已删除, 不再调 osascript"""
    assert not hasattr(catfish_tools, "_get_frontmost_window_id_macos")


# ============================================================
# 文件超大 → 拒绝, 不返回 base64 撑爆 socket
# ============================================================


@mock.patch.object(catfish_tools, "platform")
@mock.patch.object(catfish_tools, "shutil")
@mock.patch.object(catfish_tools, "subprocess")
def test_macos_oversize_file_rejected(
    mock_sub: mock.MagicMock,
    mock_shutil: mock.MagicMock,
    mock_platform: mock.MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """截图 > _MAX_SCREENSHOT_BYTES → 不返 base64, 给 error 提示员工框小点"""
    mock_platform.system.return_value = "Darwin"
    mock_shutil.which.return_value = "/usr/sbin/screencapture"

    # 假装拍了一张比 max 还大 1 字节的图
    huge_size = catfish_tools._MAX_SCREENSHOT_BYTES + 1

    def fake_run(cmd: Any, **_: Any) -> Any:
        Path(cmd[-1]).write_bytes(b"\x00" * huge_size)
        return mock.MagicMock(returncode=0)

    mock_sub.run.side_effect = fake_run
    mock_sub.TimeoutExpired = Exception
    # tempfile.gettempdir() 缓存第一次调用结果, setenv TMPDIR 不再生效 →
    # 直接打 catfish_tools.tempfile.gettempdir 强制改路径 (隔离每个测试)
    monkeypatch.setattr(
        catfish_tools.tempfile, "gettempdir", lambda: str(tmp_path)
    )

    result = catfish_tools.capture_screenshot(
        {"mode": "fullscreen", "reason": "员工要求"}
    )
    assert result["type"] == "error"
    assert "太大" in result["error"]


# ============================================================
# 跨平台 fallback
# ============================================================


@mock.patch.object(catfish_tools, "platform")
def test_unsupported_platform(mock_platform: mock.MagicMock) -> None:
    """非 Darwin / Windows → 友好报错, 不抛"""
    mock_platform.system.return_value = "Linux"
    result = catfish_tools.capture_screenshot(
        {"mode": "interactive", "reason": "test"}
    )
    assert result["type"] == "error"
    assert "Linux" in result["error"]


# ============================================================
# dispatch 入口集成
# ============================================================


def test_is_native_screenshot() -> None:
    """is_native('catfish_screenshot') 应该返回 True"""
    assert catfish_tools.is_native("catfish_screenshot") is True


def test_dispatch_unknown_returns_error() -> None:
    """dispatch_native 收到陌生 tool name → 抛"""
    with pytest.raises(ValueError, match="unknown native tool"):
        catfish_tools.dispatch_native("not_a_real_tool", {})


@mock.patch.object(catfish_tools, "platform")
def test_dispatch_screenshot_routes_correctly(
    mock_platform: mock.MagicMock,
) -> None:
    """dispatch_native('catfish_screenshot', ...) 应该路由到 capture_screenshot"""
    mock_platform.system.return_value = "Linux"  # 故意不支持, 直接返 error
    result = catfish_tools.dispatch_native(
        "catfish_screenshot", {"mode": "interactive", "reason": "test"}
    )
    # 走到了 capture_screenshot, 因为 Linux 返了 error
    assert result["type"] == "error"


# ============================================================
# catfish_browser_* (Playwright connect_over_cdp 后端)
# ============================================================
#
# 历史 (本测试文件):
#   v1: 测直 CDP /json/list + WebSocket
#   v2 (现): 直 CDP 全废, 换 Playwright. 测 mock playwright 实现 + 入参校验 + 友好错误


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
    monkeypatch.setattr(catfish_tools, "_import_playwright", fake_import)
    result = catfish_tools.browser_goto({"url": "https://example.com"})
    assert result["type"] == "error"
    assert "playwright" in result["error"]


def test_browser_click_required_selector() -> None:
    tool = next(
        t for t in catfish_tools.CATFISH_NATIVE_TOOLS
        if t["name"] == "catfish_browser_click"
    )
    assert "selector" in tool["input_schema"]["required"]


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

    monkeypatch.setattr(catfish_tools, "_import_playwright", lambda: FakePlaywright)

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

    monkeypatch.setattr(catfish_tools, "_import_playwright", lambda: FakePlaywright)

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

    monkeypatch.setattr(catfish_tools, "_import_playwright", lambda: FakePlaywright)

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

    monkeypatch.setattr(catfish_tools, "_import_playwright", lambda: FakePlaywright)

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
    monkeypatch.setattr(catfish_tools, "_import_playwright", fake_import)
    result = catfish_tools.dispatch_native("catfish_browser_snapshot", {})
    assert result["type"] == "error"


# ============================================================
# _flatten_a11y 纯函数 (accessibility tree → 元素列表)
# ============================================================


def test_flatten_a11y_empty() -> None:
    out: list = []
    catfish_tools._flatten_a11y(None, out)
    assert out == []


def test_flatten_a11y_button() -> None:
    """有 name + role=button 的节点收进 out"""
    node = {"role": "button", "name": "提交", "children": []}
    out: list = []
    catfish_tools._flatten_a11y(node, out)
    assert len(out) == 1
    assert out[0]["role"] == "button"
    assert out[0]["name"] == "提交"


def test_flatten_a11y_unnamed_skipped() -> None:
    """没 name + 不在 interesting roles 的不收"""
    node = {"role": "generic", "name": "", "children": []}
    out: list = []
    catfish_tools._flatten_a11y(node, out)
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
    catfish_tools._flatten_a11y(node, out, max_count=10)
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
    catfish_tools._flatten_a11y(node, out)
    # form + 3 children = 4
    assert len(out) == 4
    roles = [e["role"] for e in out]
    assert roles == ["form", "textbox", "textbox", "button"]


# ============================================================
# BL-FIX3 (5/8) — browser_snapshot DOM fallback
# ============================================================
#
# 5/8 EIS 登录 demo 撞 page.accessibility 在新版 Playwright 上 None / 抛 AttributeError,
# LLM 看到 "工具坏了" 直接放弃. 修法 — 双路径: a11y → fallback page.evaluate().
# 这组 test 覆盖:
#   - _evaluate_dom_snapshot 输出归一化 (脏 / 非 dict 过滤掉)
#   - browser_snapshot accessibility=None → 走 dom_evaluate
#   - browser_snapshot accessibility 抛异常 → 走 dom_evaluate + 记 fallback_reason
#   - browser_snapshot a11y 拿到东西 → 不进 dom_evaluate
#   - page.title() 抛 → friendly error


from typing import Optional  # noqa: E402  (used by BL-FIX3 fakes below)


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

    monkeypatch.setattr(catfish_tools, "_import_playwright", fake_import)
    monkeypatch.setattr(catfish_tools, "_connect_playwright_browser", fake_connect)


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

    out = catfish_tools._evaluate_dom_snapshot(_FakeP(), max_count=200)
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

    out = catfish_tools._evaluate_dom_snapshot(_FakeP(), max_count=200)
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


# ============================================================
# catfish_skill_backup (Skill lifecycle 阶段 5 防御)
# ============================================================


def test_skill_backup_in_native_tools_list() -> None:
    """catfish_skill_backup 必须出现在 CATFISH_NATIVE_TOOLS"""
    names = [t["name"] for t in catfish_tools.CATFISH_NATIVE_TOOLS]
    assert "catfish_skill_backup" in names


def test_skill_backup_required_fields() -> None:
    """skill_name 和 reason 都必填"""
    tool = next(
        t for t in catfish_tools.CATFISH_NATIVE_TOOLS
        if t["name"] == "catfish_skill_backup"
    )
    required = set(tool["input_schema"]["required"])
    assert required == {"skill_name", "reason"}


def test_skill_backup_missing_skill_name_returns_error() -> None:
    """没传 skill_name → error"""
    result = catfish_tools.skill_backup({"reason": "test"})
    assert result["type"] == "error"
    assert "skill_name" in result["error"]


def test_skill_backup_missing_reason_returns_error() -> None:
    """没传 reason → error"""
    result = catfish_tools.skill_backup({"skill_name": "ns/foo"})
    assert result["type"] == "error"
    assert "reason" in result["error"]


def test_skill_backup_invalid_format_returns_error() -> None:
    """skill_name 没含 / → error"""
    result = catfish_tools.skill_backup(
        {"skill_name": "no_slash", "reason": "x"}
    )
    assert result["type"] == "error"
    assert "格式错" in result["error"] or "/" in result["error"]


def test_skill_backup_skill_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """skill 不存在 → error"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("USERPROFILE", raising=False)
    result = catfish_tools.skill_backup(
        {"skill_name": "productivity/no-such-skill", "reason": "test"}
    )
    assert result["type"] == "error"
    assert "找不到" in result["error"]


def test_skill_backup_success_creates_versions_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """skill 存在 → backup 到 .versions/<ts>.md, 返回 ok"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("USERPROFILE", raising=False)

    # 建一个 fake skill
    skill_dir = tmp_path / ".hermes" / "skills" / "productivity" / "test-skill"
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\nname: test-skill\ndescription: test\n---\n# Body\n步骤 1: foo\n"
    )

    result = catfish_tools.skill_backup({
        "skill_name": "productivity/test-skill",
        "reason": "员工要求改",
    })
    assert result["type"] == "ok"
    assert result["skill_name"] == "productivity/test-skill"
    assert result["version_count"] == 1
    backup_path = Path(result["backup_path"])
    assert backup_path.exists()
    assert backup_path.parent.name == ".versions"
    # backup 内容跟原 SKILL.md 一致
    assert backup_path.read_text() == skill_md.read_text()


def test_skill_backup_symlink_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """catfish-* skill 通过 install.sh 软链, LLM 不能改 — 软链一律 deny"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("USERPROFILE", raising=False)

    # 建 catfish 源代码 skill
    src_dir = tmp_path / "catfish-src" / "catfish-email"
    src_dir.mkdir(parents=True)
    (src_dir / "SKILL.md").write_text("---\nname: catfish-email\n---\nbody")

    # 软链到 ~/.hermes/skills/productivity/catfish-email (模拟 install.sh)
    skills_root = tmp_path / ".hermes" / "skills" / "productivity"
    skills_root.mkdir(parents=True)
    (skills_root / "catfish-email").symlink_to(src_dir)

    result = catfish_tools.skill_backup({
        "skill_name": "productivity/catfish-email",
        "reason": "尝试改 catfish skill",
    })
    assert result["type"] == "error"
    assert "软链" in result["error"]
    assert "catfish 自家" in result["error"]


def test_skill_backup_increments_version_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """连续 backup 同一个 skill, version_count 应递增"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("USERPROFILE", raising=False)

    skill_dir = tmp_path / ".hermes" / "skills" / "ns" / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("v1")

    r1 = catfish_tools.skill_backup({"skill_name": "ns/skill", "reason": "first"})
    assert r1["version_count"] == 1

    # 等 1.1s 让 unix-ts 真变 (秒级精度)
    import time as _time
    _time.sleep(1.1)

    (skill_dir / "SKILL.md").write_text("v2")
    r2 = catfish_tools.skill_backup({"skill_name": "ns/skill", "reason": "second"})
    assert r2["version_count"] == 2


def test_dispatch_skill_backup_routes_correctly() -> None:
    """dispatch_native('catfish_skill_backup', ...) 应该路由到 skill_backup"""
    # missing skill_name → 走 skill_backup, 立即 error
    result = catfish_tools.dispatch_native("catfish_skill_backup", {})
    assert result["type"] == "error"
    assert "skill_name" in result["error"]


# ============================================================
# BL-FIX7 (5/8) — catfish_browser_screenshot (Playwright 截浏览器)
# ============================================================
#
# 之前 LLM 想看浏览器内容 (验证码 / 按钮) 只剩 catfish_screenshot (mac screencapture),
# 不对路 / 要权限 / 容易失败. BL-FIX7 加 Playwright page.screenshot 直截 Chrome tab.
# 同 connect_over_cdp 链路, 0 权限, 直接返 data_uri.


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

    monkeypatch.setattr(catfish_tools, "_import_playwright", fake_import)
    monkeypatch.setattr(catfish_tools, "_connect_playwright_browser", fake_connect)


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
    monkeypatch.setattr(catfish_tools, "_import_playwright", fake_import)
    result = catfish_tools.dispatch_native("catfish_browser_screenshot", {})
    assert result["type"] == "error"
    assert "playwright" in result["error"]


# ============================================================
# BL-FIX9 (5/8) — snapshot truncated 时返 hint_for_llm 引导加大
# ============================================================
#
# 现网坑 (鸿波 5/8 点出): LLM 偷懒主动选 max_elements=50, CAS 登录页 nav / footer
# link 多, 登录 button 挤出 50, LLM 看不到 → 想截图 → 撞 Playwright sync 卡死.
# 修法: 默认 200→500 cap 500→1000, truncated 时显式返 hint_for_llm 引导加大不减小.


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


# ============================================================
# BL-FIX10 (5/8) — Playwright hard timeout wrapper
# ============================================================
#
# 现网坑: tool-bridge 单线程, Playwright sync API 卡死时 ignore 自带 timeout 参数,
# 拖死整个 daemon → Companion 停止按钮失效. 套 ThreadPoolExecutor + 硬超时, 卡了
# 直接返 error, 线程泄漏接受 (没法真杀, mac 重启 GC).


def test_run_with_hard_timeout_normal_case() -> None:
    """正常返回 — 不到 timeout, 直接返结果"""
    def fast_fn(_args: Any) -> Dict[str, Any]:
        return {"type": "ok", "result": "fast"}

    result = catfish_tools._run_with_hard_timeout(fast_fn, {}, hard_timeout_sec=2.0)
    assert result == {"type": "ok", "result": "fast"}


def test_run_with_hard_timeout_hits_timeout() -> None:
    """卡住超过 timeout — 返 error, 不抛"""
    import time as _time
    def slow_fn(_args: Any) -> Dict[str, Any]:
        _time.sleep(5.0)  # 比 timeout 长
        return {"type": "ok", "result": "never reached"}

    result = catfish_tools._run_with_hard_timeout(slow_fn, {}, hard_timeout_sec=0.5)
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
        catfish_tools._run_with_hard_timeout(boom_fn, {}, hard_timeout_sec=2.0)


def test_browser_goto_routes_through_hard_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """browser_goto 公开函数走 hard timeout wrapper, 不直接调 _impl"""
    called = {"impl": False}
    def fake_impl(_args: Any) -> Dict[str, Any]:
        called["impl"] = True
        return {"type": "ok", "marker": "via_impl"}
    monkeypatch.setattr(catfish_tools, "_browser_goto_impl", fake_impl)

    result = catfish_tools.browser_goto({"url": "http://x"})
    assert called["impl"] is True
    assert result["marker"] == "via_impl"


def test_browser_screenshot_routes_through_hard_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """browser_screenshot 也走 wrapper (跟 goto / click / fill / snapshot 一起套)"""
    called = {"impl": False}
    def fake_impl(_args: Any) -> Dict[str, Any]:
        called["impl"] = True
        return {"type": "image", "marker": "via_impl"}
    monkeypatch.setattr(catfish_tools, "_browser_screenshot_impl", fake_impl)

    result = catfish_tools.browser_screenshot({})
    assert called["impl"] is True
    assert result["marker"] == "via_impl"


def test_browser_snapshot_routes_through_hard_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """browser_snapshot 走 wrapper"""
    called = {"impl": False}
    def fake_impl(_args: Any) -> Dict[str, Any]:
        called["impl"] = True
        return {"type": "ok", "elements": []}
    monkeypatch.setattr(catfish_tools, "_browser_snapshot_impl", fake_impl)

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
    result = catfish_tools._run_with_hard_timeout(stuck_fn, {}, hard_timeout_sec=1.0)
    elapsed = _time.monotonic() - start

    # 关键 assert: wrapper 1s 后必须返, 不能等到 stuck_fn 自己结束 (60s)
    assert elapsed < 3.0, f"wrapper 卡了 {elapsed}s, BL-FIX11 没生效"
    assert result["type"] == "error"
    assert "硬超时" in result["error"]
    # cleanup
    stop_event.set()


# ============================================================
# BL-FIX16 (5/8) — catfish_browser_find_by_text 文字直接定位元素
# ============================================================
#
# 现网坑 (鸿波 5/8 晚): CAS 登录页登录按钮不是标准 <button>, snapshot DOM evaluate
# 拿不到, LLM 找不到 → 卡死. 这条工具按文字找, 不挑 tag.


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

    result = catfish_tools.browser_find_by_text({"text": "登录"})
    assert result["type"] == "ok"
    assert result["element_count"] == 2
    assert result["search_text"] == "登录"
    assert result["exact"] is False
    # 第 1 个候选拿 selector_hint
    assert result["elements"][0]["selector_hint"] == "a.login-btn"
    assert result["elements"][0]["tag_name"] == "a"
    # summary 含 selector
    assert "a.login-btn" in result["summary"]


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
    """max_results clamp 到 [1, 20]"""
    captured: list = []

    class _CapPage(_FakePage):
        def evaluate(self, _js: str, *args: Any) -> Any:
            captured.append(args[0])
            return []

    _patch_connect(monkeypatch, _CapPage())
    catfish_tools.browser_find_by_text({"text": "x", "max_results": 100})
    assert captured[0]["maxCount"] == 20  # clamp 到 20

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


def test_find_by_text_routes_through_hard_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """browser_find_by_text 也走 BL-FIX10 wrapper"""
    called = {"impl": False}

    def fake_impl(_args: Any) -> Dict[str, Any]:
        called["impl"] = True
        return {"type": "ok", "element_count": 0, "elements": []}

    monkeypatch.setattr(catfish_tools, "_browser_find_by_text_impl", fake_impl)
    catfish_tools.browser_find_by_text({"text": "test"})
    assert called["impl"] is True


# ============================================================
# BL-FIX17 (5/8) — 截图智能压缩
# ============================================================
#
# 现网 (鸿波 5/8): catfish_browser_screenshot 返 4.4MB Base64 → IPC + LLM
# context (3-4K tokens) + vision 推理三连卡死. 修法: viewport / full_page
# 自动 downscale + JPEG, element 截图保 PNG 不压.


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
