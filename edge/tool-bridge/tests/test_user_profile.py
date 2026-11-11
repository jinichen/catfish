"""user_profile 单元测试 (11/12 改: 证据只认员工原话, 不扫工作日志)。

覆盖:
- get 列全 9 个字段 + options + 待确认提议的原话
- propose 一次就成待确认, 不覆盖已确认的值; 锁定 / 红线 / 同值 / 重复原话
- 老文件里没 src 的证据 (日志关键词套的) 读时清掉, 没原话撑的提议一起撤; 确认值和锁定不动
- 不再读工作日志
- confirm / clear (全部 / 单字段 / 只撤提议)
- 文件损坏 → 空画像
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path: Path, monkeypatch):
    fake_path = tmp_path / "user_profile.json"
    monkeypatch.setattr("catfish_tool_bridge.user_profile.USER_PROFILE_PATH", fake_path)
    return fake_path


@pytest.fixture
def up():
    from catfish_tool_bridge import user_profile
    return user_profile


def _read(up):
    return json.loads(up.USER_PROFILE_PATH.read_text(encoding="utf-8"))


TONE = "writing_style.tone"


# ── get ──────────────────────────────────────────────────────


def test_get_empty_lists_all_fields_unset(up):
    r = up.user_profile_get({})["result"]
    assert set(up.ALLOWED_FIELDS) <= set(r)
    assert all(v["value"] is None and v["proposed_value"] is None for v in r.values())
    assert r[TONE]["options"] == up.ALLOWED_FIELDS[TONE]
    assert r["work_pattern.peak_hours"]["options"] is None  # 自由文本


def test_get_shows_quote_for_pending_proposal(up):
    up.user_profile_confirm({"field": TONE, "value": "直接"})
    up.user_profile_propose({"field": TONE, "value": "委婉", "evidence": "以后说话委婉点"})
    item = up.user_profile_get({})["result"][TONE]
    assert item["value"] == "直接"
    assert item["proposed_value"] == "委婉"
    assert item["proposed_quote"] == "以后说话委婉点"
    assert "evidence" not in item and "evidence_count" not in item


def test_get_does_not_read_journal(up, isolated_profile):
    """11/12: 不再拿关键词套工作日志 —— 日志里写满「偏好委婉」也不产生提议。"""
    (isolated_profile.parent / "employee_journal.md").write_text("员工偏好委婉。\n" * 50, encoding="utf-8")
    r = up.user_profile_get({})["result"]
    assert r[TONE]["proposed_value"] is None
    assert not isolated_profile.exists()


# ── 老数据清理 ─────────────────────────────────────────────


def test_legacy_journal_evidence_dropped_keeps_confirmed_and_lock(up, isolated_profile):
    isolated_profile.write_text(json.dumps({
        TONE: {"value": "直接", "locked": False, "last_confirmed": 1.0, "proposed_value": "casual",
               "evidence": [{"text": "日志句子", "ts": 1.0, "value": "casual"}] * 2},
        "personality.pace": {"value": "急", "locked": True, "last_confirmed": 1.0, "proposed_value": None,
                             "evidence": [{"text": "日志", "ts": 1.0, "value": "急"}]},
        "writing_style.bullet_pref": {"value": "列表", "locked": False, "last_confirmed": 1.0,
                                      "proposed_value": "段落",
                                      "evidence": [{"text": "别用列表了", "ts": 2.0, "value": "段落", "src": "chat"}]},
    }), encoding="utf-8")
    r = up.user_profile_get({})["result"]
    assert r[TONE]["value"] == "直接" and r[TONE]["proposed_value"] is None
    assert r["personality.pace"]["value"] == "急" and r["personality.pace"]["locked"] is True
    # 员工原话撑着的提议保留
    assert r["writing_style.bullet_pref"]["proposed_value"] == "段落"
    saved = _read(up)
    assert saved[TONE]["evidence"] == [] and saved["personality.pace"]["evidence"] == []
    # 清一次就稳定: 再读不再改文件
    mtime = isolated_profile.stat().st_mtime_ns
    up.user_profile_get({})
    assert isolated_profile.stat().st_mtime_ns == mtime


# ── propose ──────────────────────────────────────────────────


def test_propose_once_is_pending_not_confirmed(up):
    r = up.user_profile_propose({"field": TONE, "value": "直接", "evidence": "别绕弯子"})
    assert r["type"] == "should_confirm"
    f = _read(up)[TONE]
    assert f["value"] is None and f["proposed_value"] == "直接"
    assert f["evidence"][0]["src"] == "chat"


def test_propose_does_not_overwrite_confirmed(up):
    up.user_profile_confirm({"field": TONE, "value": "直接"})
    up.user_profile_propose({"field": TONE, "value": "幽默", "evidence": "可以轻松点"})
    assert _read(up)[TONE]["value"] == "直接"


def test_propose_latest_statement_wins(up):
    up.user_profile_propose({"field": TONE, "value": "委婉", "evidence": "温和点"})
    up.user_profile_propose({"field": TONE, "value": "直接", "evidence": "算了还是直说"})
    item = up.user_profile_get({})["result"][TONE]
    assert item["proposed_value"] == "直接" and item["proposed_quote"] == "算了还是直说"


def test_propose_deduplicates_same_evidence(up):
    args = {"field": TONE, "value": "直接", "evidence": "员工说不要绕弯子"}
    assert up.user_profile_propose(args)["type"] == "should_confirm"
    assert up.user_profile_propose(args) == {"type": "skipped", "reason": "evidence already recorded"}


def test_propose_red_line_field_returns_error(up):
    r = up.user_profile_propose({"field": "personal.health", "value": "x", "evidence": "..."})
    assert r["type"] == "error" and "红线" in r["error"]


def test_propose_locked_field_returns_skipped(up):
    up.user_profile_confirm({"field": TONE, "value": "直接", "locked": True})
    r = up.user_profile_propose({"field": TONE, "value": "委婉", "evidence": "..."})
    assert r == {"type": "skipped", "reason": "field is locked by user"}


def test_propose_already_confirmed_same_value_skipped(up):
    up.user_profile_confirm({"field": TONE, "value": "直接"})
    r = up.user_profile_propose({"field": TONE, "value": "直接", "evidence": "..."})
    assert r == {"type": "skipped", "reason": "value already confirmed"}


def test_propose_invalid_value_for_enum_field(up):
    r = up.user_profile_propose({"field": TONE, "value": "随便", "evidence": "..."})
    assert r["type"] == "error"


def test_propose_missing_evidence_returns_error(up):
    r = up.user_profile_propose({"field": TONE, "value": "直接", "evidence": ""})
    assert r["type"] == "error" and "evidence" in r["error"]


def test_propose_evidence_truncated_and_capped(up):
    up.user_profile_propose({"field": TONE, "value": "直接", "evidence": "x" * 600})
    for i in range(5):
        up.user_profile_propose({"field": TONE, "value": "直接", "evidence": f"ev{i}"})
    ev = _read(up)[TONE]["evidence"]
    assert len(ev) == up.MAX_EVIDENCE_PER_FIELD
    assert ev[-1]["text"] == "ev4"


# ── confirm ──────────────────────────────────────────────────


def test_confirm_writes_value_and_clears_proposed(up):
    up.user_profile_propose({"field": TONE, "value": "直接", "evidence": "..."})
    r = up.user_profile_confirm({"field": TONE, "value": "直接"})
    assert r["result"]["value"] == "直接" and r["result"]["previous_value"] is None
    f = _read(up)[TONE]
    assert f["value"] == "直接" and f["proposed_value"] is None and f["last_confirmed"]


def test_confirm_locked_true_persists(up):
    up.user_profile_confirm({"field": TONE, "value": "直接", "locked": True})
    assert up.user_profile_get({})["result"][TONE]["locked"] is True


def test_confirm_free_text_field(up):
    r = up.user_profile_confirm({"field": "work_pattern.peak_hours", "value": "9:00-12:00"})
    assert r["type"] == "result"


# ── clear ────────────────────────────────────────────────────


def test_clear_all_deletes_file(up, isolated_profile):
    up.user_profile_confirm({"field": TONE, "value": "直接"})
    assert "全部" in up.user_profile_clear({})["result"]
    assert not isolated_profile.exists()


def test_clear_single_field(up):
    up.user_profile_confirm({"field": TONE, "value": "直接"})
    up.user_profile_confirm({"field": "personality.pace", "value": "急"})
    up.user_profile_clear({"field": TONE})
    profile = _read(up)
    assert TONE not in profile and "personality.pace" in profile


def test_clear_proposal_only_keeps_value(up):
    up.user_profile_confirm({"field": TONE, "value": "直接"})
    up.user_profile_propose({"field": TONE, "value": "委婉", "evidence": "温和点"})
    up.user_profile_clear({"field": TONE, "proposal_only": True})
    item = up.user_profile_get({})["result"][TONE]
    assert item["value"] == "直接" and item["proposed_value"] is None


# ── 文件损坏 ───────────────────────────────────────────────


def test_get_with_corrupted_file_returns_empty_profile(up, isolated_profile):
    isolated_profile.write_text("not json {", encoding="utf-8")
    r = up.user_profile_get({})["result"]
    assert all(v["value"] is None for v in r.values())


def test_propose_with_corrupted_file_starts_fresh(up, isolated_profile):
    isolated_profile.write_text("garbage", encoding="utf-8")
    r = up.user_profile_propose({"field": TONE, "value": "直接", "evidence": "..."})
    assert r["type"] == "should_confirm"
