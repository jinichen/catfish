"""BL-REMINDER (5/13 鸿波"macOS 提醒联动") — catfish_create_reminder 模块.

跟本机任务库（用户行动事实源）以及 5/2 BL-E13 notify（macOS 通知中心右上角横幅消息）互补:
- notify: 一次性弹窗, 几秒消失, 不持久, 不跨设备
- task library: 用户行动、状态和截止时间的本机事实源
- create_reminder: 直接系统提醒, 用户能勾完成, iCloud 同步到 iPhone/iPad

实现走 osascript + Reminders.app (跟 commands/system.rs Tauri command 同源).
tool-bridge 这边是给 LLM 调用用 (走 hermes adapter dispatch_native), Companion
里 Tauri command 是给 UI 直接调 (e.g. 仪表盘加"加 reminder" 按钮).

为什么两份实现? 因为 tool-bridge 跟 Companion 是不同进程, 各自需要 osascript 调用.

用例:
- 普通用户行动 → LLM 调 catfish_create_task(title='交月报', due_date_iso='2026-05-14T09:00:00')
- 用户明确要求写入系统提醒 → LLM 调 catfish_create_reminder(title='交月报', due_date_iso='...')
- 需要把任务库行动显示在 macOS Reminders → 调 catfish_sync_tasks_to_reminders
"""
from __future__ import annotations

import logging
import platform
import shlex
import subprocess
from datetime import datetime, time, timedelta
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.reminders")

_FIELD_SEPARATOR = "\x1f"
_RECORD_SEPARATOR = "\x1e"
_LIST_SCOPES = frozenset({"today", "week", "overdue", "all"})


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _run_osascript(script: str, timeout_sec: float = 10.0) -> tuple[bool, str, str]:
    """跑一段 AppleScript, 返 (success, stdout, stderr).

    timeout_sec: 防卡住. Reminders.app 没启动的话首次会慢一点 (1-2 秒), 给 10 秒够.
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        return (
            result.returncode == 0,
            (result.stdout or "").strip(),
            (result.stderr or "").strip(),
        )
    except subprocess.TimeoutExpired:
        return (False, "", f"osascript 超 {timeout_sec}s")
    except FileNotFoundError:
        return (False, "", "osascript 命令不存在 (非 macOS?)")
    except Exception as e:  # noqa: BLE001
        return (False, "", f"osascript 调用异常: {e}")


def _escape_applescript_string(s: str) -> str:
    """AppleScript 字符串里 " 和 \\ 都要 escape."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _convert_iso_to_applescript_date(iso: str) -> str:
    """ISO 8601 (e.g. '2026-05-14T09:00:00') → AppleScript 'YYYY-MM-DD HH:MM:SS'.

    AppleScript 接受 'date "YYYY-MM-DD HH:MM:SS"' 形式 (本地时区).
    简单转换: 替换 T 为空格, 砍掉时区后缀.
    """
    s = iso.replace("T", " ")
    # 砍掉 +08:00 / +0800 / Z 之类时区后缀
    for sep in ("+", "-"):
        # 找 HH:MM 之后的 +/-, 不是日期里的 -
        idx = s.rfind(sep)
        if idx > 10:  # 日期 yyyy-mm-dd 有 - 在前 10 字符内
            s = s[:idx]
            break
    if s.endswith("Z"):
        s = s[:-1]
    return s.strip()


def tool_create_reminder(args: dict[str, Any]) -> dict[str, Any]:
    """catfish_create_reminder tool 入口.

    顺序: 输入校验 → 平台校验 → osascript.
    校验放前面是为了 LLM 错调时不论什么平台都能拿到清晰错误 (e.g. 忘传 title).
    """
    title = (args.get("title") or "").strip()
    if not title:
        return {"ok": False, "error": "title 不能空 (catfish_create_reminder 必填字段)"}

    body = (args.get("body") or "").strip()
    due_date_iso = (args.get("due_date_iso") or "").strip()
    list_name = (args.get("list_name") or "提醒事项").strip()
    priority_raw = args.get("priority")

    # priority 校验提前 (LLM 传错可能性高), 平台校验之前
    priority_int: int | None = None
    if priority_raw is not None:
        try:
            priority_int = max(0, min(9, int(priority_raw)))
        except (TypeError, ValueError):
            return {"ok": False, "error": f"priority 应是 0-9 整数, got: {priority_raw!r}"}

    if not _is_macos():
        return {
            "ok": False,
            "error": "create_reminder 只 macOS 支持 (走 Reminders.app). 当前平台: "
                     + platform.system(),
        }

    # 拼 AppleScript properties record
    safe_title = _escape_applescript_string(title)
    safe_list = _escape_applescript_string(list_name)
    props = [f'name:"{safe_title}"']

    if body:
        safe_body = _escape_applescript_string(body)
        props.append(f'body:"{safe_body}"')

    if due_date_iso:
        try:
            applescript_date = _convert_iso_to_applescript_date(due_date_iso)
            safe_date = _escape_applescript_string(applescript_date)
            props.append(f'due date:date "{safe_date}"')
        except Exception as e:  # noqa: BLE001
            return {
                "ok": False,
                "error": f"due_date_iso 格式错 (期望 'YYYY-MM-DDTHH:MM:SS'): {e}",
            }

    if priority_int is not None:
        props.append(f"priority:{priority_int}")

    script = f'''tell application "Reminders"
    set targetList to first list whose name is "{safe_list}"
    set newReminder to make new reminder at end of targetList with properties {{{", ".join(props)}}}
    return name of newReminder
end tell'''

    ok, stdout, stderr = _run_osascript(script)
    if not ok:
        # 常见错误友好化
        if "Not authorized" in stderr or "权限" in stderr or "not allowed" in stderr.lower():
            return {
                "ok": False,
                "error": (
                    "Reminders.app 权限未给 — 系统设置 → 隐私与安全性 → 提醒事项 → "
                    "勾上 Catfish Companion (或 Terminal / Python). 然后再试. "
                    f"原始错误: {stderr}"
                ),
                "needs_permission": True,
            }
        if "Can’t get list" in stderr or "can’t get" in stderr.lower() or "doesn’t exist" in stderr.lower():
            return {
                "ok": False,
                "error": (
                    f"list \"{list_name}\" 不存在. 可用 list 调 catfish_list_reminder_lists 看. "
                    f"中文系统默认 '提醒事项', 英文系统 'Reminders'. 原始错误: {stderr}"
                ),
                "list_not_found": list_name,
            }
        return {"ok": False, "error": f"osascript 失败: {stderr}"}

    logger.info("BL-REMINDER: 创建提醒 '%s' (list=%s, due=%s)", title, list_name, due_date_iso or "无")
    return {
        "ok": True,
        "reminder_name": stdout or title,
        "list_name": list_name,
        "due_date_iso": due_date_iso or None,
        "summary": f"⏰ 已在 Reminders.app 「{list_name}」list 创建提醒 「{title}」"
                   + (f" — {due_date_iso}" if due_date_iso else " (无截止)")
                   + ". iCloud 同步到 iPhone/iPad. 用户在 Reminders.app 里能勾完成.",
    }


def tool_list_reminder_lists(args: dict[str, Any]) -> dict[str, Any]:
    """catfish_list_reminder_lists tool 入口."""
    if not _is_macos():
        return {
            "ok": False,
            "error": "list_reminder_lists 只 macOS 支持. 当前平台: " + platform.system(),
            "list_names": [],
        }

    script = '''tell application "Reminders"
    set lst to {}
    repeat with L in lists
        set end of lst to name of L
    end repeat
    return lst
end tell'''

    ok, stdout, stderr = _run_osascript(script)
    if not ok:
        if "Not authorized" in stderr or "权限" in stderr:
            return {
                "ok": False,
                "error": "Reminders.app 权限未给 — 系统设置 → 隐私与安全性 → 提醒事项",
                "needs_permission": True,
                "list_names": [],
            }
        return {"ok": False, "error": f"osascript 失败: {stderr}", "list_names": []}

    # osascript list 返回是 ", " 分隔字符串
    lists = [s.strip() for s in stdout.split(", ") if s.strip()]
    return {
        "ok": True,
        "list_names": lists,
        "count": len(lists),
        "summary": f"📋 macOS Reminders.app 有 {len(lists)} 个 list: {', '.join(lists) if lists else '(无)'}",
    }


def _now_local() -> datetime:
    """当前本地时间；独立函数便于边界测试固定时间。"""
    return datetime.now()


def _parse_reminders_output(stdout: str) -> list[dict[str, Any]]:
    """解析 AppleScript 返回的稳定分隔格式。

    字段依次为 id/title/list/due/completed/priority/body。AppleScript 侧会把
    换行和两个控制分隔符替换为空格，避免用户文本破坏记录边界。
    """
    reminders: list[dict[str, Any]] = []
    for row in stdout.split(_RECORD_SEPARATOR):
        if not row:
            continue
        fields = row.split(_FIELD_SEPARATOR)
        # _run_osascript 对整个 stdout 做 strip；最后一条 body 为空时，末尾的
        # ASCII 31 也会被当作空白吃掉，只剩 6 字段。仅允许这个明确形态补空 body。
        if len(fields) == 6:
            fields.append("")
        if len(fields) != 7:
            logger.warning("跳过无法解析的 Reminders 记录: fields=%d", len(fields))
            continue
        reminder_id, title, list_name, due, completed, priority, body = fields
        try:
            priority_value = int(priority or 0)
        except ValueError:
            priority_value = 0
        reminders.append({
            "id": reminder_id,
            "title": title,
            "list_name": list_name,
            "due_date_iso": due or None,
            "completed": completed.lower() == "true",
            "priority": priority_value,
            "body": body,
        })
    return reminders


def _parse_local_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _scope_bounds(scope: str, now: datetime) -> tuple[datetime, datetime] | None:
    """返回 AppleScript 预过滤所需的本地自然日边界。"""
    today_start = datetime.combine(now.date(), time.min)
    if scope == "today":
        return today_start, today_start + timedelta(days=1)
    if scope == "week":
        week_start = today_start - timedelta(days=today_start.weekday())
        return week_start, week_start + timedelta(days=7)
    if scope == "overdue":
        # AppleScript cannot reliably parse datetime.min; reminders predating
        # 1970 are outside any supported local mailbox/calendar dataset.
        return datetime(1970, 1, 1), today_start
    return None


def _build_list_reminders_script(
    scope: str,
    *,
    include_completed: bool,
    list_name: str,
    now: datetime,
) -> str:
    """为 Reminders 生成带服务端预过滤的 AppleScript。

    原脚本会把所有清单、所有字段先完整传回 Python，再由 Python 做 scope
    筛选。清单或历史提醒较多时，AppleScript 序列化本身就可能超过早安页的
    15 秒来源超时。把同样的条件前移到 AppleScript，只序列化候选记录；Python
    侧仍保留二次过滤，防止时区或 AppleScript 类型转换差异越界。
    """
    bounds = _scope_bounds(scope, now)
    if bounds is None:
        # all 不会用到边界，但仍填合法的本地日期，避免 AppleScript 无法解析
        # datetime.min/max 这类超出系统日期范围的值。
        scope_start, scope_end = now, now
    else:
        scope_start, scope_end = bounds

    replacements = {
        "__SCOPE__": _escape_applescript_string(scope),
        "__INCLUDE_COMPLETED__": "true" if include_completed else "false",
        "__LIST_NAME__": _escape_applescript_string(list_name),
        "__SCOPE_START__": _convert_iso_to_applescript_date(
            scope_start.isoformat(timespec="seconds")
        ),
        "__SCOPE_END__": _convert_iso_to_applescript_date(
            scope_end.isoformat(timespec="seconds")
        ),
    }
    script = _LIST_REMINDERS_SCRIPT
    for marker, value in replacements.items():
        script = script.replace(marker, value)
    return script


def _filter_reminders(
    reminders: list[dict[str, Any]],
    scope: str,
    *,
    now: datetime | None = None,
    include_completed: bool = False,
    list_name: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """按本地自然日/自然周筛选并稳定排序。"""
    current = now or _now_local()
    today_start = datetime.combine(current.date(), time.min)
    tomorrow_start = today_start + timedelta(days=1)
    week_start = today_start - timedelta(days=today_start.weekday())
    next_week_start = week_start + timedelta(days=7)

    selected: list[tuple[datetime | None, dict[str, Any]]] = []
    for reminder in reminders:
        if list_name and reminder.get("list_name") != list_name:
            continue
        if not include_completed and reminder.get("completed"):
            continue

        due = _parse_local_iso(reminder.get("due_date_iso"))
        if due is not None and due.tzinfo is not None:
            due = due.astimezone().replace(tzinfo=None)

        matches = scope == "all"
        if scope == "today":
            matches = due is not None and today_start <= due < tomorrow_start
        elif scope == "week":
            matches = due is not None and week_start <= due < next_week_start
        elif scope == "overdue":
            matches = (
                due is not None
                and due < today_start
                and not reminder.get("completed")
            )
        if matches:
            selected.append((due, reminder))

    selected.sort(key=lambda item: (item[0] is None, item[0] or datetime.max, item[1]["title"]))
    return [item[1] for item in selected[:limit]]


_LIST_REMINDERS_SCRIPT = r'''on cleanText(rawValue)
    if rawValue is missing value then return ""
    set valueText to rawValue as text
    set AppleScript's text item delimiters to {return, linefeed, (character id 30), (character id 31)}
    set valueParts to text items of valueText
    set AppleScript's text item delimiters to " "
    set valueText to valueParts as text
    set AppleScript's text item delimiters to ""
    return valueText
end cleanText

on pad2(numberValue)
    set valueText to numberValue as text
    if (count of valueText) is 1 then return "0" & valueText
    return valueText
end pad2

on isoDate(dateValue)
    set yearText to (year of dateValue as integer) as text
    set monthText to my pad2(month of dateValue as integer)
    set dayText to my pad2(day of dateValue as integer)
    set hourText to my pad2(hours of dateValue)
    set minuteText to my pad2(minutes of dateValue)
    set secondText to my pad2(seconds of dateValue)
    return yearText & "-" & monthText & "-" & dayText & "T" & hourText & ":" & minuteText & ":" & secondText
end isoDate

set scopeMode to "__SCOPE__"
set includeCompleted to __INCLUDE_COMPLETED__
set targetListName to "__LIST_NAME__"
set scopeStart to date "__SCOPE_START__"
set scopeEnd to date "__SCOPE_END__"

tell application "Reminders"
    set outputRows to {}
    repeat with reminderList in lists
        set listText to my cleanText(name of reminderList)
        if targetListName is "" or listText is targetListName then
            repeat with reminderItem in reminders of reminderList
                set includeRow to true
                if not includeCompleted then
                    try
                        if completed of reminderItem then set includeRow to false
                    end try
                end if
                if includeRow and scopeMode is not "all" then
                    set includeRow to false
                    try
                        set reminderDue to due date of reminderItem
                        if reminderDue is not missing value then
                            if scopeMode is "overdue" then
                                set includeRow to reminderDue < scopeEnd
                            else
                                set includeRow to reminderDue >= scopeStart and reminderDue < scopeEnd
                            end if
                        end if
                    end try
                end if
                if includeRow then
                    set idText to my cleanText(id of reminderItem)
                    set titleText to my cleanText(name of reminderItem)
                    set dueText to ""
                    try
                        set reminderDue to due date of reminderItem
                        if reminderDue is not missing value then set dueText to my isoDate(reminderDue)
                    end try
                    set completedText to (completed of reminderItem) as text
                    set priorityText to "0"
                    try
                        set priorityText to (priority of reminderItem) as text
                    end try
                    set bodyText to ""
                    try
                        set bodyText to my cleanText(body of reminderItem)
                    end try
                    set fieldSeparator to (character id 31)
                    set rowText to idText & fieldSeparator & titleText & fieldSeparator & listText & fieldSeparator & dueText & fieldSeparator & completedText & fieldSeparator & priorityText & fieldSeparator & bodyText
                    set end of outputRows to rowText
                end if
            end repeat
        end if
    end repeat
end tell
set AppleScript's text item delimiters to (character id 30)
set outputText to outputRows as text
set AppleScript's text item delimiters to ""
return outputText'''


def tool_list_reminders(args: dict[str, Any]) -> dict[str, Any]:
    """读取用户真实的 macOS Reminders.app 条目。"""
    scope = str(args.get("scope") or "week").strip().lower()
    if scope not in _LIST_SCOPES:
        return {
            "ok": False,
            "error": "scope 只支持 today/week/overdue/all",
            "reminders": [],
        }

    try:
        limit = max(1, min(500, int(args.get("limit", 100))))
    except (TypeError, ValueError):
        return {"ok": False, "error": "limit 应是 1-500 整数", "reminders": []}

    if not _is_macos():
        return {
            "ok": False,
            "error": "list_reminders 只 macOS 支持 (读取 Reminders.app). 当前平台: "
                     + platform.system(),
            "reminders": [],
        }

    list_name = str(args.get("list_name") or "").strip()
    include_completed = bool(args.get("include_completed", False))
    query_now = _now_local()
    query_script = _build_list_reminders_script(
        scope,
        include_completed=include_completed,
        list_name=list_name,
        now=query_now,
    )
    ok, stdout, stderr = _run_osascript(query_script, timeout_sec=30.0)
    if not ok:
        if "Not authorized" in stderr or "权限" in stderr or "not allowed" in stderr.lower():
            return {
                "ok": False,
                "error": (
                    "Reminders.app 权限未给 — 系统设置 → 隐私与安全性 → 提醒事项 → "
                    "勾上 Catfish Companion (或 Terminal / Python)."
                ),
                "needs_permission": True,
                "reminders": [],
            }
        return {"ok": False, "error": f"osascript 失败: {stderr}", "reminders": []}

    all_items = _parse_reminders_output(stdout)
    selected = _filter_reminders(
        all_items,
        scope,
        now=query_now,
        include_completed=include_completed,
        list_name=list_name,
        limit=limit,
    )
    scope_names = {
        "today": "今天",
        "week": "本周",
        "overdue": "已逾期",
        "all": "全部",
    }
    logger.info(
        "BL-REMINDER: 读取 %s 条提醒 (scope=%s, list=%s)",
        len(selected), scope, list_name or "全部",
    )
    return {
        "ok": True,
        "scope": scope,
        "list_name": list_name or None,
        "include_completed": include_completed,
        "reminders": selected,
        "count": len(selected),
        "summary": f"📋 Reminders.app {scope_names[scope]}共有 {len(selected)} 条符合条件的待办.",
    }


__all__ = [
    "tool_create_reminder",
    "tool_list_reminders",
    "tool_list_reminder_lists",
    "_convert_iso_to_applescript_date",  # 给单测
    "_escape_applescript_string",  # 给单测
    "_filter_reminders",  # 给单测
    "_build_list_reminders_script",  # 给单测
    "_parse_reminders_output",  # 给单测
]
