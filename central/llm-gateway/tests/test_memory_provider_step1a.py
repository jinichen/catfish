"""BL-MEMORY-MIGRATE-STEP1A — 3 个新 Provider 测试.

覆盖 SessionMetaProvider / SessionFactsProvider / SessionHistoryProvider:
  - Protocol 校验
  - 数据空 → 返 None
  - 数据有 → 返字符串内容跟原 inject_X 一致 (切换零行为变化)
  - 异常 → 返 None 不抛

注: SessionMetaProvider 需要 Python 3.11+ (session_meta.py 用 datetime.UTC).
项目 pyproject 写 requires-python>=3.12, 沙盒 (3.10) 那部分 skip.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from catfish_gateway import inject_session_history as ish
from catfish_gateway.memory import InjectContext, MemoryProvider
from catfish_gateway.memory.providers.session_facts import SessionFactsProvider
from catfish_gateway.memory.providers.session_history import SessionHistoryProvider

_HAS_UTC = sys.version_info >= (3, 11)
if _HAS_UTC:
    from catfish_gateway import session_meta
    from catfish_gateway.memory.providers.session_meta import SessionMetaProvider


# ── SessionMetaProvider (需 Python 3.11+) ─────────────


@pytest.mark.skipif(not _HAS_UTC, reason="session_meta 用 datetime.UTC, 需 Python 3.11+")
def test_session_meta_provider_protocol():
    assert isinstance(SessionMetaProvider(), MemoryProvider)
    p = SessionMetaProvider()
    assert p.name == "session_meta"
    assert p.priority == 30
    assert p.budget_bytes == 500


@pytest.mark.skipif(not _HAS_UTC, reason="session_meta 用 datetime.UTC, 需 Python 3.11+")
def test_session_meta_provider_empty_returns_none(tmp_path, monkeypatch):
    """没 session_meta.json → None."""
    monkeypatch.setattr(session_meta, "META_PATH", tmp_path / "x.json")
    monkeypatch.setattr(session_meta, "meta_path", lambda: tmp_path / "x.json")
    assert SessionMetaProvider().prefetch(InjectContext()) is None


@pytest.mark.skipif(not _HAS_UTC, reason="session_meta 用 datetime.UTC, 需 Python 3.11+")
def test_session_meta_provider_with_data(tmp_path, monkeypatch):
    """有 session_meta.json → 返 'Session Meta' markdown."""
    meta_file = tmp_path / "session_meta.json"
    from datetime import datetime, timezone

    yesterday = datetime.now(timezone.utc).timestamp() - 86400
    meta_file.write_text(json.dumps({
        "last_chat_at": datetime.fromtimestamp(
            yesterday, timezone.utc).isoformat(),
        "today_count": 1,
        "today_date": datetime.now(timezone.utc).date().isoformat(),
    }))
    monkeypatch.setattr(session_meta, "META_PATH", meta_file)
    monkeypatch.setattr(session_meta, "meta_path", lambda: meta_file)

    out = SessionMetaProvider().prefetch(InjectContext())
    assert out is not None
    assert "Session Meta" in out
    assert "距上次找我" in out


# ── SessionFactsProvider ──────────────────────────────


def test_session_facts_provider_protocol():
    assert isinstance(SessionFactsProvider(), MemoryProvider)
    p = SessionFactsProvider()
    assert p.name == "session_facts"
    assert p.priority == 20
    # BL-MEMORY-INJECT-OPTIMIZE B (5/16 鸿波本机实盘 12:08): 4000 还触 budget,
    # 调到 6000 (员工真有 60+ fact 且 value 长).
    assert p.budget_bytes == 6000


def test_session_facts_provider_empty_returns_none(tmp_path, monkeypatch):
    """没 session_facts.json → None."""
    monkeypatch.setenv("HOME", str(tmp_path))
    out = SessionFactsProvider().prefetch(InjectContext())
    assert out is None


def test_session_facts_provider_with_data(tmp_path, monkeypatch):
    """有 facts → 返渲染过的 block, 含硬事实."""
    monkeypatch.setenv("HOME", str(tmp_path))
    cat_dir = tmp_path / ".catfish"
    cat_dir.mkdir()
    facts_file = cat_dir / "session_facts.json"
    facts_file.write_text(json.dumps({
        "EIS": [{"value": "http://eis.example.com", "ts": time.time(),
                 "prev_value": None}],
        "password": [{"value": "keychain://x", "ts": time.time(),
                      "prev_value": None}],
    }))

    out = SessionFactsProvider().prefetch(InjectContext())
    assert out is not None
    assert "EIS" in out
    assert "http://eis.example.com" in out
    assert "硬事实" in out  # 渲染 block 文案


def test_session_facts_provider_corrupt_json_returns_none(tmp_path, monkeypatch):
    """损坏的 json → 返 None 不抛."""
    monkeypatch.setenv("HOME", str(tmp_path))
    cat_dir = tmp_path / ".catfish"
    cat_dir.mkdir()
    (cat_dir / "session_facts.json").write_text("{not valid json")
    assert SessionFactsProvider().prefetch(InjectContext()) is None


# ── SessionHistoryProvider ────────────────────────────


def _make_state_db(path: Path) -> None:
    """造 hermes-shape state.db (跟 test_session_history_fts5.py 同)."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL,
                               message_count INTEGER, title TEXT);
        CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
                               session_id TEXT, role TEXT, content TEXT);
    """)
    now = time.time()
    conn.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                 ("s1", now - 86400, 4, "EIS 教学"))
    conn.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                 ("s2", now - 3600, 6, "资质方案"))
    conn.execute(
        "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
        ("s1", "user", "教 EIS 登录"),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
        ("s2", "user", "讨论资质方案"),
    )
    conn.commit()
    conn.close()


def test_session_history_provider_protocol():
    assert isinstance(SessionHistoryProvider(), MemoryProvider)
    p = SessionHistoryProvider()
    assert p.name == "session_history"
    assert p.priority == 50
    assert p.budget_bytes == 3000


def test_session_history_provider_uses_relevance_when_query_hits(
    tmp_path, monkeypatch,
):
    """user message 命中 → 用相关性召回 (跟原 inject 行为一致)."""
    db = tmp_path / "state.db"
    _make_state_db(db)
    monkeypatch.setattr(ish, "get_state_db_path", lambda: db)

    ctx = InjectContext(
        messages=[{"role": "user", "content": "上次 EIS 登录怎么做"}],
        last_user_message="上次 EIS 登录怎么做",
    )
    out = SessionHistoryProvider().prefetch(ctx)
    assert out is not None
    assert "EIS 教学" in out
    # 用相关性标题, 不是时间窗口
    assert "跟你当前提问最相关的 session" in out


def test_session_history_provider_falls_back_to_recent_when_no_hit(
    tmp_path, monkeypatch,
):
    """无相关命中 → fallback 时间窗口."""
    db = tmp_path / "state.db"
    _make_state_db(db)
    monkeypatch.setattr(ish, "get_state_db_path", lambda: db)

    ctx = InjectContext(
        messages=[{"role": "user", "content": "完全不相关的 zxcvbnm"}],
        last_user_message="完全不相关的 zxcvbnm",
    )
    out = SessionHistoryProvider().prefetch(ctx)
    assert out is not None
    # fallback 时间窗口 → 老标题
    assert "员工最近 7 天 session 历史" in out


def test_session_history_provider_no_db_returns_none(monkeypatch):
    monkeypatch.setattr(ish, "get_state_db_path", lambda: None)
    out = SessionHistoryProvider().prefetch(
        InjectContext(last_user_message="hi"),
    )
    assert out is None


# ── 切换零行为验证 (跟原 inject 输出一致) ─────────────


def test_session_facts_provider_output_matches_legacy_inject(tmp_path, monkeypatch):
    """SessionFactsProvider 输出跟原 inject_session_facts 渲染同样内容."""
    from catfish_gateway.session_facts import (
        read_session_facts,
        render_facts_block,
    )

    monkeypatch.setenv("HOME", str(tmp_path))
    cat_dir = tmp_path / ".catfish"
    cat_dir.mkdir()
    (cat_dir / "session_facts.json").write_text(json.dumps({
        "key1": [{"value": "v1", "ts": time.time(), "prev_value": None}],
    }))

    provider_output = SessionFactsProvider().prefetch(InjectContext())
    legacy_output = render_facts_block(read_session_facts())
    assert provider_output == legacy_output
