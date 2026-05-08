"""BL-MM10 (5/8) — memory 自精炼 loop 单测.

覆盖:
  - _count_journal_entries: 数 ## 段
  - _split_journal_chunks: 按段切, 不超 max_chars
  - _extract_peak_hours: 时间戳分布抽 morning/afternoon/evening
  - _extract_bullet_preference: list vs 散文比例
  - distill_journal_to_traits: 整链路 (规则版)
  - 红线 trait 自动过滤
  - should_run_distillation: 阈值 + 24h 限流
  - maybe_run_distillation: 整流程, 多 chunk 去重
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from catfish_gateway import memory_distill as md


# ============================================================
# _count_journal_entries
# ============================================================


def test_count_empty() -> None:
    assert md._count_journal_entries("") == 0
    assert md._count_journal_entries("\n\n\n") == 0


def test_count_single_entry() -> None:
    text = "## 2026-05-01 08:30 - 周报\n内容"
    assert md._count_journal_entries(text) == 1


def test_count_multiple_entries() -> None:
    text = "\n".join([
        "## 2026-05-01 08:30 - 周报",
        "内容 1",
        "",
        "## 2026-05-02 09:15 - 立项",
        "内容 2",
        "",
        "## 2026-05-03 10:00 - 评审",
        "内容 3",
    ])
    assert md._count_journal_entries(text) == 3


# ============================================================
# _split_journal_chunks
# ============================================================


def test_split_empty_returns_empty_list() -> None:
    assert md._split_journal_chunks("") == []
    assert md._split_journal_chunks("   ") == []


def test_split_single_short_journal_one_chunk() -> None:
    text = "## 2026-05-01 - x\n内容"
    chunks = md._split_journal_chunks(text, max_chars=1000)
    assert len(chunks) == 1
    assert chunks[0].startswith("## ")


def test_split_long_journal_multiple_chunks() -> None:
    parts = [f"## 2026-05-{i:02d} - x\n" + ("a" * 200) for i in range(20)]
    text = "\n\n".join(parts)
    chunks = md._split_journal_chunks(text, max_chars=500)
    assert len(chunks) > 1
    # 每 chunk 不应过分超 max_chars (允许略超因段不切)
    for c in chunks:
        assert len(c) <= 500 + 250  # 留余量


# ============================================================
# _extract_peak_hours
# ============================================================


def test_peak_hours_too_few_data_returns_none() -> None:
    text = "## 2026-05-01 08:30 - x\n## 2026-05-02 09:15 - y"
    assert md._extract_peak_hours(text) is None


def test_peak_hours_morning_dominant() -> None:
    """≥ 5 个时间戳, 早晨高频应判 morning"""
    timestamps = [
        "2026-05-01 08:30",
        "2026-05-02 09:15",
        "2026-05-03 08:50",
        "2026-05-04 09:00",
        "2026-05-05 10:30",
        "2026-05-06 09:45",
        "2026-05-07 08:15",
    ]
    text = "\n\n".join(f"## {ts} - 早会\n本周关键内容." for ts in timestamps)
    result = md._extract_peak_hours(text)
    assert result is not None
    value, evidence = result
    assert "早晨型" in value
    assert len(evidence) >= 1


def test_peak_hours_no_dominance_returns_none() -> None:
    """全天平均分布 → 没明显倾向, 不 propose"""
    timestamps = [
        "2026-05-01 03:00",
        "2026-05-02 09:00",
        "2026-05-03 15:00",
        "2026-05-04 21:00",
        "2026-05-05 04:00",
        "2026-05-06 11:00",
        "2026-05-07 17:00",
        "2026-05-08 23:00",
    ]
    text = "\n\n".join(f"## {ts} - x\nstuff." for ts in timestamps)
    result = md._extract_peak_hours(text)
    # 4 段 (morning/afternoon/evening/night) 各 2, 都 25%, 没一个 ≥ 40%
    assert result is None


# ============================================================
# _extract_bullet_preference
# ============================================================


def test_bullet_preference_too_few_lines_returns_none() -> None:
    assert md._extract_bullet_preference("简短文字") is None


def test_bullet_preference_list_dominant() -> None:
    text = "\n".join([
        "- item 1",
        "- item 2",
        "- item 3",
        "- item 4",
        "- item 5",
        "- item 6",
        "- item 7",
        "- item 8",
        "- item 9",
        "- item 10",
        "- item 11",
        "- item 12",
        "- item 13",
        "- item 14",
        "- item 15",
        "一段总结的文字",
    ] * 2)
    result = md._extract_bullet_preference(text)
    assert result is not None
    value, evidence = result
    assert "列表型" in value


def test_bullet_preference_prose_dominant() -> None:
    text = "\n".join([f"这是一段散文 {i}." for i in range(40)])
    result = md._extract_bullet_preference(text)
    assert result is not None
    value, _ = result
    assert "散文型" in value


# ============================================================
# 红线过滤
# ============================================================


def test_redline_trait_blocked() -> None:
    assert md._is_redline_trait("health.weight_pref", "员工想减肥")
    assert md._is_redline_trait("finance.budget", "员工预算 1000")
    assert md._is_redline_trait("religion.practice", "祈祷")
    assert md._is_redline_trait("political.stance", "支持某党")
    # 中文红线
    assert md._is_redline_trait("健康", "员工有糖尿病")
    assert md._is_redline_trait("工资", "员工说工资低")


def test_non_redline_trait_pass() -> None:
    assert not md._is_redline_trait("work_pattern.peak_hours", "早晨型")
    assert not md._is_redline_trait("writing_style.bullet_pref", "偏列表型")


# ============================================================
# distill_journal_to_traits 整链路
# ============================================================


def test_distill_extracts_peak_and_bullet() -> None:
    timestamps = [f"2026-05-{i:02d} 09:00" for i in range(1, 8)]
    journal = "\n\n".join(
        f"## {ts} - 周报\n- bullet 1\n- bullet 2\n- bullet 3\n- bullet 4\n- bullet 5\n- bullet 6"
        for ts in timestamps
    )
    proposals = md.distill_journal_to_traits(journal)
    fields = {p["field"] for p in proposals}
    assert "work_pattern.peak_hours" in fields
    assert "writing_style.bullet_pref" in fields


def test_distill_filters_redline_trait(monkeypatch: pytest.MonkeyPatch) -> None:
    """模拟规则抽出红线 trait, 应该被过滤掉"""
    # 临时 monkey-patch 一个红线 extractor
    def fake_redline_extractor(text: str) -> tuple[str, list[str]] | None:
        return ("健康预警", ["员工说体检不正常"])

    monkeypatch.setattr(md, "_extract_peak_hours", fake_redline_extractor)
    proposals = md.distill_journal_to_traits("## 2026-05-01\n内容")
    # 红线 trait 不应在 proposals 里
    fields = {p["field"] for p in proposals}
    assert "work_pattern.peak_hours" not in fields  # 该被红线过滤掉


# ============================================================
# should_run_distillation
# ============================================================


def test_should_run_below_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(md, "DISTILL_STATE_PATH", tmp_path / "state.json")
    short = "## 2026-05-01\n内容"
    assert md.should_run_distillation(short) is False


def test_should_run_at_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(md, "DISTILL_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(md, "DISTILL_THRESHOLD", 5)
    text = "\n\n".join(f"## 2026-05-{i:02d}\n内容" for i in range(1, 7))
    assert md.should_run_distillation(text) is True


def test_should_not_run_within_24h(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(md, "DISTILL_STATE_PATH", state_path)
    monkeypatch.setattr(md, "DISTILL_THRESHOLD", 5)
    state_path.write_text(json.dumps({"last_run_ts": time.time() - 3600}))  # 1 小时前
    text = "\n\n".join(f"## 2026-05-{i:02d}\n内容" for i in range(1, 7))
    assert md.should_run_distillation(text) is False


def test_should_run_after_24h(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(md, "DISTILL_STATE_PATH", state_path)
    monkeypatch.setattr(md, "DISTILL_THRESHOLD", 5)
    state_path.write_text(json.dumps({"last_run_ts": time.time() - 25 * 3600}))  # 25 小时前
    text = "\n\n".join(f"## 2026-05-{i:02d}\n内容" for i in range(1, 7))
    assert md.should_run_distillation(text) is True


# ============================================================
# maybe_run_distillation 整流程
# ============================================================


def test_maybe_run_no_op_below_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(md, "DISTILL_STATE_PATH", tmp_path / "state.json")
    proposals = md.maybe_run_distillation("## 2026-05-01\n内容")
    assert proposals == []


def test_maybe_run_returns_proposals_above_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(md, "DISTILL_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(md, "DISTILL_THRESHOLD", 5)
    timestamps = [f"2026-05-{i:02d} 08:30" for i in range(1, 8)]
    journal = "\n\n".join(
        f"## {ts} - 周报\n- a\n- b\n- c\n- d\n- e\n- f\n- g"
        for ts in timestamps
    )
    proposals = md.maybe_run_distillation(journal)
    assert len(proposals) >= 1
    fields = {p["field"] for p in proposals}
    assert "work_pattern.peak_hours" in fields


def test_maybe_run_writes_state_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(md, "DISTILL_STATE_PATH", state_path)
    monkeypatch.setattr(md, "DISTILL_THRESHOLD", 5)
    timestamps = [f"2026-05-{i:02d} 08:30" for i in range(1, 8)]
    journal = "\n\n".join(
        f"## {ts} - x\n内容." for ts in timestamps
    )
    md.maybe_run_distillation(journal)
    assert state_path.exists()
    state = json.loads(state_path.read_text())
    assert "last_run_ts" in state
    assert "last_num_proposals" in state


def test_maybe_run_dedups_same_field_across_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """如果两个 chunk 都抽出同 field 的 trait, 取 evidence 多的那个"""
    monkeypatch.setattr(md, "DISTILL_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(md, "DISTILL_THRESHOLD", 5)
    monkeypatch.setattr(md, "DISTILL_CHUNK_CHARS", 200)
    timestamps = [f"2026-05-{i:02d} 08:30" for i in range(1, 12)]
    journal = "\n\n".join(
        f"## {ts} - x\n内容." for ts in timestamps
    )
    proposals = md.maybe_run_distillation(journal)
    fields = [p["field"] for p in proposals]
    # 同 field 不重复
    assert len(fields) == len(set(fields))
