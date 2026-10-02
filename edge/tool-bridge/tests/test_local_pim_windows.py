"""Windows 上的提醒 / 日历存在 Catfish 自己 (10/2 鸿波拍板, local_pim.py)。

新版 Outlook 没有 COM, Windows 上不靠任何客户端: 提醒 = 任务库 + 闹钟 (到点弹通知),
日历 = 任务库 + calendar_events + .ics。这里用真 SQLite (临时目录) + 假通知, 在
mac / Linux 上模拟 Windows 跑。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from catfish_tool_bridge import calendar_events, local_pim, reminders, task_library


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_TASK_LIBRARY_PATH", str(tmp_path / "task_library.db"))
    monkeypatch.setattr(local_pim, "_ics_dir", lambda: tmp_path / "calendar")
    monkeypatch.setattr("platform.system", lambda: "Windows")
    return tmp_path


class Toasts(list):
    def __call__(self, title, body):
        self.append((title, body))


def test_reminder_fires_once_at_due_time():
    r = reminders.tool_create_reminder({"title": "交月报", "due_date_iso": "2026-10-09T09:30:00"})
    assert r["ok"] is True and r["remind_at"] == "2026-10-09T09:30"
    toasts = Toasts()
    assert local_pim.fire_due_alarms(datetime(2026, 10, 9, 9, 29), toasts) == []
    assert local_pim.fire_due_alarms(datetime(2026, 10, 9, 9, 30, 5), toasts) == [r["task_id"]]
    assert toasts == [("⏰ 小鲶提醒", "交月报 · 10-09 09:30")]
    # 弹过就不再弹
    assert local_pim.fire_due_alarms(datetime(2026, 10, 9, 10, 0), toasts) == []


def test_date_only_due_reminds_at_nine_not_midnight():
    r = reminders.tool_create_reminder({"title": "备份", "due_date_iso": "2026-10-10"})
    assert r["remind_at"] == "2026-10-10T09:00"


def test_completed_task_is_not_announced():
    r = reminders.tool_create_reminder({"title": "打电话", "due_date_iso": "2026-10-09T10:00:00"})
    task_library.upsert_task({"task_id": r["task_id"], "title": "打电话", "status": "completed",
                              "due_date_iso": "2026-10-09T10:00:00", "source": "reminder"})
    toasts = Toasts()
    assert local_pim.fire_due_alarms(datetime(2026, 10, 9, 11, 0), toasts) == []
    assert toasts == []


def test_rescheduled_task_moves_the_alarm():
    r = reminders.tool_create_reminder({"title": "评审", "due_date_iso": "2026-10-09T10:00:00"})
    task_library.upsert_task({"task_id": r["task_id"], "title": "评审", "source": "reminder",
                              "due_date_iso": "2026-10-12T15:00:00"})
    toasts = Toasts()
    # 原时间到了: 发现改期 → 重算, 不弹
    assert local_pim.fire_due_alarms(datetime(2026, 10, 9, 10, 1), toasts) == []
    assert local_pim.fire_due_alarms(datetime(2026, 10, 12, 15, 0), toasts) == [r["task_id"]]


def test_missed_alarm_fires_once_marked_as_missed():
    reminders.tool_create_reminder({"title": "周会", "due_date_iso": "2026-10-09T09:00:00"})
    toasts = Toasts()
    local_pim.fire_due_alarms(datetime(2026, 10, 9, 14, 0), toasts)  # Catfish 上午没开
    assert toasts[0][0] == "⏰ 小鲶提醒 (错过的)"


def test_list_reminders_and_lists():
    reminders.tool_create_reminder({"title": "A", "due_date_iso": "2026-10-02T18:00:00", "list_name": "工作"})
    reminders.tool_create_reminder({"title": "B"})
    task_library.tool_create_task({"title": "普通任务, 不是提醒"})
    r = reminders.tool_list_reminders({"scope": "all"})
    assert r["ok"] is True and {x["title"] for x in r["reminders"]} == {"A", "B"}
    assert reminders.tool_list_reminder_lists({})["list_names"] == ["提醒事项", "工作"]


def test_calendar_event_ics_alarm_and_briefing_shape(tmp_db):
    r = calendar_events.tool_create_calendar_event({
        "title": "现场审核, 3 楼", "start_iso": "2026-10-09T08:40:00", "location": "福州; 3 楼",
        "description": "带材料\n第二行", "alarm_minutes_before": [15, 60],
    })
    assert r["ok"] is True and r["calendar_name"] == "工作"
    ics = (tmp_db / "calendar").glob("*.ics").__next__().read_bytes().decode("utf-8")  # 不能 read_text: 会把 RFC 5545 要求的 CRLF 吃成 LF
    assert "DTSTART:20261009T084000\r\n" in ics and "DTEND:20261009T094000\r\n" in ics
    assert "SUMMARY:现场审核\\, 3 楼" in ics and "LOCATION:福州\\; 3 楼" in ics
    assert "DESCRIPTION:带材料\\n第二行" in ics
    assert "VALARM" not in ics, "提醒由 Catfish 弹, .ics 再带一个会弹两次"

    # 提醒取最早那个 (60 分钟前)
    toasts = Toasts()
    assert local_pim.fire_due_alarms(datetime(2026, 10, 9, 7, 39), toasts) == []
    assert len(local_pim.fire_due_alarms(datetime(2026, 10, 9, 7, 40, 30), toasts)) == 1

    week = local_pim.events_for_range("natural-week", now=datetime(2026, 10, 8, 12, 0))
    assert len(week) == 1
    ev = week[0]
    assert ev["summary"] == "现场审核, 3 楼" and ev["calendar"] == "工作" and ev["all_day"] is False
    assert ev["start"] == datetime(2026, 10, 9, 8, 40).astimezone(timezone.utc).isoformat()
    assert local_pim.events_for_range("today", now=datetime(2026, 10, 8, 12, 0)) == []
    assert "工作" in calendar_events.tool_list_calendars({})["calendar_names"]


def test_cancelled_event_leaves_the_briefing(tmp_db):
    calendar_events.tool_create_calendar_event({"title": "取消的会", "start_iso": "2026-10-09T10:00:00"})
    tid = task_library.list_tasks({"scope": "all"})["tasks"][0]["task_id"]
    task_library.upsert_task({"task_id": tid, "title": "取消的会", "status": "cancelled",
                              "due_date_iso": "2026-10-09T10:00:00", "source": "calendar"})
    assert local_pim.events_for_range("today", now=datetime(2026, 10, 9, 8, 0)) == []


def test_sync_tasks_to_reminders_on_windows_is_idempotent():
    task_library.tool_create_task({"title": "有截止", "due_date_iso": "2026-10-03T17:00:00"})
    task_library.tool_create_task({"title": "没截止"})
    first = task_library.tool_sync_tasks_to_reminders({"scope": "all"})
    assert first["ok"] is True and first["count"] == 1
    assert task_library.tool_sync_tasks_to_reminders({"scope": "all"})["count"] == 0


def test_rpc_calendar_events_for_briefing(monkeypatch):
    """Companion calendar.rs (Windows) 走 pim/calendar_events 拿 Catfish 自己记的事件。"""
    import asyncio

    from catfish_tool_bridge import server

    monkeypatch.setattr(local_pim, "_open_with_default_app", lambda p: False)
    today = datetime.now().replace(hour=15, minute=0, second=0, microsecond=0)
    calendar_events.tool_create_calendar_event({"title": "今天下午的会", "start_iso": today.isoformat()})
    resp = asyncio.run(server._handle_request({"id": 1, "method": "pim/calendar_events", "params": {"range": "today"}}))
    assert [e["summary"] for e in resp["result"]] == ["今天下午的会"]
    bad = asyncio.run(server._handle_request({"id": 2, "method": "pim/calendar_events", "params": {"range": "year"}}))
    assert "error" in bad
