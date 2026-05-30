"""BL-FILE-SESSION-INDEX-V1 Phase 2 (5/30) — catfish_search_attachments tool.

跨会话搜员工上传过的附件 (PDF / Excel / Word / 图片 / 音频).

# 真问题
catfish_search_sessions (BL-FIX-SESSION-SEARCH) 只搜对话 content (messages.content
里的文字), 完全不搜附件. 用户问"上次客户 X 的 PDF 里说啥" / "我上传过的 Excel
里关于 Y 的内容" 全都搜不到 — 因为附件内容不在 messages.content.

# 怎么做
两层搜:
1. **名字搜** (按 attachments.name LIKE) — 快, 找 "客户合同_v3.pdf" 这种
2. **内容搜** (走每个匹配附件的 BM25 sidecar) — 准, 找 PDF 里某段话

Phase 1 (Companion 端 Rust) 已经把附件 metadata 写到 ~/.catfish/attachments.db
含 kept_path / parsed_text_path. 本工具直读 sqlite + 跑 BM25 sidecar.

# 数据源
~/.catfish/attachments.db (read-only, Companion 写) — 跟 sessions_search 同源风格:
- read-only 连接 (mode=ro)
- 永不抛 (失败返空 + 友好 summary)
- LIKE 转义防 wildcard
- user_id 隔离 — 必须指定 user_id, 不串其它员工

# BM25 复用
Companion 已经把每个大文件 (≥50KB) 解析时写了 .parsed.txt sidecar (BL-L26).
本工具直接调跟 file_parse.rs:attachment_bm25_search 用的同 Python helper
(attachment_bm25.py), 不要 Companion 中转 — 直接读 sidecar 跑 BM25.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.attachments_search")


_DEFAULT_DAYS_BACK = 90
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100
_BM25_TOP_K_PER_FILE = 3   # 每个匹配附件取 top-N BM25 段落
_BM25_TIMEOUT_SEC = 5      # 单个文件 BM25 计算上限


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


def _find_bm25_helper() -> Path | None:
    """按优先级查 attachment_bm25.py helper 真实路径.

    Companion 装到 mac 后路径跟 dev 不同, 走 3 候选:
      1. env CATFISH_BM25_HELPER (显式覆盖)
      2. ~/.catfish/scripts/attachment_bm25.py (生产装位置, 由 Companion 安装时拷)
      3. ~/person_task/catfish/edge/companion-app/src-tauri/scripts/attachment_bm25.py (dev)

    都没找到返 None — caller 不要 BM25, 退化到名字搜.
    """
    env_path = os.environ.get("CATFISH_BM25_HELPER", "").strip()
    if env_path:
        p = Path(env_path).expanduser()
        if p.exists():
            return p
    candidates = [
        Path.home() / ".catfish" / "scripts" / "attachment_bm25.py",
        Path.home() / "person_task" / "catfish" / "edge" / "companion-app" / "src-tauri" / "scripts" / "attachment_bm25.py",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _bm25_search_sidecar(sidecar_path: str, query: str, top_k: int = _BM25_TOP_K_PER_FILE) -> list[dict[str, Any]]:
    """调 attachment_bm25.py helper 跑 BM25, 返 top-K 段落.

    复用 Companion file_parse.rs 用的同 Python helper (单进程 stateless).
    失败返 [] (不抛).
    """
    helper = _find_bm25_helper()
    if helper is None:
        logger.debug("attachment_bm25.py helper 找不到, 跳过内容搜 (仅名字搜可用)")
        return []
    if not Path(sidecar_path).exists():
        return []
    try:
        proc = subprocess.run(
            [
                "python3", str(helper),
                "--text-path", sidecar_path,
                "--query", query,
                "--top-k", str(top_k),
            ],
            capture_output=True,
            text=True,
            timeout=_BM25_TIMEOUT_SEC,
        )
        if proc.returncode != 0:
            logger.debug("BM25 helper 非 0 退出 (%s): %s", sidecar_path, proc.stderr[:200])
            return []
        data = json.loads(proc.stdout)
        return data.get("passages", []) or []
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        logger.debug("BM25 search 失败 (%s): %s", sidecar_path, e)
        return []


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


def _all_attachments_for_user(
    user_id: str,
    *,
    days_back: int = _DEFAULT_DAYS_BACK,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """列员工所有附件 (按时间倒序). 用于内容搜的候选 — 先取 N 个最近, 再跑 BM25."""
    if not user_id.strip():
        return []
    conn = _connect_ro()
    if conn is None:
        return []
    cutoff = (datetime.now() - timedelta(days=max(0, days_back))).timestamp()
    limit = max(1, min(500, int(limit)))
    try:
        rows = conn.execute(
            f"SELECT {_SELECT_COLS} FROM attachments "
            f"WHERE user_id = ? AND created_at >= ? "
            f"ORDER BY created_at DESC LIMIT ?",
            (user_id, cutoff, limit),
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        logger.warning("attachments _all_attachments_for_user 失败: %s", e)
        return []
    return [_row_to_dict(r) for r in rows]


def search_by_content(
    user_id: str,
    query: str,
    *,
    days_back: int = _DEFAULT_DAYS_BACK,
    limit: int = _DEFAULT_LIMIT,
    candidate_pool: int = 100,
) -> list[dict[str, Any]]:
    """内容搜: 取最近 candidate_pool 个附件, 对每个有 parsed_text_path 的跑 BM25.

    返 [{ ...attachment metadata, bm25_passages: [{text, score}, ...] }]
    只返有 BM25 命中的. score 倒序.
    """
    if not user_id.strip() or not query.strip():
        return []
    candidates = _all_attachments_for_user(user_id, days_back=days_back, limit=candidate_pool)
    if not candidates:
        return []
    out: list[dict[str, Any]] = []
    for att in candidates:
        sidecar = att.get("parsed_text_path") or ""
        if not sidecar:
            continue
        passages = _bm25_search_sidecar(sidecar, query)
        if not passages:
            continue
        top_score = max((p.get("score", 0.0) for p in passages), default=0.0)
        out.append({
            **att,
            "bm25_passages": passages,
            "top_score": top_score,
        })
    out.sort(key=lambda x: x.get("top_score", 0.0), reverse=True)
    return out[:limit]


def tool_search_attachments(args: dict[str, Any]) -> dict[str, Any]:
    """catfish_search_attachments tool 入口.

    args:
      user_id: str (必填) — 限定员工
      query: str (必填) — 关键字
      mode: 'name' | 'content' | 'both' (默认 'both')
      days_back: int (默认 90) — 搜过去几天
      limit: int (默认 20, 上限 100)
    """
    user_id = (args.get("user_id") or "").strip()
    if not user_id:
        return {"ok": False, "error": "user_id 必填 — 防止跨员工串数据"}
    query = (args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "query 必填"}
    mode = (args.get("mode") or "both").lower()
    if mode not in ("name", "content", "both"):
        mode = "both"
    try:
        days_back = int(args.get("days_back") or _DEFAULT_DAYS_BACK)
    except (TypeError, ValueError):
        days_back = _DEFAULT_DAYS_BACK
    try:
        limit = int(args.get("limit") or _DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = _DEFAULT_LIMIT

    by_name: list[dict[str, Any]] = []
    by_content: list[dict[str, Any]] = []
    if mode in ("name", "both"):
        by_name = search_by_name(user_id, query, days_back=days_back, limit=limit)
    if mode in ("content", "both"):
        by_content = search_by_content(user_id, query, days_back=days_back, limit=limit)

    # 合并去重 (按 attachment id), 内容搜结果优先 (有 bm25_passages 更有用)
    merged: dict[str, dict[str, Any]] = {}
    for m in by_content:
        merged[m["id"]] = m
    for m in by_name:
        if m["id"] not in merged:
            merged[m["id"]] = m
    matches = list(merged.values())[:limit]

    if not matches:
        return {
            "ok": True,
            "matches": [],
            "count": 0,
            "summary": (
                f"🔍 搜员工 {user_id} 上传过的附件含 '{query}' (过去 {days_back} 天): 0 条命中. "
                f"试试: 改关键词 / 加大 days_back / 用 catfish_list_my_outputs 看 AI 产出"
            ),
        }

    # 摘要 — 按 file_kind 分组列
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
            f"🔍 搜员工 {user_id} 上传过的附件含 '{query}': 命中 {len(matches)} 条 "
            f"({kind_summary}). 按相关度倒序. 每条带 session_id / kept_path / "
            f"bm25_passages (如有). 想看全文调 catfish_read_file(path=kept_path)."
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

    # 按 name 聚合, 每组合并 sessions list
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
                # 最大的那条记录作 representative (拿 kept_path)
                "kept_path": d["kept_path"],
                "parsed_text_path": d["parsed_text_path"],
                "size_bytes": d["size_bytes"],
            }
        g = by_name[name]
        g["reference_count"] += 1
        # session 去重 (同 session 多次提到同名文件算 1 次)
        if d["session_id"] not in {s["session_id"] for s in g["sessions"]}:
            g["sessions"].append({
                "session_id": d["session_id"],
                "first_seen_iso": d["created_iso"],
            })
        # 更新 first/last seen
        if d["created_at"] < g["first_seen"]:
            g["first_seen"] = d["created_at"]
            g["first_seen_iso"] = d["created_iso"]
        if d["created_at"] > g["last_seen"]:
            g["last_seen"] = d["created_at"]
            g["last_seen_iso"] = d["created_iso"]

    # 按 last_seen 倒序 (最近用的在前)
    out = sorted(by_name.values(), key=lambda x: x["last_seen"], reverse=True)
    return out[:limit]


def tool_list_my_attachments(args: dict[str, Any]) -> dict[str, Any]:
    """catfish_list_my_attachments tool 入口.

    args:
      user_id: str (必填) — 限定员工
      file_kind: str (可选) — 'pdf' / 'xlsx' / 'docx' / ... filter
      days_back: int (默认 90)
      limit: int (默认 50, 上限 500)

    返每条文件 + 它被引用过的所有 session_id, 给 LLM 做反向索引用 ('这个 PDF 在哪些会话里').
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
                f"用户上传过附件后再调本工具."
            ),
        }

    # 摘要 — 列 top-5 + 总数 + kind 分布
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
    "search_by_content",
    "list_by_user_grouped",
    "tool_search_attachments",
    "tool_list_my_attachments",
]
