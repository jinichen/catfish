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
    tool = next(
        t for t in catfish_tools.CATFISH_NATIVE_TOOLS
        if t["name"] == "catfish_browser_fill"
    )
    required = set(tool["input_schema"]["required"])
    assert {"selector", "text"} <= required


def test_browser_fill_password_blocked() -> None:
    """selector 含 password → 拒绝, 不让自动填密码"""
    result = catfish_tools.browser_fill({
        "selector": "input[name='password']",
        "text": "secret123",
    })
    assert result["type"] == "error"
    assert "密码" in result["error"]


def test_browser_fill_pwd_keyword_blocked() -> None:
    """各种密码框命名变体都拒"""
    for sel in ["input#pwd", "input[name='passwd']", "#user-password"]:
        result = catfish_tools.browser_fill({"selector": sel, "text": "x"})
        assert result["type"] == "error", f"selector {sel} 应该被拒但通过了"


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
