"""11/27 待办正文开头状态比后面旧 (task_notes.stale_head) + 插件里那份判据跟它一致。"""
import importlib.util
from datetime import date
from pathlib import Path

import pytest

from catfish_tool_bridge import task_library, task_notes

TODAY = date(2026, 11, 27)
ITSS = ("【当前状态 2026-11-20（员工确认口径）】11月20日官网公示, 公示≠已发证, 现阶段不写已取证。" + "通稿改动记录。" * 60
        + "\n- 2026-11-22 15:52 全稿清「技术评审」。"
        + "\n2026-11-27 09:08 官网挂网取证：ITSS-YW-1-350020260016｜一级｜有效｜2026-11-27至2029-11-26。"
        + "\n【截止】2026-12-31。\n[catfish-task:itsspub]")

CASES = [
    (ITSS, "2026-11-27 09:08"),
    ("【当前状态 2026-11-20】等公示。" + "记录。" * 120 + "\n11/27 09:08 已取证", "2026-11-27 09:08"),   # 行首不带年份
    ("【当前状态 2026-11-27】已取证。" + "旧记录。" * 120 + "\n2026-11-20 公示。", None),               # 开头就是最新
    ("本周进度: 等出证\n下周计划: 跟踪\n" + "历史。" * 120 + "\n2026-11-20 公示。", None),               # 开头没日期, 不判
    ("【当前状态 2026-11-20】短。\n2026-11-27 已取证", None),                                         # 整段都在开头里
    ("【当前状态 2026-11-20】等。" + "记录。" * 120 + "\n有效期至 2029-11-26, 截止 2026-12-31", None),   # 今天以后的日期不算
    ("", None), (None, None),
]


@pytest.mark.parametrize("body,newest", CASES)
def test_判据(body, newest):
    got = task_notes.stale_head(body, TODAY)
    assert (got and got["newest_date"]) == newest
    if got:
        assert got["head_date"] == "2026-11-20"


def test_插件里那份判据跟这份一致():
    p = Path(__file__).resolve().parents[2] / "hermes-plugins" / "catfish-xcatfish-user" / "todo_head_guard.py"
    spec = importlib.util.spec_from_file_location("thg", p)
    thg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(thg)
    for body, _ in CASES:
        assert thg.stale_head(body, TODAY) == task_notes.stale_head(body, TODAY)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    return tmp_path


def test_带body更新任务_开头旧就当场提醒(home, monkeypatch):
    monkeypatch.setattr(task_notes, "date", type("D", (), {"today": staticmethod(lambda: TODAY)}))
    r = task_library.tool_create_task({"title": "ITSS 运维服务能力成熟度一级", "body": ITSS})
    assert r["ok"] and r["head_stale"]["newest_date"] == "2026-11-27 09:08"
    assert "开头换成最新的【当前状态" in r["summary"]
    fixed = "【当前状态 2026-11-27】已取证 ITSS-YW-1-350020260016。\n【下一步】归档。\n【历史】" + ITSS
    r = task_library.tool_create_task({"task_id": r["task"]["task_id"], "title": "ITSS 运维服务能力成熟度一级", "body": fixed})
    assert r["ok"] and "head_stale" not in r


def test_列任务时标出来(home, monkeypatch):
    monkeypatch.setattr(task_notes, "date", type("D", (), {"today": staticmethod(lambda: TODAY)}))
    monkeypatch.setattr(task_library.platform, "system", lambda: "Linux")
    task_library.tool_create_task({"title": "ITSS 运维服务能力成熟度一级", "body": ITSS, "due_date_iso": "2026-11-28T18:00:00"})
    task_library.tool_create_task({"title": "别的事", "body": "没问题", "due_date_iso": "2026-11-28T18:00:00"})
    r = task_library.tool_list_tasks({"scope": "all"})
    flagged = [t["title"] for t in r["tasks"] if t.get("head_stale")]
    assert flagged == ["ITSS 运维服务能力成熟度一级"] and "ITSS" in r["head_stale_notice"]
