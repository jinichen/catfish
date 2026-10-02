"""Windows 上的提醒 / 日历工具走 Outlook (10/2)。

原来 Windows 上这 6 个工具直接返"只 macOS 支持", 工具清单里也被藏掉。现在落到
Outlook 的任务 / 日历 —— 跟 Companion 界面 system_outlook.rs 同一套 PowerShell。
这里在 mac / Linux 上模拟: platform.system() 返 Windows, subprocess.run 换成假的,
检查传给 PowerShell 的环境变量和对输出的解析。
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from catfish_tool_bridge import calendar_events, outlook_pim, reminders, tool_availability
from catfish_tool_bridge.catfish_tool_schemas_task import TASK_TOOLS


class FakePS:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.calls = []
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr

    def __call__(self, cmd, env=None, capture_output=None, timeout=None, creationflags=0):
        self.calls.append({"cmd": cmd, "env": env, "creationflags": creationflags})
        return SimpleNamespace(
            returncode=self.returncode,
            stdout=self.stdout.encode("utf-8"),
            stderr=self.stderr.encode("utf-8"),
        )


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Windows")

    def install(**kw):
        fake = FakePS(**kw)
        monkeypatch.setattr(outlook_pim.subprocess, "run", fake)
        return fake
    return install


def test_create_reminder_goes_to_outlook_task(windows):
    ps = windows(stdout="任务\n")
    r = reminders.tool_create_reminder({
        "title": '交 "月报"', "due_date_iso": "2026-10-09T09:00:00", "priority": 1, "body": "第二行\n第三行",
    })
    assert r["ok"] is True and r["list_name"] == "任务"
    call = ps.calls[0]
    assert call["cmd"][0] == "powershell" and "Outlook.Application" in call["cmd"][-1]
    env = call["env"]
    # 参数走环境变量, 不拼脚本: 引号 / 换行原样
    assert env["CF_TITLE"] == '交 "月报"' and env["CF_BODY"] == "第二行\n第三行"
    assert env["CF_DUE"] == "2026-10-09T09:00:00"
    assert env["CF_IMPORTANCE"] == "2", "Reminders 优先级 1 (高) → Outlook Importance 2"
    assert env["CF_LIST"] == "提醒事项", "mac 默认清单名照传, Outlook 那边找不到就落默认任务文件夹"
    assert call["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_no_outlook_is_a_clear_error_not_fake_success(windows):
    windows(returncode=3, stderr="NO_OUTLOOK")
    r = reminders.tool_create_reminder({"title": "开会"})
    assert r["ok"] is False and r["needs_outlook"] is True
    assert "Outlook" in r["error"] and "catfish_create_task" in r["error"]


def test_bad_due_date_rejected_before_outlook(windows):
    ps = windows()
    r = reminders.tool_create_reminder({"title": "x", "due_date_iso": "下周三"})
    assert r["ok"] is False and "due_date_iso" in r["error"]
    assert ps.calls == []


def test_list_reminders_parses_outlook_json_and_filters_scope(windows, monkeypatch):
    from datetime import datetime

    monkeypatch.setattr(reminders, "_now_local", lambda: datetime(2026, 10, 2, 10, 0, 0))
    rows = [
        {"id": "E1", "title": "今天交", "list_name": "任务", "due_date_iso": "2026-10-02T00:00:00",
         "completed": False, "importance": 2, "body": "[catfish-task:t1]"},
        {"id": "E2", "title": "没截止", "list_name": "任务", "due_date_iso": None,
         "completed": False, "importance": 1, "body": ""},
        {"id": "E3", "title": "下周", "list_name": "任务", "due_date_iso": "2026-10-12T00:00:00",
         "completed": False, "importance": 0, "body": ""},
    ]
    ps = windows(stdout=json.dumps(rows, ensure_ascii=False))
    r = reminders.tool_list_reminders({"scope": "today"})
    assert r["ok"] is True
    assert [x["title"] for x in r["reminders"]] == ["今天交"]
    assert r["reminders"][0]["priority"] == 1 and r["reminders"][0]["body"] == "[catfish-task:t1]"
    assert ps.calls[0]["env"]["CF_ALL"] == "0"

    r = reminders.tool_list_reminders({"scope": "all", "include_completed": True})
    titles = {x["title"] for x in r["reminders"]}
    assert titles == {"今天交", "没截止", "下周"}
    assert ps.calls[1]["env"]["CF_ALL"] == "1"


def test_list_reminders_single_item_object(windows):
    """PowerShell 5.1 有时把单元素数组输出成对象, 也要认。"""
    windows(stdout=json.dumps({"id": "E1", "title": "唯一", "list_name": "任务", "due_date_iso": None,
                               "completed": False, "importance": 1, "body": ""}, ensure_ascii=False))
    r = reminders.tool_list_reminders({"scope": "all"})
    assert [x["title"] for x in r["reminders"]] == ["唯一"]


def test_create_calendar_event_uses_earliest_alarm(windows):
    ps = windows(stdout="日历\n")
    r = calendar_events.tool_create_calendar_event({
        "title": "现场审核", "start_iso": "2026-10-09T08:40:00", "location": "3 楼",
        "alarm_minutes_before": [15, 1440],
    })
    assert r["ok"] is True and r["calendar_name"] == "日历"
    env = ps.calls[0]["env"]
    assert env["CF_START"] == "2026-10-09T08:40:00" and env["CF_END"] == "2026-10-09T09:40:00"
    assert env["CF_REMIND"] == "1440", "Outlook 一个事件只有一个提醒, 取最早的"
    assert env["CF_CAL"] == "工作"
    assert "1440" in r["summary"]


def test_list_calendars_and_lists(windows):
    windows(stdout="日历\n团队日历\n")
    assert calendar_events.tool_list_calendars({})["calendar_names"] == ["日历", "团队日历"]
    windows(stdout="任务\n")
    assert reminders.tool_list_reminder_lists({})["list_names"] == ["任务"]


def test_tools_now_offered_on_windows():
    names = {
        "catfish_create_reminder", "catfish_list_reminders", "catfish_list_reminder_lists",
        "catfish_create_calendar_event", "catfish_list_calendars", "catfish_sync_tasks_to_reminders",
    }
    schemas = [s for s in TASK_TOOLS if s["name"] in names]
    assert len(schemas) == len(names)
    for s in schemas:
        resolved = tool_availability.with_runtime_availability(s, system_name="Windows")
        assert resolved["supported"] is True, s["name"]
        linux = tool_availability.with_runtime_availability(s, system_name="Linux")
        assert linux["supported"] is False, s["name"]
