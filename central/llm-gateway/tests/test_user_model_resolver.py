"""BL-RESOLVER-SOURCE-FIX (5/17): user_model_resolver 实盘 bug 防回归.

# 背景

老版本 get_user_last_session_model 用 `WHERE source = ?` 匹配 user_email, 但
Companion session_write.rs:127 写的是字面量 `source='companion'`. 这 SQL 永远
match 不上 → memory_distill 7th use case 实际没生效 (一直 skip "拿不到 user
最近 session model").

# 测试覆盖

  - 真 hermes-shape state.db + Companion 写 source='companion' → 返 最新 model
  - DB 不存在 → None
  - 表空 → None
  - 所有 session model 都 NULL → None
  - user_email 传不传都不影响查询结果 (仅 log 上下文)
  - 跟 identity.rs 同模式: ORDER BY started_at DESC, 取最新
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from catfish_gateway import user_model_resolver
from catfish_gateway.user_model_resolver import (
    get_session_model,
    get_user_last_session_model,
)


def _make_state_db(path: Path) -> sqlite3.Connection:
    """造 hermes-shape state.db (跟 Companion session_write.rs 同 schema)."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT,
            model TEXT,
            started_at REAL,
            title TEXT
        );
        """
    )
    return conn


# ── BL-RESOLVER-SOURCE-FIX 主验证 ────────────────────


def test_companion_source_literal_still_finds_model(tmp_path, monkeypatch):
    """Companion 写 source='companion' (固定字面量, 不是 user_email).
    resolver 不该依赖 source 字段匹配 email, 该照常返最新 model.

    这是 5/17 实盘 bug 的最直接回归测试.
    """
    db = tmp_path / "state.db"
    conn = _make_state_db(db)
    now = time.time()
    # 跟 Companion session_write.rs:127 行为完全一致: source 写 'companion'
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
        ("s_recent", "companion", "catfish-public-nvidia-nemotron", now, "test"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(user_model_resolver, "STATE_DB", db)
    # user_email 传值 — 老 bug 时这里会去 SQL 匹配, 找不到返 None
    out = get_user_last_session_model("chenhongbo@ffcs.cn")
    assert out == "catfish-public-nvidia-nemotron", (
        f"应返最近 session 的 model, 实际 {out!r}. "
        "BL-RESOLVER-SOURCE-FIX 回归了?"
    )


def test_returns_most_recent_by_started_at(tmp_path, monkeypatch):
    """多 session → 取 started_at 最大的 (跟 identity.rs:124 同模式)."""
    db = tmp_path / "state.db"
    conn = _make_state_db(db)
    now = time.time()
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
        ("s_old", "companion", "catfish-public-qwen-flash", now - 86400, "yesterday"),
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
        ("s_recent", "companion", "catfish-public-nvidia-nemotron", now, "now"),
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
        ("s_middle", "companion", "catfish-private-main", now - 3600, "1h ago"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(user_model_resolver, "STATE_DB", db)
    out = get_user_last_session_model("chenhongbo@ffcs.cn")
    assert out == "catfish-public-nvidia-nemotron"


def test_no_user_email_still_works(tmp_path, monkeypatch):
    """user_email 参数现在仅 log 用, 不传也该工作."""
    db = tmp_path / "state.db"
    conn = _make_state_db(db)
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
        ("s1", "companion", "catfish-private-main", time.time(), "x"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(user_model_resolver, "STATE_DB", db)
    assert get_user_last_session_model() == "catfish-private-main"
    assert get_user_last_session_model(None) == "catfish-private-main"
    assert get_user_last_session_model("") == "catfish-private-main"


def test_db_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(user_model_resolver, "STATE_DB", tmp_path / "nope.db")
    assert get_user_last_session_model("x@y.com") is None


def test_empty_table_returns_none(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    _make_state_db(db).close()
    monkeypatch.setattr(user_model_resolver, "STATE_DB", db)
    assert get_user_last_session_model("x@y.com") is None


def test_all_models_null_returns_none(tmp_path, monkeypatch):
    """老 session 没 model 字段 (5/15 之前 Companion 没写) → None, 不爆."""
    db = tmp_path / "state.db"
    conn = _make_state_db(db)
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
        ("s1", "companion", None, time.time(), "x"),
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
        ("s2", "companion", "", time.time() - 100, "y"),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(user_model_resolver, "STATE_DB", db)
    assert get_user_last_session_model("x@y.com") is None


def test_skips_null_picks_filled(tmp_path, monkeypatch):
    """混合: 最新一条 model=NULL, 早一条有值 → 返早那条 (NULL 过滤掉)."""
    db = tmp_path / "state.db"
    conn = _make_state_db(db)
    now = time.time()
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
        ("s_newest_null", "companion", None, now, "new"),
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
        ("s_older_valid", "companion", "catfish-private-main", now - 100, "old"),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(user_model_resolver, "STATE_DB", db)
    assert get_user_last_session_model("x@y.com") == "catfish-private-main"


# ── get_session_model (by session_id) — 改动前后行为一致 ─


def test_get_session_model_by_id(tmp_path, monkeypatch):
    """summarizer 用的路径: 按 session_id 精确查."""
    db = tmp_path / "state.db"
    conn = _make_state_db(db)
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
        ("specific_sid", "companion", "catfish-public-deepseek-flash", time.time(), "x"),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(user_model_resolver, "STATE_DB", db)

    assert get_session_model("specific_sid") == "catfish-public-deepseek-flash"
    assert get_session_model("does_not_exist") is None
