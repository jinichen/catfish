"""P3.5.42 (6/18 鸿波) — memory_enforce pre_tool_call hook 单测.

跑法: cd edge/hermes-plugins/catfish-xcatfish-user && python3 -m pytest tests/test_memory_enforce.py -q

覆盖:
- picker chain — picker_state.json > role_resolver chat_default > 兜底 catfish-private-main
- hook 早 return (非 memory tool / 非 add/replace / target 非 memory/user / content 空)
- classify LLM 返不同 route 的 expected 行为 (route == target 放行 / 不匹配 block)
- classify fail-silent (LLM 调挂 → 放行)
- audit log 写入正确
- 鸿波截图 4 条 content 端到端 (mock LLM 返 expected route)
"""
from __future__ import annotations

import json
import sys
import os
from pathlib import Path
from unittest import mock

import pytest

# 让 memory_enforce.py 直接可 import (跟 hermes plugin loader 同 pattern)
PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_DIR))

import memory_enforce  # noqa: E402


@pytest.fixture
def tmp_catfish(tmp_path: Path, monkeypatch):
    """临时 ~/.catfish/. env CATFISH_HOME 重定向."""
    catfish = tmp_path / ".catfish"
    catfish.mkdir()
    monkeypatch.setenv("CATFISH_HOME", str(catfish))
    return catfish


# ── picker chain 测试 ───────────────────────────────────────────


def test_picker_state_highest_priority(tmp_catfish):
    """picker_state.json 存在 → 走它."""
    (tmp_catfish / "picker_state.json").write_text(
        json.dumps({"chat_model": "catfish-private-main"}), encoding="utf-8",
    )
    assert memory_enforce.get_verifier_model() == "catfish-private-main"


def test_picker_state_empty_falls_through(tmp_catfish, monkeypatch):
    """picker_state.json 没 chat_model 字段 → fallback role_resolver, 再兜底 catfish-private-main."""
    (tmp_catfish / "picker_state.json").write_text("{}", encoding="utf-8")
    # mock role_resolver 返空
    monkeypatch.setattr(memory_enforce, "_resolve_role_via_gateway", lambda role: "")
    assert memory_enforce.get_verifier_model() == "catfish-private-main"


def test_picker_state_missing_falls_through(tmp_catfish, monkeypatch):
    """picker_state.json 不存在 → role_resolver → 兜底."""
    monkeypatch.setattr(memory_enforce, "_resolve_role_via_gateway",
                        lambda role: "catfish-private-main-from-role")
    assert memory_enforce.get_verifier_model() == "catfish-private-main-from-role"


def test_picker_state_corrupted_falls_through(tmp_catfish, monkeypatch):
    """picker_state.json 损坏 → fallback."""
    (tmp_catfish / "picker_state.json").write_text("not json", encoding="utf-8")
    monkeypatch.setattr(memory_enforce, "_resolve_role_via_gateway", lambda role: "")
    assert memory_enforce.get_verifier_model() == "catfish-private-main"


# ── hook 早 return 测试 (非 memory 调) ────────────────────────────


def test_hook_skip_non_memory_tool(tmp_catfish):
    assert memory_enforce.memory_enforce_hook(
        tool_name="read_file", args={"path": "/tmp/x"}, task_id="t1",
    ) is None


def test_hook_skip_non_add_replace_action(tmp_catfish):
    assert memory_enforce.memory_enforce_hook(
        tool_name="memory", args={"action": "remove", "old_text": "x"}, task_id="t1",
    ) is None


def test_hook_skip_invalid_target(tmp_catfish):
    assert memory_enforce.memory_enforce_hook(
        tool_name="memory", args={"action": "add", "target": "foo", "content": "x"},
        task_id="t1",
    ) is None


def test_hook_skip_empty_content(tmp_catfish):
    assert memory_enforce.memory_enforce_hook(
        tool_name="memory", args={"action": "add", "target": "memory", "content": ""},
        task_id="t1",
    ) is None


def test_hook_skip_non_dict_args(tmp_catfish):
    assert memory_enforce.memory_enforce_hook(
        tool_name="memory", args=None, task_id="t1",
    ) is None


# ── classify mock — 端到端行为 ───────────────────────────────────


def _mock_classify(route: str, reason: str = "test reason", confidence: float = 0.9):
    """返一个 mock 给 _classify_memory_route."""
    return lambda content, model: {
        "route": route, "reason": reason, "confidence": confidence,
    }


def test_hook_allow_when_route_matches_target(tmp_catfish, monkeypatch):
    """LLM 判定 route=memory + target=memory → 放行."""
    monkeypatch.setattr(memory_enforce, "_classify_memory_route",
                        _mock_classify("memory"))
    monkeypatch.setattr(memory_enforce, "get_verifier_model", lambda: "test-model")
    result = memory_enforce.memory_enforce_hook(
        tool_name="memory",
        args={"action": "add", "target": "memory",
              "content": "ISO 27001 申报流程: 1. 准备材料 2. 提交 3. 复核"},
        task_id="t1",
    )
    assert result is None
    # audit log 写了 allow
    audit_path = tmp_catfish / "memory_audit.jsonl"
    assert audit_path.exists()
    entry = json.loads(audit_path.read_text().strip().splitlines()[-1])
    assert entry["decision"] == "allow"
    assert entry["llm_route"] == "memory"


def test_hook_block_when_route_journal(tmp_catfish, monkeypatch):
    """鸿波截图实景: 'X 6/19 飞抵福州' LLM 判定 journal 但 target=memory → block."""
    monkeypatch.setattr(memory_enforce, "_classify_memory_route",
                        _mock_classify("journal", reason="含日期 + 单次事件 (飞抵)"))
    monkeypatch.setattr(memory_enforce, "get_verifier_model", lambda: "test-model")
    result = memory_enforce.memory_enforce_hook(
        tool_name="memory",
        args={"action": "add", "target": "memory",
              "content": "陈淡孜 6/19 飞抵福州长乐, MF878"},
        task_id="t1",
    )
    assert result is not None
    assert result["action"] == "block"
    assert "journal" in result["message"]
    assert "employee_journal.md" in result["message"]
    # audit log block
    audit_path = tmp_catfish / "memory_audit.jsonl"
    entry = json.loads(audit_path.read_text().strip().splitlines()[-1])
    assert entry["decision"] == "block_wrong_kind"


def test_hook_block_when_target_mismatch(tmp_catfish, monkeypatch):
    """target=memory 但 LLM 判定 user → block + 建议改 target=user."""
    monkeypatch.setattr(memory_enforce, "_classify_memory_route",
                        _mock_classify("user", reason="是员工偏好"))
    monkeypatch.setattr(memory_enforce, "get_verifier_model", lambda: "test-model")
    result = memory_enforce.memory_enforce_hook(
        tool_name="memory",
        args={"action": "add", "target": "memory",
              "content": "鸿波喜欢直接输出, 不要确认"},
        task_id="t1",
    )
    assert result is not None
    assert result["action"] == "block"
    assert "USER.md" in result["message"]
    assert "target=user" in result["message"]


def test_hook_block_when_route_todo(tmp_catfish, monkeypatch):
    """带 deadline 任务 → todo, block + 建议 catfish_create_reminder."""
    monkeypatch.setattr(memory_enforce, "_classify_memory_route",
                        _mock_classify("todo"))
    monkeypatch.setattr(memory_enforce, "get_verifier_model", lambda: "test-model")
    result = memory_enforce.memory_enforce_hook(
        tool_name="memory",
        args={"action": "add", "target": "memory",
              "content": "9 月底前提交 ISO 27001 复审报告"},
        task_id="t1",
    )
    assert "catfish_create_reminder" in result["message"]


def test_hook_block_when_route_skill(tmp_catfish, monkeypatch):
    """多步流程 → skill, block + 建议 catfish_propose_skill."""
    monkeypatch.setattr(memory_enforce, "_classify_memory_route",
                        _mock_classify("skill"))
    monkeypatch.setattr(memory_enforce, "get_verifier_model", lambda: "test-model")
    result = memory_enforce.memory_enforce_hook(
        tool_name="memory",
        args={"action": "add", "target": "memory",
              "content": "如何申报资质: 步骤 1 / 2 / 3 / 4 / 5"},
        task_id="t1",
    )
    assert "catfish_propose_skill" in result["message"]


# ── fail-silent 测试 ────────────────────────────────────────────


def test_hook_fail_silent_when_classify_fails(tmp_catfish, monkeypatch):
    """classify 返 None (LLM 调挂) → 放行 + audit log allow_fallback."""
    monkeypatch.setattr(memory_enforce, "_classify_memory_route",
                        lambda content, model: None)
    monkeypatch.setattr(memory_enforce, "get_verifier_model", lambda: "test-model")
    result = memory_enforce.memory_enforce_hook(
        tool_name="memory",
        args={"action": "add", "target": "memory", "content": "测试内容"},
        task_id="t1",
    )
    assert result is None  # 放行
    audit_path = tmp_catfish / "memory_audit.jsonl"
    entry = json.loads(audit_path.read_text().strip().splitlines()[-1])
    assert entry["decision"] == "allow_fallback"


def test_hook_fail_silent_when_classify_raises(tmp_catfish, monkeypatch):
    """classify 抛异常 → hook 抓住 + 放行 (不阻 LLM)."""
    def _raise(content, model):
        raise RuntimeError("boom")
    monkeypatch.setattr(memory_enforce, "_classify_memory_route", _raise)
    monkeypatch.setattr(memory_enforce, "get_verifier_model", lambda: "test-model")
    # hook 内有 try/except 兜底
    result = memory_enforce.memory_enforce_hook(
        tool_name="memory",
        args={"action": "add", "target": "memory", "content": "x"},
        task_id="t1",
    )
    assert result is None  # 放行兜底
