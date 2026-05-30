"""BL-FILE-SESSION-INDEX-V1 Phase 2 单测 — catfish_search_attachments.

测试覆盖:
- search_by_name 跨 session + 用户隔离
- LIKE 转义防 wildcard
- empty / missing args 友好返
- mode 切换 name / content / both
- tool 入口 args 校验

不测真 BM25 (那要 Companion attachment_bm25.py helper + 真 sidecar 文件,
单测环境难造). BM25 helper 自带单测, 我们这里只测 db query 层和 tool 入口.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# 让单测能 import catfish_tool_bridge (本地 dev 跑)
sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "src"),
)

from catfish_tool_bridge import attachments_search  # noqa: E402


@pytest.fixture
def tmp_db(monkeypatch):
    """造一个临时 ~/.catfish/attachments.db, 灌数据, 让 attachments_search 读它."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CATFISH_HOME"] = tmp
        catfish_dir = Path(tmp) / ".catfish" if False else Path(tmp)
        catfish_dir.mkdir(parents=True, exist_ok=True)
        db = catfish_dir / "attachments.db"
        conn = sqlite3.connect(db)
        conn.execute(
            """
            CREATE TABLE attachments (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                session_id TEXT NOT NULL, message_id TEXT NOT NULL,
                kind TEXT NOT NULL, file_kind TEXT,
                name TEXT NOT NULL, mime_type TEXT, size_bytes INTEGER,
                kept_path TEXT, parsed_text_path TEXT, meta TEXT,
                created_at REAL NOT NULL
            )
            """
        )
        conn.commit()
        yield conn, db
        conn.close()
        os.environ.pop("CATFISH_HOME", None)


def _insert(conn, *, id, user_id, session_id, name, file_kind="pdf",
            kept_path=None, parsed_text_path=None, created_at=None):
    import time
    if created_at is None:
        created_at = time.time()  # default: 现在, 不被 days_back 滤
    conn.execute(
        "INSERT INTO attachments VALUES (?, ?, ?, ?, 'file', ?, ?, NULL, NULL, ?, ?, NULL, ?)",
        (id, user_id, session_id, "msg-1", file_kind, name, kept_path, parsed_text_path, created_at),
    )
    conn.commit()


# ───────────────────────────────────────────────────────────────
# search_by_name
# ───────────────────────────────────────────────────────────────

def test_search_by_name_basic(tmp_db):
    conn, _ = tmp_db
    _insert(conn, id="a", user_id="u@x.com", session_id="s1", name="客户合同_v3.pdf")
    _insert(conn, id="b", user_id="u@x.com", session_id="s2", name="周报-W23.xlsx", file_kind="xlsx")
    _insert(conn, id="c", user_id="u@x.com", session_id="s1", name="其它文件.docx", file_kind="docx")

    hits = attachments_search.search_by_name("u@x.com", "客户合同")
    assert len(hits) == 1
    assert hits[0]["name"] == "客户合同_v3.pdf"
    assert hits[0]["session_id"] == "s1"


def test_search_by_name_user_isolation(tmp_db):
    """关键: 隔离不串到其它员工."""
    conn, _ = tmp_db
    _insert(conn, id="mine", user_id="me@x.com", session_id="s", name="敏感合同.pdf")
    _insert(conn, id="other", user_id="other@x.com", session_id="s", name="敏感合同.pdf")

    hits = attachments_search.search_by_name("me@x.com", "敏感合同")
    assert len(hits) == 1
    assert hits[0]["id"] == "mine", "不可看到 other@x.com 的同名文件"


def test_search_by_name_like_escape(tmp_db):
    """LIKE 转义防 wildcard 串味 (用户输 'a%' 不应匹配所有 'a*')."""
    conn, _ = tmp_db
    _insert(conn, id="a1", user_id="u", session_id="s", name="abc.pdf")
    _insert(conn, id="a2", user_id="u", session_id="s", name="a%special.pdf")

    # 真正含 '%' 字面应匹配
    hits_literal = attachments_search.search_by_name("u", "a%special")
    assert len(hits_literal) == 1
    assert hits_literal[0]["name"] == "a%special.pdf"

    # 仅 'a' 应匹配两条
    hits_a = attachments_search.search_by_name("u", "a")
    assert len(hits_a) == 2


def test_search_by_name_days_back_filter(tmp_db):
    """days_back 滤掉太老的."""
    conn, _ = tmp_db
    import time
    now = time.time()
    _insert(conn, id="recent", user_id="u", session_id="s", name="x.pdf", created_at=now - 3600)
    _insert(conn, id="old", user_id="u", session_id="s", name="x.pdf", created_at=now - 100 * 86400)

    hits = attachments_search.search_by_name("u", "x", days_back=30)
    assert len(hits) == 1
    assert hits[0]["id"] == "recent"


def test_search_by_name_empty_returns_empty(tmp_db):
    """空 user_id / query 友好返空, 不抛."""
    assert attachments_search.search_by_name("", "x") == []
    assert attachments_search.search_by_name("u", "") == []


def test_search_by_name_db_missing_returns_empty(monkeypatch, tmp_path):
    """db 文件不存在友好返空."""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    # tmp_path 下没 attachments.db
    assert attachments_search.search_by_name("u", "x") == []


# ───────────────────────────────────────────────────────────────
# tool_search_attachments (入口)
# ───────────────────────────────────────────────────────────────

def test_tool_missing_user_id(tmp_db):
    out = attachments_search.tool_search_attachments({"query": "x"})
    assert out["ok"] is False
    assert "user_id" in out["error"]


def test_tool_missing_query(tmp_db):
    out = attachments_search.tool_search_attachments({"user_id": "u@x.com"})
    assert out["ok"] is False
    assert "query" in out["error"]


def test_tool_no_matches_returns_summary(tmp_db):
    out = attachments_search.tool_search_attachments({
        "user_id": "u@x.com", "query": "不存在的关键字",
    })
    assert out["ok"] is True
    assert out["count"] == 0
    assert "0 条命中" in out["summary"]


def test_tool_name_mode_only(tmp_db):
    conn, _ = tmp_db
    _insert(conn, id="a", user_id="u", session_id="s", name="客户合同.pdf")
    out = attachments_search.tool_search_attachments({
        "user_id": "u", "query": "客户合同", "mode": "name",
    })
    assert out["ok"] is True
    assert out["count"] == 1
    assert out["matches"][0]["name"] == "客户合同.pdf"


def test_tool_summary_includes_file_kind(tmp_db):
    conn, _ = tmp_db
    _insert(conn, id="a", user_id="u", session_id="s", name="a.pdf", file_kind="pdf")
    _insert(conn, id="b", user_id="u", session_id="s", name="b.pdf", file_kind="pdf")
    _insert(conn, id="c", user_id="u", session_id="s", name="c.xlsx", file_kind="xlsx")

    out = attachments_search.tool_search_attachments({
        "user_id": "u", "query": "", "mode": "name",
    })
    # query 空 → 工具拒绝 (ok=False)
    assert out["ok"] is False

    out = attachments_search.tool_search_attachments({
        "user_id": "u", "query": ".", "mode": "name",
    })
    assert out["count"] == 3
    # summary 含 kind 统计
    assert "pdf: 2" in out["summary"]
    assert "xlsx: 1" in out["summary"]


def test_tool_invalid_mode_falls_back_to_both(tmp_db):
    conn, _ = tmp_db
    _insert(conn, id="a", user_id="u", session_id="s", name="x.pdf")
    out = attachments_search.tool_search_attachments({
        "user_id": "u", "query": "x", "mode": "garbage_mode",
    })
    # 没炸, 默认 both
    assert out["ok"] is True
