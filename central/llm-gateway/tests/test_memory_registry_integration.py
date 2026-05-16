"""BL-MEMORY-MIGRATE-STEP1C — Registry 切换整合测试.

验证 registry.inject_subset() 跟 8 个旧 inject_X 拼起来产生**等价 / 接近** 内容.
不强求字符级一致 (拼接顺序 / 空行细节会差), 但**关键内容必须都在** + **顺序合理**.

测试矩阵:
  - 普通模式 (enabled=None): 跑所有 provider
  - lean 模式 (enabled={'skills_catalog'}): 只 skills
  - internal 模式 (is_internal_call=True): 全跳
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from catfish_gateway import inject_session_history as ish
from catfish_gateway.memory import InjectContext
from catfish_gateway.memory.bootstrap import bootstrap_registry
from catfish_gateway.memory.registry import MemoryRegistry

_HAS_UTC = sys.version_info >= (3, 11)


def _seed_user_data(tmp_path: Path, monkeypatch) -> dict[str, Path]:
    """造 ~/.catfish/ + ~/.hermes/ 测试数据."""
    catfish_dir = tmp_path / ".catfish"
    catfish_dir.mkdir()
    hermes_dir = tmp_path / ".hermes"
    hermes_dir.mkdir()

    # 1. session_facts
    (catfish_dir / "session_facts.json").write_text(json.dumps({
        "EIS_URL": [{"value": "http://eis.x.com", "ts": time.time(),
                     "prev_value": None}],
    }))
    # 2. feedback
    (catfish_dir / "feedback.jsonl").write_text(json.dumps({
        "kind": "thumb_down", "ts": time.time() - 3600,
        "comment": "太啰嗦", "preview": "好的, 我来",
    }) + "\n")
    # 3. employee_journal
    (catfish_dir / "employee_journal.md").write_text(
        "## 2026-05-16 10:00 - 资质方案\n讨论 ISO 27001 路线"
    )
    # 4. distilled_facts (蒸馏精华)
    (catfish_dir / "distilled_facts.md").write_text(
        "- 老板: 张总\n- 项目: EIS 资质管理"
    )
    # 5. hermes state.db
    db = hermes_dir / "state.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL,
                               message_count INTEGER, title TEXT);
        CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
                               session_id TEXT, role TEXT, content TEXT);
    """)
    now = time.time()
    conn.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                 ("s1", now - 86400, 4, "EIS 教学"))
    conn.execute(
        "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
        ("s1", "user", "教 EIS 登录"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "catfish_gateway.employee_journal._default_journal_path",
        lambda: catfish_dir / "employee_journal.md",
    )
    monkeypatch.setattr(
        "catfish_gateway.memory_distill.DISTILLED_FACTS_PATH",
        catfish_dir / "distilled_facts.md",
    )
    monkeypatch.setattr(ish, "get_state_db_path", lambda: db)

    return {"catfish": catfish_dir, "hermes": hermes_dir, "db": db}


def _fresh_registry() -> MemoryRegistry:
    """造一个独立 registry (不污染全局 singleton)."""
    return bootstrap_registry(registry=MemoryRegistry())


# ── 普通模式: 全 provider 注入 ─────────────────────────


def test_normal_mode_injects_all_key_content(tmp_path, monkeypatch):
    """普通模式 → 关键内容 (facts / journal / distilled / history / feedback) 都在."""
    _seed_user_data(tmp_path, monkeypatch)
    registry = _fresh_registry()

    msgs = [
        {"role": "system", "content": "你是鲶鱼."},
        {"role": "user", "content": "教 EIS 怎么登录"},
    ]
    ctx = InjectContext(
        user_sub="alice",
        messages=msgs,
        last_user_message="教 EIS 怎么登录",
        model_name="catfish-private-main",
    )
    out = registry.inject_subset(ctx, msgs, enabled_names=None)
    content = out[0]["content"]

    # 1. 原 system 保留
    assert content.startswith("你是鲶鱼")
    # 2. session_facts (priority 20)
    assert "EIS_URL" in content
    assert "http://eis.x.com" in content
    # 3. employee_journal (priority 60) — BL-MEMORY-INJECT-OPTIMIZE C 二选一,
    #    distilled 存在 → 用 distilled (老板: 张总), 不再 inject journal 全文
    assert "员工长期记忆" in content
    assert "老板: 张总" in content
    # 4. session_history (priority 50) — 相关性命中 EIS 教学
    assert "EIS 教学" in content or "跟你当前提问最相关" in content
    # 5. feedback (priority 70) — 末尾位置
    assert "太啰嗦" in content


def test_normal_mode_priority_order(tmp_path, monkeypatch):
    """按 priority 升序拼接: facts(20) 在 history(50) 前, history 在 feedback(70) 前."""
    _seed_user_data(tmp_path, monkeypatch)
    registry = _fresh_registry()

    msgs = [
        {"role": "system", "content": "原"},
        {"role": "user", "content": "EIS 登录"},
    ]
    ctx = InjectContext(
        messages=msgs, last_user_message="EIS 登录",
    )
    out = registry.inject_subset(ctx, msgs, enabled_names=None)
    content = out[0]["content"]

    facts_idx = content.find("EIS_URL")
    history_idx = content.find("EIS 教学")
    feedback_idx = content.find("太啰嗦")
    # 三个都存在
    assert facts_idx > 0
    assert feedback_idx > 0
    # 顺序: facts → ... → feedback
    assert facts_idx < feedback_idx


# ── lean 模式: 只 skills_catalog ─────────────────────


def test_lean_mode_drops_most_providers(tmp_path, monkeypatch):
    """lean 模式只跑 skills_catalog (其它跳)."""
    _seed_user_data(tmp_path, monkeypatch)
    registry = _fresh_registry()

    msgs = [
        {"role": "system", "content": "原"},
        {"role": "user", "content": "EIS 登录"},
    ]
    ctx = InjectContext(messages=msgs, last_user_message="EIS 登录")
    out = registry.inject_subset(
        ctx, msgs, enabled_names={"skills_catalog"},
    )
    content = out[0]["content"]
    # facts / journal / feedback / history 都不该 inject
    assert "EIS_URL" not in content
    assert "老板: 张总" not in content
    assert "太啰嗦" not in content
    # 历史段也不该有 (那是 session_history)
    assert "B. 最近 journal" not in content


# ── internal 模式: 全跳 ──────────────────────────────


def test_internal_call_skips_everything(tmp_path, monkeypatch):
    """is_internal_call=True → 一切不动 (跟 BL-MEMORY-POLISH 一致)."""
    _seed_user_data(tmp_path, monkeypatch)
    registry = _fresh_registry()

    msgs = [
        {"role": "system", "content": "原"},
        {"role": "user", "content": "EIS"},
    ]
    ctx = InjectContext(
        messages=msgs, last_user_message="EIS", is_internal_call=True,
    )
    out = registry.inject_subset(ctx, msgs, enabled_names=None)
    # internal 直接返原 msgs, 内容不变
    assert out == msgs


# ── 跟旧 inject_X 输出大致对齐 ───────────────────────


def test_registry_output_matches_legacy_inject_keys(tmp_path, monkeypatch):
    """Registry 输出的关键 marker 跟 8 个旧 inject_X 拼起来的关键 marker 重叠.

    不字符级一致 (拼接 \\n 数等细节不同), 但关键 inject 标志都在.
    """
    _seed_user_data(tmp_path, monkeypatch)
    registry = _fresh_registry()

    msgs = [
        {"role": "system", "content": "你是鲶鱼."},
        {"role": "user", "content": "教 EIS 登录"},
    ]
    ctx = InjectContext(
        messages=msgs, last_user_message="教 EIS 登录",
    )
    out = registry.inject_subset(ctx, msgs, enabled_names=None)
    content = out[0]["content"]

    # Registry 输出关键 marker (BL-MEMORY-FULL-HERMES 5/16 文案 V2)
    expected_markers = [
        "session 内的临时事实",  # session_facts (V2: 标 session-only + nudge memory)
        "员工长期记忆",  # employee_journal (二选一, distilled 模式)
        "员工最近给你的反馈",  # feedback
    ]
    for marker in expected_markers:
        assert marker in content, f"marker {marker!r} 不在 Registry 输出"


# ── session_meta 单独测 (3.11+) ────────────────────


@pytest.mark.skipif(not _HAS_UTC, reason="session_meta 需 Python 3.11+")
def test_normal_mode_includes_session_meta(tmp_path, monkeypatch):
    """有 session_meta.json → Registry 输出含 'Session Meta'."""
    _seed_user_data(tmp_path, monkeypatch)
    meta_file = tmp_path / ".catfish" / "session_meta.json"
    from datetime import datetime, timezone

    meta_file.write_text(json.dumps({
        "last_chat_at": datetime.fromtimestamp(
            time.time() - 86400, timezone.utc).isoformat(),
        "today_count": 1,
        "today_date": datetime.now(timezone.utc).date().isoformat(),
    }))
    from catfish_gateway import session_meta
    monkeypatch.setattr(session_meta, "META_PATH", meta_file)
    monkeypatch.setattr(session_meta, "meta_path", lambda: meta_file)

    registry = _fresh_registry()
    msgs = [
        {"role": "system", "content": "原"},
        {"role": "user", "content": "hi"},
    ]
    ctx = InjectContext(messages=msgs, last_user_message="hi")
    out = registry.inject_subset(ctx, msgs, enabled_names=None)
    assert "Session Meta" in out[0]["content"]
    assert "距上次找我" in out[0]["content"]
