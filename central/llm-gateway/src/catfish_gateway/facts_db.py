"""BL-Q3-FACT P0 MVP — PG 主存储 / jsonl 兜底层 (5/10).

跟 jsonl 落地双写, 跟 metrics.py / quota.py 同款 `_use_pg()` 模式. 设计文档 §5.

设计原则:
  - PG 失败 → 兜底走 jsonl 写 (不阻塞业务). 跟 mcp-registry storage 同款.
  - 读优先 PG, 没数据再 jsonl. PG 主键 = fact_id, 跟 jsonl 目录名一致.
  - 没配 CATFISH_DB_URL → 纯 jsonl (dev / 离线), 跟其他 service 一致.

跟 facts_router.py 解耦: router 管 HTTP, 这里管存储抽象.
"""

from __future__ import annotations

import json
import logging
import os
import time

from .facts_schema import (
    project_audit_meta,
    project_facts_json,
    project_patch_changes,
)

logger = logging.getLogger("catfish.gateway.facts_db")

# 8/9: 本文件里每一个 `json.dumps(...)` 的第一个参数**必须**是 project_* 调用。
# 这条由 tests/test_facts_pg_write_closed.py 的 AST 闸守着 —— 新加 jsonb 写入
# 点忘了投影会直接红。理由见 facts_schema.py 顶部 (8/8 那个 raw 就是这么进去的)。


def _use_pg() -> bool:
    """有 CATFISH_DB_URL → PG. 跟 metrics.py / quota.py 同款."""
    return bool(os.environ.get("CATFISH_DB_URL", "").strip())


def _pg_conn():
    """psycopg sync 连接, 一次性. 复用 metrics.py 同模板."""
    import psycopg  # 懒 import

    return psycopg.connect(os.environ["CATFISH_DB_URL"])


# ── PG writers (失败返 False 让 caller 走 jsonl 兜底) ────────


def pg_upsert_fact(meta: dict) -> bool:
    if not _use_pg():
        return False
    try:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fact_changes (
                  id, title, original_filename, raw_path, ext, size_bytes,
                  effective_date, uploaded_by, uploaded_at_ms,
                  extracted_at_ms, analyzed_at_ms, approved_at_ms,
                  dismissed_at_ms, dismissed_by, status,
                  facts_json, llm_summary
                ) VALUES (
                  %s, %s, %s, %s, %s, %s,
                  %s, %s, %s,
                  %s, %s, %s,
                  %s, %s, %s,
                  %s::jsonb, %s
                )
                ON CONFLICT (id) DO UPDATE SET
                  title = EXCLUDED.title,
                  status = EXCLUDED.status,
                  extracted_at_ms = COALESCE(EXCLUDED.extracted_at_ms, fact_changes.extracted_at_ms),
                  analyzed_at_ms  = COALESCE(EXCLUDED.analyzed_at_ms,  fact_changes.analyzed_at_ms),
                  approved_at_ms  = COALESCE(EXCLUDED.approved_at_ms,  fact_changes.approved_at_ms),
                  dismissed_at_ms = COALESCE(EXCLUDED.dismissed_at_ms, fact_changes.dismissed_at_ms),
                  dismissed_by    = COALESCE(EXCLUDED.dismissed_by,    fact_changes.dismissed_by),
                  facts_json      = COALESCE(EXCLUDED.facts_json,      fact_changes.facts_json),
                  llm_summary     = COALESCE(EXCLUDED.llm_summary,     fact_changes.llm_summary)
                """,
                (
                    meta["id"], meta.get("title", ""),
                    meta.get("original_filename", ""), meta.get("raw_path", ""),
                    meta.get("ext", ""), int(meta.get("size_bytes", 0)),
                    meta.get("effective_date"),
                    meta.get("uploaded_by", ""), int(meta.get("uploaded_at_ms", 0)),
                    meta.get("extracted_at_ms"), meta.get("analyzed_at_ms"),
                    meta.get("approved_at_ms"),
                    meta.get("dismissed_at_ms"), meta.get("dismissed_by"),
                    meta.get("status", "uploaded"),
                    json.dumps(project_facts_json(meta["facts_json"]))
                    if meta.get("facts_json") else None,
                    meta.get("llm_summary"),
                ),
            )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("pg_upsert_fact 失败 (兜底走 jsonl): %s", e)
        return False


def pg_write_facts_json(fact_id: str, facts_data: dict) -> bool:
    """写 extract 出的事实点 JSON 进 fact_changes.facts_json + llm_summary.

    8/9: 落盘前过 `project_facts_json` —— 约定外的键丢掉 (见 facts_schema.py)。
    """
    if not _use_pg():
        return False
    try:
        projected = project_facts_json(facts_data)
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE fact_changes
                   SET facts_json = %s::jsonb,
                       llm_summary = %s,
                       extracted_at_ms = COALESCE(extracted_at_ms, %s)
                   WHERE id = %s""",
                (
                    json.dumps(projected, ensure_ascii=False),
                    projected.get("summary"),
                    int(time.time() * 1000),
                    fact_id,
                ),
            )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("pg_write_facts_json 失败: %s", e)
        return False


def pg_replace_impacts(fact_id: str, impacts: list[dict]) -> bool:
    """一个 fact 的 impacts 整批替换 (重跑 analyze 时清旧的)."""
    if not _use_pg():
        return False
    try:
        now_ms = int(time.time() * 1000)
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM fact_skill_impacts WHERE fact_change_id = %s", (fact_id,))
            for imp in impacts:
                cur.execute(
                    """INSERT INTO fact_skill_impacts
                       (fact_change_id, fact_point_id, fact_summary,
                        skill_namespace, skill_name, confidence, reason,
                        detection_method, created_at_ms)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        fact_id,
                        imp.get("fact_id"),
                        imp.get("fact_summary"),
                        imp["skill_namespace"], imp["skill_name"],
                        float(imp.get("confidence", 0.0)),
                        imp.get("reason"),
                        imp.get("detection_method", "llm_semantic"),
                        now_ms,
                    ),
                )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("pg_replace_impacts 失败: %s", e)
        return False


def pg_replace_patches(fact_id: str, patches: list[dict]) -> bool:
    """整批替换 patches (重跑 analyze 时清旧的). 注意: 这会**丢失**已 approved patch
    的发布版本记录, 真生产应该只清 pending. P0 简化: 整批替换, 重跑 analyze 前
    UI 上提示员工."""
    if not _use_pg():
        return False
    try:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM fact_skill_patches WHERE fact_change_id = %s", (fact_id,))
            for p in patches:
                cur.execute(
                    """INSERT INTO fact_skill_patches
                       (fact_change_id, fact_point_id, skill_namespace, skill_name,
                        skill_version_base, confidence, rationale, changes_json,
                        full_new_content, status, generated_at_ms,
                        approved_at_ms, approved_by, rejected_at_ms, rejected_by,
                        published_version)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                               %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        fact_id, p.get("fact_id"),
                        p["skill_namespace"], p["skill_name"],
                        p.get("skill_version_base", "1.0"),
                        float(p.get("confidence", 0.0)),
                        p.get("rationale"),
                        json.dumps(project_patch_changes(p.get("changes")), ensure_ascii=False),
                        p.get("full_new_content", ""),
                        p.get("status", "pending"),
                        int(p.get("generated_at_ms", time.time() * 1000)),
                        p.get("approved_at_ms"), p.get("approved_by"),
                        p.get("rejected_at_ms"), p.get("rejected_by"),
                        p.get("published_version"),
                    ),
                )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("pg_replace_patches 失败: %s", e)
        return False


def pg_update_patch_status(
    fact_id: str, patch_idx: int, *, status: str,
    approved_at_ms: int | None = None, approved_by: str | None = None,
    rejected_at_ms: int | None = None, rejected_by: str | None = None,
    published_version: str | None = None,
) -> bool:
    """改单条 patch 状态 (approve / reject 用). idx 按 generated_at_ms 顺序 (跟 jsonl 一致)."""
    if not _use_pg():
        return False
    try:
        with _pg_conn() as conn, conn.cursor() as cur:
            # 按 generated_at_ms 顺序选第 patch_idx 条
            cur.execute(
                """SELECT id FROM fact_skill_patches
                   WHERE fact_change_id = %s
                   ORDER BY generated_at_ms ASC, id ASC
                   OFFSET %s LIMIT 1""",
                (fact_id, patch_idx),
            )
            row = cur.fetchone()
            if not row:
                return False
            patch_pk = row[0]
            cur.execute(
                """UPDATE fact_skill_patches
                   SET status = %s,
                       approved_at_ms = COALESCE(%s, approved_at_ms),
                       approved_by    = COALESCE(%s, approved_by),
                       rejected_at_ms = COALESCE(%s, rejected_at_ms),
                       rejected_by    = COALESCE(%s, rejected_by),
                       published_version = COALESCE(%s, published_version)
                   WHERE id = %s""",
                (status, approved_at_ms, approved_by,
                 rejected_at_ms, rejected_by, published_version, patch_pk),
            )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("pg_update_patch_status 失败: %s", e)
        return False


def pg_audit(fact_id: str, action: str, by_user: str, meta: dict | None = None) -> bool:
    """append 一条操作审计."""
    if not _use_pg():
        return False
    try:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO fact_audit (ts_ms, action, fact_change_id, by_user, meta_json)
                   VALUES (%s, %s, %s, %s, %s::jsonb)""",
                (
                    int(time.time() * 1000),
                    action,
                    fact_id,
                    by_user,
                    json.dumps(project_audit_meta(meta), ensure_ascii=False),
                ),
            )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("pg_audit 失败: %s", e)
        return False


# ── PG readers (查 list / detail) ────────────────────────────


def pg_list_facts(limit: int = 50) -> list[dict] | None:
    """列所有 fact_changes. 返 None 表示 PG 不可用 (caller 兜底走 jsonl)."""
    if not _use_pg():
        return None
    try:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT fc.id, fc.title, fc.original_filename, fc.raw_path, fc.ext,
                          fc.size_bytes, fc.effective_date, fc.uploaded_by,
                          fc.uploaded_at_ms, fc.status,
                          (SELECT COUNT(*) FROM fact_skill_impacts WHERE fact_change_id = fc.id)
                            AS impacts_count,
                          (SELECT COUNT(*) FROM fact_skill_patches WHERE fact_change_id = fc.id)
                            AS patches_count
                   FROM fact_changes fc
                   ORDER BY uploaded_at_ms DESC
                   LIMIT %s""",
                (limit,),
            )
            rows = cur.fetchall()
        result: list[dict] = []
        for r in rows:
            result.append({
                "id": r[0], "title": r[1], "original_filename": r[2],
                "raw_path": r[3], "ext": r[4], "size_bytes": r[5],
                "effective_date": r[6].isoformat() if r[6] else None,
                "uploaded_by": r[7], "uploaded_at_ms": r[8],
                "status": r[9],
                "impacts_count": r[10], "patches_count": r[11],
            })
        return result
    except Exception as e:  # noqa: BLE001
        logger.warning("pg_list_facts 失败 (兜底 jsonl): %s", e)
        return None


def pg_get_fact_detail(fact_id: str) -> dict | None:
    """详情 = meta + facts_json + impacts + patches + audit. 返 None = PG 不可用 / 不存在."""
    if not _use_pg():
        return None
    try:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT id, title, original_filename, raw_path, ext, size_bytes,
                          effective_date, uploaded_by, uploaded_at_ms,
                          extracted_at_ms, analyzed_at_ms, approved_at_ms,
                          dismissed_at_ms, dismissed_by, status, facts_json, llm_summary
                   FROM fact_changes WHERE id = %s""",
                (fact_id,),
            )
            r = cur.fetchone()
            if not r:
                return None
            meta = {
                "id": r[0], "title": r[1], "original_filename": r[2],
                "raw_path": r[3], "ext": r[4], "size_bytes": r[5],
                "effective_date": r[6].isoformat() if r[6] else None,
                "uploaded_by": r[7], "uploaded_at_ms": r[8],
                "extracted_at_ms": r[9], "analyzed_at_ms": r[10],
                "approved_at_ms": r[11],
                "dismissed_at_ms": r[12], "dismissed_by": r[13],
                "status": r[14],
            }
            facts = r[15] if isinstance(r[15], dict) else (json.loads(r[15]) if r[15] else None)

            cur.execute(
                """SELECT fact_point_id, fact_summary, skill_namespace, skill_name,
                          confidence, reason, detection_method
                   FROM fact_skill_impacts
                   WHERE fact_change_id = %s
                   ORDER BY confidence DESC""",
                (fact_id,),
            )
            impacts = [
                {
                    "fact_id": x[0], "fact_summary": x[1],
                    "skill_namespace": x[2], "skill_name": x[3],
                    "confidence": float(x[4]), "reason": x[5],
                    "detection_method": x[6],
                }
                for x in cur.fetchall()
            ]

            cur.execute(
                """SELECT fact_point_id, skill_namespace, skill_name, skill_version_base,
                          confidence, rationale, changes_json, full_new_content, status,
                          generated_at_ms, approved_at_ms, approved_by,
                          rejected_at_ms, rejected_by, published_version
                   FROM fact_skill_patches
                   WHERE fact_change_id = %s
                   ORDER BY generated_at_ms ASC, id ASC""",
                (fact_id,),
            )
            patches = [
                {
                    "fact_id": x[0],
                    "skill_namespace": x[1], "skill_name": x[2],
                    "skill_version_base": x[3], "confidence": float(x[4]) if x[4] is not None else 0,
                    "rationale": x[5],
                    "changes": x[6] if isinstance(x[6], list) else (json.loads(x[6]) if x[6] else []),
                    "full_new_content": x[7],
                    "status": x[8], "generated_at_ms": x[9],
                    "approved_at_ms": x[10], "approved_by": x[11],
                    "rejected_at_ms": x[12], "rejected_by": x[13],
                    "published_version": x[14],
                }
                for x in cur.fetchall()
            ]

            cur.execute(
                """SELECT ts_ms, action, by_user, meta_json
                   FROM fact_audit WHERE fact_change_id = %s
                   ORDER BY ts_ms ASC""",
                (fact_id,),
            )
            audit = [
                {
                    "ts_ms": x[0], "action": x[1], "by_user": x[2],
                    "meta": x[3] if isinstance(x[3], dict) else (json.loads(x[3]) if x[3] else {}),
                }
                for x in cur.fetchall()
            ]

        return {
            "meta": meta,
            "facts": facts,
            "impacts": impacts,
            "patches": patches,
            "audit": audit,
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("pg_get_fact_detail 失败 (兜底 jsonl): %s", e)
        return None


def pg_get_patches(fact_id: str) -> list[dict] | None:
    """单独拉 patches (approve / reject 用). 返 None = PG 不可用."""
    if not _use_pg():
        return None
    try:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT fact_point_id, skill_namespace, skill_name, skill_version_base,
                          confidence, rationale, changes_json, full_new_content, status,
                          generated_at_ms, approved_at_ms, approved_by,
                          rejected_at_ms, rejected_by, published_version
                   FROM fact_skill_patches
                   WHERE fact_change_id = %s
                   ORDER BY generated_at_ms ASC, id ASC""",
                (fact_id,),
            )
            return [
                {
                    "fact_id": x[0],
                    "skill_namespace": x[1], "skill_name": x[2],
                    "skill_version_base": x[3], "confidence": float(x[4]) if x[4] is not None else 0,
                    "rationale": x[5],
                    "changes": x[6] if isinstance(x[6], list) else (json.loads(x[6]) if x[6] else []),
                    "full_new_content": x[7],
                    "status": x[8], "generated_at_ms": x[9],
                    "approved_at_ms": x[10], "approved_by": x[11],
                    "rejected_at_ms": x[12], "rejected_by": x[13],
                    "published_version": x[14],
                }
                for x in cur.fetchall()
            ]
    except Exception as e:  # noqa: BLE001
        logger.warning("pg_get_patches 失败: %s", e)
        return None
