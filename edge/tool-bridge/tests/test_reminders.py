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
from datetime import datetime
from unittest.mock import patch

import pytest

from catfish_tool_bridge import reminders


_FS = "\x1f"
_RS = "\x1e"


def _reminder_row(
    reminder_id: str,
    title: str,
    list_name: str,
    due_date_iso: str = "",
    completed: bool = False,
    priority: int = 0,
    body: str = "",
) -> str:
    return _FS.join([
        reminder_id,
        title,
        list_name,
        due_date_iso,
        "true" if completed else "false",
        str(priority),
        body,
    ])


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


@pytest.mark.skipif(platform.system() == "Darwin", reason="非 macOS 才测")
def test_non_macos_list_reminders_returns_friendly_error():
    r = reminders.tool_list_reminders({"scope": "week"})
    assert r["ok"] is False
    assert "macOS" in r["error"]
    assert r["reminders"] == []


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


# ─── 读取 Reminders.app 条目 ─────────────────────────────


def test_list_script_uses_character_id_delimiters():
    """新版 macOS 不再接受 ASCII character，必须使用 character id。"""
    script = reminders._LIST_REMINDERS_SCRIPT
    assert script.count("(character id 30)") == 2
    assert script.count("(character id 31)") == 2
    assert "ASCII character" not in script


def test_week_query_pushes_scope_and_completion_filter_into_applescript():
    """早安查询不能先序列化全部历史提醒再等 Python 慢慢过滤。"""
    fixed_now = datetime(2026, 9, 8, 14, 0, 0)
    with patch.object(reminders, "_is_macos", return_value=True), \
         patch.object(reminders, "_now_local", return_value=fixed_now), \
         patch.object(reminders, "_run_osascript", return_value=(True, "", "")) as mock_run:
        result = reminders.tool_list_reminders({"scope": "week"})

    assert result["ok"] is True
    script = mock_run.call_args.args[0]
    assert 'set scopeMode to "week"' in script
    assert 'set includeCompleted to false' in script
    assert 'set scopeStart to date "2026-09-07 00:00:00"' in script
    assert 'set scopeEnd to date "2026-09-14 00:00:00"' in script
    # 9/10: 已完成过滤交给 Reminders 自己 (whose), 属性整列批量取 —— 不再逐条访问
    assert "whose completed is false" in script
    assert "set idList to id of candidates" in script
    assert "completed of reminderItem" not in script, "逐条访问属性 = 每条一次 Apple Event, 几千条要 30s"
    assert "set end of outputRows to rowText" in script


def test_task_library_import_uses_bounded_timeout():
    """task_library 每次读都先导 Reminders; 上限必须短于 Companion 30s RPC,
    否则导入慢 = 早安页「RPC tools/dispatch 超时」(9/10 实盘)。"""
    from catfish_tool_bridge import task_library

    assert task_library.REMINDERS_IMPORT_TIMEOUT_SEC < 15
    with patch.object(task_library.platform, "system", return_value="Darwin"), \
         patch.object(reminders, "tool_list_reminders",
                      return_value={"ok": False, "error": "osascript 超 12.0s"}) as mock_list, \
         patch.object(task_library, "list_tasks", return_value={"ok": True, "tasks": [], "count": 0}):
        result = task_library.tool_list_tasks({"scope": "active"})
    assert mock_list.call_args.kwargs["timeout_sec"] == task_library.REMINDERS_IMPORT_TIMEOUT_SEC
    assert result["ok"] is True, "导入超时要退化成本地数据, 不能让整个读失败"
    assert "超" in result["sync_warning"]


def test_parse_reminders_output_keeps_structured_fields():
    stdout = _RS.join([
        _reminder_row(
            "x-1", "ISO 资质申报", "工作", "2026-08-28T18:00:00",
            priority=1, body="准备申请材料",
        ),
        _reminder_row("x-2", "已完成事项", "家庭", completed=True),
    ])

    items = reminders._parse_reminders_output(stdout)

    assert items == [
        {
            "id": "x-1",
            "title": "ISO 资质申报",
            "list_name": "工作",
            "due_date_iso": "2026-08-28T18:00:00",
            "completed": False,
            "priority": 1,
            "body": "准备申请材料",
        },
        {
            "id": "x-2",
            "title": "已完成事项",
            "list_name": "家庭",
            "due_date_iso": None,
            "completed": True,
            "priority": 0,
            "body": "",
        },
    ]


def test_parse_reminders_output_accepts_stripped_empty_last_body():
    """_run_osascript.strip() 会吃掉末尾控制分隔符，最后一条空 body 只剩 6 字段。"""
    stdout = _reminder_row(
        "x-last", "最后一条", "工作", "2026-08-28T18:00:00",
    ).strip()

    items = reminders._parse_reminders_output(stdout)

    assert len(items) == 1
    assert items[0]["id"] == "x-last"
    assert items[0]["body"] == ""


def test_filter_reminders_supports_today_week_overdue_and_all():
    now = datetime(2026, 8, 24, 13, 30, 0)  # 周一
    items = reminders._parse_reminders_output(_RS.join([
        _reminder_row("today", "今天", "工作", "2026-08-24T18:00:00"),
        _reminder_row("week", "周五", "工作", "2026-08-28T18:00:00"),
        _reminder_row("overdue", "昨天", "工作", "2026-08-23T18:00:00"),
        _reminder_row("next", "下周", "工作", "2026-08-31T09:00:00"),
        _reminder_row("none", "无截止", "工作"),
        _reminder_row("done", "已完成", "工作", "2026-08-26T09:00:00", True),
    ]))

    assert [x["id"] for x in reminders._filter_reminders(items, "today", now=now)] == ["today"]
    assert [x["id"] for x in reminders._filter_reminders(items, "week", now=now)] == ["today", "week"]
    assert [x["id"] for x in reminders._filter_reminders(items, "overdue", now=now)] == ["overdue"]
    assert [x["id"] for x in reminders._filter_reminders(items, "all", now=now)] == [
        "overdue", "today", "week", "next", "none",
    ]


def test_filter_reminders_week_is_monday_through_sunday():
    now = datetime(2026, 8, 31, 10, 30, 0)  # 周一
    items = reminders._parse_reminders_output(_RS.join([
        _reminder_row("before", "上周日", "工作", "2026-08-30T18:00:00"),
        _reminder_row("monday", "本周一", "工作", "2026-08-31T18:00:00"),
        _reminder_row("friday", "本周五", "工作", "2026-09-04T18:00:00"),
        _reminder_row("sunday", "本周日", "工作", "2026-09-06T18:00:00"),
        _reminder_row("after", "下周一", "工作", "2026-09-07T09:00:00"),
    ]))

    assert [x["id"] for x in reminders._filter_reminders(items, "week", now=now)] == [
        "monday", "friday", "sunday",
    ]


def test_filter_reminders_can_include_completed_and_filter_list():
    now = datetime(2026, 8, 24, 13, 30, 0)
    items = reminders._parse_reminders_output(_RS.join([
        _reminder_row("work-open", "工作未完成", "工作", "2026-08-25T09:00:00"),
        _reminder_row("work-done", "工作已完成", "工作", "2026-08-26T09:00:00", True),
        _reminder_row("home", "家庭", "家庭", "2026-08-27T09:00:00"),
    ]))

    result = reminders._filter_reminders(
        items,
        "week",
        now=now,
        include_completed=True,
        list_name="工作",
        limit=1,
    )

    assert [x["id"] for x in result] == ["work-open"]


def test_list_reminders_rejects_unknown_scope_before_osascript():
    with patch.object(reminders, "_run_osascript") as mock_run:
        result = reminders.tool_list_reminders({"scope": "month"})
    assert result["ok"] is False
    assert "scope" in result["error"]
    mock_run.assert_not_called()


def test_list_reminders_permission_denied_is_actionable():
    with patch.object(reminders, "_is_macos", return_value=True), \
         patch.object(reminders, "_run_osascript", return_value=(
             False, "", "Not authorized to send Apple events to Reminders.",
         )):
        result = reminders.tool_list_reminders({"scope": "week"})
    assert result["ok"] is False
    assert result["needs_permission"] is True
    assert result["reminders"] == []
    assert "权限" in result["error"]


def test_list_reminders_week_success():
    stdout = _RS.join([
        _reminder_row("week", "本周五交周报", "工作", "2026-08-28T18:00:00"),
        _reminder_row("next", "下周任务", "工作", "2026-08-31T09:00:00"),
    ])
    fixed_now = datetime(2026, 8, 24, 13, 30, 0)
    with patch.object(reminders, "_is_macos", return_value=True), \
         patch.object(reminders, "_run_osascript", return_value=(True, stdout, "")), \
         patch.object(reminders, "_now_local", return_value=fixed_now):
        result = reminders.tool_list_reminders({"scope": "week"})
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["reminders"][0]["title"] == "本周五交周报"
    assert "本周" in result["summary"]


def test_dispatch_via_catfish_tools():
    """通过 catfish_tools.dispatch_native 走 tool 路由."""
    from catfish_tool_bridge import catfish_tools
    with patch.object(reminders, "_is_macos", return_value=True), \
         patch.object(reminders, "_run_osascript", return_value=(True, "test", "")):
        r = catfish_tools.dispatch_native("catfish_create_reminder", {"title": "test"})
    assert r["ok"] is True


def test_list_reminders_dispatch_via_catfish_tools():
    from catfish_tool_bridge import catfish_tools
    with patch.object(reminders, "_is_macos", return_value=True), \
         patch.object(reminders, "_run_osascript", return_value=(True, "", "")):
        r = catfish_tools.dispatch_native("catfish_list_reminders", {"scope": "week"})
    assert r["ok"] is True
    assert r["reminders"] == []


def test_schema_in_native_tools_list():
    """catfish_create_reminder + catfish_list_reminder_lists 都在 NATIVE_TOOL_NAMES."""
    from catfish_tool_bridge import catfish_tools
    assert "catfish_create_reminder" in catfish_tools.NATIVE_TOOL_NAMES
    assert "catfish_list_reminder_lists" in catfish_tools.NATIVE_TOOL_NAMES
    assert "catfish_list_reminders" in catfish_tools.NATIVE_TOOL_NAMES
    schemas = {t["name"]: t for t in catfish_tools.CATFISH_NATIVE_TOOLS}
    assert "title" in schemas["catfish_create_reminder"]["input_schema"]["required"]
    # description 必告诉 LLM 跟 notify 区别
    assert "notify" in schemas["catfish_create_reminder"]["description"]
    assert "iCloud" in schemas["catfish_create_reminder"]["description"]
    list_schema = schemas["catfish_list_reminders"]
    assert "scope" in list_schema["input_schema"]["properties"]
    assert "Reminders.app" in list_schema["description"]
    assert "Hermes todo" in list_schema["description"]
