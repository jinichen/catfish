"""按期滚动的任务: 新一期建出来, 上一期自动收口 (11/25)。"""
from __future__ import annotations

from catfish_tool_bridge import task_library
from catfish_tool_bridge.task_library_series import series_key


def _open_titles():
    return sorted(t["title"] for t in task_library.list_tasks({"scope": "active"})["tasks"])


def test_series_key_only_for_titles_with_a_date_range():
    assert series_key("资质邮件周扫描 11/02-11/09") == series_key("⭐ 资质邮件周扫描 11/13–11/20")
    assert series_key("周报 2026/11/02 至 2026/11/09") == series_key("周报 11.9~11.16")
    assert series_key("资质邮件周扫描") is None
    assert series_key("CCRC：12月12日前完成其余四项整改") is None
    assert series_key("11/02-11/09") is None  # 只剩日期, 没有系列名
    assert series_key("资质邮件周扫描 11/02-11/09") != series_key("安全邮件周扫描 11/02-11/09")


def test_new_period_closes_previous_and_leaves_a_pointer(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_TASK_LIBRARY_PATH", str(tmp_path / "tasks.db"))
    old = task_library.tool_create_task({"title": "资质邮件周扫描 11/02-11/09", "body": "李倩那封没回"})["task"]
    task_library.tool_create_task({"title": "CCRC：12月12日前完成其余四项整改"})
    task_library.tool_create_task({"title": "安全邮件周扫描 11/02-11/09"})

    result = task_library.tool_create_task({"title": "资质邮件周扫描 11/09-11/16", "body": "本周 3 封"})

    assert [c["task_id"] for c in result["superseded"]] == [old["task_id"]]
    assert _open_titles() == ["CCRC：12月12日前完成其余四项整改", "安全邮件周扫描 11/02-11/09", "资质邮件周扫描 11/09-11/16"]
    assert old["task_id"] in result["task"]["body"] and result["task"]["body"].startswith("本周 3 封")
    assert "possible_duplicates" not in result or all(
        d["task_id"] != old["task_id"] for d in result["possible_duplicates"])
    closed = [t for t in task_library.list_tasks({"scope": "all", "include_completed": True})["tasks"]
              if t["task_id"] == old["task_id"]][0]
    assert closed["status"] == "completed"
    assert "李倩那封没回" in closed["body"] and "资质邮件周扫描 11/09-11/16" in closed["body"]


def test_editing_an_old_period_does_not_close_the_newer_one(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_TASK_LIBRARY_PATH", str(tmp_path / "tasks.db"))
    clock = iter(range(1000, 2000, 10))
    monkeypatch.setattr(task_library, "_now", lambda: float(next(clock)))
    old = task_library.upsert_task({"title": "周报 11/02-11/09", "source": "conversation"})
    new = task_library.upsert_task({"title": "周报 11/09-11/16", "source": "conversation"})

    result = task_library.tool_create_task({"task_id": old["task_id"], "title": "周报 11/02-11/09", "body": "补一句"})

    assert "superseded" not in result
    assert _open_titles() == ["周报 11/02-11/09", "周报 11/09-11/16"]
    assert new["status"] == "pending"


# ── 11/25: 提醒没变时, 导入不拿提醒的旧内容盖掉任务库里的新进展 ──────────────

def test_reminder_import_keeps_library_edits_until_the_reminder_itself_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_TASK_LIBRARY_PATH", str(tmp_path / "tasks.db"))
    clock = iter(range(1000, 9000, 10))
    monkeypatch.setattr(task_library, "_now", lambda: float(next(clock)))
    reminder = {"id": "x-apple-reminder://A", "title": "集采三期：11/13 递交", "body": "11/07 的备注",
                "due_date_iso": "2026-12-31T18:00:00"}
    task_library.upsert_reminders([reminder])
    row = task_library.list_tasks({"scope": "all"})["tasks"][0]

    # 小鲶按 task_id 把进展写进任务库
    task_library.tool_create_task({"task_id": row["task_id"], "title": "集采三期：两包均已定候选人", "body": "11/25 进展"})
    edited_at = task_library.list_tasks({"scope": "all"})["tasks"][0]["updated_at"]

    task_library.upsert_reminders([reminder])  # 提醒事项里那条一个字没动
    kept = task_library.list_tasks({"scope": "all"})["tasks"][0]
    assert (kept["title"], kept["body"]) == ("集采三期：两包均已定候选人", "11/25 进展")
    assert kept["updated_at"] == edited_at, "没变就不该刷新改动时间 (早安靠它判断搁置多久)"
    assert kept["source_id"] == "x-apple-reminder://A"

    task_library.upsert_reminders([{**reminder, "title": "集采三期：员工在提醒事项里改的"}])
    assert task_library.list_tasks({"scope": "all"})["tasks"][0]["title"] == "集采三期：员工在提醒事项里改的"

    task_library.upsert_reminders([{**reminder, "title": "集采三期：员工在提醒事项里改的", "completed": True}])
    assert task_library.list_tasks({"scope": "all", "include_completed": True})["tasks"][0]["status"] == "completed"


def test_rows_imported_before_this_version_keep_library_content_on_first_import(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_TASK_LIBRARY_PATH", str(tmp_path / "tasks.db"))
    task_library.upsert_task({"task_id": "t1", "title": "库里的新标题", "body": "新进展", "source": "reminders",
                              "source_id": "x-apple-reminder://B"})
    task_library.upsert_reminders([{"id": "x-apple-reminder://B", "title": "提醒里的旧标题", "body": "旧备注"}])
    assert task_library.list_tasks({"scope": "all"})["tasks"][0]["title"] == "库里的新标题"
    task_library.upsert_reminders([{"id": "x-apple-reminder://B", "title": "提醒改了", "body": "旧备注"}])
    assert task_library.list_tasks({"scope": "all"})["tasks"][0]["title"] == "提醒改了"
