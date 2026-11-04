"""11/4: 「我的记忆」待你确认的整理建议 —— 用 hermes 真函数, 在临时 HERMES_HOME 里跑。

没装 hermes-agent (不在 sys.path) 就跳过; 本机用 hermes venv 跑:
  PYTHONPATH=src:~/.hermes/hermes-agent ~/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_memory_pending.py
"""
from __future__ import annotations

import pytest

wa = pytest.importorskip("tools.write_approval")
memory_tool = pytest.importorskip("tools.memory_tool")

from catfish_tool_bridge import memory_pending  # noqa: E402

SEP = "\n§\n"


@pytest.fixture
def hhome(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "memories").mkdir()
    (tmp_path / "config.yaml").write_text("memory:\n  memory_char_limit: 4000\n  user_char_limit: 2000\n", encoding="utf-8")
    return tmp_path


def _write(home, target, entries):
    name = "USER.md" if target == "user" else "MEMORY.md"
    (home / "memories" / name).write_text(SEP.join(entries), encoding="utf-8")


def _read(home, target):
    name = "USER.md" if target == "user" else "MEMORY.md"
    return [e.strip() for e in (home / "memories" / name).read_text(encoding="utf-8").split(SEP) if e.strip()]


def _stage(target, operations):
    payload = {"action": "batch", "target": target, "operations": operations}
    return wa.stage_write(wa.MEMORY, payload, summary="test", origin="background_review")["id"]


def test_list_covers_user_and_memory(hhome):
    _write(hhome, "memory", ["老条目 A", "老条目 B"])
    _write(hhome, "user", ["我叫鸿波", "我叫鸿波, 在福富"])
    _stage("memory", [{"action": "remove", "old_text": "老条目 A", "matched_entry": "老条目 A"}])
    _stage("user", [{"action": "remove", "old_text": "我叫鸿波", "matched_entry": "我叫鸿波"}])
    items = memory_pending.list_pending()["items"]
    assert sorted(i["target"] for i in items) == ["memory", "user"]
    assert all(not i["stale"] for i in items)
    assert items[0]["ops"][0] == {"action": "remove", "entry": "老条目 A", "pinned": True, "content": "", "old_text": ""} or \
        items[1]["ops"][0]["entry"] == "老条目 A"


def test_approve_applies_merge_and_drops_proposal(hhome):
    _write(hhome, "memory", ["口径 1: 写正式", "口径 2: 写正式些", "别的事"])
    pid = _stage("memory", [
        {"action": "remove", "old_text": "口径 2", "matched_entry": "口径 2: 写正式些"},
        {"action": "replace", "old_text": "口径 1", "matched_entry": "口径 1: 写正式", "content": "口径: 对外文书写正式"},
    ])
    r = memory_pending.resolve({"id": pid, "approve": True})
    assert r == {"ok": True, "applied": True}
    assert _read(hhome, "memory") == ["口径: 对外文书写正式", "别的事"]
    assert memory_pending.list_pending()["items"] == []


def test_patch_keeps_rest_of_entry(hhome):
    _write(hhome, "memory", ["研发队伍 2200+, 对外写法见答辩稿"])
    pid = _stage("memory", [{"action": "patch", "old_text": "2200+", "content": "4200+",
                             "matched_entry": "研发队伍 2200+, 对外写法见答辩稿"}])
    (item,) = memory_pending.list_pending()["items"]
    assert item["ops"][0]["old_text"] == "2200+" and item["ops"][0]["content"] == "4200+"
    assert memory_pending.resolve({"id": pid, "approve": True})["applied"] is True
    assert _read(hhome, "memory") == ["研发队伍 4200+, 对外写法见答辩稿"]


def test_reject_leaves_memory_untouched(hhome):
    _write(hhome, "user", ["偏好: 简短"])
    pid = _stage("user", [{"action": "remove", "old_text": "偏好", "matched_entry": "偏好: 简短"}])
    assert memory_pending.resolve({"id": pid, "approve": False}) == {"ok": True, "applied": False}
    assert _read(hhome, "user") == ["偏好: 简短"]
    assert memory_pending.list_pending()["items"] == []


def test_stale_proposal_is_flagged_and_refused(hhome):
    _write(hhome, "memory", ["已经被员工改过的条目"])
    pid = _stage("memory", [{"action": "remove", "old_text": "原条目", "matched_entry": "原条目"}])
    (item,) = memory_pending.list_pending()["items"]
    assert item["stale"] is True
    r = memory_pending.resolve({"id": pid, "approve": True})
    assert r["ok"] is False and "放弃" in r["error"]
    assert _read(hhome, "memory") == ["已经被员工改过的条目"]
    assert len(memory_pending.list_pending()["items"]) == 1  # 没改成不删建议


def test_bad_id_rejected(hhome):
    with pytest.raises(ValueError):
        memory_pending.resolve({"id": "../../etc", "approve": True})
    assert memory_pending.resolve({"id": "deadbeef", "approve": True})["ok"] is False
