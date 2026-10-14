"""审批留痕: hermes post_approval_response 钩子 + P54 只读免批都记到 ~/.catfish/approval_audit.jsonl。"""
from __future__ import annotations

import contextvars
import json
import types

import approval_audit
import plugin_approval_ux as ux


def _lines(tmp_path):
    return [json.loads(l) for l in (tmp_path / "approval_audit.jsonl").read_text(encoding="utf-8").splitlines()]


def test_smart_decision_is_recorded_with_employee(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    cv = contextvars.ContextVar("u", default="")
    monkeypatch.setattr(approval_audit, "ctx_module", types.SimpleNamespace(CV_CF_USER=cv))
    cv.set("alice@x")
    approval_audit.post_approval_response_hook(
        command="rm -rf /tmp/x" + "y" * 500, description="recursive delete", pattern_key="rm_rf",
        session_key="s1", surface="smart", choice="smart_approve", decided_by="aux_llm",
        turn_id="t", tool_call_id="c")
    [e] = _lines(tmp_path)
    assert e["employee"] == "alice@x" and e["decided_by"] == "aux_llm" and e["choice"] == "smart_approve"
    assert len(e["command"]) <= 301, "命令截短, 不记完整脚本"


def test_human_answer_defaults_decided_by_human(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    approval_audit.post_approval_response_hook(command="x", choice="deny", surface="gateway")
    assert _lines(tmp_path)[0]["decided_by"] == "human"


def test_hook_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path / "file-not-dir"))
    (tmp_path / "file-not-dir").write_text("x")
    approval_audit.post_approval_response_hook(choice="once")  # 写不了也不抛


def test_p54_readonly_auto_approve_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    monkeypatch.setattr(ux, "audit", approval_audit)
    analyzer = types.SimpleNamespace(analyze=lambda code: types.SimpleNamespace(read_only=True),
                                     describe=lambda v: "读 3 个 .md 文件")
    guard = ux.wrap_execute_code_guard(lambda *a, **k: {"approved": False}, analyzer)
    assert guard("print(open('a.md').read())", "local")["approved"] is True
    [e] = _lines(tmp_path)
    assert e["decided_by"] == "readonly_rule" and e["choice"] == "readonly_auto"
    assert e["description"] == "读 3 个 .md 文件"
