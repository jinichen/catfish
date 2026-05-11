"""BL-Q3-ARCHIVE — PG 主存储 + jsonl 兜底 (5/11).

跟 facts_db.py / mcp-registry storage 同 `_use_pg()` 模式. PG 挂时降级 jsonl.

落盘:
  ~/.catfish/tool_archives/<session_id_safe>/<ref>.json

session_id 做安全替换 (邮件含 @ / 路径 / 冒号 — 转 _ 防越权).
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.gateway.tool_archive.db")

#: 落盘根目录
ARCHIVE_DIR = (
    Path(os.environ["CATFISH_TOOL_ARCHIVE_DIR"])
    if os.environ.get("CATFISH_TOOL_ARCHIVE_DIR")
    else Path.home() / ".catfish" / "tool_archives"
)

#: archive 默认保留天数
RETENTION_DAYS = int(os.environ.get("CATFISH_TOOL_ARCHIVE_RETENTION_DAYS", "14"))


def _use_pg() -> bool:
    """有 CATFISH_DB_URL → PG. 跟 facts_db / metrics 同款."""
    return bool(os.environ.get("CATFISH_DB_URL", "").strip())


def _pg_conn():
    """psycopg sync 连接. 复用 facts_db 同模板."""
    import psycopg  # 懒 import

    return psycopg.connect(os.environ["CATFISH_DB_URL"])


def _safe_session_dir(session_id: str) -> str:
    """session_id 转文件系统安全字符串. user@host:conv → user_at_host_conv."""
    return (
        session_id.replace("@", "_at_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
        .replace("..", "_")[:80]
    )


def _jsonl_path(session_id: str, ref: str) -> Path:
    d = ARCHIVE_DIR / _safe_session_dir(session_id)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{ref}.json"


# ── 写: PG 主, 失败 jsonl 兜底 ────────────────────────────────


def upsert_archive(row: dict) -> tuple[bool, str]:
    """upsert 一条 archive. 返 (ok, backend) — backend ∈ {'pg', 'jsonl'}.

    必填字段: ref / session_id / user_email / content / content_bytes / lines.
    """
    if _pg_upsert(row):
        # 双写一份 jsonl 作 PG 灾备 (跟 facts 同思路 — PG 是主, jsonl 是审计兜底)
        try:
            _jsonl_write(row)
        except Exception as e:  # noqa: BLE001
            logger.debug("jsonl 双写失败 (不影响主路径): %s", e)
        return True, "pg"
    # PG 写失败 → 纯 jsonl
    try:
        _jsonl_write(row)
        return True, "jsonl"
    except Exception as e:  # noqa: BLE001
        logger.error("upsert_archive 主备全挂 ref=%s err=%s", row.get("ref"), e)
        return False, "none"


def _pg_upsert(row: dict) -> bool:
    if not _use_pg():
        return False
    try:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tool_archives (
                    ref, session_id, user_email, tool_call_id, tool_name,
                    content, content_bytes, lines,
                    summary, summary_model, summary_at, summary_error,
                    origin_model,
                    created_at, expires_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s,
                    %s, %s
                )
                ON CONFLICT (ref) DO NOTHING
                """,
                (
                    row["ref"],
                    row["session_id"],
                    row["user_email"],
                    row.get("tool_call_id"),
                    row.get("tool_name"),
                    row["content"],
                    int(row["content_bytes"]),
                    int(row["lines"]),
                    row.get("summary"),
                    row.get("summary_model"),
                    row.get("summary_at"),
                    row.get("summary_error"),
                    row.get("origin_model"),
                    row.get("created_at", datetime.now(timezone.utc)),
                    row.get(
                        "expires_at",
                        datetime.now(timezone.utc)
                        + timedelta(days=RETENTION_DAYS),
                    ),
                ),
            )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("pg_upsert tool_archives 失败 (兜底走 jsonl): %s", e)
        return False


def _jsonl_write(row: dict) -> None:
    """jsonl 兜底落盘. created_at / expires_at 转 ISO str."""
    p = _jsonl_path(row["session_id"], row["ref"])
    if p.exists():
        # 幂等: 已有不覆盖 (跟 PG ON CONFLICT DO NOTHING 一致)
        return
    body = dict(row)
    for k in ("created_at", "expires_at", "summary_at"):
        v = body.get(k)
        if isinstance(v, datetime):
            body[k] = v.isoformat()
    if "created_at" not in body or body["created_at"] is None:
        body["created_at"] = datetime.now(timezone.utc).isoformat()
    if "expires_at" not in body or body["expires_at"] is None:
        body["expires_at"] = (
            datetime.now(timezone.utc) + timedelta(days=RETENTION_DAYS)
        ).isoformat()
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


# ── 读: PG 主, miss 走 jsonl ───────────────────────────────────


def get_archive(ref: str) -> dict | None:
    """按 ref 拿 archive 全量. None = 不存在 / 已过期."""
    row = _pg_get(ref)
    if row:
        return row
    return _jsonl_get(ref)


def _pg_get(ref: str) -> dict | None:
    if not _use_pg():
        return None
    try:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT ref, session_id, user_email, tool_call_id, tool_name,
                       content, content_bytes, lines,
                       summary, summary_model, summary_at, summary_error,
                       origin_model,
                       created_at, expires_at
                FROM tool_archives
                WHERE ref = %s AND expires_at > NOW()
                """,
                (ref,),
            )
            r = cur.fetchone()
            if not r:
                return None
            cols = [
                "ref", "session_id", "user_email", "tool_call_id", "tool_name",
                "content", "content_bytes", "lines",
                "summary", "summary_model", "summary_at", "summary_error",
                "origin_model",
                "created_at", "expires_at",
            ]
            return dict(zip(cols, r))
    except Exception as e:  # noqa: BLE001
        logger.warning("pg_get tool_archives 失败 (走 jsonl): %s", e)
        return None


def _jsonl_get(ref: str) -> dict | None:
    """jsonl 兜底 — 扫所有 session_id 目录 (没办法不知道 session). 慢但能用."""
    if not ARCHIVE_DIR.exists():
        return None
    # ref 是 16 字 sha256 前缀, 文件名直接 <ref>.json
    for session_dir in ARCHIVE_DIR.iterdir():
        if not session_dir.is_dir():
            continue
        p = session_dir / f"{ref}.json"
        if p.exists():
            try:
                body = json.loads(p.read_text(encoding="utf-8"))
                # 校验过期
                exp = body.get("expires_at")
                if exp and isinstance(exp, str):
                    try:
                        exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
                        if exp_dt < datetime.now(timezone.utc):
                            return None
                    except ValueError:
                        pass
                return body
            except Exception as e:  # noqa: BLE001
                logger.warning("jsonl_get parse 失败 ref=%s: %s", ref, e)
                return None
    return None


# ── 摘要 worker 用的批扫 / 更新 ────────────────────────────────


def pick_unsummarized(limit: int = 10) -> list[dict]:
    """PG 优先扫. PG 没 / 挂 → jsonl 扫.

    返回每条只含 ref / content / tool_name (摘要器够用了).
    """
    if _use_pg():
        try:
            with _pg_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ref, content, tool_name, origin_model
                    FROM tool_archives
                    WHERE summary IS NULL AND summary_error IS NULL
                    ORDER BY created_at
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()
                return [
                    {
                        "ref": r[0],
                        "content": r[1],
                        "tool_name": r[2],
                        "origin_model": r[3],
                    }
                    for r in rows
                ]
        except Exception as e:  # noqa: BLE001
            logger.warning("pg pick_unsummarized 失败: %s", e)

    # jsonl 兜底 — 扫文件
    out: list[dict] = []
    if not ARCHIVE_DIR.exists():
        return out
    for session_dir in sorted(ARCHIVE_DIR.iterdir()):
        if not session_dir.is_dir():
            continue
        for p in session_dir.iterdir():
            if not p.name.endswith(".json"):
                continue
            try:
                body = json.loads(p.read_text(encoding="utf-8"))
                if body.get("summary") or body.get("summary_error"):
                    continue
                out.append({
                    "ref": body["ref"],
                    "content": body["content"],
                    "tool_name": body.get("tool_name"),
                    "origin_model": body.get("origin_model"),
                })
                if len(out) >= limit:
                    return out
            except Exception:  # noqa: BLE001
                continue
    return out


def update_summary(
    ref: str,
    *,
    summary: str | None,
    model: str | None = None,
    error: str | None = None,
) -> bool:
    """summary IS NOT NULL 或 summary_error 都算"已处理过"."""
    now = datetime.now(timezone.utc)
    pg_ok = _pg_update_summary(ref, summary=summary, model=model, error=error, at=now)
    jsonl_ok = _jsonl_update_summary(ref, summary=summary, model=model, error=error, at=now)
    return pg_ok or jsonl_ok


def _pg_update_summary(ref, *, summary, model, error, at) -> bool:
    if not _use_pg():
        return False
    try:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE tool_archives
                SET summary = %s,
                    summary_model = %s,
                    summary_at = %s,
                    summary_error = %s
                WHERE ref = %s
                """,
                (summary, model, at, error, ref),
            )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("pg update_summary 失败 ref=%s: %s", ref, e)
        return False


def _jsonl_update_summary(ref, *, summary, model, error, at) -> bool:
    if not ARCHIVE_DIR.exists():
        return False
    for session_dir in ARCHIVE_DIR.iterdir():
        if not session_dir.is_dir():
            continue
        p = session_dir / f"{ref}.json"
        if not p.exists():
            continue
        try:
            body = json.loads(p.read_text(encoding="utf-8"))
            body["summary"] = summary
            body["summary_model"] = model
            body["summary_at"] = at.isoformat() if at else None
            body["summary_error"] = error
            p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("jsonl update_summary 失败 ref=%s: %s", ref, e)
    return False


# ── GC: 过期清理 ───────────────────────────────────────────────


def gc_expired() -> int:
    """删过期 archive. 返删除条数."""
    n_pg = _pg_gc()
    n_jsonl = _jsonl_gc()
    return n_pg + n_jsonl


def _pg_gc() -> int:
    if not _use_pg():
        return 0
    try:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM tool_archives WHERE expires_at < NOW()")
            n = cur.rowcount or 0
            conn.commit()
        if n > 0:
            logger.info("PG GC: 删 %d 条过期 archive", n)
        return n
    except Exception as e:  # noqa: BLE001
        logger.warning("pg_gc 失败: %s", e)
        return 0


def _jsonl_gc() -> int:
    if not ARCHIVE_DIR.exists():
        return 0
    deleted = 0
    now = datetime.now(timezone.utc)
    for session_dir in ARCHIVE_DIR.iterdir():
        if not session_dir.is_dir():
            continue
        for p in session_dir.iterdir():
            if not p.name.endswith(".json"):
                continue
            try:
                body = json.loads(p.read_text(encoding="utf-8"))
                exp = body.get("expires_at")
                if exp and isinstance(exp, str):
                    exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
                    if exp_dt < now:
                        p.unlink()
                        deleted += 1
            except Exception:  # noqa: BLE001
                continue
    if deleted > 0:
        logger.info("jsonl GC: 删 %d 条过期 archive", deleted)
    return deleted


__all__ = [
    "ARCHIVE_DIR",
    "RETENTION_DAYS",
    "upsert_archive",
    "get_archive",
    "pick_unsummarized",
    "update_summary",
    "gc_expired",
]
