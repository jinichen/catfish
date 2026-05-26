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
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
    """upsert 一条 archive. 返 (ok, backend) — 5/26 后 backend 永远 'jsonl'.

    必填字段: ref / session_id / user_email / content / content_bytes / lines.

    # 5/26 真隐私修: 砍 PG 路径

    老逻辑: PG 写 content + jsonl 双写灾备. PG 那条把 **archive content** (大 tool
    output: browser_screenshot base64 PNG / email_search 邮件内容 / browser_snapshot
    HTML 等) 写到**中央 PG** → 违反 "中央只看 metadata" 承诺. content 是员工业务
    数据, 不该上中央. 5/26 audit 抓到 (比 5/25 aggregator vision 更严重, 因为
    aggregator 是 transit 不落盘, PG upsert 真持久化中央).

    新逻辑: jsonl-only. content 永远员工 mac (~/.catfish/tool_archives/). PG schema
    不再写 (老 PG 数据保留作历史, get_archive 也不再读 PG, 全走 jsonl).
    """
    try:
        _jsonl_write(row)
        return True, "jsonl"
    except Exception as e:  # noqa: BLE001
        logger.error("upsert_archive jsonl 写挂 ref=%s err=%s", row.get("ref"), e)
        return False, "none"


def _pg_upsert(row: dict) -> bool:
    """5/26 砍 — 真隐私违规 (PG 写 content 上中央). 留 stub 防回归."""
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
        body["created_at"] = datetime.now(UTC).isoformat()
    if "expires_at" not in body or body["expires_at"] is None:
        body["expires_at"] = (
            datetime.now(UTC) + timedelta(days=RETENTION_DAYS)
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
    """5/26 砍 — 跟 _pg_upsert 同批 (PG 不再存 content). 留 stub 防回归."""
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
                        if exp_dt < datetime.now(UTC):
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
    """jsonl-only (5/26 砍 PG 路径). 扫 ~/.catfish/tool_archives 找未摘要的.

    返回每条只含 ref / content / tool_name (摘要器够用了).
    """
    # 5/26: PG 路径砍 (PG 不再存 content), 整个走 jsonl.
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
    now = datetime.now(UTC)
    pg_ok = _pg_update_summary(ref, summary=summary, model=model, error=error, at=now)
    jsonl_ok = _jsonl_update_summary(ref, summary=summary, model=model, error=error, at=now)
    return pg_ok or jsonl_ok


def _pg_update_summary(ref, *, summary, model, error, at) -> bool:
    """5/26 砍 — 跟 _pg_upsert 同批 (PG 不再存 content 也不存 summary). 留 stub."""
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
    """5/26 砍 — PG 不再存 archive, GC 也没意义. 留 stub."""
    return 0


def _jsonl_gc() -> int:
    if not ARCHIVE_DIR.exists():
        return 0
    deleted = 0
    now = datetime.now(UTC)
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
