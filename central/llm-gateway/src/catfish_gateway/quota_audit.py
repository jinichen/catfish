"""配额用量的聚合查询 —— 按人 / 按部门 / 全局。

2026-08-15 从 quota.py 切出来 (730 行, 是原文件里最大的一块)。
纯搬迁, 逻辑一行未改。

PG / sqlite 双 backend, where 子句的 placeholder 不同 —— 这也是这一整块最
容易出错的地方, 所以它们集中在一起而不是散在各处。
"""
from __future__ import annotations

from typing import Any

import logging

logger = logging.getLogger("catfish.gateway.quota")

from .quota_base import _get_conn, _pg_conn, _use_pg

def _sum_tokens(where_clause_sqlite: str, where_clause_pg: str, params: tuple[Any, ...]) -> int:
    """通用 sum 查询. PG / sqlite 双 backend, where 子句 placeholder 不同 (? vs %s).

    cutoff_ms 在 params 里. PG 走 ts_ms 字段, sqlite 老 schema 字段名 ts.
    """
    if _use_pg():
        try:
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT COALESCE(SUM(tokens_in + tokens_out), 0) "
                        f"FROM quota_events WHERE {where_clause_pg}",
                        params,
                    )
                    row = cur.fetchone()
            return int((row[0] if row else 0) or 0)
        except Exception as e:
            logger.warning("_sum_tokens PG 失败 (where=%s): %s", where_clause_pg, e)
            return 0

    try:
        conn = _get_conn()
        cur = conn.execute(
            f"SELECT COALESCE(SUM(tokens_in + tokens_out), 0) "
            f"FROM quota_events WHERE {where_clause_sqlite}",
            params,
        )
        result = cur.fetchone()[0]
        conn.close()
        return int(result or 0)
    except Exception as e:
        logger.warning("_sum_tokens sqlite 失败 (where=%s): %s", where_clause_sqlite, e)
        return 0


def sum_tokens_user_since(user_email: str, cutoff_ms: int) -> int:
    return _sum_tokens(
        "user_email = ? AND ts >= ?",
        "user_email = %s AND ts_ms >= %s",
        (user_email, cutoff_ms),
    )


def sum_tokens_model_since(model: str, cutoff_ms: int) -> int:
    return _sum_tokens(
        "model = ? AND ts >= ?",
        "model = %s AND ts_ms >= %s",
        (model, cutoff_ms),
    )


def audit_summary_user_since(user_email: str, cutoff_ms: int) -> dict:
    """单员工的 audit 聚合 — 给 /api/audit/me 用 (员工自查 "中央到底存了我啥").

    跟 audit_summary_dept_since 同套路, 区别:
      - 按 user_email 过滤 (不是 department)
      - 不返 by_user (按定义就一个员工自己)
      - 加 first_seen_ts / last_seen_ts (员工想知道"中央从哪天开始有我的记录")

    返:
      - request_count: 我今日请求总数
      - total_tokens:  我今日 token 总数
      - by_model:      [{model, count, total_tokens}]
      - first_seen_ts / last_seen_ts: 中央这个员工的首/末次记录 ms
                                       (不局限 cutoff, 反映"中央到底存了多久")

    Privacy contract:
      返的字段全是 metadata (count / token / model / 时间戳), 没有对话内容.
      audit/me 是给员工自查"中央存了我啥", 跟客户买 catfish 时承诺的
      "中央只看 metadata, 不看 prompt/response" 一致.
    """
    empty = {
        "request_count": 0,
        "total_tokens": 0,
        "by_model": [],
        "first_seen_ts": None,
        "last_seen_ts": None,
    }

    if _use_pg():
        try:
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT COUNT(*), COALESCE(SUM(tokens_in + tokens_out), 0)
                           FROM quota_events WHERE user_email = %s AND ts_ms >= %s""",
                        (user_email, cutoff_ms),
                    )
                    row = cur.fetchone()
                    request_count = int(row[0] or 0)
                    total_tokens = int(row[1] or 0)

                    cur.execute(
                        """SELECT model, COUNT(*) AS cnt, SUM(tokens_in + tokens_out) AS used
                           FROM quota_events WHERE user_email = %s AND ts_ms >= %s
                           GROUP BY model ORDER BY used DESC LIMIT 20""",
                        (user_email, cutoff_ms),
                    )
                    by_model = [
                        {"model": r[0], "count": int(r[1]), "total_tokens": int(r[2] or 0)}
                        for r in cur.fetchall()
                    ]

                    cur.execute(
                        """SELECT MIN(ts_ms), MAX(ts_ms) FROM quota_events
                           WHERE user_email = %s""",
                        (user_email,),
                    )
                    fmin, fmax = cur.fetchone() or (None, None)

            return {
                "request_count": request_count,
                "total_tokens": total_tokens,
                "by_model": by_model,
                "first_seen_ts": int(fmin) if fmin else None,
                "last_seen_ts": int(fmax) if fmax else None,
            }
        except Exception as e:
            logger.warning("audit_summary_user_since PG 失败: %s", e)
            return empty

    try:
        conn = _get_conn()
        cur = conn.execute(
            """SELECT COUNT(*), COALESCE(SUM(tokens_in + tokens_out), 0)
               FROM quota_events WHERE user_email = ? AND ts >= ?""",
            (user_email, cutoff_ms),
        )
        row = cur.fetchone()
        request_count = int(row[0] or 0)
        total_tokens = int(row[1] or 0)

        cur = conn.execute(
            """SELECT model, COUNT(*) AS cnt, SUM(tokens_in + tokens_out) AS used
               FROM quota_events WHERE user_email = ? AND ts >= ?
               GROUP BY model ORDER BY used DESC LIMIT 20""",
            (user_email, cutoff_ms),
        )
        by_model = [
            {"model": r[0], "count": int(r[1]), "total_tokens": int(r[2] or 0)}
            for r in cur.fetchall()
        ]

        cur = conn.execute(
            """SELECT MIN(ts), MAX(ts) FROM quota_events
               WHERE user_email = ?""",
            (user_email,),
        )
        fmin, fmax = cur.fetchone() or (None, None)

        conn.close()
        return {
            "request_count": request_count,
            "total_tokens": total_tokens,
            "by_model": by_model,
            "first_seen_ts": int(fmin) if fmin else None,
            "last_seen_ts": int(fmax) if fmax else None,
        }
    except Exception as e:
        logger.warning("audit_summary_user_since sqlite 失败: %s", e)
        return empty


def sum_tokens_dept_since(department: str, cutoff_ms: int) -> int:
    return _sum_tokens(
        "department = ? AND ts >= ?",
        "department = %s AND ts_ms >= %s",
        (department, cutoff_ms),
    )


# ── 部门级聚合 (manager / admin Dashboard 用) ─────────────────
#
# 五一 sprint 5/2 RBAC: manager 看本部门 quota / audit, admin 全权.
# 双 backend (PG 主, sqlite 兜底). 五一 sprint 5/2 收尾加 PG.


def top_users_in_department(
    department: str, cutoff_ms: int, limit: int = 10
) -> list[dict]:
    """部门内 top N 员工今日 token 用量 (从大到小). PG / sqlite 双 backend."""
    if _use_pg():
        try:
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT user_email, SUM(tokens_in + tokens_out) AS used
                           FROM quota_events
                           WHERE department = %s AND ts_ms >= %s
                           GROUP BY user_email
                           ORDER BY used DESC
                           LIMIT %s""",
                        (department, cutoff_ms, limit),
                    )
                    rows = cur.fetchall()
            return [{"user_email": r[0], "tokens_used": int(r[1] or 0)} for r in rows]
        except Exception as e:
            logger.warning("top_users_in_department PG 失败: %s", e)
            return []

    try:
        conn = _get_conn()
        cur = conn.execute(
            """SELECT user_email, SUM(tokens_in + tokens_out) AS used
               FROM quota_events
               WHERE department = ? AND ts >= ?
               GROUP BY user_email
               ORDER BY used DESC
               LIMIT ?""",
            (department, cutoff_ms, limit),
        )
        rows = cur.fetchall()
        conn.close()
        return [{"user_email": r[0], "tokens_used": int(r[1] or 0)} for r in rows]
    except Exception as e:
        logger.warning("top_users_in_department sqlite 失败: %s", e)
        return []


def audit_summary_dept_since(department: str, cutoff_ms: int) -> dict:
    """部门级 audit 聚合 — 给 /api/audit/department/{dept}. PG / sqlite 双 backend.

    返:
      - request_count: 部门今日请求总数
      - total_tokens:  部门今日 token 总数
      - by_model:      [{model, count, total_tokens}]
      - by_user:       [{user_email, count, total_tokens}] (top 10)
    """
    empty = {
        "request_count": 0,
        "total_tokens": 0,
        "by_model": [],
        "by_user": [],
    }

    if _use_pg():
        try:
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT COUNT(*), COALESCE(SUM(tokens_in + tokens_out), 0)
                           FROM quota_events WHERE department = %s AND ts_ms >= %s""",
                        (department, cutoff_ms),
                    )
                    row = cur.fetchone()
                    request_count = int(row[0] or 0)
                    total_tokens = int(row[1] or 0)

                    cur.execute(
                        """SELECT model, COUNT(*) AS cnt, SUM(tokens_in + tokens_out) AS used
                           FROM quota_events WHERE department = %s AND ts_ms >= %s
                           GROUP BY model ORDER BY used DESC LIMIT 20""",
                        (department, cutoff_ms),
                    )
                    by_model = [
                        {"model": r[0], "count": int(r[1]), "total_tokens": int(r[2] or 0)}
                        for r in cur.fetchall()
                    ]

                    cur.execute(
                        """SELECT user_email, COUNT(*) AS cnt, SUM(tokens_in + tokens_out) AS used
                           FROM quota_events WHERE department = %s AND ts_ms >= %s
                           GROUP BY user_email ORDER BY used DESC LIMIT 10""",
                        (department, cutoff_ms),
                    )
                    by_user = [
                        {"user_email": r[0], "count": int(r[1]), "total_tokens": int(r[2] or 0)}
                        for r in cur.fetchall()
                    ]
            return {
                "request_count": request_count,
                "total_tokens": total_tokens,
                "by_model": by_model,
                "by_user": by_user,
            }
        except Exception as e:
            logger.warning("audit_summary_dept_since PG 失败: %s", e)
            return empty

    try:
        conn = _get_conn()
        cur = conn.execute(
            """SELECT COUNT(*), COALESCE(SUM(tokens_in + tokens_out), 0)
               FROM quota_events WHERE department = ? AND ts >= ?""",
            (department, cutoff_ms),
        )
        row = cur.fetchone()
        request_count = int(row[0] or 0)
        total_tokens = int(row[1] or 0)

        cur = conn.execute(
            """SELECT model, COUNT(*) AS cnt, SUM(tokens_in + tokens_out) AS used
               FROM quota_events WHERE department = ? AND ts >= ?
               GROUP BY model ORDER BY used DESC LIMIT 20""",
            (department, cutoff_ms),
        )
        by_model = [
            {"model": r[0], "count": int(r[1]), "total_tokens": int(r[2] or 0)}
            for r in cur.fetchall()
        ]

        cur = conn.execute(
            """SELECT user_email, COUNT(*) AS cnt, SUM(tokens_in + tokens_out) AS used
               FROM quota_events WHERE department = ? AND ts >= ?
               GROUP BY user_email ORDER BY used DESC LIMIT 10""",
            (department, cutoff_ms),
        )
        by_user = [
            {"user_email": r[0], "count": int(r[1]), "total_tokens": int(r[2] or 0)}
            for r in cur.fetchall()
        ]

        conn.close()
        return {
            "request_count": request_count,
            "total_tokens": total_tokens,
            "by_model": by_model,
            "by_user": by_user,
        }
    except Exception as e:
        logger.warning("audit_summary_dept_since sqlite 失败: %s", e)
        return empty


# ── 全局聚合 (admin Dashboard 用) ────────────────────────────
#
# 五一 sprint 5/2 RBAC: admin 看全员/全部门/全模型. 双 backend (PG / sqlite).


def top_departments(cutoff_ms: int, limit: int = 10) -> list[dict]:
    """全局 top N 部门今日 token 用量. PG / sqlite 双 backend."""
    if _use_pg():
        try:
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT department, COUNT(*) AS req, SUM(tokens_in + tokens_out) AS tokens
                           FROM quota_events
                           WHERE ts_ms >= %s AND department <> ''
                           GROUP BY department
                           ORDER BY tokens DESC
                           LIMIT %s""",
                        (cutoff_ms, limit),
                    )
                    rows = cur.fetchall()
            return [
                {"department": r[0], "request_count": int(r[1] or 0), "tokens_used": int(r[2] or 0)}
                for r in rows
            ]
        except Exception as e:
            logger.warning("top_departments PG 失败: %s", e)
            return []

    try:
        conn = _get_conn()
        cur = conn.execute(
            """SELECT department, COUNT(*) AS req, SUM(tokens_in + tokens_out) AS tokens
               FROM quota_events
               WHERE ts >= ? AND department != ''
               GROUP BY department
               ORDER BY tokens DESC
               LIMIT ?""",
            (cutoff_ms, limit),
        )
        rows = cur.fetchall()
        conn.close()
        return [
            {"department": r[0], "request_count": int(r[1] or 0), "tokens_used": int(r[2] or 0)}
            for r in rows
        ]
    except Exception as e:
        logger.warning("top_departments sqlite 失败: %s", e)
        return []


def _build_audit_filter(
    model: str | None,
    dept: str | None,
    user_email: str | None,
    placeholder: str,
) -> tuple[str, list[Any]]:
    """BL-AUDIT-UX-P2 (5/17): 把 drill-down filter 转 SQL 片段.

    用 COALESCE 兼容 '(未分组)' / '(未分组员工)' 合成桶名 (跟 by_dept / by_user
    显示一致). placeholder 是 '%s' (PG) 或 '?' (sqlite).
    返 (sql_extra, params_extra) — caller 拼到 WHERE 后, 参数追加到 query.
    """
    extras: list[str] = []
    params: list[Any] = []
    if model:
        extras.append(f"AND model = {placeholder}")
        params.append(model)
    if dept:
        # 兼容 '(未分组)' 桶 — 选这桶时匹配空字符串 + NULL
        extras.append(
            f"AND COALESCE(NULLIF(department, ''), '(未分组)') = {placeholder}"
        )
        params.append(dept)
    if user_email:
        extras.append(
            f"AND COALESCE(NULLIF(user_email, ''), '(未分组员工)') = {placeholder}"
        )
        params.append(user_email)
    return (" " + " ".join(extras) if extras else "", params)


def audit_period_totals(
    start_ms: int,
    end_ms: int,
    *,
    filter_model: str | None = None,
    filter_dept: str | None = None,
    filter_user: str | None = None,
) -> dict:
    """BL-AUDIT-UX-P1 (5/17): 时间窗内 top-level 数字 (no by_* breakdown).

    给 trend ↑↓ vs 上期对照用 — 当前期跟上期同样查一遍, 前端做差算 % 变化.

    BL-AUDIT-UX-P2 (5/17): 加 drill-down filter 参数 — 上期 trend 跟当前期同
    filter 才有意义 (不然 "model=X 这期 ↑20%" 跟 "model=* 上期" 比毫无意义).

    Returns: {request_count, total_tokens, active_users, active_departments}.
    """
    empty = {
        "request_count": 0,
        "total_tokens": 0,
        "active_users": 0,
        "active_departments": 0,
    }
    if _use_pg():
        try:
            extra_sql, extra_params = _build_audit_filter(
                filter_model, filter_dept, filter_user, "%s",
            )
            sql = (
                f"""SELECT COUNT(*),
                          COALESCE(SUM(tokens_in + tokens_out), 0),
                          COUNT(DISTINCT user_email),
                          COUNT(DISTINCT department)
                            FILTER (WHERE department <> '')
                   FROM quota_events
                   WHERE ts_ms >= %s AND ts_ms < %s
                     AND user_email NOT LIKE 'internal:%%'{extra_sql}"""
            )
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (start_ms, end_ms, *extra_params))
                    row = cur.fetchone()
            return {
                "request_count": int(row[0] or 0),
                "total_tokens": int(row[1] or 0),
                "active_users": int(row[2] or 0),
                "active_departments": int(row[3] or 0),
            }
        except Exception as e:
            logger.warning("audit_period_totals PG 失败: %s", e)
            return empty

    # sqlite (test 路径)
    try:
        extra_sql, extra_params = _build_audit_filter(
            filter_model, filter_dept, filter_user, "?",
        )
        # 注: sqlite 路径仍保留 department != '' (老 schema 兼容). 加 dept filter
        # 时 COALESCE 已经处理空 dept 合成桶, 不冲突.
        sql = (
            f"""SELECT COUNT(*),
                      COALESCE(SUM(tokens_in + tokens_out), 0),
                      COUNT(DISTINCT user_email),
                      COUNT(DISTINCT department)
               FROM quota_events
               WHERE ts >= ? AND ts < ?
                 AND user_email NOT LIKE 'internal:%'
                 AND department != ''{extra_sql}"""
        )
        conn = _get_conn()
        cur = conn.execute(sql, (start_ms, end_ms, *extra_params))
        row = cur.fetchone()
        conn.close()
        return {
            "request_count": int(row[0] or 0),
            "total_tokens": int(row[1] or 0),
            "active_users": int(row[2] or 0),
            "active_departments": int(row[3] or 0),
        }
    except Exception as e:
        logger.warning("audit_period_totals sqlite 失败: %s", e)
        return empty


def audit_summary_global_since(
    cutoff_ms: int,
    *,
    filter_model: str | None = None,
    filter_dept: str | None = None,
    filter_user: str | None = None,
) -> dict:
    """全局聚合 — admin /api/audit/global 用. 跟 audit_summary_dept_since 同结构, 不限部门.

    BL-AUDIT-UX-P2 (5/17): 加 drill-down filter (model/dept/user). 应用到 top-level
    totals + by_model + by_dept + by_user. **不应用到 internal_loopback** (loopback
    跟员工筛选无关, 它就是 gateway 自己的消耗).
    """
    empty = {
        "request_count": 0,
        "total_tokens": 0,
        "active_users": 0,
        "active_departments": 0,
        "by_model": [],
        "by_department": [],
        "by_user": [],
        # BL-AUDIT-INTERNAL-SPLIT (5/17): internal loopback 单独算
        "internal_request_count": 0,
        "internal_tokens": 0,
    }

    # BL-AUDIT-INTERNAL-SPLIT (5/17): SQL 过滤 user_email NOT LIKE 'internal:%' —
    # internal:gateway-loopback / internal:summarizer / 等内部循环 token 单独算
    # internal_*, 不混进员工业务总览. 防 sysadmin 误以为业务用量 1.87x.
    if _use_pg():
        try:
            extra_sql_pg, extra_params_pg = _build_audit_filter(
                filter_model, filter_dept, filter_user, "%s",
            )
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""SELECT COUNT(*), COALESCE(SUM(tokens_in + tokens_out), 0),
                                  COUNT(DISTINCT user_email),
                                  COUNT(DISTINCT department) FILTER (WHERE department <> '')
                           FROM quota_events
                           WHERE ts_ms >= %s AND user_email NOT LIKE 'internal:%%'{extra_sql_pg}""",
                        (cutoff_ms, *extra_params_pg),
                    )
                    row = cur.fetchone()
                    request_count = int(row[0] or 0)
                    total_tokens = int(row[1] or 0)
                    active_users = int(row[2] or 0)
                    active_departments = int(row[3] or 0)

                    # internal: 单独算, drill-down filter 不影响 (loopback 跟员工
                    # 筛选无关). audit transparency: sysadmin 看 gateway 内部循环
                    # 消耗多少 — 5 维 inject / summarizer / proactive 等.
                    cur.execute(
                        """SELECT COUNT(*), COALESCE(SUM(tokens_in + tokens_out), 0)
                           FROM quota_events
                           WHERE ts_ms >= %s AND user_email LIKE 'internal:%%'""",
                        (cutoff_ms,),
                    )
                    irow = cur.fetchone()
                    internal_request_count = int(irow[0] or 0)
                    internal_tokens = int(irow[1] or 0)

                    cur.execute(
                        f"""SELECT model, COUNT(*) AS cnt, SUM(tokens_in + tokens_out) AS tk
                           FROM quota_events
                           WHERE ts_ms >= %s AND user_email NOT LIKE 'internal:%%'{extra_sql_pg}
                           GROUP BY model ORDER BY tk DESC LIMIT 20""",
                        (cutoff_ms, *extra_params_pg),
                    )
                    by_model = [
                        {"model": r[0], "count": int(r[1]), "total_tokens": int(r[2] or 0)}
                        for r in cur.fetchall()
                    ]

                    # BL-AUDIT-P0-FIX (5/17): 同时聚合空 dept 桶, 不再吞 — 否则
                    # 总请求 405 vs 按部门加和 117 这种"数据消失" bug 让客户立刻
                    # 不信任所有数字. 用 COALESCE 把空 dept 统一标 '(未分组)'.
                    cur.execute(
                        f"""SELECT COALESCE(NULLIF(department, ''), '(未分组)') AS dept,
                                  COUNT(*) AS cnt,
                                  SUM(tokens_in + tokens_out) AS tk
                           FROM quota_events
                           WHERE ts_ms >= %s AND user_email NOT LIKE 'internal:%%'{extra_sql_pg}
                           GROUP BY dept ORDER BY tk DESC LIMIT 20""",
                        (cutoff_ms, *extra_params_pg),
                    )
                    by_department = [
                        {"department": r[0], "count": int(r[1]), "total_tokens": int(r[2] or 0)}
                        for r in cur.fetchall()
                    ]

                    # BL-AUDIT-P0-FIX (5/17): LIMIT 10 → 50, 防 top 50 但只显示
                    # 几个的"数据消失"印象. user_email 空时也归 (未分组员工).
                    cur.execute(
                        f"""SELECT COALESCE(NULLIF(user_email, ''), '(未分组员工)') AS ue,
                                  COALESCE(NULLIF(department, ''), '(未分组)') AS dept,
                                  COUNT(*) AS cnt,
                                  SUM(tokens_in + tokens_out) AS tk
                           FROM quota_events
                           WHERE ts_ms >= %s AND user_email NOT LIKE 'internal:%%'{extra_sql_pg}
                           GROUP BY ue, dept ORDER BY tk DESC LIMIT 50""",
                        (cutoff_ms, *extra_params_pg),
                    )
                    by_user = [
                        {
                            "user_email": r[0],
                            "department": r[1],
                            "count": int(r[2]),
                            "total_tokens": int(r[3] or 0),
                        }
                        for r in cur.fetchall()
                    ]
            return {
                "request_count": request_count,
                "total_tokens": total_tokens,
                "active_users": active_users,
                "active_departments": active_departments,
                "by_model": by_model,
                "by_department": by_department,
                "by_user": by_user,
                # BL-AUDIT-INTERNAL-SPLIT (5/17): internal loopback 透明度
                "internal_request_count": internal_request_count,
                "internal_tokens": internal_tokens,
            }
        except Exception as e:
            logger.warning("audit_summary_global_since PG 失败: %s", e)
            return empty

    # sqlite fallback (BL-AUDIT-INTERNAL-SPLIT: 同样过滤 internal:* user)
    try:
        conn = _get_conn()
        # BL-AUDIT-UX-P2 (5/17): drill-down filter — 用 ? placeholder
        extra_sql_sq, extra_params_sq = _build_audit_filter(
            filter_model, filter_dept, filter_user, "?",
        )

        # top-level COUNT/SUM/active_users + active_departments
        # 注: active_departments 仍只数有 dept 的 (排除未分组), 这个语义更有用.
        cur = conn.execute(
            f"""SELECT COUNT(*), COALESCE(SUM(tokens_in + tokens_out), 0),
                       COUNT(DISTINCT user_email),
                       COUNT(DISTINCT department)
                FROM quota_events
                WHERE ts >= ? AND department != ''
                  AND user_email NOT LIKE 'internal:%'{extra_sql_sq}""",
            (cutoff_ms, *extra_params_sq),
        )
        row = cur.fetchone()
        active_departments = int(row[3] or 0)

        # top-level COUNT/SUM/active_users 不过滤 dept, 跟 PG 路径对齐
        cur = conn.execute(
            f"""SELECT COUNT(*), COALESCE(SUM(tokens_in + tokens_out), 0),
                       COUNT(DISTINCT user_email)
                FROM quota_events
                WHERE ts >= ? AND user_email NOT LIKE 'internal:%'{extra_sql_sq}""",
            (cutoff_ms, *extra_params_sq),
        )
        row = cur.fetchone()
        request_count = int(row[0] or 0)
        total_tokens = int(row[1] or 0)
        active_users = int(row[2] or 0)

        # internal: 单独算, drill-down filter 不影响.
        cur = conn.execute(
            """SELECT COUNT(*), COALESCE(SUM(tokens_in + tokens_out), 0)
               FROM quota_events
               WHERE ts >= ? AND user_email LIKE 'internal:%'""",
            (cutoff_ms,),
        )
        irow = cur.fetchone()
        internal_request_count = int(irow[0] or 0)
        internal_tokens = int(irow[1] or 0)

        cur = conn.execute(
            f"""SELECT model, COUNT(*), SUM(tokens_in + tokens_out)
                FROM quota_events
                WHERE ts >= ? AND user_email NOT LIKE 'internal:%'{extra_sql_sq}
                GROUP BY model ORDER BY 3 DESC LIMIT 20""",
            (cutoff_ms, *extra_params_sq),
        )
        by_model = [
            {"model": r[0], "count": int(r[1]), "total_tokens": int(r[2] or 0)}
            for r in cur.fetchall()
        ]

        # BL-AUDIT-P0-FIX (5/17): 空 dept 不再吞, 统一标 '(未分组)'.
        cur = conn.execute(
            f"""SELECT COALESCE(NULLIF(department, ''), '(未分组)') AS dept,
                       COUNT(*), SUM(tokens_in + tokens_out)
                FROM quota_events
                WHERE ts >= ? AND user_email NOT LIKE 'internal:%'{extra_sql_sq}
                GROUP BY dept ORDER BY 3 DESC LIMIT 20""",
            (cutoff_ms, *extra_params_sq),
        )
        by_department = [
            {"department": r[0], "count": int(r[1]), "total_tokens": int(r[2] or 0)}
            for r in cur.fetchall()
        ]

        # BL-AUDIT-P0-FIX (5/17): LIMIT 10 → 50, 空 user_email / department 归桶.
        cur = conn.execute(
            f"""SELECT COALESCE(NULLIF(user_email, ''), '(未分组员工)') AS ue,
                       COALESCE(NULLIF(department, ''), '(未分组)') AS dept,
                       COUNT(*), SUM(tokens_in + tokens_out)
                FROM quota_events
                WHERE ts >= ? AND user_email NOT LIKE 'internal:%'{extra_sql_sq}
                GROUP BY ue, dept ORDER BY 4 DESC LIMIT 50""",
            (cutoff_ms, *extra_params_sq),
        )
        by_user = [
            {
                "user_email": r[0],
                "department": r[1],
                "count": int(r[2]),
                "total_tokens": int(r[3] or 0),
            }
            for r in cur.fetchall()
        ]
        conn.close()
        return {
            "request_count": request_count,
            "total_tokens": total_tokens,
            "active_users": active_users,
            "active_departments": active_departments,
            "by_model": by_model,
            "by_department": by_department,
            "by_user": by_user,
            "internal_request_count": internal_request_count,
            "internal_tokens": internal_tokens,
        }
    except Exception as e:
        logger.warning("audit_summary_global_since sqlite 失败: %s", e)
        return empty


# ── 检查接口 ────────────────────────────────────────────────
