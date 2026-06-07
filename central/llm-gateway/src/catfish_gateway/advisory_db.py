"""Advisory PG 存储 — 6/7 BL-MANIFESTO-ADVISORY-PHASE2.

Spec: docs/ADVISORY-FEED-SPEC.md §6.1

# 设计

跟 facts_db.py / metrics.py 同 _use_pg() 模式. 区别:

- advisory 是**中央 publish** 内容 (不是员工数据), PG 失败不走 jsonl 兜底,
  直接 fail loud — admin 看到错误自己处理
- yaml seed: 没 PG / PG 没数据 → fallback yaml (Phase 1 demo 仍然能展示)

# Schema (跟 alembic 20260607_005_advisories.py 一致)

advisories(
  id, severity, category, title, description, recommendation,
  target_json, references_json, tags_json, remediation_actions_json,
  published, expires, published_by, revoked_at, revoked_by
)

# 跟 manifesto 公理 4 关系

advisory 表持久化的是**中央 publish 给员工的安全/升级/政策建议**, 不是员工
对话内容. 中央存合规 (类似 npm advisories / GitHub security advisories
中央数据库). 员工对话 / skill / wiki 仍然 0 出端.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("catfish.gateway.advisory_db")


def use_pg() -> bool:
    """有 CATFISH_DB_URL → PG. 否则 dev / yaml-only 模式 (Phase 1 兼容)."""
    return bool(os.environ.get("CATFISH_DB_URL", "").strip())


def _pg_conn():
    """psycopg sync 连接. 跟 facts_db.py 同模板."""
    import psycopg  # noqa: PLC0415

    return psycopg.connect(os.environ["CATFISH_DB_URL"])


def _row_to_advisory(row: tuple[Any, ...], cols: list[str]) -> dict[str, Any]:
    """PG row → advisory dict (跟 yaml schema 一致)."""
    d: dict[str, Any] = dict(zip(cols, row, strict=False))
    # JSON 字段 PG 已经解了 (JSONB), 但 None 兜底
    advisory: dict[str, Any] = {
        "id": d["id"],
        "severity": d["severity"],
        "category": d["category"],
        "title": d["title"],
        "published": _iso(d["published"]),
        "published_by": d["published_by"],
    }
    if d.get("description"):
        advisory["description"] = d["description"]
    if d.get("recommendation"):
        advisory["recommendation"] = d["recommendation"]
    if d.get("target_json"):
        advisory["target"] = d["target_json"]
    if d.get("references_json"):
        advisory["references"] = d["references_json"]
    if d.get("tags_json"):
        advisory["tags"] = d["tags_json"]
    if d.get("remediation_actions_json"):
        advisory["remediation_actions"] = d["remediation_actions_json"]
    if d.get("expires"):
        advisory["expires"] = _iso(d["expires"])
    if d.get("revoked_at"):
        advisory["revoked_at"] = _iso(d["revoked_at"])
        advisory["revoked_by"] = d.get("revoked_by")
    return advisory


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# ── 读 ──────────────────────────────────────────────────────────


def pg_list_active() -> list[dict[str, Any]]:
    """列出**未 revoked + 未 expired** 的 advisory. feed.json 用."""
    if not use_pg():
        return []
    cols = [
        "id", "severity", "category", "title", "description", "recommendation",
        "target_json", "references_json", "tags_json", "remediation_actions_json",
        "published", "expires", "published_by",
    ]
    try:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {", ".join(cols)}
                FROM advisories
                WHERE revoked_at IS NULL
                  AND (expires IS NULL OR expires > NOW())
                ORDER BY published DESC
                """,
            )
            rows = cur.fetchall()
        return [_row_to_advisory(r, cols) for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.warning("pg_list_active 失败 (返空 list): %s", e)
        return []


def pg_list_all(include_revoked: bool = True) -> list[dict[str, Any]]:
    """列出全部 advisory (含 revoked). admin 看 dashboard 用."""
    if not use_pg():
        return []
    cols = [
        "id", "severity", "category", "title", "description", "recommendation",
        "target_json", "references_json", "tags_json", "remediation_actions_json",
        "published", "expires", "published_by", "revoked_at", "revoked_by",
    ]
    where = "" if include_revoked else "WHERE revoked_at IS NULL"
    try:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {", ".join(cols)}
                FROM advisories {where}
                ORDER BY published DESC
                """,
            )
            rows = cur.fetchall()
        return [_row_to_advisory(r, cols) for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.warning("pg_list_all 失败: %s", e)
        return []


def pg_get(advisory_id: str) -> dict[str, Any] | None:
    if not use_pg():
        return None
    cols = [
        "id", "severity", "category", "title", "description", "recommendation",
        "target_json", "references_json", "tags_json", "remediation_actions_json",
        "published", "expires", "published_by", "revoked_at", "revoked_by",
    ]
    try:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(cols)} FROM advisories WHERE id = %s",
                (advisory_id,),
            )
            row = cur.fetchone()
        return _row_to_advisory(row, cols) if row else None
    except Exception as e:  # noqa: BLE001
        logger.warning("pg_get 失败: %s", e)
        return None


# ── 写 (admin only) ────────────────────────────────────────────


def pg_insert(advisory: dict[str, Any], published_by: str) -> None:
    """新建 advisory. 调用方保证 advisory 字段合法 (router 端 pydantic 验过).

    raise: psycopg.Error — admin 看到错误自己重试 / 修字段
    """
    if not use_pg():
        raise RuntimeError("CATFISH_DB_URL 没设, advisory publish 必须 PG 模式")
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO advisories (
              id, severity, category, title, description, recommendation,
              target_json, references_json, tags_json, remediation_actions_json,
              published, expires, published_by
            ) VALUES (
              %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s
            )
            """,
            (
                advisory["id"],
                advisory["severity"],
                advisory["category"],
                advisory["title"],
                advisory.get("description"),
                advisory.get("recommendation"),
                json.dumps(advisory["target"]) if advisory.get("target") else None,
                json.dumps(advisory["references"]) if advisory.get("references") else None,
                json.dumps(advisory["tags"]) if advisory.get("tags") else None,
                json.dumps(advisory["remediation_actions"])
                if advisory.get("remediation_actions") else None,
                advisory["published"],
                advisory.get("expires"),
                published_by,
            ),
        )


def pg_revoke(advisory_id: str, revoked_by: str) -> bool:
    """改 revoked_at + revoked_by. 不真删 (审计历史保留).

    Returns: True 找到并 revoke, False 不存在 / 已 revoke.
    """
    if not use_pg():
        raise RuntimeError("CATFISH_DB_URL 没设, advisory revoke 必须 PG 模式")
    now = datetime.now(timezone.utc)
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE advisories
            SET revoked_at = %s, revoked_by = %s
            WHERE id = %s AND revoked_at IS NULL
            """,
            (now, revoked_by, advisory_id),
        )
        return cur.rowcount > 0


__all__ = [
    "use_pg",
    "pg_list_active",
    "pg_list_all",
    "pg_get",
    "pg_insert",
    "pg_revoke",
]
