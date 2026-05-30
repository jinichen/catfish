"""BL-FILE-SESSION-INDEX-V1 attachments 元数据 + 反向索引工具.

# 历史决策反转 (5/30 鸿波)

Phase 2 (5/30 早) 我自己写了 BM25 sidecar 跨会话搜附件内容. **过度工程** —
local_search (catfish-local-search MCP server) 早就有 FTS5 trigram + bm25
能力, 跨文件搜远比我们的 subprocess BM25 helper 稳.

Phase 4 (5/30 晚 鸿波拍板) 把 `~/.catfish/uploads/` 加进 local_search 默认
search-scope. uploads 文件被 local_search 一并索引, **内容搜归一到 local_search**.

本文件**只保留两项 attachments.db 独有的能力**:
  1. 按名字搜 (name LIKE) + 拿到 session_id (LLM 反查 "在哪个会话提到的")
  2. 反向索引 (list_by_user_grouped) — "我上传过的所有 Excel" + 每个文件出现在哪些会话

# 内容搜怎么走

LLM 应调 local_search(query="xxx") — 它自然 cover 员工本机 + Companion uploads.
schema description 已引导.

# 设计原则

  - 中央 0 红线 — attachments.db 在边缘 ~/.catfish/, 不上中央
  - user_id 强制隔离 — query 必带 WHERE user_id = ?
  - 永不抛 — db 不存在/损坏返空 + 友好 summary, LLM 看到接得住
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.attachments_search")


_DEFAULT_DAYS_BACK = 90
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100


def _db_path() -> Path | None:
    catfish_home = os.environ.get("CATFISH_HOME", "").strip()
    base = Path(catfish_home).expanduser() if catfish_home else Path.home() / ".catfish"
    p = base / "attachments.db"
    return p if p.exists() else None


def _connect_ro() -> sqlite3.Connection | None:
    p = _db_path()
    if p is None:
        return None
    try:
        return sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=1.5)
    except sqlite3.Error as e:
        logger.warning("BL-FILE-SESSION-INDEX-V1: 连 attachments.db 失败: %s", e)
        return None


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row[0],
        "session_id": row[1],
        "message_id": row[2],
        "kind": row[3],
        "file_kind": row[4],
        "name": row[5],
        "mime_type": row[6],
        "size_bytes": row[7],
        "kept_path": row[8],
        "parsed_text_path": row[9],
        "meta": row[10],
        "created_at": row[11],
        "created_iso": datetime.fromtimestamp(row[11]).isoformat() if row[11] else "",
    }


_SELECT_COLS = (
    "id, session_id, message_id, kind, file_kind, name, mime_type, "
    "size_bytes, kept_path, parsed_text_path, meta, created_at"
)


def search_by_name(
    user_id: str,
    query: str,
    *,
    days_back: int = _DEFAULT_DAYS_BACK,
    limit: int = _DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """按附件名 LIKE 搜 (跨会话, 限定 user_id).

    永不抛, 失败返 [].
    """
    if not user_id.strip() or not query.strip():
        return []
    conn = _connect_ro()
    if conn is None:
        return []
    cutoff = (datetime.now() - timedelta(days=max(0, days_back))).timestamp()
    pattern = (
        "%"
        + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        + "%"
    )
    limit = max(1, min(_MAX_LIMIT, int(limit)))
    try:
        rows = conn.execute(
            f"SELECT {_SELECT_COLS} FROM attachments "
            f"WHERE user_id = ? AND name LIKE ? ESCAPE '\\' AND created_at >= ? "
            f"ORDER BY created_at DESC LIMIT ?",
            (user_id, pattern, cutoff, limit),
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        logger.warning("attachments search_by_name 失败: %s", e)
        return []
    return [_row_to_dict(r) for r in rows]


def tool_search_attachments(args: dict[str, Any]) -> dict[str, Any]:
    """catfish_search_attachments tool 入口.

    Phase 4 (5/30): 只剩 name mode (按文件名搜). content mode 退役,
    内容搜走 local_search (catfish-local-search MCP).

    args:
      user_id: str (必填) — 限定员工
      query: str (必填) — 关键字 (匹配文件名)
      days_back: int (默认 90)
      limit: int (默认 20, 上限 100)
    """
    user_id = (args.get("user_id") or "").strip()
    if not user_id:
        return {"ok": False, "error": "user_id 必填 — 防止跨员工串数据"}
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

    matches = search_by_name(user_id, query, days_back=days_back, limit=limit)
    if not matches:
        return {
            "ok": True,
            "matches": [],
            "count": 0,
            "summary": (
                f"🔍 员工 {user_id} 上传过的附件**文件名**含 '{query}' (过去 {days_back} 天): 0 条命中. "
                f"💡 想搜文件**内容**? 调 local_search(query='{query}') — 走 FTS5 全文索引, "
                f"覆盖员工 Documents/Desktop/Downloads + 上传到鲶鱼的附件."
            ),
        }

    by_kind: dict[str, int] = {}
    for m in matches:
        fk = m.get("file_kind") or m.get("kind") or "?"
        by_kind[fk] = by_kind.get(fk, 0) + 1
    kind_summary = ", ".join(f"{k}: {v}" for k, v in sorted(by_kind.items()))

    return {
        "ok": True,
        "count": len(matches),
        "matches": matches,
        "summary": (
            f"🔍 员工 {user_id} 上传过的附件文件名含 '{query}': 命中 {len(matches)} 条 "
            f"({kind_summary}). 每条带 session_id 可反查会话. "
            f"💡 想看附件全文内容 → catfish_read_file(path=kept_path) 或 local_search(query='{query}')."
        ),
    }


# ─────────────────────────────────────────────────────────────────────────
# Phase 3 (5/30): 反向索引 — 文件 → 会话
# ─────────────────────────────────────────────────────────────────────────

def list_by_user_grouped(
    user_id: str,
    *,
    days_back: int = _DEFAULT_DAYS_BACK,
    limit: int = 50,
    file_kind: str | None = None,
) -> list[dict[str, Any]]:
    """按文件名去重, 每条带 sessions list (这个文件出现在哪些会话).

    给"列我所有上传过的文件 + 用过哪些会话"场景. 跟 search_by_name 不同 —
    那个返每条记录 (重复文件可能出现多次), 这个按 name 聚合.
    """
    if not user_id.strip():
        return []
    conn = _connect_ro()
    if conn is None:
        return []
    cutoff = (datetime.now() - timedelta(days=max(0, days_back))).timestamp()
    limit = max(1, min(500, int(limit)))
    try:
        if file_kind:
            rows = conn.execute(
                f"SELECT {_SELECT_COLS} FROM attachments "
                f"WHERE user_id = ? AND file_kind = ? AND created_at >= ? "
                f"ORDER BY created_at DESC",
                (user_id, file_kind, cutoff),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {_SELECT_COLS} FROM attachments "
                f"WHERE user_id = ? AND created_at >= ? "
                f"ORDER BY created_at DESC",
                (user_id, cutoff),
            ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        logger.warning("attachments list_by_user_grouped 失败: %s", e)
        return []

    # 按 name 聚合
    by_name: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = _row_to_dict(r)
        name = d["name"]
        if name not in by_name:
            by_name[name] = {
                "name": name,
                "file_kind": d["file_kind"],
                "kind": d["kind"],
                "mime_type": d["mime_type"],
                "first_seen": d["created_at"],
                "last_seen": d["created_at"],
                "first_seen_iso": d["created_iso"],
                "last_seen_iso": d["created_iso"],
                "reference_count": 0,
                "sessions": [],
                "kept_path": d["kept_path"],
                "parsed_text_path": d["parsed_text_path"],
                "size_bytes": d["size_bytes"],
            }
        g = by_name[name]
        g["reference_count"] += 1
        if d["session_id"] not in {s["session_id"] for s in g["sessions"]}:
            g["sessions"].append({
                "session_id": d["session_id"],
                "first_seen_iso": d["created_iso"],
            })
        if d["created_at"] < g["first_seen"]:
            g["first_seen"] = d["created_at"]
            g["first_seen_iso"] = d["created_iso"]
        if d["created_at"] > g["last_seen"]:
            g["last_seen"] = d["created_at"]
            g["last_seen_iso"] = d["created_iso"]

    out = sorted(by_name.values(), key=lambda x: x["last_seen"], reverse=True)
    return out[:limit]


def tool_list_my_attachments(args: dict[str, Any]) -> dict[str, Any]:
    """catfish_list_my_attachments tool 入口.

    args:
      user_id: str (必填)
      file_kind: str (可选) — 'pdf' / 'xlsx' / ... filter
      days_back: int (默认 90)
      limit: int (默认 50, 上限 500)

    返每条文件 + 它被引用过的所有 session_id (反向索引).
    """
    user_id = (args.get("user_id") or "").strip()
    if not user_id:
        return {"ok": False, "error": "user_id 必填"}
    file_kind = (args.get("file_kind") or "").strip() or None
    try:
        days_back = int(args.get("days_back") or _DEFAULT_DAYS_BACK)
    except (TypeError, ValueError):
        days_back = _DEFAULT_DAYS_BACK
    try:
        limit = int(args.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50

    files = list_by_user_grouped(
        user_id, days_back=days_back, limit=limit, file_kind=file_kind,
    )
    if not files:
        kind_filter = f" (file_kind={file_kind})" if file_kind else ""
        return {
            "ok": True,
            "count": 0,
            "files": [],
            "summary": (
                f"📚 员工 {user_id} 过去 {days_back} 天{kind_filter} 没上传过附件. "
                f"💡 想看员工硬盘所有文档? 调 local_search."
            ),
        }

    kind_count: dict[str, int] = {}
    for f in files:
        fk = f.get("file_kind") or f.get("kind") or "?"
        kind_count[fk] = kind_count.get(fk, 0) + 1
    kind_summary = ", ".join(f"{k}: {v}" for k, v in sorted(kind_count.items()))
    top_files = files[:5]
    top_summary = "\n".join(
        f"  - {f['name']} ({f['reference_count']} 次提及, 跨 {len(f['sessions'])} 会话)"
        for f in top_files
    )
    return {
        "ok": True,
        "count": len(files),
        "files": files,
        "summary": (
            f"📚 员工 {user_id} 过去 {days_back} 天附件: {len(files)} 个 "
            f"({kind_summary}). 按最近使用倒序, top 5:\n{top_summary}"
        ),
    }


__all__ = [
    "search_by_name",
    "list_by_user_grouped",
    "tool_search_attachments",
    "tool_list_my_attachments",
]
