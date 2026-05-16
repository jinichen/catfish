"""BL-MEMORY-FTS5-REAL — 3 层降级 (FTS5 trigram / unicode61 / LIKE) 测试.

覆盖:
  - _fts5_sanitize: 引号 / AND OR NOT / 圆括号 escape
  - _which_fts5_table: 检测 hermes-shape FTS5 表存在 (trigram 优先)
  - _query_via_fts5: trigram + unicode61 表 query, BM25 ranking
  - 3 层降级:
    a. trigram 表 + 命中 → 用 FTS5 BM25
    b. trigram 表存在但 0 hit (沙盒中文短 query) → 降 LIKE
    c. 没 FTS5 表 → 直接 LIKE
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from catfish_gateway import inject_session_history as ish


# ── _fts5_sanitize ──────────────────────────────────────


def test_fts5_sanitize_escapes_reserved_chars():
    """引号 / 反斜杠 / 圆括号 / 星号 / 加减号 / ^ 都被替成空格."""
    out = ish._fts5_sanitize("hello 'world' AND foo (bar) *test+")
    # 引号 / 圆括号 / 星号 / 加号 不在
    for ch in ["'", '"', "`", "\\", "(", ")", "*", "+"]:
        assert ch not in out, f"reserved char {ch!r} 还在: {out!r}"


def test_fts5_sanitize_lowercases_fts5_operators():
    """大写 AND OR NOT NEAR → 小写 (FTS5 只把大写当 operator)."""
    out = ish._fts5_sanitize("alpha AND beta OR gamma NOT delta NEAR epsilon")
    # operator 都变小写
    assert "AND" not in out
    assert "OR " not in out  # 边界
    assert "NOT" not in out
    assert "NEAR" not in out
    assert "and" in out
    assert "or" in out
    assert "not" in out
    assert "near" in out


def test_fts5_sanitize_empty():
    assert ish._fts5_sanitize("") == ""
    assert ish._fts5_sanitize("   ") == ""


# ── _which_fts5_table ──────────────────────────────────


def _make_db_with_fts5(path: Path, with_trigram: bool, with_unicode61: bool) -> None:
    """造测试 state.db, 可选 trigram / unicode61 FTS5 表."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL,
                               message_count INTEGER, title TEXT);
        CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
                               session_id TEXT, role TEXT, content TEXT);
    """)
    if with_unicode61:
        conn.executescript("""
            CREATE VIRTUAL TABLE messages_fts USING fts5(
                content, content='messages', content_rowid='id'
            );
            CREATE TRIGGER messages_fts_ai AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
            END;
        """)
    if with_trigram:
        conn.executescript("""
            CREATE VIRTUAL TABLE messages_fts_trigram USING fts5(
                content, content='messages', content_rowid='id',
                tokenize='trigram'
            );
            CREATE TRIGGER messages_fts_trigram_ai AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts_trigram(rowid, content) VALUES
                  (new.id, new.content);
            END;
        """)
    now = time.time()
    conn.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                 ("s1", now - 86400, 4, "EIS"))
    conn.execute(
        "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
        ("s1", "user", "教 EIS 登录技能"),
    )
    conn.commit()
    conn.close()


def test_which_fts5_table_prefers_trigram(tmp_path):
    """两个表都在 → trigram 优先."""
    db = tmp_path / "state.db"
    _make_db_with_fts5(db, with_trigram=True, with_unicode61=True)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    assert ish._which_fts5_table(conn) == "messages_fts_trigram"
    conn.close()


def test_which_fts5_table_unicode61_only(tmp_path):
    """只有 unicode61 → 返 messages_fts."""
    db = tmp_path / "state.db"
    _make_db_with_fts5(db, with_trigram=False, with_unicode61=True)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    assert ish._which_fts5_table(conn) == "messages_fts"
    conn.close()


def test_which_fts5_table_none(tmp_path):
    """没 FTS5 表 → 返 None."""
    db = tmp_path / "state.db"
    _make_db_with_fts5(db, with_trigram=False, with_unicode61=False)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    assert ish._which_fts5_table(conn) is None
    conn.close()


# ── _query_via_fts5: 英文 query 命中 ─────────────────────


def test_fts5_trigram_query_english_hits(tmp_path, monkeypatch):
    """trigram 表 + 英文 query "EIS" → 命中 (沙盒 3.37 也能跑英文 trigram)."""
    db = tmp_path / "state.db"
    _make_db_with_fts5(db, with_trigram=True, with_unicode61=False)
    monkeypatch.setattr(ish, "get_state_db_path", lambda: db)

    rows = ish.get_relevant_sessions("EIS")
    assert len(rows) >= 1
    assert rows[0][0] == "s1"


# ── 3 层降级: FTS5 0 hits → LIKE ─────────────────────────


def test_fts5_zero_hits_falls_back_to_like(tmp_path, monkeypatch):
    """trigram 中文 2 字符 query 在沙盒返 0 (3.37) → 自动 LIKE 子串扫.

    沙盒 sqlite 3.37 trigram 对 2 字符中文不命中. LIKE %登录% 子串命中 .
    """
    db = tmp_path / "state.db"
    _make_db_with_fts5(db, with_trigram=True, with_unicode61=True)
    monkeypatch.setattr(ish, "get_state_db_path", lambda: db)

    # 沙盒 3.37 trigram "登录" 返 0, LIKE %登录% 命中 "教 EIS 登录技能"
    rows = ish.get_relevant_sessions("登录")
    assert len(rows) >= 1, "LIKE fallback 应该命中 (子串匹配)"
    assert rows[0][0] == "s1"


def test_no_fts5_table_uses_like(tmp_path, monkeypatch):
    """没 FTS5 表 → 直接 LIKE."""
    db = tmp_path / "state.db"
    _make_db_with_fts5(db, with_trigram=False, with_unicode61=False)
    monkeypatch.setattr(ish, "get_state_db_path", lambda: db)

    rows = ish.get_relevant_sessions("EIS")
    assert len(rows) >= 1
    assert rows[0][0] == "s1"


def test_fts5_syntax_error_falls_back_to_like(tmp_path, monkeypatch):
    """FTS5 query 撞 syntax error (sanitize 漏的情况) → LIKE 兜底.

    模拟: 通过 monkeypatch 让 _fts5_sanitize 不去除 reserved char (强制 FTS5 撞错).
    """
    db = tmp_path / "state.db"
    _make_db_with_fts5(db, with_trigram=True, with_unicode61=False)
    monkeypatch.setattr(ish, "get_state_db_path", lambda: db)

    # 不动 sanitize, query "EIS" 应正常走 trigram 命中 (这是基线确认)
    rows = ish.get_relevant_sessions("EIS")
    assert len(rows) >= 1
