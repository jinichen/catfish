"""catfish session_search router — 替换 hermes builtin session_search tool.

# P3.4.B (6/15 鸿波: session_search 76s 优化)

## 真因 (从 ~/.hermes/logs/agent.log + ~/.hermes/hermes-agent/tools/session_search_tool.py audit)

hermes 原生 session_search 的 `_discover` mode 实际跑:
  1. db.search_messages(query, limit=50) — FTS5 主搜 (本身快, ~50ms)
  2. **50 条 raw 命中, 每条调 _resolve_to_parent 走 parent_session_id 链**
     — 每条 ~3-5 次 db.get_session, 累计 150-250 次 SQL (~2-5 秒)
  3. dedupe 到 limit=3 个 lineage_root
  4. **每个 lineage 调 db.get_anchored_view(window=5, bookend=3) 拿 ±5 message + 6 bookend**
     — 11 messages/session × 3 sessions = ~33 messages
     — 鸿波 messages 表最大 content 80-92KB (是历次 session_search 自己的 tool result
       叠加, mode=discover JSON), 单条 anchored_view 可能拉几百 KB
  5. JSON serialize 返 40910 chars

实测鸿波 6/15 跑 76.22s. 不是 SQL 慢 (FTS5 索引在), 是数据组装 + 大 content 序列化.

## 设计

catfish 已有 `catfish_search_sessions` (sessions_search.py) — 单 SQL LIKE 限 30 天 +
limit 20, 1.5s timeout, **实测 340ms** (10024 行 messages). 不返 ±5 window / bookend,
但 advisor agent loop 只需要 snippet 推理, 不需要这些重型 feature.

monkey-patch hermes session_search → 内部根据 mode 路由:
  - discovery (有 query, 无 session_id) → catfish 快版 (340ms)
  - scroll / read / browse → fallback hermes 原生 (那俩快, 不需要换)

## schema 兼容

ctx.register_tool(override=True) 让 LLM 看到我们的 schema. 必须保留 hermes
原 4 mode 的 args (query / session_id / around_message_id / limit / window /
profile / role_filter / sort), 否则 LLM 调 scroll/read 会 fail.

输出严格保留 hermes _discover entry 字段 (session_id / when / source / model /
title / matched_role / match_message_id / snippet / bookend_start / messages /
bookend_end / messages_before / messages_after), 简化的字段值返 [] / 0 / "" 占位.
LLM 拿到字段不会 missing error, 内容仍可用 snippet 推理.

## fallback

catfish 快版异常 → fallback hermes 原生 (不阻塞用户).
hermes 原生异常 → 返 success=False (跟 hermes 原行为一致).

## 性能预期

advisor agent loop 一次 session_search 调用: 76s → ~400ms (catfish 340ms + schema 转换 ~50ms).
agent loop 总耗时: 1:30-3:00 → ~30-60s.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger("catfish.xcatfish_user.session_search_router")


# ─── inline catfish 快版 search (源自 catfish/edge/tool-bridge/src/catfish_tool_bridge/sessions_search.py) ─

# catfish_tool_bridge 是独立进程 (跟 hermes 走 unix socket JSON-RPC),
# 不在 hermes venv sys.path. 不能 import, 直接 inline 算法.

_DEFAULT_DAYS_BACK = 30
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
        logger.warning("session_search_router 连 state.db 失败: %s", e)
        return None


def _search_messages_inline(
    query: str,
    days_back: int = _DEFAULT_DAYS_BACK,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """跨 session 搜 messages.content. 跟 catfish sessions_search.py 同算法.

    返每条: {session_id, session_title, role, snippet (±200 字符上下文), created_iso}.
    LIKE 大小写不敏感, 中文字面匹配. 永不抛, 失败返 [].
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
    limit = max(1, min(100, int(limit)))
    try:
        # schema 权威: hermes messages.timestamp (REAL unix ts), 不是 created_at.
        # 见 catfish/edge/tool-bridge/src/catfish_tool_bridge/sessions_search.py
        # P3.4.B 同 audit (5/24 鸿波撞过 created_at silent 失败).
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
        logger.warning("session_search_router search_messages 失败: %s", e)
        return []

    out: List[Dict[str, Any]] = []
    for sid, role, content, ts, title in rows:
        snippet = (content or "").strip()
        # 高亮 query 周围 ±N 字符 (跟 catfish sessions_search 一致)
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
            "created_iso": datetime.fromtimestamp(ts).isoformat() if ts else "",
        })
    return out


# ─── schema (LLM 看的) ────────────────────────────────────────────────────
# 保留 hermes 原 session_search 4 mode 的全部 args, 不破坏 LLM 现有调用习惯.

CATFISH_SESSION_SEARCH_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Discovery mode: 关键字搜跨 session 历史消息. catfish 快版 (~340ms vs "
                "hermes 原生 76s). 默认搜过去 30 天, top 20 命中按 session 分组."
            ),
        },
        "session_id": {
            "type": "string",
            "description": (
                "Scroll/Read mode: 指定 session id. 配合 around_message_id 是 scroll, "
                "单独是 read. 这两个 mode 走 hermes 原生 (catfish 快版不接管)."
            ),
        },
        "around_message_id": {
            "type": "integer",
            "description": "Scroll mode: 锚点 message id. 走 hermes 原生.",
        },
        "limit": {
            "type": "integer",
            "description": "Discovery: 返回 session 数 (1-10, 默认 3). Browse: 同.",
        },
        "window": {
            "type": "integer",
            "description": "Scroll mode: ±window 消息 (1-20, 默认 5). 走 hermes 原生.",
        },
        "sort": {
            "type": "string",
            "description": "Discovery sort 选项. catfish 快版按 timestamp desc 固定.",
        },
        "profile": {
            "type": "string",
            "description": "Cross-profile 读其他 profile 的 sessions. 走 hermes 原生.",
        },
        "role_filter": {
            "type": "string",
            "description": "Discovery 过滤 role, e.g. 'user,assistant'. 默认 user+assistant.",
        },
    },
    "required": [],
}


# ─── 主入口 (ctx.register_tool 的 handler) ────────────────────────────────

def handle_session_search(args: Dict[str, Any], **kw: Any) -> str:
    """catfish session_search 路由 handler.

    路由:
      - Discovery (query 非空 + session_id 空) → catfish 快版 340ms
      - Scroll (session_id + around_message_id)  → hermes 原生
      - Read (session_id 非空, 无 around)        → hermes 原生
      - Browse (无 query 无 session_id)          → hermes 原生

    返 JSON string (跟 hermes 原 session_search 同接口).
    """
    query = args.get("query")
    query = query.strip() if isinstance(query, str) else ""

    session_id = args.get("session_id")
    session_id = session_id.strip() if isinstance(session_id, str) else ""

    around_msg = args.get("around_message_id")

    # Scroll / Read / Browse / cross-profile → hermes 原生
    if session_id or around_msg is not None or args.get("profile"):
        return _call_hermes_original(args, **kw)
    if not query:
        return _call_hermes_original(args, **kw)

    # Discovery → catfish 快版
    try:
        return _discovery_via_catfish(query, args)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "catfish session_search 快版异常, fallback hermes 原生: %s", e, exc_info=True
        )
        return _call_hermes_original(args, **kw)


# ─── discovery 走 catfish 快版 ─────────────────────────────────────────────

def _discovery_via_catfish(query: str, args: Dict[str, Any]) -> str:
    """catfish 快版 discovery, 输出 hermes _discover schema.

    算法 inline 自 catfish edge/tool-bridge/src/catfish_tool_bridge/sessions_search.py
    (不能 import — catfish_tool_bridge 是独立进程, 不在 hermes venv sys.path).
    单 SQL LIKE 查 ~/.hermes/state.db messages 表, 限 30 天 + limit, ~340ms 实测.
    """
    limit = args.get("limit", 3)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 3
    limit = max(1, min(limit, 10))

    # catfish 快版多取一些 (5x), 后按 session 分组 trim 到 limit. 上限 50 安全.
    raw_limit = min(50, limit * 5)
    matches = _search_messages_inline(query, days_back=30, limit=raw_limit)

    if not matches:
        return json.dumps({
            "success": True,
            "mode": "discover",
            "query": query,
            "results": [],
            "count": 0,
            "message": "No matching sessions found.",
            "_catfish_fast": True,
        }, ensure_ascii=False)

    # 按 session 分组取第一个 hit (跟 hermes lineage dedupe 同 spirit, 简化版)
    by_session: Dict[str, Dict[str, Any]] = {}
    for m in matches:
        sid = m.get("session_id", "")
        if sid and sid not in by_session:
            by_session[sid] = m
        if len(by_session) >= limit:
            break

    # 转 hermes _discover entry schema. 缺失字段 (bookend / messages window)
    # 返空 []/0 占位 — LLM 看 results 拿到 fields 不会 KeyError, snippet 够推理.
    results = []
    for sid, m in by_session.items():
        when = m.get("created_iso", "")
        if when:
            when = when[:16].replace("T", " ")  # ISO → "YYYY-MM-DD HH:MM" 给 LLM 友好
        else:
            when = "unknown"
        results.append({
            "session_id": sid,
            "when": when,
            "source": "",  # catfish 不返 source
            "model": "",   # catfish 不返 model
            "title": m.get("session_title") or None,
            "matched_role": m.get("role", ""),
            "match_message_id": None,  # catfish 无 message_id, LLM 不能 scroll
            "snippet": m.get("snippet", ""),
            "bookend_start": [],  # 简化, 不返 ±3 bookend
            "messages": [],       # 简化, 不返 ±5 window
            "bookend_end": [],
            "messages_before": 0,
            "messages_after": 0,
        })

    return json.dumps({
        "success": True,
        "mode": "discover",
        "query": query,
        "results": results,
        "count": len(results),
        "sessions_searched": len(by_session),
        "_catfish_fast": True,  # debug marker (LLM 可忽略)
        "_catfish_note": (
            "catfish 快版 — 不返 bookend / ±5 messages window. 要这些 detail 用 "
            "scroll mode (传 session_id + around_message_id, 走 hermes 原生)."
        ),
    }, ensure_ascii=False)


# ─── fallback hermes 原生 ─────────────────────────────────────────────────

def _call_hermes_original(args: Dict[str, Any], **kw: Any) -> str:  # noqa: ARG001
    """fallback hermes 原生 tools.session_search_tool.session_search.

    用 importlib 懒加载避免 import-time 循环依赖.
    异常返 success=False (跟 hermes 原行为一致, 不抛).
    """
    try:
        from tools.session_search_tool import session_search as _orig
        return _orig(**args)
    except TypeError as e:
        # args 含 hermes 不认识的 key (e.g. catfish 加的) → 过滤再试一次
        try:
            allowed = {
                "query", "role_filter", "limit", "db", "current_session_id",
                "session_id", "around_message_id", "window", "sort", "profile",
            }
            filtered = {k: v for k, v in args.items() if k in allowed}
            from tools.session_search_tool import session_search as _orig
            return _orig(**filtered)
        except Exception as e2:  # noqa: BLE001
            logger.exception("hermes 原生 session_search fallback 挂: %s", e2)
            return json.dumps({
                "success": False,
                "error": f"session_search 不可用: TypeError {e}, retry {e2}",
            }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        logger.exception("hermes 原生 session_search 调用挂: %s", e)
        return json.dumps({
            "success": False,
            "error": f"session_search 不可用: {e}",
        }, ensure_ascii=False)


__all__ = [
    "CATFISH_SESSION_SEARCH_SCHEMA",
    "handle_session_search",
]
