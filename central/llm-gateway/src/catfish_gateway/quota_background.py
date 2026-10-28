"""10/29 — 审计里拆出后台任务 / 网关自身调用 (从 quota_audit 拆出, 那个文件已近 800 行)。

后台任务 = 带 X-Catfish-Internal 头、token 有 background.tasks scope 的调用 (员工本机记忆插件等),
quota_events.background = true: 判超额不算, 审计 / 成本算。网关自己的 internal:* 调用也打这个
标记, 但在审计里一直是单列的 internal_* —— 这里给它按模型拆三段, 好按单价算成本。
"""

from __future__ import annotations

import logging
from typing import Any

from .quota_audit import _build_audit_filter, _model_row
from .quota_base import _get_conn, _pg_conn, _use_pg

logger = logging.getLogger("catfish.gateway.quota")



def background_totals(
    cutoff_ms: int,
    *,
    user_email: str | None = None,
    department: str | None = None,
    filter_model: str | None = None,
    filter_dept: str | None = None,
    filter_user: str | None = None,
) -> dict:
    """时间窗里后台任务 (background) 的请求数和 token, 不含网关自己的 internal:* 调用
    (那部分在 internal_* 里单列)。给审计页「其中后台任务」用。"""
    zero = {"background_request_count": 0, "background_tokens": 0}
    ph = "%s" if _use_pg() else "?"
    ts_col = "ts_ms" if _use_pg() else "ts"
    flag = "background" if _use_pg() else "background = 1"
    where = [f"{ts_col} >= {ph}", flag, "user_email NOT LIKE 'internal:%%'" if _use_pg() else "user_email NOT LIKE 'internal:%'"]
    params: list[Any] = [cutoff_ms]
    if user_email:
        where.append(f"user_email = {ph}")
        params.append(user_email)
    if department:
        where.append(f"department = {ph}")
        params.append(department)
    extra_sql, extra_params = _build_audit_filter(filter_model, filter_dept, filter_user, ph)
    sql = (
        "SELECT COUNT(*), COALESCE(SUM(tokens_in + tokens_out), 0) FROM quota_events WHERE "
        + " AND ".join(where) + extra_sql
    )
    params.extend(extra_params)
    try:
        if _use_pg():
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    row = cur.fetchone()
        else:
            conn = _get_conn()
            row = conn.execute(sql, params).fetchone()
            conn.close()
        return {"background_request_count": int(row[0] or 0), "background_tokens": int(row[1] or 0)}
    except Exception as e:
        logger.warning("background_totals 失败: %s", e)
        return zero


def internal_by_model(cutoff_ms: int) -> list[dict]:
    """网关自己 (internal:*) 调用按模型拆三段 —— 上游照样收钱, 审计页按单价给它算成本。"""
    ph = "%s" if _use_pg() else "?"
    ts_col = "ts_ms" if _use_pg() else "ts"
    like = "'internal:%%'" if _use_pg() else "'internal:%'"
    sql = (
        "SELECT model, COUNT(*), SUM(tokens_in + tokens_out), SUM(tokens_in), SUM(tokens_out), "
        f"SUM(tokens_cache_read) FROM quota_events WHERE {ts_col} >= {ph} AND user_email LIKE {like} "
        "GROUP BY model ORDER BY 3 DESC LIMIT 20"
    )
    try:
        if _use_pg():
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (cutoff_ms,))
                    rows = cur.fetchall()
        else:
            conn = _get_conn()
            rows = conn.execute(sql, (cutoff_ms,)).fetchall()
            conn.close()
        return [_model_row(r) for r in rows]
    except Exception as e:
        logger.warning("internal_by_model 失败: %s", e)
        return []
