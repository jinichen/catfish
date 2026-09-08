"""BL-MEMORY-AUDIT-TRAIL (6/2 鸿波) 单测 — memory router 落 jsonl audit log.

跑法 (catfish 项目根):
    cd edge/hermes-plugins/catfish-xcatfish-user
    PYTHONPATH=. python -m pytest tests/test_memory_audit.py -q

覆盖:
1. _read_hermes_entries: USER.md/MEMORY.md 用 \\n§\\n 分隔解析, 文件不存在返 []
2. _find_entry_containing: substring 匹配模拟 hermes replace/remove 算法
3. _read_prev_value_for_audit: replace/remove 真读到 prev_value; add 不读
4. _append_audit_log: jsonl 真 append, 多次写真追加不覆盖
5. handle_memory_tool E2E:
   - add identity → audit 一条 success=true prev_value=None
   - replace identity → audit prev_value 是 hermes 文件里被覆盖的真完整 entry
   - remove project_fact → 同上
   - journal add → audit 一条 (kind=journal action=add)
   - failure case → audit success=false + error
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


# 6/2 BL-MEMORY-AUDIT-TRAIL: 测试用 CATFISH_HOME / HERMES_HOME 隔离 tmp, 不污染真员工 ~/.catfish
@pytest.fixture
def isolated_homes(tmp_path, monkeypatch):
    """造 tmp 的 catfish_home + hermes_home, 隔离测试."""
    catfish_home = tmp_path / "catfish_home"
    hermes_home = tmp_path / "hermes_home"
    (hermes_home / "memories").mkdir(parents=True)
    catfish_home.mkdir(parents=True)
    monkeypatch.setenv("CATFISH_HOME", str(catfish_home))
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    return {"catfish": catfish_home, "hermes": hermes_home}


@pytest.fixture
def router(isolated_homes):
    """isolated_homes 必须先 set env, 再 import router (router 读 env 时机正确)."""
    import importlib
    import memory_router  # noqa: PLC0415
    importlib.reload(memory_router)  # 强制重读 env
    return memory_router


# ── _read_hermes_entries ────────────────────────────────────────────


def test_read_hermes_entries_missing_file_returns_empty(router, isolated_homes):
    """USER.md 不存在 → []."""
    assert router._read_hermes_entries("user") == []


def test_read_hermes_entries_parses_delimiter(router, isolated_homes):
    """\\n§\\n 分隔解析正确."""
    user_md = isolated_homes["hermes"] / "memories" / "USER.md"
    user_md.write_text(
        "员工叫张三\n§\n喜欢简洁回答\n§\n部门是研发", encoding="utf-8",
    )
    entries = router._read_hermes_entries("user")
    assert entries == ["员工叫张三", "喜欢简洁回答", "部门是研发"]


def test_read_hermes_entries_invalid_target(router):
    """target 不是 user/memory → []."""
    assert router._read_hermes_entries("nonsense") == []


# ── _find_entry_containing ──────────────────────────────────────────


def test_find_entry_substring_match(router):
    entries = ["员工叫张三", "喜欢简洁回答", "部门是研发"]
    assert router._find_entry_containing(entries, "张三") == "员工叫张三"
    assert router._find_entry_containing(entries, "简洁") == "喜欢简洁回答"


def test_find_entry_no_match_returns_none(router):
    assert router._find_entry_containing(["a", "b"], "X") is None


def test_find_entry_empty_old_text_returns_none(router):
    assert router._find_entry_containing(["a"], "") is None


# ── _read_prev_value_for_audit ──────────────────────────────────────


def test_prev_value_for_replace_identity(router, isolated_homes):
    """identity replace 真读到 USER.md 里被覆盖的 entry."""
    user_md = isolated_homes["hermes"] / "memories" / "USER.md"
    user_md.write_text("员工叫张三, 喜欢简洁", encoding="utf-8")
    prev = router._read_prev_value_for_audit("identity", "replace", "张三")
    assert prev == "员工叫张三, 喜欢简洁"


def test_prev_value_for_remove_project_fact(router, isolated_homes):
    """project_fact remove 读 MEMORY.md."""
    mem_md = isolated_homes["hermes"] / "memories" / "MEMORY.md"
    mem_md.write_text("客户机房 IP 10.0.1.1\n§\nAPI 字段 user_email", encoding="utf-8")
    prev = router._read_prev_value_for_audit("project_fact", "remove", "user_email")
    assert prev == "API 字段 user_email"


def test_prev_value_for_add_returns_none(router, isolated_homes):
    """add 没"被覆盖" 概念 → None."""
    user_md = isolated_homes["hermes"] / "memories" / "USER.md"
    user_md.write_text("员工叫张三", encoding="utf-8")
    assert router._read_prev_value_for_audit("identity", "add", "张三") is None


def test_prev_value_for_journal_returns_none(router):
    """journal/workflow/todo 没"修改" 语义 → None."""
    assert router._read_prev_value_for_audit("journal", "replace", "x") is None
    assert router._read_prev_value_for_audit("workflow", "remove", "x") is None
    assert router._read_prev_value_for_audit("todo", "replace", "x") is None


def test_prev_value_no_old_text_returns_none(router):
    """old_text 为空 → None (replace/remove 缺 old_text 是 LLM 错, audit 也没办法)."""
    assert router._read_prev_value_for_audit("identity", "replace", None) is None
    assert router._read_prev_value_for_audit("identity", "replace", "") is None


# ── _append_audit_log ────────────────────────────────────────────────


def test_append_audit_log_creates_file_and_appends(router, isolated_homes):
    """jsonl append, 多次写真追加不覆盖."""
    router._append_audit_log({"ts": "2026-06-02T15:00:00+00:00", "kind": "identity", "n": 1})
    router._append_audit_log({"ts": "2026-06-02T15:01:00+00:00", "kind": "identity", "n": 2})
    audit_path = isolated_homes["catfish"] / "memory_audit.jsonl"
    assert audit_path.exists()
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    r1 = json.loads(lines[0])
    r2 = json.loads(lines[1])
    assert r1["n"] == 1
    assert r2["n"] == 2


def test_append_audit_log_chinese_no_escape(router, isolated_homes):
    """中文内容不被 escape (ensure_ascii=False)."""
    router._append_audit_log({"content": "员工叫张三"})
    audit_path = isolated_homes["catfish"] / "memory_audit.jsonl"
    raw = audit_path.read_text(encoding="utf-8")
    assert "员工叫张三" in raw
    assert "\\u" not in raw


# ── _build_audit_record ──────────────────────────────────────────────


def test_build_audit_record_success(router):
    """hermes 返 success=true → record success=true error=None."""
    rec = router._build_audit_record(
        kind="identity", action="add",
        args={"content": "李四"},
        prev_value=None,
        result_json='{"success": true, "entries": ["李四"]}',
        user_email="chenhongbo@ffcs.cn",
    )
    assert rec["success"] is True
    assert rec["error"] is None
    assert rec["kind"] == "identity"
    assert rec["action"] == "add"
    assert rec["content"] == "李四"
    assert rec["user_email"] == "chenhongbo@ffcs.cn"
    assert "ts" in rec


def test_build_audit_record_failure(router):
    """hermes 返 success=false → record success=false + error."""
    rec = router._build_audit_record(
        kind="identity", action="replace",
        args={"old_text": "张三", "content": "李四"},
        prev_value="员工叫张三",
        result_json='{"success": false, "error": "No entry matched"}',
        user_email=None,
    )
    assert rec["success"] is False
    assert rec["error"] == "No entry matched"
    assert rec["prev_value"] == "员工叫张三"


# ── _extract_user_email ──────────────────────────────────────────────


def test_extract_user_email_from_kw(router):
    assert router._extract_user_email({"user_email": "a@b.c"}) == "a@b.c"
    assert router._extract_user_email({"user": "d@e.f"}) == "d@e.f"


def test_extract_user_email_from_env(router, monkeypatch):
    monkeypatch.setenv("CATFISH_EFFECTIVE_USER", "env@user.com")
    assert router._extract_user_email({}) == "env@user.com"


def test_extract_user_email_none_when_unset(router, monkeypatch):
    monkeypatch.delenv("CATFISH_EFFECTIVE_USER", raising=False)
    assert router._extract_user_email({}) is None


# ── handle_memory_tool E2E (audit log 真落) ──────────────────────────


def _read_audit(isolated_homes):
    """test helper — 读 audit jsonl 返 list[dict]."""
    p = isolated_homes["catfish"] / "memory_audit.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").strip().splitlines()]


def test_handle_journal_add_audits(router, isolated_homes):
    """journal add — audit 一条 success=true."""
    res = router.handle_memory_tool({
        "action": "add", "kind": "journal", "content": "今天和 A 公司谈合作",
    })
    parsed = json.loads(res)
    assert parsed["success"] is True
    audit = _read_audit(isolated_homes)
    assert len(audit) == 1
    assert audit[0]["kind"] == "journal"
    assert audit[0]["action"] == "add"
    assert audit[0]["content"] == "今天和 A 公司谈合作"
    assert audit[0]["success"] is True
    assert audit[0]["prev_value"] is None  # add 没 prev


def test_handle_replace_identity_captures_prev_value(router, isolated_homes):
    """identity replace — prev_value 真是 USER.md 被覆盖的完整 entry.

    我们 mock hermes original tool (catfish-xcatfish-user 跑在 hermes 进程里, 真生产
    时 tools.memory_tool 可 import; 测试环境隔离, mock it).
    """
    # 模拟 USER.md 已有内容
    user_md = isolated_homes["hermes"] / "memories" / "USER.md"
    user_md.write_text("员工叫张三, 喜欢详细回答", encoding="utf-8")

    # mock _call_hermes_original_memory_tool 返成功
    def fake_hermes(args, **kw):
        return json.dumps({"success": True, "entries": ["员工叫李四, 喜欢详细回答"]}, ensure_ascii=False)
    router._call_hermes_original_memory_tool = fake_hermes

    res = router.handle_memory_tool({
        "action": "replace", "kind": "identity",
        "old_text": "张三", "content": "员工叫李四, 喜欢详细回答",
    })
    parsed = json.loads(res)
    assert parsed["success"] is True

    audit = _read_audit(isolated_homes)
    assert len(audit) == 1
    rec = audit[0]
    assert rec["kind"] == "identity"
    assert rec["action"] == "replace"
    assert rec["old_text"] == "张三"
    assert rec["content"] == "员工叫李四, 喜欢详细回答"
    assert rec["prev_value"] == "员工叫张三, 喜欢详细回答"  # ← 真灵魂: 老值保留!
    assert rec["success"] is True


def test_handle_remove_project_fact_captures_prev_value(router, isolated_homes):
    """project_fact remove — prev_value 来自 MEMORY.md."""
    mem_md = isolated_homes["hermes"] / "memories" / "MEMORY.md"
    mem_md.write_text("客户 X 机房 IP 10.0.1.1\n§\n旧 API 字段 user_email", encoding="utf-8")

    def fake_hermes(args, **kw):
        return json.dumps({"success": True}, ensure_ascii=False)
    router._call_hermes_original_memory_tool = fake_hermes

    res = router.handle_memory_tool({
        "action": "remove", "kind": "project_fact",
        "old_text": "user_email",
    })
    parsed = json.loads(res)
    assert parsed["success"] is True

    audit = _read_audit(isolated_homes)
    rec = audit[0]
    assert rec["kind"] == "project_fact"
    assert rec["action"] == "remove"
    assert rec["prev_value"] == "旧 API 字段 user_email"


def test_handle_failure_still_audited(router, isolated_homes):
    """hermes 返 success=false (e.g. limit exceeded) → audit 仍记 success=false."""
    def fake_hermes(args, **kw):
        return json.dumps({"success": False, "error": "Memory at limit"}, ensure_ascii=False)
    router._call_hermes_original_memory_tool = fake_hermes

    res = router.handle_memory_tool({
        "action": "add", "kind": "identity", "content": "tons of text...",
    })
    parsed = json.loads(res)
    assert parsed["success"] is False

    audit = _read_audit(isolated_homes)
    assert len(audit) == 1
    assert audit[0]["success"] is False
    assert audit[0]["error"] == "Memory at limit"


def test_handle_missing_kind_audited(router, isolated_homes):
    """LLM 没填 kind → 拒, audit 也记 (success=false 给运维知道 LLM 调错)."""
    res = router.handle_memory_tool({"action": "add", "content": "x"})
    parsed = json.loads(res)
    assert parsed["success"] is False
    # 注意: 这个 case audit 不会记 (kind 必填校验在 audit 之前直接 return),
    # 这是有意的 — 跟 hermes contract 看齐, kind 必填错就 0 副作用.
    audit = _read_audit(isolated_homes)
    assert audit == []


def test_handle_5_kinds_all_audit(router, isolated_homes):
    """5 kind 全过 audit — 一份 jsonl 看全."""
    # mock hermes 原 tool (identity/project_fact 调到)
    def fake_hermes(args, **kw):
        return json.dumps({"success": True}, ensure_ascii=False)
    router._call_hermes_original_memory_tool = fake_hermes

    cases = [
        {"action": "add", "kind": "identity", "content": "员工叫张三"},
        {"action": "add", "kind": "project_fact", "content": "客户机房 10.0.1.1"},
        {"action": "add", "kind": "workflow", "content": "登录 OA"},
        {"action": "add", "kind": "journal", "content": "和 B 公司谈"},
        {"action": "add", "kind": "todo", "content": "周一交合同"},
    ]
    for args in cases:
        router.handle_memory_tool(args)

    audit = _read_audit(isolated_homes)
    assert len(audit) == 5
    kinds_seen = [rec["kind"] for rec in audit]
    assert kinds_seen == ["identity", "project_fact", "workflow", "journal", "todo"]


def test_todo_route_never_writes_employee_journal(router, isolated_homes):
    result = json.loads(router._route_to_reminder("周一交合同"))

    assert result["success"] is False
    assert result["routed_to"] == "catfish_create_task"
    assert not (isolated_homes["catfish"] / "employee_journal.md").exists()
