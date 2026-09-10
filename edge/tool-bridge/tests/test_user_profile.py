"""BL-MM7 user_profile 单元测试.

覆盖:
- get / propose / confirm / clear 四个工具的 happy path
- propose 阈值: 累积 N 次 evidence 才 should_confirm
- propose 同 value 已 confirmed → skipped
- propose locked 字段 → skipped
- propose 红线字段 (健康/财务/感情等) → error
- confirm locked=True → 之后 propose 同字段被 skipped
- 校验: field 必填 / value 枚举 / evidence 必填 / 长度上限
- clear 单字段 vs 全部
- 文件损坏/不存在 → 返空 dict
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path: Path, monkeypatch):
    """每个测试用 tmp_path 隔离 profile 文件."""
    fake_path = tmp_path / "user_profile.json"
    monkeypatch.setattr(
        "catfish_tool_bridge.user_profile.USER_PROFILE_PATH", fake_path
    )
    monkeypatch.setattr(
        "catfish_tool_bridge.user_profile.JOURNAL_PATH",
        tmp_path / "employee_journal.md",
    )
    monkeypatch.setattr(
        "catfish_tool_bridge.user_profile._last_journal_signature", None
    )
    return fake_path


@pytest.fixture
def up():
    """快捷 import."""
    from catfish_tool_bridge import user_profile
    return user_profile


# ── get ──────────────────────────────────────────────────────


def test_get_empty_returns_empty_dict(up):
    r = up.user_profile_get({})
    assert r == {"type": "result", "result": {}}


def test_get_returns_summary_not_full_evidence(up, isolated_profile):
    """get 不返完整 evidence list (省 tokens), 只返 evidence_count."""
    isolated_profile.parent.mkdir(parents=True, exist_ok=True)
    isolated_profile.write_text(
        json.dumps({
            "writing_style.tone": {
                "value": "直接",
                "evidence": [{"text": "...", "ts": 1.0}, {"text": "...", "ts": 2.0}],
                "locked": False,
                "last_confirmed": 100.0,
                "proposed_value": None,
            }
        }),
        encoding="utf-8",
    )
    r = up.user_profile_get({})
    assert r["type"] == "result"
    summary = r["result"]
    assert "writing_style.tone" in summary
    item = summary["writing_style.tone"]
    assert item["value"] == "直接"
    assert item["evidence_count"] == 2
    assert "evidence" not in item  # 完整 list 不返


def test_get_auto_observes_journal_without_overwriting_profile(up, isolated_profile):
    """新 journal 自动形成待确认建议，但不能静默覆盖已确认值."""
    from catfish_tool_bridge import user_profile

    user_profile.user_profile_confirm({
        "field": "writing_style.tone",
        "value": "直接",
    })
    user_profile.JOURNAL_PATH.write_text(
        "\n".join([
            "员工偏好委婉，先说明背景。",
            "员工偏好委婉，语气保持温和。",
            "员工偏好委婉，不要太生硬。",
        ]),
        encoding="utf-8",
    )

    first = up.user_profile_get({})["result"]["writing_style.tone"]
    second = up.user_profile_get({})["result"]["writing_style.tone"]

    assert first["value"] == "直接"
    assert first["proposed_value"] == "委婉"
    assert first["proposed_evidence_count"] == 3
    assert second["evidence_count"] == first["evidence_count"]


def test_propose_deduplicates_same_evidence(up):
    args = {
        "field": "writing_style.tone",
        "value": "直接",
        "evidence": "员工说不要绕弯子",
    }
    assert up.user_profile_propose(args)["type"] == "result"
    duplicate = up.user_profile_propose(args)
    assert duplicate == {"type": "skipped", "reason": "evidence already recorded"}


# ── propose ──────────────────────────────────────────────────


def test_propose_first_time_returns_result_below_threshold(up):
    """第一次 propose 应该累积 evidence, 不到阈值 (3 次), 返 result 不返 should_confirm."""
    r = up.user_profile_propose({
        "field": "writing_style.tone",
        "value": "直接",
        "evidence": "员工说'别绕弯子'",
    })
    assert r["type"] == "result"
    assert r["result"]["evidence_count"] == 1
    assert r["result"]["needed_to_propose"] == 3


def test_propose_three_times_same_value_triggers_should_confirm(up):
    """累积 3 次同 value 的 evidence → should_confirm."""
    for i in range(3):
        r = up.user_profile_propose({
            "field": "writing_style.tone",
            "value": "直接",
            "evidence": f"evidence {i}",
        })
    assert r["type"] == "should_confirm"
    assert r["proposed_value"] == "直接"
    assert r["evidence_count"] == 3
    assert r["current_value"] is None
    assert len(r["evidence_examples"]) == 3
    assert "跟员工自然语言确认" in r["hint"]


def test_propose_evidence_with_different_values_dont_combine(up):
    """3 次不同 value 不触发 should_confirm (各自独立累积)."""
    for v in ["直接", "委婉", "幽默"]:
        r = up.user_profile_propose({
            "field": "writing_style.tone",
            "value": v,
            "evidence": f"evidence for {v}",
        })
    # 最后一次返 result, evidence_count=1 (只有'幽默' 1 次)
    assert r["type"] == "result"
    assert r["result"]["evidence_count"] == 1


def test_propose_red_line_field_returns_error(up):
    r = up.user_profile_propose({
        "field": "personal.health",
        "value": "高血压",
        "evidence": "员工说血压高",
    })
    assert r["type"] == "error"
    assert "红线" in r["error"]


def test_propose_locked_field_returns_skipped(up):
    """先 confirm + locked, 之后 propose 同字段 → skipped."""
    up.user_profile_confirm({
        "field": "writing_style.tone",
        "value": "直接",
        "locked": True,
    })
    r = up.user_profile_propose({
        "field": "writing_style.tone",
        "value": "委婉",
        "evidence": "...",
    })
    assert r["type"] == "skipped"
    assert "locked" in r["reason"]


def test_propose_already_confirmed_same_value_skipped(up):
    """已 confirm 过同值, propose 同值不再累积 (员工已认可)."""
    up.user_profile_confirm({"field": "writing_style.tone", "value": "直接"})
    r = up.user_profile_propose({
        "field": "writing_style.tone",
        "value": "直接",
        "evidence": "...",
    })
    assert r["type"] == "skipped"
    assert "already confirmed" in r["reason"]


def test_propose_invalid_value_for_enum_field(up):
    r = up.user_profile_propose({
        "field": "writing_style.tone",
        "value": "moonwalk",  # 不在枚举里
        "evidence": "...",
    })
    assert r["type"] == "error"
    assert "必须是" in r["error"]


def test_propose_missing_evidence_returns_error(up):
    r = up.user_profile_propose({
        "field": "writing_style.tone",
        "value": "直接",
        "evidence": "",
    })
    assert r["type"] == "error"
    assert "evidence" in r["error"]


def test_propose_evidence_truncated_at_max_len(up):
    long_ev = "x" * 600
    up.user_profile_propose({
        "field": "writing_style.tone",
        "value": "直接",
        "evidence": long_ev,
    })
    profile = json.loads(__read_isolated())
    ev = profile["writing_style.tone"]["evidence"][-1]["text"]
    assert len(ev) <= 501  # 500 + 省略号


def test_propose_evidence_capped_at_max_per_field(up):
    """超过 MAX_EVIDENCE_PER_FIELD 后老的截掉."""
    for i in range(15):
        up.user_profile_propose({
            "field": "writing_style.tone",
            "value": "直接",
            "evidence": f"ev{i}",
        })
    profile = json.loads(__read_isolated())
    assert len(profile["writing_style.tone"]["evidence"]) == 10


# ── confirm ──────────────────────────────────────────────────


def test_confirm_writes_value_and_clears_proposed(up):
    """propose 后 confirm 应清掉 proposed_value, 写入 value + last_confirmed."""
    up.user_profile_propose({
        "field": "writing_style.tone",
        "value": "直接",
        "evidence": "...",
    })
    r = up.user_profile_confirm({
        "field": "writing_style.tone",
        "value": "直接",
    })
    assert r["type"] == "result"
    assert r["result"]["value"] == "直接"
    assert r["result"]["previous_value"] is None
    profile = json.loads(__read_isolated())
    f = profile["writing_style.tone"]
    assert f["value"] == "直接"
    assert f["proposed_value"] is None
    assert f["last_confirmed"] is not None


def test_confirm_locked_true_persists(up):
    up.user_profile_confirm({
        "field": "writing_style.tone",
        "value": "直接",
        "locked": True,
    })
    r = up.user_profile_get({})
    assert r["result"]["writing_style.tone"]["locked"] is True


def test_confirm_returns_previous_value(up):
    up.user_profile_confirm({"field": "writing_style.tone", "value": "直接"})
    r = up.user_profile_confirm({"field": "writing_style.tone", "value": "委婉"})
    assert r["result"]["previous_value"] == "直接"
    assert r["result"]["value"] == "委婉"


# ── clear ────────────────────────────────────────────────────


def test_clear_all_deletes_file(up, isolated_profile):
    up.user_profile_confirm({"field": "writing_style.tone", "value": "直接"})
    assert isolated_profile.exists()
    r = up.user_profile_clear({})
    assert r["type"] == "result"
    assert "全部" in r["result"]
    assert not isolated_profile.exists()


def test_clear_single_field(up):
    up.user_profile_confirm({"field": "writing_style.tone", "value": "直接"})
    up.user_profile_confirm({"field": "personality.pace", "value": "急"})
    r = up.user_profile_clear({"field": "writing_style.tone"})
    assert r["type"] == "result"
    profile = json.loads(__read_isolated())
    assert "writing_style.tone" not in profile
    assert "personality.pace" in profile


# ── 文件损坏 / 不存在 ───────────────────────────────────────


def test_get_with_corrupted_file_returns_empty(up, isolated_profile):
    isolated_profile.parent.mkdir(parents=True, exist_ok=True)
    isolated_profile.write_text("not json {", encoding="utf-8")
    r = up.user_profile_get({})
    assert r == {"type": "result", "result": {}}


def test_propose_with_corrupted_file_starts_fresh(up, isolated_profile):
    isolated_profile.parent.mkdir(parents=True, exist_ok=True)
    isolated_profile.write_text("garbage", encoding="utf-8")
    r = up.user_profile_propose({
        "field": "writing_style.tone",
        "value": "直接",
        "evidence": "...",
    })
    assert r["type"] == "result"


# ── helper ────────────────────────────────────────────────────


def __read_isolated():
    """读 isolated_profile 当前内容. 跟 fixture 配对."""
    from catfish_tool_bridge import user_profile
    return user_profile.USER_PROFILE_PATH.read_text(encoding="utf-8")
