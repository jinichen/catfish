"""LLM 性能汇总 —— 单员工 / 全局, 都走 gateway_audit 表。

2026-08-15 从 metrics.py 切出来 (997 行超限)。纯搬迁, 逻辑一行未改。
切口就是原文件 525 行那条分节横幅, 不是我另划的。

# 为什么 audit_path / _use_pg / _pg_conn 是在函数体里 import 的

metrics.py 末尾 re-export 了本模块的两个函数 (给 app.py 三处
`_metrics.query_perf_summary_*` 用), 顶层 import 会撞成循环 —— 实测谁先
import metrics_perf 谁当场 ImportError, 而且 Python 不会提前告诉你, 换个
import 顺序才炸。

延迟 import 还保住了一件事: audit_path 是调用时才从 metrics 取, 于是测试里
`monkeypatch.setattr(metrics, "_audit_path", deep)` 照样生效。
(第一版我抽了 metrics_base.py 放共用的三样, 循环断了但 13 条测试当场红 ——
`from X import name` 建的是新绑定不是别名, 打桩 metrics._audit_path 不再影响
metrics_base 里的那份。)
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger("catfish.metrics")

# ─────────────────────────────────────────────
# P3.5.59 Phase 2 (6/22 鸿波 catch "是不是应该把中央端完成"):
# 单员工 LLM perf 聚合 — 走 gateway_audit 表 (有 latency_ms / ttft_ms).
#
# 跟 quota.audit_summary_user_since 区别:
#   - quota_events 是配额表, 不含 latency
#   - gateway_audit (本模块写的) 是 audit 表, 含 latency_ms / ttft_ms / status
#
# 用例: Companion PerfCard LLM section 调 /api/audit/me/perf 拿这数据.
# ─────────────────────────────────────────────


def _percentile_sorted(arr: list[float], p: float) -> float | None:
    """对已排序 arr 取 p 分位. 空返 None."""
    if not arr:
        return None
    idx = round((len(arr) - 1) * p)
    return arr[min(idx, len(arr) - 1)]


def query_perf_summary_user(user_email: str, cutoff_ms: int) -> dict:
    """单员工 LLM perf 聚合 — 走 gateway_audit 表 (PG 优先, JSONL fallback).

    返字段:
      - request_count / ok_count / error_count
      - total_tokens
      - latency_p50_ms / latency_p95_ms / latency_p99_ms
      - ttft_p50_ms / ttft_p95_ms (streaming 才有, NULL 跳)
      - by_model: [{model, count, total_tokens, p50_ms}]
      - source: 'pg' | 'jsonl' | 'none' (数据源诊断用)

    Privacy: 全 metadata, 跟 audit_summary_user_since 同合同 — 不返 prompt/response.

    缺数据返 None percentile + 空 list — caller UI 显 "—" 友好.
    """
    # 延迟 import —— 见文件头 (循环 + 保住 monkeypatch 语义)
    from .metrics import _pg_conn, _use_pg, audit_path  # noqa: PLC0415

    empty = {
        "request_count": 0,
        "ok_count": 0,
        "error_count": 0,
        "total_tokens": 0,
        "latency_p50_ms": None,
        "latency_p95_ms": None,
        "latency_p99_ms": None,
        "ttft_p50_ms": None,
        "ttft_p95_ms": None,
        "by_model": [],
        "source": "none",
    }

    # ─── PG 主路径 (生产 SaaS 走这条) ───
    if _use_pg():
        try:
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    # 总览 + 6 分位 (latency + ttft) 一次 query
                    cur.execute(
                        """SELECT
                            COUNT(*),
                            COUNT(*) FILTER (WHERE status = 'ok'),
                            COUNT(*) FILTER (WHERE status != 'ok'),
                            COALESCE(SUM(tokens_total), 0),
                            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY latency_ms),
                            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms),
                            PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY latency_ms),
                            PERCENTILE_CONT(0.5) WITHIN GROUP (
                                ORDER BY ttft_ms
                            ) FILTER (WHERE ttft_ms IS NOT NULL),
                            PERCENTILE_CONT(0.95) WITHIN GROUP (
                                ORDER BY ttft_ms
                            ) FILTER (WHERE ttft_ms IS NOT NULL)
                           FROM gateway_audit
                           WHERE user_email = %s AND ts_ms >= %s""",
                        (user_email, cutoff_ms),
                    )
                    row = cur.fetchone() or (0, 0, 0, 0, None, None, None, None, None)

                    # by_model 分组
                    cur.execute(
                        """SELECT
                            model,
                            COUNT(*),
                            COALESCE(SUM(tokens_total), 0),
                            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY latency_ms)
                           FROM gateway_audit
                           WHERE user_email = %s AND ts_ms >= %s
                           GROUP BY model
                           ORDER BY COUNT(*) DESC
                           LIMIT 20""",
                        (user_email, cutoff_ms),
                    )
                    by_model = [
                        {
                            "model": r[0] or "",
                            "count": int(r[1] or 0),
                            "total_tokens": int(r[2] or 0),
                            "p50_ms": float(r[3]) if r[3] is not None else None,
                        }
                        for r in cur.fetchall()
                    ]

            return {
                "request_count": int(row[0] or 0),
                "ok_count": int(row[1] or 0),
                "error_count": int(row[2] or 0),
                "total_tokens": int(row[3] or 0),
                "latency_p50_ms": float(row[4]) if row[4] is not None else None,
                "latency_p95_ms": float(row[5]) if row[5] is not None else None,
                "latency_p99_ms": float(row[6]) if row[6] is not None else None,
                "ttft_p50_ms": float(row[7]) if row[7] is not None else None,
                "ttft_p95_ms": float(row[8]) if row[8] is not None else None,
                "by_model": by_model,
                "source": "pg",
            }
        except Exception as e:
            logger.warning("query_perf_summary_user PG 失败 (fallback jsonl): %s", e)

    # ─── JSONL fallback (dev / 私有部署没 PG 时走) ───
    try:
        path = audit_path()
        if not path.exists():
            return empty
        cutoff_s = cutoff_ms / 1000.0
        latencies: list[float] = []
        ttfts: list[float] = []
        ok = err = total_toks = 0
        by_model_agg: dict[str, dict] = {}

        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("user") != user_email:
                    continue
                ts = r.get("ts", 0)
                if isinstance(ts, str):
                    try:
                        ts = float(ts)
                    except ValueError:
                        continue
                if ts < cutoff_s:
                    continue

                if r.get("status") == "ok":
                    ok += 1
                else:
                    err += 1
                total_toks += int(r.get("total_tokens", 0))
                lat = r.get("latency_ms")
                if lat is not None and lat > 0:
                    latencies.append(float(lat))
                ttft = r.get("ttft_ms")
                if ttft is not None and ttft > 0:
                    ttfts.append(float(ttft))
                m = r.get("model", "")
                if m:
                    entry = by_model_agg.setdefault(
                        m, {"count": 0, "total_tokens": 0, "latencies": []}
                    )
                    entry["count"] += 1
                    entry["total_tokens"] += int(r.get("total_tokens", 0))
                    if lat is not None and lat > 0:
                        entry["latencies"].append(float(lat))

        latencies.sort()
        ttfts.sort()

        by_model = sorted(
            [
                {
                    "model": k,
                    "count": v["count"],
                    "total_tokens": v["total_tokens"],
                    "p50_ms": _percentile_sorted(sorted(v["latencies"]), 0.5),
                }
                for k, v in by_model_agg.items()
            ],
            key=lambda x: -x["count"],
        )[:20]

        return {
            "request_count": ok + err,
            "ok_count": ok,
            "error_count": err,
            "total_tokens": total_toks,
            "latency_p50_ms": _percentile_sorted(latencies, 0.5),
            "latency_p95_ms": _percentile_sorted(latencies, 0.95),
            "latency_p99_ms": _percentile_sorted(latencies, 0.99),
            "ttft_p50_ms": _percentile_sorted(ttfts, 0.5),
            "ttft_p95_ms": _percentile_sorted(ttfts, 0.95),
            "by_model": by_model,
            "source": "jsonl",
        }
    except OSError as e:
        logger.warning("query_perf_summary_user JSONL 失败: %s", e)
        return empty


# ─────────────────────────────────────────────
# P3.5.60 (6/22 鸿波 catch "继续完成"): 全公司 LLM perf 聚合 — 给 web /admin/perf
# 页 admin 看, 走 gateway_audit 表. 跟 query_perf_summary_user 区别: 不按 user
# 过滤, 加 by_department + active_users 全公司维度.
# ─────────────────────────────────────────────


def query_perf_summary_global(
    cutoff_ms: int,
    model_filter: str | None = None,
    dept_filter: str | None = None,
) -> dict:
    """全公司 LLM perf 聚合. admin RBAC 调用方守门.

    返字段: 同 query_perf_summary_user + by_department (按 dept 分聚合) +
    active_users (有调用的不同员工数) + active_departments.

    Privacy: 全 metadata, 跟 audit_summary_global_since 同合同.
    """
    # 延迟 import —— 见文件头 (循环 + 保住 monkeypatch 语义)
    from .metrics import _pg_conn, _use_pg, audit_path  # noqa: PLC0415

    empty = {
        "request_count": 0,
        "ok_count": 0,
        "error_count": 0,
        "total_tokens": 0,
        "active_users": 0,
        "active_departments": 0,
        "latency_p50_ms": None,
        "latency_p95_ms": None,
        "latency_p99_ms": None,
        "ttft_p50_ms": None,
        "ttft_p95_ms": None,
        "by_model": [],
        "by_department": [],
        "source": "none",
    }

    # PG 主路径
    if _use_pg():
        try:
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    where_parts = ["ts_ms >= %s"]
                    params: list = [cutoff_ms]
                    if model_filter:
                        where_parts.append("model = %s")
                        params.append(model_filter)
                    if dept_filter:
                        where_parts.append("department = %s")
                        params.append(dept_filter)
                    where = " AND ".join(where_parts)

                    cur.execute(
                        f"""SELECT
                            COUNT(*),
                            COUNT(*) FILTER (WHERE status = 'ok'),
                            COUNT(*) FILTER (WHERE status != 'ok'),
                            COALESCE(SUM(tokens_total), 0),
                            COUNT(DISTINCT user_email),
                            COUNT(DISTINCT department),
                            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY latency_ms),
                            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms),
                            PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY latency_ms),
                            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ttft_ms) FILTER (WHERE ttft_ms IS NOT NULL),
                            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY ttft_ms) FILTER (WHERE ttft_ms IS NOT NULL)
                           FROM gateway_audit WHERE {where}""",
                        params,
                    )
                    row = cur.fetchone() or [0] * 11

                    cur.execute(
                        f"""SELECT
                            model,
                            COUNT(*),
                            COUNT(*) FILTER (WHERE status != 'ok'),
                            COALESCE(SUM(tokens_total), 0),
                            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY latency_ms),
                            PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY latency_ms)
                           FROM gateway_audit WHERE {where}
                           GROUP BY model ORDER BY COUNT(*) DESC LIMIT 20""",
                        params,
                    )
                    by_model = [
                        {
                            "model": r[0] or "",
                            "count": int(r[1] or 0),
                            "error_count": int(r[2] or 0),
                            "total_tokens": int(r[3] or 0),
                            "p50_ms": float(r[4]) if r[4] is not None else None,
                            "p99_ms": float(r[5]) if r[5] is not None else None,
                        }
                        for r in cur.fetchall()
                    ]

                    cur.execute(
                        f"""SELECT
                            department,
                            COUNT(*),
                            COUNT(*) FILTER (WHERE status != 'ok'),
                            COALESCE(SUM(tokens_total), 0),
                            COUNT(DISTINCT user_email),
                            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY latency_ms),
                            PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY latency_ms)
                           FROM gateway_audit WHERE {where}
                           GROUP BY department ORDER BY COUNT(*) DESC LIMIT 20""",
                        params,
                    )
                    by_department = [
                        {
                            "department": r[0] or "(未分组)",
                            "count": int(r[1] or 0),
                            "error_count": int(r[2] or 0),
                            "total_tokens": int(r[3] or 0),
                            "active_users": int(r[4] or 0),
                            "p50_ms": float(r[5]) if r[5] is not None else None,
                            "p99_ms": float(r[6]) if r[6] is not None else None,
                        }
                        for r in cur.fetchall()
                    ]

            return {
                "request_count": int(row[0] or 0),
                "ok_count": int(row[1] or 0),
                "error_count": int(row[2] or 0),
                "total_tokens": int(row[3] or 0),
                "active_users": int(row[4] or 0),
                "active_departments": int(row[5] or 0),
                "latency_p50_ms": float(row[6]) if row[6] is not None else None,
                "latency_p95_ms": float(row[7]) if row[7] is not None else None,
                "latency_p99_ms": float(row[8]) if row[8] is not None else None,
                "ttft_p50_ms": float(row[9]) if row[9] is not None else None,
                "ttft_p95_ms": float(row[10]) if row[10] is not None else None,
                "by_model": by_model,
                "by_department": by_department,
                "source": "pg",
            }
        except Exception as e:
            logger.warning("query_perf_summary_global PG 失败 (fallback jsonl): %s", e)

    # JSONL fallback
    try:
        path = audit_path()
        if not path.exists():
            return empty
        cutoff_s = cutoff_ms / 1000.0
        latencies: list[float] = []
        ttfts: list[float] = []
        ok = err = total_toks = 0
        active_users: set[str] = set()
        active_depts: set[str] = set()
        by_model_agg: dict[str, dict] = {}
        by_dept_agg: dict[str, dict] = {}

        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = r.get("ts", 0)
                if isinstance(ts, str):
                    try:
                        ts = float(ts)
                    except ValueError:
                        continue
                if ts < cutoff_s:
                    continue
                m = r.get("model", "")
                d = r.get("department", "")
                u = r.get("user", "")
                if model_filter and m != model_filter:
                    continue
                if dept_filter and d != dept_filter:
                    continue

                if r.get("status") == "ok":
                    ok += 1
                else:
                    err += 1
                total_toks += int(r.get("total_tokens", 0))
                if u:
                    active_users.add(u)
                if d:
                    active_depts.add(d)
                lat = r.get("latency_ms")
                if lat is not None and lat > 0:
                    latencies.append(float(lat))
                ttft = r.get("ttft_ms")
                if ttft is not None and ttft > 0:
                    ttfts.append(float(ttft))

                if m:
                    e_m = by_model_agg.setdefault(
                        m, {"count": 0, "error_count": 0, "total_tokens": 0, "lats": []}
                    )
                    e_m["count"] += 1
                    if r.get("status") != "ok":
                        e_m["error_count"] += 1
                    e_m["total_tokens"] += int(r.get("total_tokens", 0))
                    if lat is not None and lat > 0:
                        e_m["lats"].append(float(lat))

                dept_key = d or "(未分组)"
                e_d = by_dept_agg.setdefault(
                    dept_key,
                    {"count": 0, "error_count": 0, "total_tokens": 0, "users": set(), "lats": []},
                )
                e_d["count"] += 1
                if r.get("status") != "ok":
                    e_d["error_count"] += 1
                e_d["total_tokens"] += int(r.get("total_tokens", 0))
                if u:
                    e_d["users"].add(u)
                if lat is not None and lat > 0:
                    e_d["lats"].append(float(lat))

        latencies.sort()
        ttfts.sort()

        by_model = sorted(
            [
                {
                    "model": k,
                    "count": v["count"],
                    "error_count": v["error_count"],
                    "total_tokens": v["total_tokens"],
                    "p50_ms": _percentile_sorted(sorted(v["lats"]), 0.5),
                    "p99_ms": _percentile_sorted(sorted(v["lats"]), 0.99),
                }
                for k, v in by_model_agg.items()
            ],
            key=lambda x: -x["count"],
        )[:20]

        by_department = sorted(
            [
                {
                    "department": k,
                    "count": v["count"],
                    "error_count": v["error_count"],
                    "total_tokens": v["total_tokens"],
                    "active_users": len(v["users"]),
                    "p50_ms": _percentile_sorted(sorted(v["lats"]), 0.5),
                    "p99_ms": _percentile_sorted(sorted(v["lats"]), 0.99),
                }
                for k, v in by_dept_agg.items()
            ],
            key=lambda x: -x["count"],
        )[:20]

        return {
            "request_count": ok + err,
            "ok_count": ok,
            "error_count": err,
            "total_tokens": total_toks,
            "active_users": len(active_users),
            "active_departments": len(active_depts),
            "latency_p50_ms": _percentile_sorted(latencies, 0.5),
            "latency_p95_ms": _percentile_sorted(latencies, 0.95),
            "latency_p99_ms": _percentile_sorted(latencies, 0.99),
            "ttft_p50_ms": _percentile_sorted(ttfts, 0.5),
            "ttft_p95_ms": _percentile_sorted(ttfts, 0.95),
            "by_model": by_model,
            "by_department": by_department,
            "source": "jsonl",
        }
    except OSError as e:
        logger.warning("query_perf_summary_global JSONL 失败: %s", e)
        return empty
