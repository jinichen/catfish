"""Sessions 浏览 + 跨日搜索 (5/12 鸿波借鉴 hermes-desktop A 路线).

# 真用途

催 Companion / catfish-web `/me/sessions` 页用. 员工想"找 3 天前那次跟客户 X
讨论资质审核的对话, 接着聊" — 当前没法搜, 只能滚 Companion 左侧列表.

借鉴 hermes-desktop (fathah) 的 Sessions browse + resume + search 设计, 但走
catfish 自己的 hermes state.db 读路径 (read-only sqlite, 跟 inject_session_history
同源).

# 隐私边界

- state.db 在员工自己 mac (`~/.hermes/state.db`), gateway 跑在同 mac 直接读
- 不上行中央, 中央 catfish-web 走 vite proxy / nginx 反代到 localhost:8999
- 员工只能查自己的 db (db 本来就只有自己的, 不需要 RBAC 二次校验)
- 搜索关键字也只在本地 LIKE, 不外发

# API

- list_sessions(days_back, search_q, limit, offset, user_filter) → list[dict]
- count_sessions(...) → int (分页 total)
- get_session(id) → dict | None (含 messages 数组)
- search_messages(q, days_back, limit) → list[dict] (跨 session 命中行)

# 数据模型

hermes state.db 里:
- sessions: id, started_at (unix s), message_count, title
- messages: id, session_id, role ('user'/'assistant'/'tool'/'system'),
  content (text), created_at, …
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("catfish.gateway.sessions_browse")


_DEFAULT_DAYS_BACK = 30
_DEFAULT_LIMIT = 50
_DEFAULT_MAX_LIMIT = 200
_PREVIEW_CHARS = 100


def _state_db_path() -> Path | None:
    """hermes session DB 路径. 跟 inject_session_history.get_state_db_path 同."""
    p = Path.home() / ".hermes" / "state.db"
    return p if p.exists() else None


def _connect_ro() -> sqlite3.Connection | None:
    """read-only 连 state.db. 不存在 / 失败返 None."""
    p = _state_db_path()
    if p is None:
        return None
    try:
        return sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=1.5)
    except sqlite3.Error as e:
        logger.warning("sessions_browse: 连 state.db 失败: %s", e)
        return None


def _build_where_for_list(
    days_back: int,
    search_q: str,
) -> tuple[str, list[Any]]:
    """list_sessions / count_sessions 共用的 where 段构造."""
    cutoff_ts = (datetime.now() - timedelta(days=max(0, days_back))).timestamp()
    clauses = ["s.started_at >= ?", "s.message_count > 0"]
    params: list[Any] = [cutoff_ts]
    if search_q.strip():
        # 任意 message content 含 q (大小写不敏感)
        clauses.append(
            "EXISTS (SELECT 1 FROM messages m "
            "WHERE m.session_id = s.id AND m.content LIKE ? "
            "ESCAPE '\\')"
        )
        # SQL LIKE 需要转义 % _ \ — 简单: 用反斜杠转义 + ESCAPE 子句
        like_pattern = (
            "%"
            + search_q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            + "%"
        )
        params.append(like_pattern)
    return " AND ".join(clauses), params


def list_sessions(
    *,
    days_back: int = _DEFAULT_DAYS_BACK,
    search_q: str = "",
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """列 sessions, 倒序按 started_at, 含 first_user_msg preview 给前端展示用.

    永不抛, 失败返 [].
    """
    conn = _connect_ro()
    if conn is None:
        return []
    limit = max(1, min(_DEFAULT_MAX_LIMIT, int(limit)))
    offset = max(0, int(offset))
    where_sql, params = _build_where_for_list(days_back, search_q)
    sql = (
        "SELECT s.id, s.started_at, s.message_count, s.title, "
        "       (SELECT m.content FROM messages m "
        "        WHERE m.session_id = s.id AND m.role = 'user' "
        "        ORDER BY m.id LIMIT 1) AS first_user_msg "
        "FROM sessions s "
        f"WHERE {where_sql} "
        "ORDER BY s.started_at DESC LIMIT ? OFFSET ?"
    )
    params.append(limit)
    params.append(offset)
    try:
        rows = conn.execute(sql, params).fetchall()
        conn.close()
    except sqlite3.Error as e:
        logger.warning("sessions_browse.list_sessions 失败: %s", e)
        return []

    out: list[dict[str, Any]] = []
    for sid, started_at, msg_count, title, first_msg in rows:
        preview = (first_msg or "").strip()
        if len(preview) > _PREVIEW_CHARS:
            preview = preview[:_PREVIEW_CHARS] + "…"
        out.append({
            "id": sid,
            "started_at": float(started_at) if started_at else 0.0,
            "started_iso": (
                datetime.fromtimestamp(started_at).isoformat()
                if started_at else ""
            ),
            "message_count": int(msg_count or 0),
            "title": title or "",
            "first_user_msg": preview,
        })
    return out


def count_sessions(
    *,
    days_back: int = _DEFAULT_DAYS_BACK,
    search_q: str = "",
) -> int:
    """跟 list_sessions 同筛选, 返 total (分页用). 永不抛, 失败返 0."""
    conn = _connect_ro()
    if conn is None:
        return 0
    where_sql, params = _build_where_for_list(days_back, search_q)
    try:
        row = conn.execute(
            f"SELECT COUNT(*) FROM sessions s WHERE {where_sql}",
            params,
        ).fetchone()
        conn.close()
    except sqlite3.Error as e:
        logger.warning("sessions_browse.count_sessions 失败: %s", e)
        return 0
    return int(row[0]) if row else 0


def get_session(session_id: str, *, max_messages: int = 500) -> Optional[dict[str, Any]]:
    """单个 session 详情 + messages 数组. 不存在返 None.

    max_messages: 防超长 session 撑爆响应, 默认 500 条 (按时间正序).
    """
    if not session_id or not session_id.strip():
        return None
    conn = _connect_ro()
    if conn is None:
        return None
    try:
        srow = conn.execute(
            "SELECT id, started_at, message_count, title FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not srow:
            conn.close()
            return None
        sid, started_at, msg_count, title = srow

        mrows = conn.execute(
            "SELECT id, role, content, created_at FROM messages "
            "WHERE session_id = ? ORDER BY id ASC LIMIT ?",
            (session_id, max(1, min(2000, int(max_messages)))),
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        logger.warning("sessions_browse.get_session(%s) 失败: %s", session_id, e)
        return None

    messages = []
    for mid, role, content, created_at in mrows:
        messages.append({
            "id": int(mid) if mid is not None else 0,
            "role": role or "",
            "content": content or "",
            "created_at": float(created_at) if created_at else 0.0,
        })

    return {
        "id": sid,
        "started_at": float(started_at) if started_at else 0.0,
        "started_iso": (
            datetime.fromtimestamp(started_at).isoformat()
            if started_at else ""
        ),
        "message_count": int(msg_count or 0),
        "title": title or "",
        "messages": messages,
        "messages_returned": len(messages),
    }


def search_messages(
    q: str,
    *,
    days_back: int = _DEFAULT_DAYS_BACK,
    limit: int = _DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """跨 session 命中行 — 给"全文搜索"模式 (vs list_sessions 的"session 级搜索").

    每条返 session_id / role / content (截断) / created_at, 让前端能跳到对应 session.
    """
    if not q or not q.strip():
        return []
    conn = _connect_ro()
    if conn is None:
        return []
    cutoff_ts = (datetime.now() - timedelta(days=max(0, days_back))).timestamp()
    like_pattern = (
        "%"
        + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        + "%"
    )
    limit = max(1, min(_DEFAULT_MAX_LIMIT, int(limit)))
    try:
        rows = conn.execute(
            "SELECT m.session_id, m.role, m.content, m.created_at "
            "FROM messages m "
            "JOIN sessions s ON s.id = m.session_id "
            "WHERE s.started_at >= ? AND m.content LIKE ? ESCAPE '\\' "
            "ORDER BY m.created_at DESC LIMIT ?",
            (cutoff_ts, like_pattern, limit),
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        logger.warning("sessions_browse.search_messages 失败: %s", e)
        return []

    out = []
    for sid, role, content, created_at in rows:
        snippet = (content or "").strip()
        # 高亮 q 周围 ±50 字符
        idx = snippet.lower().find(q.lower())
        if idx >= 0 and len(snippet) > 200:
            start = max(0, idx - 50)
            end = min(len(snippet), idx + len(q) + 100)
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(snippet) else ""
            snippet = prefix + snippet[start:end] + suffix
        out.append({
            "session_id": sid,
            "role": role or "",
            "snippet": snippet,
            "created_at": float(created_at) if created_at else 0.0,
        })
    return out


__all__ = [
    "list_sessions",
    "count_sessions",
    "get_session",
    "search_messages",
]
