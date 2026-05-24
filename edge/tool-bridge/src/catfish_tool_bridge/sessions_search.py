"""BL-FIX-SESSION-SEARCH (5/13 鸿波"历史会话搜不到") — catfish_search_sessions
native tool 让 LLM 跨 session 搜历史对话.

# 真问题
鸿波 5/13 在新会话里调 hermes 自带 session_search 搜 5 小时前的"8 项资质"
对话, 报"搜不到". 老 session 真在 hermes state.db sessions/messages 表里,
应该能搜到.

诊断: hermes session_search 实现可能只搜当前 session / limit 参数处理错 / 等.
catfish 5/13 早上加了 /api/sessions/me/search (gateway sessions_browse), 真
跨 session work, 但 LLM 不知道用. 暴露 catfish_search_sessions native tool
让 LLM 改用.

# 数据源
~/.hermes/state.db (read-only sqlite, 跟 gateway sessions_browse 同源).
不走 gateway HTTP — tool-bridge 跟 hermes 在同一台员工 mac, 直读最快.

# 安全
- read-only 连接 (mode=ro)
- 1.5s timeout
- LIKE 转义防 % _ \ wildcard 串味
- 永不抛异常 (失败返空, LLM 看到友好结果)
- 只搜当前员工 mac 的 hermes db, 不跨员工 (state.db 物理隔离)
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.sessions_search")


_DEFAULT_DAYS_BACK = 30
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100
_PREVIEW_CHARS = 200  # 命中行 snippet ±N 字符


def _state_db_path() -> Path | None:
    p = Path.home() / ".hermes" / "state.db"
    return p if p.exists() else None


def _connect_ro() -> sqlite3.Connection | None:
    p = _state_db_path()
    if p is None:
        return None
    try:
        return sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=1.5)
    except sqlite3.Error as e:
        logger.warning("BL-FIX-SESSION-SEARCH: 连 state.db 失败: %s", e)
        return None


def search_messages(
    query: str,
    *,
    days_back: int = _DEFAULT_DAYS_BACK,
    limit: int = _DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """跨 session 搜 messages.content 含 query 的命中行.

    返每条: {session_id, role, snippet (±200 字符上下文), created_at, session_title}.

    LIKE 大小写不敏感 (sqlite ASCII 默认), 中文字面匹配.
    永不抛, 失败返 [].
    """
    if not query or not query.strip():
        return []
    conn = _connect_ro()
    if conn is None:
        return []
    cutoff_ts = (datetime.now() - timedelta(days=max(0, days_back))).timestamp()
    like_pattern = (
        "%"
        + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        + "%"
    )
    limit = max(1, min(_MAX_LIMIT, int(limit)))
    try:
        # BL-FIX-SESSION-SEARCH-SCHEMA (5/24 鸿波"session_search 不可用"): 真实 hermes
        # `messages` 表字段叫 `timestamp` (REAL, unix ts), 不是 `created_at`. 老代码
        # 写 `m.created_at` 一直 silent 失败 ("no such column" → except → 返 []),
        # 测试套自己造了个 created_at schema 让 SQL 通过, production 永远拿不到结果.
        # 权威 schema 来源: companion-app session_write.rs:334 + gateway test_session_history.py:66.
        # LLM-facing 输出 dict 保留 'created_at' / 'created_iso' 命名不变, 只改 SQL.
        rows = conn.execute(
            "SELECT m.session_id, m.role, m.content, m.timestamp, "
            "       (SELECT s.title FROM sessions s WHERE s.id = m.session_id) AS title "
            "FROM messages m "
            "JOIN sessions s ON s.id = m.session_id "
            "WHERE s.started_at >= ? AND m.content LIKE ? ESCAPE '\\' "
            "ORDER BY m.timestamp DESC LIMIT ?",
            (cutoff_ts, like_pattern, limit),
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        logger.warning("BL-FIX-SESSION-SEARCH: search_messages 失败: %s", e)
        return []

    out = []
    for sid, role, content, ts, title in rows:
        snippet = (content or "").strip()
        # 高亮 query 周围 ±N 字符 (跟 gateway sessions_browse 一致)
        idx = snippet.lower().find(query.lower())
        if idx >= 0 and len(snippet) > _PREVIEW_CHARS * 2:
            start = max(0, idx - _PREVIEW_CHARS // 2)
            end = min(len(snippet), idx + len(query) + _PREVIEW_CHARS)
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(snippet) else ""
            snippet = prefix + snippet[start:end] + suffix
        out.append({
            "session_id": sid,
            "session_title": title or "",
            "role": role or "",
            "snippet": snippet,
            # LLM-facing 字段名仍叫 created_at — 是工具对外契约, 不动.
            # 内部 SQL 列名是 timestamp, 见上面注释.
            "created_at": float(ts) if ts else 0.0,
            "created_iso": (
                datetime.fromtimestamp(ts).isoformat()
                if ts else ""
            ),
        })
    return out


def tool_search_sessions(args: dict[str, Any]) -> dict[str, Any]:
    """catfish_search_sessions tool 入口.

    args:
      query: str (必填) — 关键字
      days_back: int (默认 30) — 搜过去几天
      limit: int (默认 20, 上限 100)
    """
    query = (args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "query 必填"}
    try:
        days_back = int(args.get("days_back") or _DEFAULT_DAYS_BACK)
    except (TypeError, ValueError):
        days_back = _DEFAULT_DAYS_BACK
    try:
        limit = int(args.get("limit") or _DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = _DEFAULT_LIMIT

    matches = search_messages(query, days_back=days_back, limit=limit)
    if not matches:
        return {
            "ok": True,
            "matches": [],
            "count": 0,
            "summary": (
                f"🔍 搜 '{query}' 在过去 {days_back} 天的会话: 0 条命中. "
                f"试试: 改关键词 / 加大 days_back / 看 catfish-web /sessions 翻列表"
            ),
        }

    # 按 session 分组, 每组最多 3 条命中
    by_session: dict[str, list[dict]] = {}
    for m in matches:
        sid = m["session_id"]
        by_session.setdefault(sid, []).append(m)

    sess_summary = []
    for sid, ms in by_session.items():
        title = ms[0]["session_title"] or sid[:8]
        when = ms[0]["created_iso"][:16] if ms[0]["created_iso"] else "?"
        sess_summary.append(f"{when} {title} ({len(ms)} 条命中)")

    return {
        "ok": True,
        "count": len(matches),
        "session_count": len(by_session),
        "matches": matches,
        "summary": (
            f"🔍 搜 '{query}': 命中 {len(matches)} 条 (跨 {len(by_session)} 个 session). "
            f"按时间倒序:\n" + "\n".join(f"  - {s}" for s in sess_summary[:10])
        ),
    }


__all__ = ["search_messages", "tool_search_sessions"]
