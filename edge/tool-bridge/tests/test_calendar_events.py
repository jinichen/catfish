"""BL-CALENDAR (5/14 0:30 鸿波 ISO 现场审核会议踩坑后拍板) — calendar_events 单测.

非 macOS / CI 上 osascript 不存在, 测试不真调 Calendar.app — 只测:
1. 输入校验 (title / start_iso 必填)
2. ISO 8601 → AppleScript date 字符串转换
3. AppleScript 字符串 escape (防注入)
4. end_iso 默认 = start + 1h
5. 非 macOS 平台返友好错误
6. mock osascript 返失败时的友好错误处理
7. AppleScript record 单行拼装 (不允许多行换行 — 鸿波 5/14 踩的坑根因)
"""
from __future__ import annotations

import platform
from unittest.mock import patch

import pytest

from catfish_tool_bridge import calendar_events


# ─── 输入校验 ──────────────────────────────────────────────


def test_title_required():
    r = calendar_events.tool_create_calendar_event({"start_iso": "2026-05-18T08:40:00"})
    assert r["ok"] is False
    assert "title" in r["error"]


def test_title_empty_string_rejected():
    r = calendar_events.tool_create_calendar_event({
        "title": "   ", "start_iso": "2026-05-18T08:40:00",
    })
    assert r["ok"] is False
    assert "title" in r["error"]


def test_start_iso_required():
    r = calendar_events.tool_create_calendar_event({"title": "ISO 现场审核"})
    assert r["ok"] is False
    assert "start_iso" in r["error"]


def test_start_iso_invalid_format():
    r = calendar_events.tool_create_calendar_event({
        "title": "test", "start_iso": "not-a-date",
    })
    assert r["ok"] is False
    assert "start_iso" in r["error"]


def test_end_iso_invalid_format_when_provided():
    r = calendar_events.tool_create_calendar_event({
        "title": "test",
        "start_iso": "2026-05-18T08:40:00",
        "end_iso": "wrong",
    })
    assert r["ok"] is False
    assert "end_iso" in r["error"]


# ─── ISO 8601 → AppleScript date 转换 ──────────────────────


def test_convert_iso_simple():
    """'2026-05-18T08:40:00' → '2026-05-18 08:40:00'"""
    assert calendar_events._convert_iso_to_applescript_date("2026-05-18T08:40:00") == "2026-05-18 08:40:00"


def test_convert_iso_with_z_timezone():
    """'2026-05-18T08:40:00Z' → '2026-05-18 08:40:00' (砍 Z)"""
    assert calendar_events._convert_iso_to_applescript_date("2026-05-18T08:40:00Z") == "2026-05-18 08:40:00"


def test_convert_iso_with_offset_timezone():
    """'2026-05-18T08:40:00+08:00' → '2026-05-18 08:40:00' (砍时区)"""
    assert calendar_events._convert_iso_to_applescript_date("2026-05-18T08:40:00+08:00") == "2026-05-18 08:40:00"


def test_convert_iso_negative_offset():
    """'2026-05-18T08:40:00-05:00' → '2026-05-18 08:40:00'"""
    assert calendar_events._convert_iso_to_applescript_date("2026-05-18T08:40:00-05:00") == "2026-05-18 08:40:00"


# ─── AppleScript 字符串 escape (防注入) ──────────────────


def test_escape_double_quote():
    assert calendar_events._escape_applescript_string('hello "world"') == 'hello \\"world\\"'


def test_escape_backslash():
    assert calendar_events._escape_applescript_string("path\\to\\file") == "path\\\\to\\\\file"


def test_escape_chinese_unaffected():
    assert calendar_events._escape_applescript_string("ISO 现场审核") == "ISO 现场审核"


# ─── end_iso 默认 (start + 1h) ─────────────────────────────


def test_default_end_iso_simple():
    """start 08:40 → end 09:40"""
    end = calendar_events._default_end_iso_from_start("2026-05-18T08:40:00", hours=1.0)
    assert end.startswith("2026-05-18T09:40")


def test_default_end_iso_custom_hours():
    """start 08:40 + 8.5h → end 17:10"""
    end = calendar_events._default_end_iso_from_start("2026-05-18T08:40:00", hours=8.5)
    assert end.startswith("2026-05-18T17:10")


def test_default_end_iso_crosses_midnight():
    """start 23:30 + 2h → next day 01:30"""
    end = calendar_events._default_end_iso_from_start("2026-05-18T23:30:00", hours=2.0)
    assert end.startswith("2026-05-19T01:30")


# ─── 非 macOS 平台兜底 ───────────────────────────────────


@pytest.mark.skipif(platform.system() == "Darwin", reason="非 macOS 才测")
def test_non_macos_returns_friendly_error():
    r = calendar_events.tool_create_calendar_event({
        "title": "test", "start_iso": "2026-05-18T08:40:00",
    })
    assert r["ok"] is False
    assert "macOS" in r["error"]


@pytest.mark.skipif(platform.system() == "Darwin", reason="非 macOS 才测")
def test_non_macos_list_returns_friendly_error():
    r = calendar_events.tool_list_calendars({})
    assert r["ok"] is False
    assert r["calendar_names"] == []


# ─── mock osascript 错误处理 (跨平台跑) ──────────────────


def test_macos_permission_denied_friendly_error():
    """mock osascript 返 'Not authorized' → 友好错误 + needs_permission flag."""
    with patch.object(calendar_events, "_is_macos", return_value=True), \
         patch.object(calendar_events, "_run_osascript", return_value=(False, "", "Not authorized to send Apple events to Calendar.")):
        r = calendar_events.tool_create_calendar_event({
            "title": "test", "start_iso": "2026-05-18T08:40:00",
        })
    assert r["ok"] is False
    assert r.get("needs_permission") is True
    assert "权限" in r["error"]
    assert "日历" in r["error"]


def test_macos_calendar_not_found_friendly_error():
    """mock osascript 返 calendar 不存在 → 友好错误 + calendar_not_found field."""
    with patch.object(calendar_events, "_is_macos", return_value=True), \
         patch.object(calendar_events, "_run_osascript", return_value=(False, "", "Can’t get calendar \"工作\" of application \"Calendar\".")):
        r = calendar_events.tool_create_calendar_event({
            "title": "test", "start_iso": "2026-05-18T08:40:00", "calendar_name": "工作",
        })
    assert r["ok"] is False
    assert r.get("calendar_not_found") == "工作"
    assert "catfish_list_calendars" in r["error"]


def test_macos_success_minimal_args():
    """只传 title + start_iso → 默认 end_iso = start + 1h."""
    with patch.object(calendar_events, "_is_macos", return_value=True), \
         patch.object(calendar_events, "_run_osascript", return_value=(True, "ISO 现场审核", "")):
        r = calendar_events.tool_create_calendar_event({
            "title": "ISO 现场审核",
            "start_iso": "2026-05-18T08:40:00",
        })
    assert r["ok"] is True
    assert r["event_summary"] == "ISO 现场审核"
    assert r["start_iso"] == "2026-05-18T08:40:00"
    assert r["end_iso"].startswith("2026-05-18T09:40")  # 默认 1h


def test_macos_success_full_args():
    """完整参数 → ok + summary 含 location + iCloud 提示."""
    with patch.object(calendar_events, "_is_macos", return_value=True), \
         patch.object(calendar_events, "_run_osascript", return_value=(True, "ISO 现场审核", "")):
        r = calendar_events.tool_create_calendar_event({
            "title": "ISO 现场审核",
            "start_iso": "2026-05-18T08:40:00",
            "end_iso": "2026-05-18T17:10:00",
            "location": "409 会议室",
            "description": "Q2 ISO 内审第一天",
            "calendar_name": "工作",
        })
    assert r["ok"] is True
    assert r["location"] == "409 会议室"
    assert r["calendar_name"] == "工作"
    assert "409 会议室" in r["summary"]
    assert "iCloud" in r["summary"]


# ─── AppleScript record 单行拼装 (鸿波 5/14 踩的坑根因) ───────


def test_script_record_is_single_line():
    """关键: AppleScript record 必须压一行, 多行换行 AppleScript 会 syntax error.

    这是鸿波 5/14 0:30 写 osascript 创建 ISO 会议时踩的坑根因 — 我们 tool 实现
    必须保证 record 段不含换行.
    """
    with patch.object(calendar_events, "_is_macos", return_value=True), \
         patch.object(calendar_events, "_run_osascript", return_value=(True, "test", "")) as mock_run:
        calendar_events.tool_create_calendar_event({
            "title": "ISO 现场审核",
            "start_iso": "2026-05-18T08:40:00",
            "end_iso": "2026-05-18T17:10:00",
            "location": "409 会议室",
            "description": "Q2 ISO 内审第一天",
        })
    script = mock_run.call_args[0][0]
    # 找到 'with properties {' 之后到 '}' 之前的内容, 必须无换行
    start = script.index("with properties {")
    end_idx = script.index("}", start)
    record_segment = script[start : end_idx + 1]
    assert "\n" not in record_segment, (
        f"AppleScript record 段含换行会 syntax error! 段内容:\n{record_segment}"
    )
    # 同时确认必含字段都在
    assert "summary:" in record_segment
    assert "start date:date" in record_segment
    assert "end date:date" in record_segment
    assert "location:" in record_segment
    assert "description:" in record_segment


def test_script_record_skips_optional_fields_when_empty():
    """没传 location / description 时不出现在 record 里 (避免拼空字符串)."""
    with patch.object(calendar_events, "_is_macos", return_value=True), \
         patch.object(calendar_events, "_run_osascript", return_value=(True, "test", "")) as mock_run:
        calendar_events.tool_create_calendar_event({
            "title": "无地点会议",
            "start_iso": "2026-05-18T08:40:00",
        })
    script = mock_run.call_args[0][0]
    assert "location:" not in script
    assert "description:" not in script


def test_macos_list_calendars_success():
    """mock osascript 返 calendar 名字符串 → 解析成数组."""
    with patch.object(calendar_events, "_is_macos", return_value=True), \
         patch.object(calendar_events, "_run_osascript", return_value=(True, "工作, 家庭, 我的日历", "")):
        r = calendar_events.tool_list_calendars({})
    assert r["ok"] is True
    assert r["calendar_names"] == ["工作", "家庭", "我的日历"]
    assert r["count"] == 3


def test_macos_list_calendars_empty():
    with patch.object(calendar_events, "_is_macos", return_value=True), \
         patch.object(calendar_events, "_run_osascript", return_value=(True, "", "")):
        r = calendar_events.tool_list_calendars({})
    assert r["ok"] is True
    assert r["calendar_names"] == []
    assert r["count"] == 0


def test_dispatch_via_catfish_tools():
    """通过 catfish_tools.dispatch_native 走 tool 路由."""
    from catfish_tool_bridge import catfish_tools
    with patch.object(calendar_events, "_is_macos", return_value=True), \
         patch.object(calendar_events, "_run_osascript", return_value=(True, "test", "")):
        r = catfish_tools.dispatch_native("catfish_create_calendar_event", {
            "title": "test", "start_iso": "2026-05-18T08:40:00",
        })
    assert r["ok"] is True


def test_schema_in_native_tools_list():
    """catfish_create_calendar_event + catfish_list_calendars 都在 NATIVE_TOOL_NAMES."""
    from catfish_tool_bridge import catfish_tools
    assert "catfish_create_calendar_event" in catfish_tools.NATIVE_TOOL_NAMES
    assert "catfish_list_calendars" in catfish_tools.NATIVE_TOOL_NAMES
    schemas = {t["name"]: t for t in catfish_tools.CATFISH_NATIVE_TOOLS}
    schema = schemas["catfish_create_calendar_event"]
    assert "title" in schema["input_schema"]["required"]
    assert "start_iso" in schema["input_schema"]["required"]
    # description 必告诉 LLM 跟 reminder / notify 区别 + 警告别自己写 osascript
    assert "reminder" in schema["description"]
    assert "iCloud" in schema["description"]
    assert "osascript" in schema["description"]  # 警告"别写脚本, 直接调本 tool"
