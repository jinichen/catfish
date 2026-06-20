"""P3.5.42.2 — audit-old-memory.py 单测.

跑法 (从 catfish 仓库根):
  python3 -m pytest scripts/tests/test_audit_old_memory.py -q

覆盖:
- 加载 memory_enforce module 不挂
- _read_entries 切 ENTRY_DELIMITER 正确 (跟 hermes-memory-cleanup.py 对齐)
- _classify_all 端到端 (mock LLM 返不同 route)
- build_report 统计算对
- render_markdown 不挂 + 每个 section 都覆盖
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

# 加载 scripts/audit-old-memory.py 这个非 importable 名字的脚本
_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "audit-old-memory.py"


@pytest.fixture
def audit_mod():
    spec = importlib.util.spec_from_file_location("audit_old_memory", _SCRIPT_PATH)
    assert spec and spec.loader, "spec_from_file_location 挂"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def tmp_hermes(tmp_path, monkeypatch):
    """临时 ~/.hermes/memories/, 写预设 USER.md + MEMORY.md."""
    hermes = tmp_path / ".hermes" / "memories"
    hermes.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    # Path.home() 在 Linux 走 HOME env, 在 macOS 走 pw_dir, 这里 monkeypatch 也兜
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return hermes


def test_load_memory_enforce_finds_module(audit_mod):
    """_load_memory_enforce 真能从 catfish 仓库内 plugin 加载."""
    enforce = audit_mod._load_memory_enforce()
    assert enforce is not None, "memory_enforce module 加载失败"
    assert hasattr(enforce, "_classify_memory_route")
    assert hasattr(enforce, "get_verifier_model")


def test_read_entries_splits_on_delimiter(audit_mod, tmp_hermes):
    """ENTRY_DELIMITER = '\\n§\\n' 按 hermes memory_provider 切."""
    (tmp_hermes / "MEMORY.md").write_text(
        "entry 1 content\n§\nentry 2 content\n§\nentry 3 content",
        encoding="utf-8",
    )
    entries = audit_mod._read_entries("MEMORY")
    assert len(entries) == 3
    assert entries[0] == "entry 1 content"
    assert entries[2] == "entry 3 content"


def test_read_entries_strips_and_drops_empty(audit_mod, tmp_hermes):
    (tmp_hermes / "USER.md").write_text(
        "  e1  \n§\n\n§\n  e2  ", encoding="utf-8",
    )
    entries = audit_mod._read_entries("USER")
    assert entries == ["e1", "e2"]


def test_read_entries_missing_file_returns_empty(audit_mod, tmp_hermes):
    assert audit_mod._read_entries("MEMORY") == []


def _mock_enforce(route_map):
    """造 fake memory_enforce module — _classify_memory_route 按 content 关键字返预设 route."""
    class _Fake:
        @staticmethod
        def get_verifier_model():
            return "test-model"

        @staticmethod
        def _classify_memory_route(content, model):
            for key, route in route_map.items():
                if key in content:
                    return {"route": route, "reason": f"matched {key}", "confidence": 0.9}
            return None  # 没 match 模拟 classify 挂
    return _Fake()


def test_classify_all_keep_decision(audit_mod, capsys):
    """route == target → decision=keep."""
    enforce = _mock_enforce({"ISO 流程": "memory"})
    results = audit_mod._classify_all("MEMORY", ["ISO 流程 详细步骤"], enforce)
    assert len(results) == 1
    assert results[0]["decision"] == "keep"
    assert results[0]["llm_route"] == "memory"


def test_classify_all_suggest_retarget(audit_mod, capsys):
    """target=MEMORY → actual_target='memory' 但 route='user' → suggest_retarget."""
    enforce = _mock_enforce({"鸿波偏好": "user"})
    results = audit_mod._classify_all("MEMORY", ["鸿波偏好 直接输出"], enforce)
    assert results[0]["decision"] == "suggest_retarget"
    assert results[0]["llm_route"] == "user"


def test_classify_all_suggest_delete_journal(audit_mod, capsys):
    """route=journal → suggest_delete."""
    enforce = _mock_enforce({"飞抵福州": "journal"})
    results = audit_mod._classify_all("MEMORY", ["陈某 6/19 飞抵福州 MF878"], enforce)
    assert results[0]["decision"] == "suggest_delete"
    assert results[0]["llm_route"] == "journal"


def test_classify_all_suggest_delete_todo(audit_mod, capsys):
    enforce = _mock_enforce({"9 月底": "todo"})
    results = audit_mod._classify_all("MEMORY", ["9 月底前提交资质报告"], enforce)
    assert results[0]["decision"] == "suggest_delete"
    assert results[0]["llm_route"] == "todo"


def test_classify_all_skip_when_llm_fails(audit_mod, capsys):
    """_classify_memory_route 返 None → decision=skip."""
    enforce = _mock_enforce({})  # 全不 match → None
    results = audit_mod._classify_all("MEMORY", ["xxx"], enforce)
    assert results[0]["decision"] == "skip"
    assert results[0]["llm_route"] is None


def test_build_report_counts(audit_mod):
    results = [
        {"decision": "keep"}, {"decision": "keep"},
        {"decision": "suggest_retarget"},
        {"decision": "suggest_delete"}, {"decision": "suggest_delete"},
        {"decision": "skip"},
    ]
    # 补必要字段防 render_markdown 挂
    for r in results:
        r.update({
            "index": 1, "target": "MEMORY", "actual_target": "memory",
            "content_preview": "x", "content_len": 1,
            "llm_route": "memory", "llm_reason": "test", "confidence": 0.5,
            "content": "x",
        })
    rep = audit_mod.build_report(results)
    assert rep["total"] == 6
    assert rep["keep"] == 2
    assert rep["suggest_retarget"] == 1
    assert rep["suggest_delete"] == 2
    assert rep["skip"] == 1


def test_render_markdown_covers_all_sections(audit_mod):
    """每种 decision 至少一个, render_markdown 不挂 + 输出含 sections."""
    results = [
        {"index": 1, "target": "MEMORY", "actual_target": "memory",
         "content_preview": "ISO 流程", "content_len": 10,
         "llm_route": "memory", "llm_reason": "稳定项目常量", "confidence": 0.9,
         "decision": "keep", "content": "x"},
        {"index": 2, "target": "MEMORY", "actual_target": "memory",
         "content_preview": "鸿波偏好", "content_len": 10,
         "llm_route": "user", "llm_reason": "员工偏好", "confidence": 0.85,
         "decision": "suggest_retarget", "content": "x"},
        {"index": 3, "target": "MEMORY", "actual_target": "memory",
         "content_preview": "陈某飞抵", "content_len": 10,
         "llm_route": "journal", "llm_reason": "单次事件 + 日期", "confidence": 0.95,
         "decision": "suggest_delete", "content": "x"},
        {"index": 4, "target": "MEMORY", "actual_target": "memory",
         "content_preview": "xxx", "content_len": 3,
         "llm_route": None, "llm_reason": "classify 失败", "confidence": 0.0,
         "decision": "skip", "content": "x"},
    ]
    rep = audit_mod.build_report(results)
    md = audit_mod.render_markdown(rep)

    assert "# Hermes Memory 旧数据 LLM 审查报告" in md
    assert "❌ 建议删" in md
    assert "⚠️ 建议改 target" in md
    assert "⏭ 跳过" in md
    assert "✅ 留" in md
    # cleanup 命令清单存在
    assert "hermes-memory-cleanup.py delete MEMORY 3" in md
    # JSON-safe — confidence float 格式化对
    assert "0.95" in md
