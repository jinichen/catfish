"""本机用户任务库测试。

任务库和 task_manager 不同：前者保存用户行动，后者只保存后台执行任务。
"""
from __future__ import annotations

from datetime import datetime

from catfish_tool_bridge import task_library


def test_upsert_is_idempotent_and_updates_existing_task(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_TASK_LIBRARY_PATH", str(tmp_path / "tasks.db"))
    first = task_library.upsert_task({
        "task_id": "action-1",
        "title": "整理材料",
        "source": "conversation",
        "due_date_iso": "2026-09-09T18:00:00",
    })
    second = task_library.upsert_task({
        "task_id": "action-1",
        "title": "整理材料并发送",
        "status": "completed",
        "source": "conversation",
        "due_date_iso": "2026-09-09T18:00:00",
    })

    assert first["task_id"] == second["task_id"] == "action-1"
    result = task_library.list_tasks(
        {"scope": "all", "include_completed": True},
        now=datetime(2026, 9, 8, 9, 0),
    )
    assert result["count"] == 1
    assert result["tasks"][0]["title"] == "整理材料并发送"
    assert result["tasks"][0]["status"] == "completed"


def test_list_tasks_week_includes_current_week_actions_not_future_history(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_TASK_LIBRARY_PATH", str(tmp_path / "tasks.db"))
    fixed_now = datetime(2026, 9, 8, 9, 0).timestamp()
    monkeypatch.setattr(task_library, "_now", lambda: fixed_now)
    for task in (
        {"task_id": "this-week", "title": "本周任务", "due_date_iso": "2026-09-08T18:00:00"},
        {"task_id": "no-due", "title": "无截止任务"},
        {"task_id": "next-week", "title": "下周任务", "due_date_iso": "2026-09-14T09:00:00"},
    ):
        task_library.upsert_task(task)

    with task_library._connect() as conn:
        conn.execute(
            "UPDATE tasks SET updated_at = ? WHERE task_id = ?",
            (datetime(2026, 9, 1, 9, 0).timestamp(), "next-week"),
        )
        conn.commit()

    result = task_library.list_tasks(
        {"scope": "week", "include_completed": False},
        now=datetime(2026, 9, 8, 9, 0),
    )
    assert [task["task_id"] for task in result["tasks"]] == ["this-week", "no-due"]


def test_list_tasks_active_returns_all_unfinished_projects(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_TASK_LIBRARY_PATH", str(tmp_path / "tasks.db"))
    task_library.upsert_task({
        "task_id": "today",
        "title": "今天行动",
        "due_date_iso": "2026-09-08T18:00:00",
    })
    task_library.upsert_task({
        "task_id": "long-project",
        "title": "长期项目",
        "status": "in_progress",
        "due_date_iso": "2026-12-31T18:00:00",
    })
    task_library.upsert_task({
        "task_id": "done",
        "title": "已完成项目",
        "status": "completed",
    })

    result = task_library.list_tasks({"scope": "active"})

    assert result["count"] == 2
    assert {task["task_id"] for task in result["tasks"]} == {"today", "long-project"}


def test_upsert_reminders_preserves_completed_state_and_source(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_TASK_LIBRARY_PATH", str(tmp_path / "tasks.db"))
    task_library.upsert_reminders([
        {
            "id": "reminder-1",
            "title": "回复邮件",
            "list_name": "工作",
            "due_date_iso": "2026-09-08T10:00:00",
            "completed": False,
            "priority": 2,
            "body": "确认附件",
        },
        {
            "id": "reminder-2",
            "title": "已完成事项",
            "list_name": "工作",
            "due_date_iso": "2026-09-08T11:00:00",
            "completed": True,
        },
    ])

    result = task_library.list_tasks(
        {"scope": "today", "include_completed": True},
        now=datetime(2026, 9, 8, 9, 0),
    )
    assert result["count"] == 2
    by_id = {task["source_id"]: task for task in result["tasks"]}
    assert by_id["reminder-1"]["source"] == "reminders"
    assert by_id["reminder-1"]["priority"] == 2
    assert by_id["reminder-2"]["status"] == "completed"


def test_upsert_reminder_with_catfish_marker_keeps_task_id(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_TASK_LIBRARY_PATH", str(tmp_path / "tasks.db"))
    task_library.upsert_task({"task_id": "action-9", "title": "同步前的行动"})
    task_library.upsert_reminders([{
        "id": "reminder-created-later",
        "title": "同步前的行动",
        "due_date_iso": "2026-09-10T18:00:00",
        "body": "详情\n[catfish-task:action-9]",
    }])

    result = task_library.list_tasks({"scope": "all"})
    assert result["count"] == 1
    assert result["tasks"][0]["task_id"] == "action-9"
    assert result["tasks"][0]["source_id"] == "reminder-created-later"


def test_overdue_scope_uses_local_today_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_TASK_LIBRARY_PATH", str(tmp_path / "tasks.db"))
    task_library.upsert_task({
        "task_id": "yesterday",
        "title": "昨天任务",
        "due_date_iso": "2026-09-07T23:59:00",
    })
    task_library.upsert_task({
        "task_id": "today",
        "title": "今天任务",
        "due_date_iso": "2026-09-08T00:00:00",
    })

    result = task_library.list_tasks(
        {"scope": "overdue", "include_completed": False},
        now=datetime(2026, 9, 8, 9, 0),
    )
    assert [task["task_id"] for task in result["tasks"]] == ["yesterday"]


def test_native_task_tool_is_registered_and_dispatches(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_TASK_LIBRARY_PATH", str(tmp_path / "tasks.db"))
    from catfish_tool_bridge import catfish_tools

    monkeypatch.setattr(task_library.platform, "system", lambda: "Linux")
    assert "catfish_list_tasks" in catfish_tools.NATIVE_TOOL_NAMES
    task_library.upsert_task({
        "task_id": "dispatch-1",
        "title": "通过 native tool 读取",
        "due_date_iso": "2026-09-08T12:00:00",
    })
    result = catfish_tools.dispatch_native("catfish_list_tasks", {"scope": "today"})
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["tasks"][0]["task_id"] == "dispatch-1"


def test_create_task_writes_structured_action(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_TASK_LIBRARY_PATH", str(tmp_path / "tasks.db"))
    result = task_library.tool_create_task({
        "task_id": "action-42",
        "title": "准备周会材料",
        "due_date_iso": "2026-09-11T17:00:00",
        "source": "conversation",
        "body": "补齐数据页",
    })
    assert result["ok"] is True
    assert result["task"]["task_id"] == "action-42"
    assert result["task"]["status"] == "pending"


def test_create_task_rejects_invalid_due_date(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_TASK_LIBRARY_PATH", str(tmp_path / "tasks.db"))
    result = task_library.tool_create_task({
        "title": "错误日期",
        "due_date_iso": "下周某天",
    })
    assert result["ok"] is False
    assert "due_date_iso" in result["error"]


def test_sync_tasks_to_reminders_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_TASK_LIBRARY_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setattr(task_library.platform, "system", lambda: "Darwin")
    task_library.upsert_task({
        "task_id": "action-sync",
        "title": "同步本周行动",
        "due_date_iso": "2026-09-10T18:00:00",
    })
    from unittest.mock import patch
    from catfish_tool_bridge import reminders

    with patch.object(reminders, "tool_list_reminders", side_effect=[
        {"ok": True, "reminders": []},
        {"ok": True, "reminders": [{
            "id": "reminder-sync",
            "title": "同步本周行动",
            "body": "[catfish-task:action-sync]",
            "due_date_iso": "2026-09-10T18:00:00",
        }]},
    ]), patch.object(reminders, "tool_create_reminder", return_value={"ok": True}) as create:
        first = task_library.tool_sync_tasks_to_reminders({"scope": "week"})
        second = task_library.tool_sync_tasks_to_reminders({"scope": "week"})

    assert first["created"] == ["action-sync"]
    assert second["created"] == []
    create.assert_called_once()


# ── 9/10: Reminders 导入节流 (早安页「RPC tools/dispatch 超时」真因的后半) ──

def _fake_reminders(monkeypatch, ok: bool):
    """替换真 reminders.tool_list_reminders (from . import 走包属性, 换 sys.modules 不生效)。"""
    from unittest.mock import MagicMock

    from catfish_tool_bridge import reminders

    m = MagicMock()
    m.tool_list_reminders.return_value = (
        {"ok": True, "reminders": [{"id": "r1", "title": "来自提醒事项", "completed": False}]}
        if ok else {"ok": False, "error": "osascript 超 12.0s"}
    )
    monkeypatch.setattr(reminders, "tool_list_reminders", m.tool_list_reminders)
    return m


def test_import_throttled_within_interval(tmp_path, monkeypatch):
    """5 分钟内第二次读不再碰 Reminders —— 每次读都导 = 每次读都等 5 秒起。"""
    monkeypatch.setenv("CATFISH_TASK_LIBRARY_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setattr(task_library.platform, "system", lambda: "Darwin")
    fake = _fake_reminders(monkeypatch, ok=True)
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(task_library, "_now", lambda: clock["t"])

    r1 = task_library.tool_list_tasks({"scope": "all"})
    assert fake.tool_list_reminders.call_count == 1
    assert [t["title"] for t in r1["tasks"]] == ["来自提醒事项"], "第一次要导进来"

    clock["t"] += task_library.REMINDERS_IMPORT_MIN_INTERVAL_SEC - 1
    task_library.tool_list_tasks({"scope": "all"})
    assert fake.tool_list_reminders.call_count == 1, "间隔内不许再导"

    clock["t"] += 2
    task_library.tool_list_tasks({"scope": "all"})
    assert fake.tool_list_reminders.call_count == 2, "过了间隔要再导"


def test_force_sync_bypasses_throttle(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_TASK_LIBRARY_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setattr(task_library.platform, "system", lambda: "Darwin")
    fake = _fake_reminders(monkeypatch, ok=True)
    monkeypatch.setattr(task_library, "_now", lambda: 1_000_000.0)

    task_library.tool_list_tasks({"scope": "all"})
    task_library.tool_list_tasks({"scope": "all", "force_sync": True})
    assert fake.tool_list_reminders.call_count == 2


def test_failed_import_does_not_arm_throttle(tmp_path, monkeypatch):
    """导入失败 (超时) 不能记成"刚导过", 否则接下来 5 分钟都拿不到 Reminders。"""
    monkeypatch.setenv("CATFISH_TASK_LIBRARY_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setattr(task_library.platform, "system", lambda: "Darwin")
    fake = _fake_reminders(monkeypatch, ok=False)
    monkeypatch.setattr(task_library, "_now", lambda: 1_000_000.0)

    r = task_library.tool_list_tasks({"scope": "all"})
    assert r["ok"] is True and "超" in r["sync_warning"]
    task_library.tool_list_tasks({"scope": "all"})
    assert fake.tool_list_reminders.call_count == 2


def test_non_macos_never_imports(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_TASK_LIBRARY_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setattr(task_library.platform, "system", lambda: "Windows")
    fake = _fake_reminders(monkeypatch, ok=True)
    task_library.tool_list_tasks({"scope": "all", "force_sync": True})
    assert fake.tool_list_reminders.call_count == 0
