"""P1 (10/22): 「此刻」块 (catfish_memory_situation)。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from catfish_memory_situation import render_situation

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 22, 13, 30, tzinfo=TZ)


def _sit(home: Path, name: str, data: dict) -> None:
    (home / "situation").mkdir(parents=True, exist_ok=True)
    (home / "situation" / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _todos(home: Path, rows):
    con = sqlite3.connect(home / "task_library.db")
    con.execute("CREATE TABLE tasks (task_id TEXT, title TEXT, status TEXT, due_date_iso TEXT, priority INT)")
    con.executemany("INSERT INTO tasks VALUES (?,?,?,?,?)", rows)
    con.commit()
    con.close()


def test_什么快照都没有时只给时间_不说没有会(tmp_path):
    out = render_situation(tmp_path, NOW)
    body = out.split("\n\n", 1)[1]
    assert body == "现在: 2026-10-22 星期四 13:30 (中午)", body


def test_日程只列还没结束的_标进行中(tmp_path):
    _sit(tmp_path, "calendar_today.json", {"date": "2026-10-22", "events": [
        {"summary": "早会", "start": "2026-10-22T09:00:00+08:00", "end": "2026-10-22T09:30:00+08:00"},
        {"summary": "资质评审会", "start": "2026-10-22T13:00:00+08:00", "end": "2026-10-22T14:00:00+08:00", "location": "3楼"},
        {"summary": "投标澄清", "start": "2026-10-22T16:00:00+08:00", "end": "2026-10-22T17:00:00+08:00"},
    ]})
    out = render_situation(tmp_path, NOW)
    assert "早会" not in out
    assert "13:00–14:00 (进行中) 资质评审会 @3楼" in out
    assert out.index("资质评审会") < out.index("投标澄清")


def test_昨天的日程快照不用(tmp_path):
    _sit(tmp_path, "calendar_today.json", {"date": "2026-10-21", "events": [
        {"summary": "昨天的会", "start": "2026-10-22T16:00:00+08:00", "end": "2026-10-22T17:00:00+08:00"}]})
    assert "昨天的会" not in render_situation(tmp_path, NOW)


def test_急邮件列主题_过期快照不用(tmp_path):
    data = {"updated_at": (NOW - timedelta(minutes=10)).isoformat(), "total": 5, "urgent": 1, "medium": 3, "low": 1,
            "items": [{"subject": "今天下班前回复投标函", "sender": "招标代理", "urgency": "急"},
                      {"subject": "周报", "sender": "x", "urgency": "中"}]}
    _sit(tmp_path, "unread_email.json", data)
    out = render_situation(tmp_path, NOW)
    assert "未读邮件 5 封 (急 1 / 中 3 / 低 1)" in out and "急「今天下班前回复投标函」— 招标代理" in out
    assert "「周报」" not in out, "不急的只计数不列"
    data["updated_at"] = (NOW - timedelta(hours=3)).isoformat()
    _sit(tmp_path, "unread_email.json", data)
    assert "未读邮件" not in render_situation(tmp_path, NOW), "Companion 没开时不能拿 3 小时前的未读冒充此刻"


def test_待办只列过期和三天内到期的(tmp_path):
    _todos(tmp_path, [
        ("1", "报能源数据", "pending", "2026-10-21T18:00:00", 1),
        ("2", "提供测试版本", "pending", "2026-10-23T18:00:00", 1),
        ("3", "高新跟进", "pending", "2026-12-31T18:00:00", 4),
        ("4", "已办完的", "completed", "2026-10-22T18:00:00", 1),
    ])
    out = render_situation(tmp_path, NOW)
    assert "待办 3 项未完成, 其中 2 项已过期或 3 天内到期" in out
    assert "[已过期] 报能源数据" in out and "[10-23 到期] 提供测试版本" in out
    assert "高新跟进" not in out and "已办完的" not in out
