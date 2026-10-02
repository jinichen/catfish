"""Windows 上小鲶的提醒 / 日历 = Catfish 自己 (10/2 鸿波拍板)。

mac 上 catfish_create_reminder / catfish_create_calendar_event 这几个工具写进系统的
Reminders.app / Calendar.app (reminders.py / calendar_events.py)。Windows 上没有对应的
系统应用; 当天先试过走 Outlook COM, 但新版 Outlook 没有 COM —— 跟邮件那边 9/26 定下的
"Windows 不再依赖客户端"冲突, 新机器上等于不能用。所以 Windows 上不靠任何客户端:

- 提醒 = 本机任务库 (task_library.db) 里 source="reminder" 的一条 + 一个闹钟;
  到点由 tool-bridge 里的闹钟循环 (alarm_loop) 弹 Windows 通知。
- 日历事件 = 任务库一条 (source="calendar") + calendar_events 表一行 (给早安页"今日
  日程"读) + 一个 .ics 文件, 用系统默认日历程序打开, 员工愿意就点一下存进去。
  .ics 里不带提醒, 提醒由 Catfish 自己弹 —— 不然导进 Outlook 后会弹两次。
- 不跨设备同步 (手机上看不到), 这是这个方案的代价, 选它时就说清楚了。

闹钟为什么在 tool-bridge 进程里轮询, 不用 Windows 计划任务:
- 任务在 Catfish 里勾完成 / 改期, 闹钟跟着取消 / 挪 —— 计划任务做不到 (它不知道任务库)。
- 计划任务起 PowerShell 会闪黑框, 还要为每条提醒改一次系统配置。
- 代价: Catfish 没在运行时不会准点弹; 下次启动时把错过的补弹一次, 标上"错过"。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from . import task_library

logger = logging.getLogger("catfish.tool_bridge.local_pim")

DEFAULT_LIST = "提醒事项"
CATFISH_CALENDAR = "Catfish 日历"
#: 只有日期没有时刻的截止 (00:00) 在早上 9 点提醒 —— 半夜 12 点弹没意义
DATE_ONLY_REMIND_AT = dtime(9, 0)
#: 晚于这个时间才弹的算"错过的提醒" (Catfish 当时没开)
LATE_AFTER = timedelta(minutes=10)
ALARM_POLL_SEC = 30.0


def _connect() -> sqlite3.Connection:
    conn = task_library._connect()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS alarms (
            task_id TEXT PRIMARY KEY,
            due_iso TEXT NOT NULL,          -- 设闹钟时任务的截止; 任务改期了就按新截止重算
            offset_min INTEGER NOT NULL DEFAULT 0,  -- 提前几分钟 (日历事件的提醒)
            remind_at TEXT NOT NULL,
            fired_at REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS calendar_events (
            task_id TEXT PRIMARY KEY,
            calendar TEXT NOT NULL,
            title TEXT NOT NULL,
            start_iso TEXT NOT NULL,
            end_iso TEXT NOT NULL,
            location TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            ics_path TEXT,
            created_at REAL NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_calendar_events_start ON calendar_events(start_iso)")
    return conn


def remind_at_for(due_iso: str, offset_min: int = 0) -> datetime | None:
    due = task_library._parse_due(due_iso)
    if due is None:
        return None
    if offset_min == 0 and due.time() == dtime(0, 0):
        due = datetime.combine(due.date(), DATE_ONLY_REMIND_AT)
    return due - timedelta(minutes=offset_min)


def set_alarm(task_id: str, due_iso: str, offset_min: int = 0) -> datetime | None:
    at = remind_at_for(due_iso, offset_min)
    if at is None:
        return None
    with _connect() as conn:
        conn.execute(
            "INSERT INTO alarms (task_id, due_iso, offset_min, remind_at, fired_at) VALUES (?, ?, ?, ?, NULL) "
            "ON CONFLICT(task_id) DO UPDATE SET due_iso=excluded.due_iso, offset_min=excluded.offset_min, "
            "remind_at=excluded.remind_at, fired_at=NULL",
            (task_id, due_iso, offset_min, at.isoformat(timespec="seconds")),
        )
    return at


# ── 提醒 ─────────────────────────────────────────────────────────────


def create_reminder(title: str, body: str, due_iso: str, list_name: str, priority: int | None) -> dict[str, Any]:
    task = task_library.upsert_task({
        "title": title, "body": body, "due_date_iso": due_iso or None, "priority": priority,
        "source": "reminder", "list_name": list_name or DEFAULT_LIST,
    })
    at = set_alarm(task["task_id"], due_iso) if due_iso else None
    return {"task": task, "remind_at": at}


def list_reminders(*, include_completed: bool, list_name: str) -> list[dict[str, Any]]:
    """形状跟 reminders._parse_reminders_output 一样; scope 过滤交给调用方。"""
    sql = (
        "SELECT t.* FROM tasks t LEFT JOIN alarms a ON a.task_id = t.task_id "
        "WHERE (t.source = 'reminder' OR a.task_id IS NOT NULL) AND t.status != 'cancelled'"
    )
    with _connect() as conn:
        rows = conn.execute(sql).fetchall()
    out = []
    for r in rows:
        if list_name and (r["list_name"] or DEFAULT_LIST) != list_name:
            continue
        completed = r["status"] == "completed"
        if completed and not include_completed:
            continue
        out.append({
            "id": r["task_id"], "title": r["title"], "list_name": r["list_name"] or DEFAULT_LIST,
            "due_date_iso": r["due_date_iso"], "completed": completed,
            "priority": int(r["priority"] or 0), "body": r["body"] or "",
        })
    return out


def list_reminder_lists() -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT list_name FROM tasks WHERE source = 'reminder' AND list_name IS NOT NULL"
        ).fetchall()
    names = [DEFAULT_LIST] + sorted({r["list_name"] for r in rows} - {DEFAULT_LIST})
    return names


def alarm_tasks(task_ids: list[str], dues: dict[str, str]) -> list[str]:
    """catfish_sync_tasks_to_reminders 的 Windows 版: 给有截止的任务挂闹钟。返回新挂上的。"""
    with _connect() as conn:
        have = {r["task_id"]: r["due_iso"] for r in conn.execute("SELECT task_id, due_iso FROM alarms")}
    created = []
    for tid in task_ids:
        due = dues.get(tid)
        if not due or have.get(tid) == due:
            continue
        if set_alarm(tid, due) is not None:
            created.append(tid)
    return created


# ── 日历 ─────────────────────────────────────────────────────────────


def _ics_text(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


def _ics_time(iso: str) -> str:
    dt = task_library._parse_due(iso)
    assert dt is not None
    return dt.strftime("%Y%m%dT%H%M%S")  # 不带时区 = 本地时间 (RFC 5545 floating time)


def build_ics(uid: str, title: str, start_iso: str, end_iso: str, location: str, description: str) -> str:
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Catfish//Companion//ZH", "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{uid}@catfish",
        f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART:{_ics_time(start_iso)}",
        f"DTEND:{_ics_time(end_iso)}",
        f"SUMMARY:{_ics_text(title)}",
    ]
    if location:
        lines.append(f"LOCATION:{_ics_text(location)}")
    if description:
        lines.append(f"DESCRIPTION:{_ics_text(description)}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def _ics_dir() -> Path:
    return Path.home() / ".catfish" / "outputs" / "calendar"


def _open_with_default_app(path: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606 —— 系统默认日历程序, 无控制台
        return True
    except OSError as e:
        logger.warning("打开 .ics 失败 (没有关联的日历程序?): %s", e)
        return False


def create_calendar_event(
    title: str, start_iso: str, end_iso: str, location: str, description: str,
    calendar_name: str, alarm_minutes_before: list[int],
) -> dict[str, Any]:
    cal = calendar_name or CATFISH_CALENDAR
    body = "\n".join(x for x in (f"地点: {location}" if location else "", description) if x)
    task = task_library.upsert_task({
        "title": title, "body": body, "due_date_iso": start_iso, "source": "calendar", "list_name": cal,
    })
    tid = task["task_id"]
    safe = re.sub(r'[\\/:*?"<>|\s]+', "-", title).strip("-")[:40] or "event"
    ics = _ics_dir() / f"{start_iso[:10]}-{safe}.ics"
    ics.parent.mkdir(parents=True, exist_ok=True)
    ics.write_text(build_ics(tid, title, start_iso, end_iso, location, description), encoding="utf-8", newline="")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO calendar_events (task_id, calendar, title, start_iso, end_iso, location, description, "
            "ics_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(task_id) DO UPDATE SET "
            "calendar=excluded.calendar, title=excluded.title, start_iso=excluded.start_iso, "
            "end_iso=excluded.end_iso, location=excluded.location, description=excluded.description, "
            "ics_path=excluded.ics_path",
            (tid, cal, title, start_iso, end_iso, location, description, str(ics), time.time()),
        )
    # Outlook / mac 一个事件多个提醒; 这里一条闹钟, 取最早那个 (离事件最远), 跟 mac 侧的取舍一致
    offset = max(alarm_minutes_before) if alarm_minutes_before else None
    at = set_alarm(tid, start_iso, offset) if offset is not None else None
    return {"task": task, "calendar": cal, "ics_path": str(ics), "opened": _open_with_default_app(ics), "remind_at": at}


def list_calendars() -> list[str]:
    with _connect() as conn:
        rows = conn.execute("SELECT DISTINCT calendar FROM calendar_events").fetchall()
    return [CATFISH_CALENDAR] + sorted({r["calendar"] for r in rows} - {CATFISH_CALENDAR})


def events_between(start: datetime, end: datetime) -> list[dict[str, Any]]:
    """早安页"今日 / 本周日程"用: 形状跟 mac 的 EventKit / JXA 一样
    (`[{calendar, summary, start, end, all_day, location?, description?}]`, 时间是 UTC ISO)。
    任务库里取消掉的事件不出。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT e.* FROM calendar_events e JOIN tasks t ON t.task_id = e.task_id "
            "WHERE t.status != 'cancelled' ORDER BY e.start_iso"
        ).fetchall()
    out = []
    for r in rows:
        s, e = task_library._parse_due(r["start_iso"]), task_library._parse_due(r["end_iso"])
        if s is None or e is None or not (start <= s < end):
            continue
        ev: dict[str, Any] = {
            "calendar": r["calendar"], "summary": r["title"],
            "start": s.astimezone(timezone.utc).isoformat(), "end": e.astimezone(timezone.utc).isoformat(),
            "all_day": False,
        }
        if r["location"]:
            ev["location"] = r["location"]
        if r["description"]:
            ev["description"] = r["description"][:500]
        out.append(ev)
    return out


def events_for_range(range_name: str, now: datetime | None = None) -> list[dict[str, Any]]:
    """range: "today" / "natural-week" (本周一 00:00 到下周一 00:00), 跟 mac 一致。"""
    today = datetime.combine((now or datetime.now()).date(), dtime.min)
    if range_name == "natural-week":
        start = today - timedelta(days=today.weekday())
        return events_between(start, start + timedelta(days=7))
    return events_between(today, today + timedelta(days=1))


# ── 闹钟 ─────────────────────────────────────────────────────────────


def fire_due_alarms(now: datetime | None = None, notify: Callable[[str, str], None] | None = None) -> list[str]:
    """弹掉所有到点、没弹过、任务还没完成的闹钟。返回弹了的 task_id。

    - 任务完成 / 取消了 → 不弹, 记成已处理
    - 任务改期了 (截止跟设闹钟时不同) → 按新截止重算, 这一轮不弹
    """
    if notify is None:
        from .task_manager_notify import _send_system_notify as notify  # noqa: PLC0415
    current = now or datetime.now()
    fired: list[str] = []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT a.task_id, a.due_iso, a.offset_min, a.remind_at, t.title, t.status, t.due_date_iso "
            "FROM alarms a JOIN tasks t ON t.task_id = a.task_id WHERE a.fired_at IS NULL"
        ).fetchall()
    for r in rows:
        tid = r["task_id"]
        if r["status"] in ("completed", "cancelled"):
            _mark_fired(tid)
            continue
        if r["due_date_iso"] and r["due_date_iso"] != r["due_iso"]:
            set_alarm(tid, r["due_date_iso"], int(r["offset_min"] or 0))
            continue
        at = datetime.fromisoformat(r["remind_at"])
        if at > current:
            continue
        late = current - at > LATE_AFTER
        when = task_library._parse_due(r["due_date_iso"] or r["due_iso"])
        detail = f"{when:%m-%d %H:%M}" if when and when.time() != dtime(0, 0) else ""
        title = "⏰ 小鲶提醒 (错过的)" if late else "⏰ 小鲶提醒"
        try:
            notify(title, " · ".join(x for x in (r["title"], detail) if x))
        except Exception:  # noqa: BLE001 —— 弹不出来也别死循环重试同一条
            logger.warning("提醒通知失败: %s", tid, exc_info=True)
        _mark_fired(tid)
        fired.append(tid)
    return fired


def _mark_fired(task_id: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE alarms SET fired_at = ? WHERE task_id = ?", (time.time(), task_id))


async def alarm_loop(poll_sec: float = ALARM_POLL_SEC) -> None:
    """tool-bridge 启动时在 Windows 上起 (server.serve_forever)。永远不抛。"""
    logger.info("local_pim: 提醒闹钟循环启动 (每 %.0f 秒)", poll_sec)
    while True:
        try:
            fired = await asyncio.to_thread(fire_due_alarms)
            if fired:
                logger.info("local_pim: 弹了 %d 条提醒", len(fired))
        except Exception:  # noqa: BLE001
            logger.exception("local_pim: 闹钟轮询失败 (下一轮再试)")
        await asyncio.sleep(poll_sec)
