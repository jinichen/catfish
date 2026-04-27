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
# catfish_browser_goto (原生 CDP, 绕开 hermes browser_navigate)
# ============================================================


def test_browser_goto_in_native_tools_list() -> None:
    """catfish_browser_goto 必须出现在 CATFISH_NATIVE_TOOLS"""
    names = [t["name"] for t in catfish_tools.CATFISH_NATIVE_TOOLS]
    assert "catfish_browser_goto" in names


def test_browser_goto_required_url() -> None:
    """url 必填"""
    tool = next(
        t for t in catfish_tools.CATFISH_NATIVE_TOOLS
        if t["name"] == "catfish_browser_goto"
    )
    assert "url" in tool["input_schema"]["required"]


def test_browser_goto_missing_url_returns_error() -> None:
    """没传 url → error, 不去连 Chrome"""
    result = catfish_tools.browser_goto({})
    assert result["type"] == "error"
    assert "url" in result["error"]


def test_browser_goto_blank_url_returns_error() -> None:
    """url 是空白字符串 → 也算没传"""
    result = catfish_tools.browser_goto({"url": "   "})
    assert result["type"] == "error"


def test_browser_goto_chrome_unreachable() -> None:
    """Chrome 没起 → 连不上 /json/list 应返友好错误, 不抛"""
    # 改 chrome base 到一个肯定连不通的端口
    import os as _os
    _os.environ["CATFISH_CHROME_BASE"] = "http://127.0.0.1:1"  # 故意死端口
    try:
        result = catfish_tools.browser_goto(
            {"url": "https://example.com", "wait_seconds": 0}
        )
        assert result["type"] == "error"
        assert "连不上" in result["error"] or "Chrome" in result["error"]
    finally:
        del _os.environ["CATFISH_CHROME_BASE"]


def test_browser_goto_no_pages_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chrome 在跑但 /json/list 返 empty 或没 page-type → 友好错误"""
    monkeypatch.setattr(
        catfish_tools, "_http_get_json",
        lambda url, timeout_sec=3.0: [
            {"type": "iframe", "webSocketDebuggerUrl": "ws://x"},  # 不是 page
        ],
    )
    result = catfish_tools.browser_goto(
        {"url": "https://example.com", "wait_seconds": 0}
    )
    assert result["type"] == "error"
    assert "page" in result["error"]


def test_dispatch_browser_goto_routes_correctly() -> None:
    """dispatch_native('catfish_browser_goto', ...) 应该路由到 browser_goto"""
    # 走 missing url 路径, 立刻 return error 不去连 Chrome
    result = catfish_tools.dispatch_native("catfish_browser_goto", {})
    assert result["type"] == "error"
    assert "url" in result["error"]
