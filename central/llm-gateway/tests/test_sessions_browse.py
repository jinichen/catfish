"""sessions_browse + /api/sessions/me/* 测试.

覆盖:
- list_sessions (跨日 / 关键字 / 分页 / 空 db)
- count_sessions
- get_session (存在 / 不存在 / max_messages 钳)
- search_messages (跨 session 命中 / snippet 截断 / 大小写不敏感)
- API endpoint 3 个 + RBAC (任何登录用户都能访问 /me, sysadmin/admin/manager/employee 都行)
- LIKE 转义 (% 不被当 wildcard, _ 不被当通配)
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from catfish_gateway import sessions_browse
from catfish_gateway.app import app


def _seed_db(home: Path, sessions: list[dict], messages: list[dict]) -> Path:
    """建一个假的 ~/.hermes/state.db 供 sessions_browse 读."""
    hermes_dir = home / ".hermes"
    hermes_dir.mkdir(parents=True, exist_ok=True)
    db = hermes_dir / "state.db"
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
            (s["id"], s["started_at"], s["message_count"], s.get("title", "")),
        )
    for m in messages:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (m["session_id"], m["role"], m["content"], m["created_at"]),
        )
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    yield tmp_path


# ── list_sessions ────────────────────────────────────────


def test_list_empty_when_no_db(fake_home: Path):
    """state.db 不存在 → 返 [] 不抛."""
    assert sessions_browse.list_sessions() == []


def test_list_basic(fake_home: Path):
    now = time.time()
    _seed_db(
        fake_home,
        sessions=[
            {"id": "s1", "started_at": now - 60, "message_count": 5, "title": "测试 1"},
            {"id": "s2", "started_at": now - 120, "message_count": 3, "title": "测试 2"},
        ],
        messages=[
            {"session_id": "s1", "role": "user", "content": "你好", "created_at": now - 60},
            {"session_id": "s1", "role": "assistant", "content": "好的", "created_at": now - 50},
            {"session_id": "s2", "role": "user", "content": "再见", "created_at": now - 120},
        ],
    )
    out = sessions_browse.list_sessions()
    assert len(out) == 2
    # 倒序: s1 (更新) 在前
    assert out[0]["id"] == "s1"
    assert out[0]["title"] == "测试 1"
    assert out[0]["message_count"] == 5
    assert out[0]["first_user_msg"] == "你好"
    assert out[1]["id"] == "s2"


def test_list_skips_zero_message_sessions(fake_home: Path):
    """message_count=0 的 session 不返 (空 session 没意义)."""
    now = time.time()
    _seed_db(
        fake_home,
        sessions=[
            {"id": "alive", "started_at": now - 60, "message_count": 1},
            {"id": "dead", "started_at": now - 60, "message_count": 0},
        ],
        messages=[
            {"session_id": "alive", "role": "user", "content": "x", "created_at": now - 60},
        ],
    )
    out = sessions_browse.list_sessions()
    assert len(out) == 1
    assert out[0]["id"] == "alive"


def test_list_days_back_filter(fake_home: Path):
    """超过 days_back 的 session 不返."""
    now = time.time()
    _seed_db(
        fake_home,
        sessions=[
            {"id": "old", "started_at": now - 10 * 86400, "message_count": 5},
            {"id": "fresh", "started_at": now - 1 * 86400, "message_count": 5},
        ],
        messages=[
            {"session_id": "old", "role": "user", "content": "old", "created_at": 0},
            {"session_id": "fresh", "role": "user", "content": "new", "created_at": 0},
        ],
    )
    out = sessions_browse.list_sessions(days_back=3)
    assert len(out) == 1
    assert out[0]["id"] == "fresh"


def test_list_search_q_finds_in_message_content(fake_home: Path):
    """q 搜任意 message content."""
    now = time.time()
    _seed_db(
        fake_home,
        sessions=[
            {"id": "s1", "started_at": now - 60, "message_count": 2},
            {"id": "s2", "started_at": now - 60, "message_count": 2},
        ],
        messages=[
            {"session_id": "s1", "role": "user", "content": "我要查资质审核流程", "created_at": now},
            {"session_id": "s2", "role": "user", "content": "今天天气真好", "created_at": now},
        ],
    )
    out = sessions_browse.list_sessions(search_q="资质")
    assert len(out) == 1
    assert out[0]["id"] == "s1"


def test_list_search_q_case_insensitive_for_ascii(fake_home: Path):
    """ASCII 字符大小写不敏感 (sqlite LIKE 默认 ASCII case-insensitive)."""
    now = time.time()
    _seed_db(
        fake_home,
        sessions=[{"id": "s1", "started_at": now, "message_count": 1}],
        messages=[
            {"session_id": "s1", "role": "user", "content": "EIS Login flow",
             "created_at": now},
        ],
    )
    out = sessions_browse.list_sessions(search_q="eis login")
    assert len(out) == 1


def test_list_pagination(fake_home: Path):
    now = time.time()
    sess = []
    msgs = []
    for i in range(10):
        sess.append({"id": f"s{i}", "started_at": now - 60 - i, "message_count": 1})
        msgs.append({"session_id": f"s{i}", "role": "user", "content": "x", "created_at": now})
    _seed_db(fake_home, sessions=sess, messages=msgs)
    p1 = sessions_browse.list_sessions(limit=3, offset=0)
    p2 = sessions_browse.list_sessions(limit=3, offset=3)
    assert [s["id"] for s in p1] == ["s0", "s1", "s2"]
    assert [s["id"] for s in p2] == ["s3", "s4", "s5"]


def test_list_preview_truncated(fake_home: Path):
    now = time.time()
    long_msg = "x" * 500
    _seed_db(
        fake_home,
        sessions=[{"id": "s1", "started_at": now, "message_count": 1}],
        messages=[
            {"session_id": "s1", "role": "user", "content": long_msg, "created_at": now},
        ],
    )
    out = sessions_browse.list_sessions()
    # _PREVIEW_CHARS=100
    assert len(out[0]["first_user_msg"]) <= 200  # 100 + 截断符
    assert "…" in out[0]["first_user_msg"]


def test_list_like_escape_no_false_match(fake_home: Path):
    """LIKE wildcard 转义 — q='100%' 不该匹 '100abc'."""
    now = time.time()
    _seed_db(
        fake_home,
        sessions=[
            {"id": "s1", "started_at": now, "message_count": 1},
            {"id": "s2", "started_at": now, "message_count": 1},
        ],
        messages=[
            {"session_id": "s1", "role": "user", "content": "100% pass", "created_at": now},
            {"session_id": "s2", "role": "user", "content": "100abc fail", "created_at": now},
        ],
    )
    out = sessions_browse.list_sessions(search_q="100%")
    assert len(out) == 1
    assert out[0]["id"] == "s1"


# ── count_sessions ────────────────────────────────────────


def test_count_no_db_returns_zero(fake_home: Path):
    assert sessions_browse.count_sessions() == 0


def test_count_with_filter(fake_home: Path):
    now = time.time()
    _seed_db(
        fake_home,
        sessions=[
            {"id": f"s{i}", "started_at": now, "message_count": 1}
            for i in range(7)
        ],
        messages=[
            {"session_id": f"s{i}", "role": "user", "content": "x" if i < 3 else "y",
             "created_at": now}
            for i in range(7)
        ],
    )
    assert sessions_browse.count_sessions() == 7
    assert sessions_browse.count_sessions(search_q="x") == 3
    assert sessions_browse.count_sessions(search_q="y") == 4


# ── get_session ──────────────────────────────────────────


def test_get_session_returns_messages(fake_home: Path):
    now = time.time()
    _seed_db(
        fake_home,
        sessions=[{"id": "s1", "started_at": now, "message_count": 3, "title": "T"}],
        messages=[
            {"session_id": "s1", "role": "user", "content": "Q1", "created_at": now},
            {"session_id": "s1", "role": "assistant", "content": "A1", "created_at": now + 1},
            {"session_id": "s1", "role": "user", "content": "Q2", "created_at": now + 2},
        ],
    )
    out = sessions_browse.get_session("s1")
    assert out is not None
    assert out["id"] == "s1"
    assert out["title"] == "T"
    assert out["message_count"] == 3
    assert len(out["messages"]) == 3
    assert out["messages"][0]["role"] == "user"
    assert out["messages"][0]["content"] == "Q1"


def test_get_session_not_found(fake_home: Path):
    _seed_db(fake_home, sessions=[], messages=[])
    assert sessions_browse.get_session("nope") is None


def test_get_session_empty_id(fake_home: Path):
    assert sessions_browse.get_session("") is None
    assert sessions_browse.get_session("   ") is None


def test_get_session_max_messages_clamps(fake_home: Path):
    now = time.time()
    _seed_db(
        fake_home,
        sessions=[{"id": "s1", "started_at": now, "message_count": 50}],
        messages=[
            {"session_id": "s1", "role": "user", "content": f"m{i}", "created_at": now + i}
            for i in range(50)
        ],
    )
    out = sessions_browse.get_session("s1", max_messages=10)
    assert len(out["messages"]) == 10
    assert out["messages_returned"] == 10


# ── search_messages ──────────────────────────────────────


def test_search_empty_q(fake_home: Path):
    assert sessions_browse.search_messages("") == []
    assert sessions_browse.search_messages("   ") == []


def test_search_returns_matching_lines(fake_home: Path):
    now = time.time()
    _seed_db(
        fake_home,
        sessions=[
            {"id": "s1", "started_at": now, "message_count": 2},
            {"id": "s2", "started_at": now, "message_count": 1},
        ],
        messages=[
            {"session_id": "s1", "role": "user", "content": "查资质审核", "created_at": now},
            {"session_id": "s1", "role": "assistant", "content": "好的", "created_at": now + 1},
            {"session_id": "s2", "role": "user", "content": "资质材料怎么准备", "created_at": now + 2},
        ],
    )
    out = sessions_browse.search_messages("资质")
    assert len(out) == 2
    sids = {m["session_id"] for m in out}
    assert sids == {"s1", "s2"}


def test_search_snippet_truncated_with_ellipsis(fake_home: Path):
    now = time.time()
    long = "前缀" * 100 + "命中关键字" + "后缀" * 100
    _seed_db(
        fake_home,
        sessions=[{"id": "s1", "started_at": now, "message_count": 1}],
        messages=[
            {"session_id": "s1", "role": "user", "content": long, "created_at": now},
        ],
    )
    out = sessions_browse.search_messages("命中关键字")
    assert len(out) == 1
    snippet = out[0]["snippet"]
    assert "命中关键字" in snippet
    assert "…" in snippet
    assert len(snippet) < len(long)


# ── API endpoints ────────────────────────────────────────


def _client_as(role: str, monkeypatch) -> TestClient:
    from catfish_gateway.auth.base import User
    fake = User(
        sub=f"{role}@ffcs.cn",
        department="engineering",
        tier="employee",
        role=role,
        managed_departments=[],
        auth_method="test",
    )

    async def fake_get_current_user():
        return fake

    from catfish_gateway.app import get_current_user as real_dep
    app.dependency_overrides[real_dep] = fake_get_current_user
    return TestClient(app)


def _cleanup():
    app.dependency_overrides.clear()


def test_endpoint_list_employee_ok(fake_home: Path, monkeypatch):
    """普通员工能访问 /me (只看自己)."""
    now = time.time()
    _seed_db(
        fake_home,
        sessions=[{"id": "s1", "started_at": now, "message_count": 2}],
        messages=[
            {"session_id": "s1", "role": "user", "content": "hi", "created_at": now},
        ],
    )
    c = _client_as("employee", monkeypatch)
    r = c.get("/api/sessions/me")
    _cleanup()
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["id"] == "s1"


def test_endpoint_list_paginated(fake_home: Path, monkeypatch):
    now = time.time()
    sess = [{"id": f"s{i}", "started_at": now - i, "message_count": 1} for i in range(8)]
    msgs = [{"session_id": s["id"], "role": "user", "content": "x", "created_at": now}
            for s in sess]
    _seed_db(fake_home, sessions=sess, messages=msgs)
    c = _client_as("employee", monkeypatch)
    r = c.get("/api/sessions/me?limit=3&offset=0")
    _cleanup()
    data = r.json()
    assert data["total"] == 8
    assert len(data["sessions"]) == 3


def test_endpoint_list_with_search(fake_home: Path, monkeypatch):
    now = time.time()
    _seed_db(
        fake_home,
        sessions=[
            {"id": "s1", "started_at": now, "message_count": 1},
            {"id": "s2", "started_at": now, "message_count": 1},
        ],
        messages=[
            {"session_id": "s1", "role": "user", "content": "资质审核", "created_at": now},
            {"session_id": "s2", "role": "user", "content": "天气好", "created_at": now},
        ],
    )
    c = _client_as("employee", monkeypatch)
    r = c.get("/api/sessions/me?q=资质")
    _cleanup()
    assert r.json()["total"] == 1


def test_endpoint_search_matches(fake_home: Path, monkeypatch):
    now = time.time()
    _seed_db(
        fake_home,
        sessions=[{"id": "s1", "started_at": now, "message_count": 1}],
        messages=[
            {"session_id": "s1", "role": "user", "content": "资质审核步骤", "created_at": now},
        ],
    )
    c = _client_as("employee", monkeypatch)
    r = c.get("/api/sessions/me/search?q=资质")
    _cleanup()
    assert r.status_code == 200
    data = r.json()
    assert len(data["matches"]) == 1
    assert data["matches"][0]["session_id"] == "s1"


def test_endpoint_search_q_required(fake_home: Path, monkeypatch):
    c = _client_as("employee", monkeypatch)
    r = c.get("/api/sessions/me/search")
    _cleanup()
    assert r.status_code == 422  # FastAPI required query param


def test_endpoint_detail_ok(fake_home: Path, monkeypatch):
    now = time.time()
    _seed_db(
        fake_home,
        sessions=[{"id": "abc-123", "started_at": now, "message_count": 2}],
        messages=[
            {"session_id": "abc-123", "role": "user", "content": "Q", "created_at": now},
            {"session_id": "abc-123", "role": "assistant", "content": "A", "created_at": now},
        ],
    )
    c = _client_as("employee", monkeypatch)
    r = c.get("/api/sessions/me/abc-123")
    _cleanup()
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == "abc-123"
    assert len(data["messages"]) == 2
    assert data["viewer"] == "employee@ffcs.cn"


def test_endpoint_detail_404(fake_home: Path, monkeypatch):
    _seed_db(fake_home, sessions=[], messages=[])
    c = _client_as("employee", monkeypatch)
    r = c.get("/api/sessions/me/nonexistent")
    _cleanup()
    assert r.status_code == 404
    assert "不存在" in r.json()["detail"]


def test_endpoint_admin_can_also_list(fake_home: Path, monkeypatch):
    """admin / sysadmin 也能看自己的 — /me 不限 role, 谁登录看谁."""
    now = time.time()
    _seed_db(
        fake_home,
        sessions=[{"id": "s1", "started_at": now, "message_count": 1}],
        messages=[
            {"session_id": "s1", "role": "user", "content": "x", "created_at": now},
        ],
    )
    for role in ["admin", "sysadmin", "manager"]:
        c = _client_as(role, monkeypatch)
        r = c.get("/api/sessions/me")
        _cleanup()
        assert r.status_code == 200, f"role={role} failed"


def test_endpoint_route_order_search_not_caught_by_id(fake_home: Path, monkeypatch):
    """关键: /me/search 必须命中 search endpoint, 不被当成 session_id='search'."""
    _seed_db(fake_home, sessions=[], messages=[])
    c = _client_as("employee", monkeypatch)
    r = c.get("/api/sessions/me/search?q=test")
    _cleanup()
    # 命中 search → 200 + matches=[], 不该是 detail 的 404
    assert r.status_code == 200
    assert "matches" in r.json()
