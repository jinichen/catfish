"""BL-MM2 catfish_remember 后端版本化测试.

覆盖:
  - 第一次写入 → revision_count=1, previous_value=None
  - 旧 schema (string value) → 自动迁移到单 revision
  - 同 key 不同 value → push 新 revision, prev_value 正确, overwrite=True
  - 同 key 同 value → no_change=True, 不污染 history
  - revision list 超 _FACTS_MAX_REVISIONS_PER_KEY → 截掉最早的, 保留最新 5 条
  - 50 个不同 key 全满 → 第 51 个新 key 拒绝, 但 update 现有 key 仍 OK
  - 各种 validation: key/value 空 / 超长
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from catfish_tool_bridge import catfish_tools


@pytest.fixture
def facts_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """每个 test 独立 ~/.catfish/session_facts.json — 直接覆盖模块级常量."""
    fp = tmp_path / "session_facts.json"
    monkeypatch.setattr(catfish_tools, "SESSION_FACTS_PATH", fp)
    return fp


# ============================================================
# 基础 validation
# ============================================================


def test_empty_key_rejected(facts_file: Path) -> None:
    out = catfish_tools.remember_fact({"key": "", "value": "v"})
    assert out["type"] == "error"
    assert "key" in out["error"]


def test_empty_value_rejected(facts_file: Path) -> None:
    out = catfish_tools.remember_fact({"key": "k", "value": ""})
    assert out["type"] == "error"
    assert "value" in out["error"]


def test_key_too_long(facts_file: Path) -> None:
    out = catfish_tools.remember_fact({"key": "a" * 101, "value": "v"})
    assert out["type"] == "error"
    assert "太长" in out["error"]


def test_value_too_long(facts_file: Path) -> None:
    out = catfish_tools.remember_fact({"key": "k", "value": "a" * 1001})
    assert out["type"] == "error"


# ============================================================
# 首次写入 (BL-MM2)
# ============================================================


def test_first_write_creates_single_revision(facts_file: Path) -> None:
    out = catfish_tools.remember_fact({"key": "eis_url", "value": "http://eis.ffcs.cn"})
    assert out["type"] == "ok"
    assert out["overwrite"] is False
    assert out["previous_value"] is None
    assert out["revision_count"] == 1
    assert out["total_facts"] == 1

    # 落盘格式: revision list
    raw = json.loads(facts_file.read_text())
    assert isinstance(raw["eis_url"], list)
    assert len(raw["eis_url"]) == 1
    assert raw["eis_url"][0]["value"] == "http://eis.ffcs.cn"
    assert raw["eis_url"][0]["prev_value"] is None
    assert isinstance(raw["eis_url"][0]["ts"], float)


# ============================================================
# 同 key 不同 value: push 新 revision, prev_value 跟踪
# ============================================================


def test_overwrite_pushes_new_revision_with_prev_value(facts_file: Path) -> None:
    catfish_tools.remember_fact({"key": "eis_url", "value": "https://old.eis"})
    out = catfish_tools.remember_fact({"key": "eis_url", "value": "http://eis.ffcs.cn"})

    assert out["type"] == "ok"
    assert out["overwrite"] is True
    assert out["previous_value"] == "https://old.eis"
    assert out["revision_count"] == 2
    # summary 应该提示 BL-MM1 quote 旧值
    assert "BL-MM1" in out["summary"] or "上次值" in out["summary"]

    raw = json.loads(facts_file.read_text())
    revs = raw["eis_url"]
    assert len(revs) == 2
    assert revs[0]["value"] == "https://old.eis"
    assert revs[1]["value"] == "http://eis.ffcs.cn"
    assert revs[1]["prev_value"] == "https://old.eis"


def test_three_revisions_chain_prev_values(facts_file: Path) -> None:
    """连续 3 次更新, 每次 prev_value 指向上一次 current."""
    catfish_tools.remember_fact({"key": "k", "value": "v1"})
    catfish_tools.remember_fact({"key": "k", "value": "v2"})
    catfish_tools.remember_fact({"key": "k", "value": "v3"})

    raw = json.loads(facts_file.read_text())
    revs = raw["k"]
    assert len(revs) == 3
    assert [r["value"] for r in revs] == ["v1", "v2", "v3"]
    assert revs[0]["prev_value"] is None
    assert revs[1]["prev_value"] == "v1"
    assert revs[2]["prev_value"] == "v2"


# ============================================================
# 同 key 同 value: no-op, 不污染 history (防重复 tool call 灌脏)
# ============================================================


def test_same_value_no_op(facts_file: Path) -> None:
    catfish_tools.remember_fact({"key": "k", "value": "same"})
    out = catfish_tools.remember_fact({"key": "k", "value": "same"})

    assert out["type"] == "ok"
    assert out.get("no_change") is True
    assert out["overwrite"] is False
    assert out["revision_count"] == 1  # 没 push 新 revision

    raw = json.loads(facts_file.read_text())
    assert len(raw["k"]) == 1


# ============================================================
# 截断: 超 _FACTS_MAX_REVISIONS_PER_KEY 时丢最早的
# ============================================================


def test_revision_list_truncated_to_max(facts_file: Path) -> None:
    max_n = catfish_tools._FACTS_MAX_REVISIONS_PER_KEY
    # 写 max_n + 3 次, 应该只剩最近 max_n 条
    for i in range(max_n + 3):
        catfish_tools.remember_fact({"key": "k", "value": f"v{i}"})

    raw = json.loads(facts_file.read_text())
    revs = raw["k"]
    assert len(revs) == max_n
    # 最早的几条被截掉, 最新的留着
    assert revs[-1]["value"] == f"v{max_n + 2}"
    assert revs[0]["value"] == f"v{3}"  # v0/v1/v2 被截


# ============================================================
# 向后兼容: 旧 schema (string value) 自动迁移
# ============================================================


def test_legacy_string_value_auto_migrates(facts_file: Path) -> None:
    """文件已有旧 schema {"k": "string"}, 调 remember_fact 后应迁到 list."""
    facts_file.write_text(json.dumps({"old_key": "legacy_value"}, ensure_ascii=False))

    # 先读一次, 看迁移结果
    facts = catfish_tools._read_session_facts()
    assert "old_key" in facts
    assert isinstance(facts["old_key"], list)
    assert facts["old_key"][0]["value"] == "legacy_value"

    # 现在更新这个 key, prev_value 应该是 legacy_value
    out = catfish_tools.remember_fact({"key": "old_key", "value": "new_value"})
    assert out["overwrite"] is True
    assert out["previous_value"] == "legacy_value"
    assert out["revision_count"] == 2

    raw = json.loads(facts_file.read_text())
    revs = raw["old_key"]
    assert len(revs) == 2
    assert revs[0]["value"] == "legacy_value"
    assert revs[1]["prev_value"] == "legacy_value"


# ============================================================
# _FACTS_MAX_ENTRIES 边界
# ============================================================


def test_max_entries_reject_new_key_but_allow_update(facts_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """50 key 全满后: 新 key 拒绝, 但 update 已有 key 仍 OK."""
    monkeypatch.setattr(catfish_tools, "_FACTS_MAX_ENTRIES", 3)
    for i in range(3):
        out = catfish_tools.remember_fact({"key": f"k{i}", "value": "v"})
        assert out["type"] == "ok"

    # 第 4 个新 key 被拒
    out = catfish_tools.remember_fact({"key": "k_new", "value": "v"})
    assert out["type"] == "error"
    assert "已满" in out["error"]

    # update 已有 key 仍 OK
    out = catfish_tools.remember_fact({"key": "k0", "value": "v_updated"})
    assert out["type"] == "ok"
    assert out["overwrite"] is True


# ============================================================
# 损坏文件不抛
# ============================================================


def test_corrupt_file_treated_as_empty(facts_file: Path) -> None:
    facts_file.write_text("{not valid json")
    # 应该当空, 写入第一条
    out = catfish_tools.remember_fact({"key": "k", "value": "v"})
    assert out["type"] == "ok"
    assert out["revision_count"] == 1


def test_non_dict_root_treated_as_empty(facts_file: Path) -> None:
    facts_file.write_text(json.dumps(["a", "b"]))
    out = catfish_tools.remember_fact({"key": "k", "value": "v"})
    assert out["type"] == "ok"
    assert out["revision_count"] == 1
