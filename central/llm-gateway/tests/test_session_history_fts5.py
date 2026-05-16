"""BL-MEMORY-FTS5-RECALL — inject_session_history LIKE-based 相关性召回测试.

覆盖:
  - _extract_query_tokens: 标点 / 停用词 / 单字符 / 重复 / 截长
  - _extract_user_query: str content / multimodal list content / 空
  - get_relevant_sessions: 命中/未命中 / 多 token AND / fallback
  - inject_session_history 主流程: 相关召回 vs 时间 fallback
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from catfish_gateway import inject_session_history as ish


# ── _extract_query_tokens ─────────────────────────────────


def test_extract_tokens_strips_quotes_and_punctuation():
    """引号 / 中英文标点都被换成空格."""
    tokens = ish._extract_query_tokens('请帮我"写周报"，关于资质管理项目。')
    # 关键词被切出来; 停用词"请"/"帮我" 等被过滤
    assert "资质管理项目" in tokens or any("资质" in t for t in tokens)


def test_extract_tokens_drops_stopwords():
    """常见中英文停用词不当 token."""
    tokens = ish._extract_query_tokens("我 the 你 of 写 资质")
    # 资质 留下, 其它停用词都丢
    assert "资质" in tokens
    for stop in ["我", "the", "你", "of", "写"]:
        assert stop not in tokens


def test_extract_tokens_dedupes_and_caps():
    """重复 token 去重 + 最多 6 个."""
    tokens = ish._extract_query_tokens(
        "alpha alpha beta gamma delta epsilon zeta eta theta iota"
    )
    assert len(tokens) <= 6
    assert len(set(tokens)) == len(tokens)  # 全唯一


def test_extract_tokens_drops_single_char():
    """1 字符 token (中英文都) noise 多, skip."""
    tokens = ish._extract_query_tokens("a b c 长 词 双字 三字符")
    for t in tokens:
        assert len(t) >= 2


def test_extract_tokens_empty_returns_empty():
    assert ish._extract_query_tokens("") == []
    assert ish._extract_query_tokens("    ") == []
    assert ish._extract_query_tokens("...,,") == []


def test_extract_tokens_truncates_long_query():
    """超 QUERY_MAX_CHARS 的字符串被截, 不崩."""
    long_query = "资质" * 1000
    tokens = ish._extract_query_tokens(long_query)
    assert isinstance(tokens, list)


def test_extract_tokens_drops_short_digit_tokens():
    """BL-MEMORY-POLISH: 纯数字 < 4 字符滤掉 (日期/时间 noise).

    例 '2026-05-12 09:26' jieba 切出 '2026 05 12 09 26', 短数字 skip,
    只留长数字 '2026'.
    """
    tokens = ish._extract_query_tokens("2026-05-12 09:26 资质 session 任务")
    # 短数字 05 / 12 / 09 / 26 skip
    for short_digit in ["05", "12", "09", "26"]:
        assert short_digit not in tokens, f"短数字 {short_digit!r} 不该留"
    # 长数字 (4+ 字符) 留下 — 年份等有定位价值
    # 实际内容 token 该有 (jieba 切"资质" 或 "任务")
    assert any(not t.isdigit() for t in tokens), "应至少有一个非数字 token"


# ── _extract_user_query ──────────────────────────────────


def test_extract_query_from_str_content():
    msgs = [
        {"role": "system", "content": "ignored"},
        {"role": "user", "content": "上次资质方案"},
        {"role": "assistant", "content": "OK"},
        {"role": "user", "content": "再写一遍"},  # 取最新的
    ]
    assert ish._extract_user_query(msgs) == "再写一遍"


def test_extract_query_from_multimodal_list_content():
    """user message content 是 list (text + image_url) 也兼容."""
    msgs = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "看这张图"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
            {"type": "text", "text": "什么意思"},
        ],
    }]
    out = ish._extract_user_query(msgs)
    assert "看这张图" in out
    assert "什么意思" in out


def test_extract_query_returns_empty_when_no_user():
    msgs = [
        {"role": "system", "content": "x"},
        {"role": "assistant", "content": "y"},
    ]
    assert ish._extract_user_query(msgs) == ""


def test_extract_query_returns_empty_when_empty_messages():
    assert ish._extract_user_query([]) == ""


# ── get_relevant_sessions FTS5 集成 ──────────────────────


def _create_test_db(path: Path) -> None:
    """造个 hermes-shape state.db 用于测试 (sessions + messages 表).

    LIKE-based 召回不需要 FTS5 表, 跟生产 hermes state.db 用一样 schema (LIKE
    扫真实表). 加 FTS5 表也没坏处, 但维持最小测试 fixture.
    """
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            started_at REAL,
            message_count INTEGER,
            title TEXT
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,
            content TEXT
        );
    """)

    import time
    now = time.time()

    # 3 个 session, 主题不同
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?)",
        ("s1", now - 86400 * 3, 4, "资质增补方案"),
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?)",
        ("s2", now - 86400 * 1, 6, "EIS 登录技能"),
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?)",
        ("s3", now - 3600, 2, "周报草稿"),
    )

    # 给 session 加 user message (FTS5 自动 index)
    conn.execute(
        "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
        ("s1", "user", "讨论资质增补 4 月方案"),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
        ("s2", "user", "教 EIS 登录技能"),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
        ("s3", "user", "写周报"),
    )
    conn.commit()
    conn.close()


def test_get_relevant_sessions_hits_correct_session(tmp_path, monkeypatch):
    """query 'EIS' 应该命中 s2 (EIS 登录技能 session), 不是 s1 / s3."""
    db = tmp_path / "state.db"
    _create_test_db(db)
    monkeypatch.setattr(ish, "get_state_db_path", lambda: db)

    rows = ish.get_relevant_sessions("EIS 登录")
    # s2 必须命中
    assert len(rows) >= 1
    assert rows[0][0] == "s2"


def test_get_relevant_sessions_chinese_substring_hits(tmp_path, monkeypatch):
    """中文子串"资质" 应命中 s1 (LIKE 不受 FTS5 中文分词 bug 影响)."""
    db = tmp_path / "state.db"
    _create_test_db(db)
    monkeypatch.setattr(ish, "get_state_db_path", lambda: db)

    rows = ish.get_relevant_sessions("资质")
    assert any(r[0] == "s1" for r in rows)


def test_get_relevant_sessions_multi_token_and_logic(tmp_path, monkeypatch):
    """多 token 走 AND, 只命中**所有 token 都包含**的 session."""
    db = tmp_path / "state.db"
    _create_test_db(db)
    monkeypatch.setattr(ish, "get_state_db_path", lambda: db)

    # "EIS" + "技能" 两个都在 s2 内 → 命中
    rows = ish.get_relevant_sessions("EIS 技能")
    assert any(r[0] == "s2" for r in rows)

    # "EIS" + "资质" 没 session 两个都包含 → 0 命中
    rows2 = ish.get_relevant_sessions("EIS 资质")
    assert rows2 == []


def test_get_relevant_sessions_no_db_returns_empty(monkeypatch):
    """state.db 不存在 → 返空."""
    monkeypatch.setattr(ish, "get_state_db_path", lambda: None)
    rows = ish.get_relevant_sessions("EIS")
    assert rows == []


def test_get_relevant_sessions_empty_query_returns_empty(tmp_path, monkeypatch):
    """sanitize 后无 token (全停用词/全标点) → 返空 (不空跑 LIKE)."""
    db = tmp_path / "state.db"
    _create_test_db(db)
    monkeypatch.setattr(ish, "get_state_db_path", lambda: db)

    # 全标点 / 全停用词 → 抽不出 token
    assert ish.get_relevant_sessions("。，、") == []
    assert ish.get_relevant_sessions("我 你 的 了") == []


# ── inject_session_history 主流程 ────────────────────────


def test_inject_uses_recall_when_query_hits(tmp_path, monkeypatch):
    """user 问"EIS" 时, 注入"相关 session", 标题用 relevant_mode 文案."""
    db = tmp_path / "state.db"
    _create_test_db(db)
    monkeypatch.setattr(ish, "get_state_db_path", lambda: db)

    msgs = [
        {"role": "system", "content": "原 system"},
        {"role": "user", "content": "上次 EIS 登录怎么做的"},
    ]
    out = ish.inject_session_history(msgs)
    content = out[0]["content"]
    # relevant_mode 标题
    assert "跟你当前提问最相关的 session" in content
    # 命中 s2 (EIS 登录)
    assert "EIS 登录技能" in content


def test_inject_fallback_to_time_when_no_hit(tmp_path, monkeypatch):
    """user 问跟所有 session 都不相关的话题 → fallback 时间窗口."""
    db = tmp_path / "state.db"
    _create_test_db(db)
    monkeypatch.setattr(ish, "get_state_db_path", lambda: db)

    msgs = [
        {"role": "system", "content": "原"},
        {"role": "user", "content": "帮我做完全不相关的 zxcvbnm qwerty"},
    ]
    out = ish.inject_session_history(msgs)
    content = out[0]["content"]
    # fallback 走时间窗口, 用老标题
    assert "员工最近 7 天 session 历史" in content
    # 时间倒序, s3 最新该出现
    assert "周报草稿" in content


def test_inject_idempotent(tmp_path, monkeypatch):
    """重复 inject → 第二次幂等不重复加."""
    db = tmp_path / "state.db"
    _create_test_db(db)
    monkeypatch.setattr(ish, "get_state_db_path", lambda: db)

    msgs = [
        {"role": "system", "content": "原"},
        {"role": "user", "content": "EIS"},
    ]
    once = ish.inject_session_history(msgs)
    twice = ish.inject_session_history(once)
    assert once[0]["content"] == twice[0]["content"]


def test_inject_no_db_returns_messages_unchanged(monkeypatch):
    """state.db 不存在 → 完全不动 messages."""
    monkeypatch.setattr(ish, "get_state_db_path", lambda: None)
    msgs = [
        {"role": "system", "content": "原"},
        {"role": "user", "content": "EIS"},
    ]
    assert ish.inject_session_history(msgs) == msgs
