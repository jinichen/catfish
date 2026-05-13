"""BL-REMINDER (5/13 鸿波"macOS 提醒联动") — catfish_create_reminder 单测.

非 macOS 上 / CI 上 osascript 不存在, 测试不真调 Reminders.app — 只测:
1. 输入校验 (title 必填 / priority 范围)
2. ISO 8601 → AppleScript date 字符串转换
3. AppleScript 字符串 escape (防注入)
4. 非 macOS 平台返友好错误
5. mock osascript 返失败时的友好错误处理
"""
from __future__ import annotations

import platform
from unittest.mock import patch

import pytest

from catfish_tool_bridge import reminders


# ─── 输入校验 ──────────────────────────────────────────────


def test_title_required():
    r = reminders.tool_create_reminder({})
    assert r["ok"] is False
    assert "title" in r["error"]


def test_title_empty_string_rejected():
    r = reminders.tool_create_reminder({"title": "   "})
    assert r["ok"] is False
    assert "title" in r["error"]


def test_invalid_priority():
    r = reminders.tool_create_reminder({"title": "test", "priority": "not-a-number"})
    assert r["ok"] is False
    assert "priority" in r["error"]


def test_invalid_due_date_iso():
    """ISO 转换内部不抛, 但格式怪异时 AppleScript 会失败 — 这里只测内部不崩."""
    # 空白 ISO 会被 strip 后视为 "" — 不传 due_date
    r = reminders.tool_create_reminder({"title": "test", "due_date_iso": ""})
    # 这条会进 osascript 调用 (非 macOS 直接返平台错), 不测具体行为
    assert "ok" in r


# ─── ISO 8601 → AppleScript date 转换 ──────────────────────


def test_convert_iso_simple():
    """'2026-05-14T09:00:00' → '2026-05-14 09:00:00'"""
    assert reminders._convert_iso_to_applescript_date("2026-05-14T09:00:00") == "2026-05-14 09:00:00"


def test_convert_iso_with_z_timezone():
    """'2026-05-14T09:00:00Z' → '2026-05-14 09:00:00' (砍 Z)"""
    assert reminders._convert_iso_to_applescript_date("2026-05-14T09:00:00Z") == "2026-05-14 09:00:00"


def test_convert_iso_with_offset_timezone():
    """'2026-05-14T09:00:00+08:00' → '2026-05-14 09:00:00' (砍时区)"""
    assert reminders._convert_iso_to_applescript_date("2026-05-14T09:00:00+08:00") == "2026-05-14 09:00:00"


def test_convert_iso_negative_offset():
    """'2026-05-14T09:00:00-05:00' → '2026-05-14 09:00:00'"""
    assert reminders._convert_iso_to_applescript_date("2026-05-14T09:00:00-05:00") == "2026-05-14 09:00:00"


def test_convert_iso_preserves_date_dashes():
    """日期里的 - 不能被砍 (砍时区时只看 idx > 10 后的)."""
    out = reminders._convert_iso_to_applescript_date("2026-05-14T09:00:00")
    assert "2026-05-14" in out


# ─── AppleScript 字符串 escape (防注入) ──────────────────


def test_escape_double_quote():
    assert reminders._escape_applescript_string('hello "world"') == 'hello \\"world\\"'


def test_escape_backslash():
    assert reminders._escape_applescript_string("path\\to\\file") == "path\\\\to\\\\file"


def test_escape_both():
    """先 escape \\ 再 escape \" — 顺序重要."""
    out = reminders._escape_applescript_string('a\\b"c')
    # \\ → \\\\ 先做, 然后 " → \"
    assert out == 'a\\\\b\\"c'


def test_escape_chinese_unaffected():
    """中文不该被 escape."""
    assert reminders._escape_applescript_string("提醒事项") == "提醒事项"


# ─── 非 macOS 平台兜底 ───────────────────────────────────


@pytest.mark.skipif(platform.system() == "Darwin", reason="非 macOS 才测")
def test_non_macos_returns_friendly_error():
    r = reminders.tool_create_reminder({"title": "test"})
    assert r["ok"] is False
    assert "macOS" in r["error"]


@pytest.mark.skipif(platform.system() == "Darwin", reason="非 macOS 才测")
def test_non_macos_list_returns_friendly_error():
    r = reminders.tool_list_reminder_lists({})
    assert r["ok"] is False
    assert r["list_names"] == []


# ─── mock osascript 错误处理 (跨平台跑) ──────────────────


def test_macos_permission_denied_friendly_error():
    """mock osascript 返 'Not authorized' → 友好错误 + needs_permission flag."""
    with patch.object(reminders, "_is_macos", return_value=True), \
         patch.object(reminders, "_run_osascript", return_value=(False, "", "Not authorized to send Apple events to Reminders.")):
        r = reminders.tool_create_reminder({"title": "test"})
    assert r["ok"] is False
    assert r.get("needs_permission") is True
    assert "权限" in r["error"]
    assert "提醒事项" in r["error"]


def test_macos_list_not_found_friendly_error():
    """mock osascript 返 list 不存在 → 友好错误 + list_not_found field."""
    with patch.object(reminders, "_is_macos", return_value=True), \
         patch.object(reminders, "_run_osascript", return_value=(False, "", "Can’t get list \"工作\" of application \"Reminders\".")):
        r = reminders.tool_create_reminder({"title": "test", "list_name": "工作"})
    assert r["ok"] is False
    assert r.get("list_not_found") == "工作"
    assert "catfish_list_reminder_lists" in r["error"]


def test_macos_success():
    """mock osascript 成功 → ok=True + reminder_name."""
    with patch.object(reminders, "_is_macos", return_value=True), \
         patch.object(reminders, "_run_osascript", return_value=(True, "交月报", "")):
        r = reminders.tool_create_reminder({
            "title": "交月报",
            "due_date_iso": "2026-05-14T09:00:00",
        })
    assert r["ok"] is True
    assert r["reminder_name"] == "交月报"
    assert r["due_date_iso"] == "2026-05-14T09:00:00"
    assert "交月报" in r["summary"]
    assert "iCloud" in r["summary"]


def test_macos_success_minimal_args():
    """只传 title 也能成功 (无 body / due / priority)."""
    with patch.object(reminders, "_is_macos", return_value=True), \
         patch.object(reminders, "_run_osascript", return_value=(True, "买菜", "")):
        r = reminders.tool_create_reminder({"title": "买菜"})
    assert r["ok"] is True
    assert r["reminder_name"] == "买菜"
    assert r["due_date_iso"] is None
    assert "无截止" in r["summary"]


def test_macos_list_lists_success():
    """mock osascript 返 list 名字符串 → 解析成数组."""
    with patch.object(reminders, "_is_macos", return_value=True), \
         patch.object(reminders, "_run_osascript", return_value=(True, "提醒事项, 工作, 家庭", "")):
        r = reminders.tool_list_reminder_lists({})
    assert r["ok"] is True
    assert r["list_names"] == ["提醒事项", "工作", "家庭"]
    assert r["count"] == 3


def test_macos_list_lists_empty():
    with patch.object(reminders, "_is_macos", return_value=True), \
         patch.object(reminders, "_run_osascript", return_value=(True, "", "")):
        r = reminders.tool_list_reminder_lists({})
    assert r["ok"] is True
    assert r["list_names"] == []
    assert r["count"] == 0


def test_priority_clamping_to_0_9():
    """priority 超 9 应钳到 9, 负数钳到 0."""
    with patch.object(reminders, "_is_macos", return_value=True), \
         patch.object(reminders, "_run_osascript", return_value=(True, "test", "")) as mock_run:
        reminders.tool_create_reminder({"title": "test", "priority": 99})
    script = mock_run.call_args[0][0]
    assert "priority:9" in script

    with patch.object(reminders, "_is_macos", return_value=True), \
         patch.object(reminders, "_run_osascript", return_value=(True, "test", "")) as mock_run:
        reminders.tool_create_reminder({"title": "test", "priority": -5})
    script = mock_run.call_args[0][0]
    assert "priority:0" in script


def test_dispatch_via_catfish_tools():
    """通过 catfish_tools.dispatch_native 走 tool 路由."""
    from catfish_tool_bridge import catfish_tools
    with patch.object(reminders, "_is_macos", return_value=True), \
         patch.object(reminders, "_run_osascript", return_value=(True, "test", "")):
        r = catfish_tools.dispatch_native("catfish_create_reminder", {"title": "test"})
    assert r["ok"] is True


def test_schema_in_native_tools_list():
    """catfish_create_reminder + catfish_list_reminder_lists 都在 NATIVE_TOOL_NAMES."""
    from catfish_tool_bridge import catfish_tools
    assert "catfish_create_reminder" in catfish_tools.NATIVE_TOOL_NAMES
    assert "catfish_list_reminder_lists" in catfish_tools.NATIVE_TOOL_NAMES
    schemas = {t["name"]: t for t in catfish_tools.CATFISH_NATIVE_TOOLS}
    assert "title" in schemas["catfish_create_reminder"]["input_schema"]["required"]
    # description 必告诉 LLM 跟 notify 区别
    assert "notify" in schemas["catfish_create_reminder"]["description"]
    assert "iCloud" in schemas["catfish_create_reminder"]["description"]
