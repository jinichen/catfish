"""10/8 catfish_advisor_close_task: 小鲶在聊天里关早安卡片, 写早安真正会读的台账。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from catfish_tool_bridge import advisor_close  # noqa: E402

REF = (
    "邮件：【提醒】【整改工作要求】 回复: 关于开展2026年期中固定资产（含无形资产）盘点工作的通知"
    "（邮件 ID: apple_mail|Corp|2890；时间: 2026-10-08T00:37:53+00:00）"
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    (tmp_path / "advisor_cache.json").write_text(json.dumps({
        "result": {"mainTasks": [
            {"taskUid": "f3g7h9", "title": "期中固定资产（含无形资产）盘点整改工作要求", "contextRefs": [REF]},
            {"taskUid": "itss01", "title": "ITSS 智能运维项目", "contextRefs": []},
        ]},
    }, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _rows(home):
    return [json.loads(l) for l in (home / "advisor_closed.jsonl").read_text(encoding="utf-8").splitlines()]


def test_norm_title_matches_ts_rule():
    # 跟 advisor_closed.test.ts 里同一组期望值 —— 两边规则漂了这条先红
    assert advisor_close._norm_title("期中固定资产（含无形资产）盘点整改工作要求") == "期中固定资产盘点整改工作要求"
    assert advisor_close._norm_title("【提醒】ITSS 智能-运维, 项目!") == "itss智能运维项目"


def test_close_by_loose_title_picks_card_uid_and_refs(home):
    out = advisor_close.close_task({"title": "期中固定资产盘点整改", "note": "员工确认J列已填"})
    assert out["ok"] and out["matched_card"]["taskUid"] == "f3g7h9"
    row = _rows(home)[0]
    assert row["op"] == "close" and row["status"] == "done" and row["by"] == "chat"
    assert row["taskUid"] == "f3g7h9" and row["refs"] == [REF] and row["note"] == "员工确认J列已填"


def test_unmatched_title_still_recorded_and_lists_current_cards(home):
    out = advisor_close.close_task({"title": "某个卡片里没有的事"})
    assert out["ok"] and out["matched_card"] is None
    assert "ITSS 智能运维项目" in out["current_cards"]
    assert _rows(home)[0]["title"] == "某个卡片里没有的事"


def test_snoozed_has_until_and_reopen_appends(home):
    advisor_close.close_task({"task_uid": "itss01", "title": "x", "status": "snoozed"})
    advisor_close.close_task({"task_uid": "itss01", "title": "x", "reopen": True})
    rows = _rows(home)
    assert rows[0]["status"] == "snoozed" and rows[0]["until"].endswith("Z")
    assert rows[1]["op"] == "reopen" and "status" not in rows[1]


def test_rejects_bad_input(home):
    assert not advisor_close.close_task({})["ok"]
    assert not advisor_close.close_task({"title": "x", "status": "maybe"})["ok"]
    assert not (home / "advisor_closed.jsonl").exists()


def test_schema_registered_and_dispatched(home):
    from catfish_tool_bridge.catfish_tool_schemas import CATFISH_NATIVE_TOOLS
    from catfish_tool_bridge.catfish_tools import dispatch_native, is_native
    assert any(t["name"] == "catfish_advisor_close_task" for t in CATFISH_NATIVE_TOOLS)
    assert is_native("catfish_advisor_close_task")
    out = dispatch_native("catfish_advisor_close_task", {"task_uid": "f3g7h9", "title": "x"})
    assert out["ok"] and out["matched_card"]["taskUid"] == "f3g7h9"


def test_scans_dispatch_still_routes(monkeypatch):
    """dispatch 把两个 scans 合成一支 getattr —— 名字映射错了这条红。"""
    from catfish_tool_bridge import advisor_scans
    from catfish_tool_bridge.catfish_tools import dispatch_native
    monkeypatch.setattr(advisor_scans, "check_compliance", lambda a: {"which": "cc"})
    monkeypatch.setattr(advisor_scans, "political_sensitivity_scan", lambda a: {"which": "ps"})
    assert dispatch_native("catfish_check_compliance", {})["which"] == "cc"
    assert dispatch_native("catfish_political_sensitivity_scan", {})["which"] == "ps"
