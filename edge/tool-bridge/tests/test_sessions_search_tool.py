"""BL-FIX-SESSION-SEARCH 测试 — catfish_search_sessions native tool.

跟 gateway sessions_browse 同源 (~/.hermes/state.db read-only sqlite),
但 tool-bridge 直读, 不走 gateway HTTP.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from catfish_tool_bridge import sessions_search


def _seed_db(home: Path, sessions: list[dict], messages: list[dict]) -> None:
    hermes = home / ".hermes"
    hermes.mkdir(parents=True, exist_ok=True)
    db = hermes / "state.db"
    conn = sqlite3.connect(str(db))
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
            content TEXT,
            created_at REAL
        );
    """)
    for s in sessions:
        conn.execute(
            "INSERT INTO sessions (id, started_at, message_count, title) VALUES (?, ?, ?, ?)",
            (s["id"], s["started_at"], s.get("message_count", 1), s.get("title", "")),
        )
    for m in messages:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (m["session_id"], m["role"], m["content"], m["created_at"]),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    yield tmp_path


# ── search_messages ──────────────────────────────────────


def test_search_no_db_returns_empty(fake_home: Path):
    assert sessions_search.search_messages("anything") == []


def test_search_empty_query(fake_home: Path):
    assert sessions_search.search_messages("") == []
    assert sessions_search.search_messages("   ") == []


def test_search_finds_match(fake_home: Path):
    now = time.time()
    _seed_db(
        fake_home,
        sessions=[{"id": "s1", "started_at": now, "title": "8 项资质"}],
        messages=[
            {"session_id": "s1", "role": "user", "content": "查 EIS 资质审核怎么搞", "created_at": now},
        ],
    )
    out = sessions_search.search_messages("资质审核")
    assert len(out) == 1
    assert out[0]["session_id"] == "s1"
    assert out[0]["session_title"] == "8 项资质"
    assert "资质审核" in out[0]["snippet"]


def test_search_cross_session(fake_home: Path):
    """关键: 跨 session 搜 (修复 hermes session_search 只搜当前的问题)."""
    now = time.time()
    _seed_db(
        fake_home,
        sessions=[
            {"id": "s_old", "started_at": now - 5 * 3600, "title": "5 小时前"},
            {"id": "s_new", "started_at": now, "title": "新会话"},
        ],
        messages=[
            {"session_id": "s_old", "role": "user", "content": "ISO 55001 资质", "created_at": now - 5 * 3600},
            {"session_id": "s_new", "role": "user", "content": "你好", "created_at": now},
        ],
    )
    out = sessions_search.search_messages("ISO 55001")
    assert len(out) == 1
    assert out[0]["session_id"] == "s_old"


def test_search_days_back_filter(fake_home: Path):
    now = time.time()
    _seed_db(
        fake_home,
        sessions=[
            {"id": "old", "started_at": now - 10 * 86400, "title": "10 天前"},
            {"id": "fresh", "started_at": now - 1 * 86400, "title": "1 天前"},
        ],
        messages=[
            {"session_id": "old", "role": "user", "content": "matched_keyword", "created_at": now - 10 * 86400},
            {"session_id": "fresh", "role": "user", "content": "matched_keyword", "created_at": now - 86400},
        ],
    )
    out = sessions_search.search_messages("matched_keyword", days_back=3)
    assert len(out) == 1
    assert out[0]["session_id"] == "fresh"


def test_search_limit_clamp(fake_home: Path):
    now = time.time()
    _seed_db(
        fake_home,
        sessions=[{"id": f"s{i}", "started_at": now - i, "title": f"s{i}"} for i in range(150)],
        messages=[
            {"session_id": f"s{i}", "role": "user", "content": "kw", "created_at": now - i}
            for i in range(150)
        ],
    )
    # limit=999 → 钳到 100
    out = sessions_search.search_messages("kw", limit=999)
    assert len(out) == 100


def test_search_snippet_truncated_with_ellipsis(fake_home: Path):
    now = time.time()
    long = "前缀" * 200 + "命中关键字" + "后缀" * 200
    _seed_db(
        fake_home,
        sessions=[{"id": "s1", "started_at": now}],
        messages=[
            {"session_id": "s1", "role": "user", "content": long, "created_at": now},
        ],
    )
    out = sessions_search.search_messages("命中关键字")
    assert len(out) == 1
    snippet = out[0]["snippet"]
    assert "命中关键字" in snippet
    assert "…" in snippet
    assert len(snippet) < len(long)


def test_search_like_escape(fake_home: Path):
    """LIKE wildcard 转义 — query='100%' 不该匹 '100abc'."""
    now = time.time()
    _seed_db(
        fake_home,
        sessions=[
            {"id": "s1", "started_at": now},
            {"id": "s2", "started_at": now},
        ],
        messages=[
            {"session_id": "s1", "role": "user", "content": "100% pass", "created_at": now},
            {"session_id": "s2", "role": "user", "content": "100abc fail", "created_at": now},
        ],
    )
    out = sessions_search.search_messages("100%")
    assert len(out) == 1
    assert out[0]["session_id"] == "s1"


# ── tool_search_sessions ─────────────────────────────────


def test_tool_query_required(fake_home: Path):
    r = sessions_search.tool_search_sessions({})
    assert r["ok"] is False
    assert "query" in r["error"]


def test_tool_no_match_returns_friendly(fake_home: Path):
    _seed_db(fake_home, sessions=[], messages=[])
    r = sessions_search.tool_search_sessions({"query": "什么也没有"})
    assert r["ok"] is True
    assert r["count"] == 0
    assert r["matches"] == []
    assert "0 条命中" in r["summary"]


def test_tool_found_returns_grouped_summary(fake_home: Path):
    now = time.time()
    _seed_db(
        fake_home,
        sessions=[
            {"id": "old1", "started_at": now - 5 * 3600, "title": "8 项资质"},
            {"id": "old2", "started_at": now - 3 * 3600, "title": "ISO 整理"},
        ],
        messages=[
            {"session_id": "old1", "role": "user", "content": "ISO 55001 资质 1", "created_at": now - 5 * 3600},
            {"session_id": "old1", "role": "assistant", "content": "ISO 55001 答案 1", "created_at": now - 5 * 3600 + 1},
            {"session_id": "old2", "role": "user", "content": "ISO 27011 资质 2", "created_at": now - 3 * 3600},
        ],
    )
    r = sessions_search.tool_search_sessions({"query": "ISO"})
    assert r["ok"] is True
    assert r["count"] == 3
    assert r["session_count"] == 2
    assert "8 项资质" in r["summary"]
    assert "ISO 整理" in r["summary"]


def test_tool_invalid_days_back_falls_back(fake_home: Path):
    """days_back 传非数字 → 走默认 30, 不抛."""
    _seed_db(fake_home, sessions=[], messages=[])
    r = sessions_search.tool_search_sessions({"query": "x", "days_back": "abc"})
    assert r["ok"] is True


def test_tool_returns_session_titles(fake_home: Path):
    """每条 match 含 session_title, 让 LLM 能告员工'在 X 会话里找到'."""
    now = time.time()
    _seed_db(
        fake_home,
        sessions=[{"id": "abc", "started_at": now, "title": "5/13 上午合并资质"}],
        messages=[
            {"session_id": "abc", "role": "user", "content": "需要 8 项资质", "created_at": now},
        ],
    )
    r = sessions_search.tool_search_sessions({"query": "资质"})
    assert r["matches"][0]["session_title"] == "5/13 上午合并资质"


# ── 集成 dispatch ────────────────────────────────────────


def test_dispatch_via_catfish_tools(fake_home: Path):
    """走 catfish_tools.dispatch_native 真接通 (跟 LLM 调用同路径)."""
    from catfish_tool_bridge import catfish_tools
    now = time.time()
    _seed_db(
        fake_home,
        sessions=[{"id": "s1", "started_at": now, "title": "test"}],
        messages=[
            {"session_id": "s1", "role": "user", "content": "ABCXYZ", "created_at": now},
        ],
    )
    r = catfish_tools.dispatch_native(
        "catfish_search_sessions", {"query": "ABCXYZ"},
    )
    assert r["ok"] is True
    assert r["count"] == 1


def test_schema_in_native_tools_list():
    """schema 进 CATFISH_NATIVE_TOOLS, LLM 能看到这个 tool."""
    from catfish_tool_bridge import catfish_tools
    assert "catfish_search_sessions" in catfish_tools.NATIVE_TOOL_NAMES
    schema = next(
        t for t in catfish_tools.CATFISH_NATIVE_TOOLS
        if t["name"] == "catfish_search_sessions"
    )
    assert schema["input_schema"]["required"] == ["query"]
    # description 必须告诉 LLM 优先用这个不要用 hermes session_search
    assert "session_search" in schema["description"]
