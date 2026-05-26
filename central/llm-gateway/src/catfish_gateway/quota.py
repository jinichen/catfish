"""Quota · 三维配额限流 — 五一 sprint 5/3 (BL-D9) + 5/2 收尾 PG migration.

# 设计 (docs/QUOTA-DESIGN.md v0.1)

三维:
- per-user: 员工每分钟 / 每天 token 上限
- per-model: 模型粒度全员每天上限 (公网 LLM 控成本用)
- per-department: 部门聚合每天上限 (manager 改)

# Sliding window

存 1h / 近 7 天 token 用量 events. 每分钟 / 每天窗口实时累加.

# 双 backend (五一 sprint 5/2 收尾加)

- **PG (生产)**: env CATFISH_DB_URL 配 → quota_events 表, 跨 gateway 实例共享
- **sqlite (dev / 单测)**: ~/.catfish/quota.db 兜底, 没 PG 配置时走  # noqa: BOUNDARY

切换透明 — 公共 API (record_usage / sum_*_since / top_users / audit_summary_dept)
不变, 内部 _use_pg() 检测后分发. 表名 schema 一致 (PG: quota_events, sqlite: 同名).

# 估算 vs 真实

请求来时用 estimated tokens (粗估 4 字符 = 1 token, 至少 1000) 检查 quota.
请求完成后用 response.usage 真实 tokens 记到存储.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("catfish.gateway.quota")


# ── Backend 选择 (生产 PG-only; 单测可 sqlite override) ───────
#
# BL-QUOTA-SQLITE-DEPRECATE + BL-CENTRAL-EDGE-BOUNDARY (5/17 鸿波):
#   规则: 中央端不许碰用户本机文件 (包括 ~/.catfish/quota.db). 生产必走 PG.  # noqa: BOUNDARY
#   单测可以用 sqlite tmp 文件 (test fixture 设 CATFISH_QUOTA_DB 显式 override).
#
#   老逻辑: PG 写失败 → fallback sqlite (双写). 这违反规则因为 sqlite 落在员工
#   Mac. 删 fallback — PG 写失败 = log warning + 丢这条 audit (audit 不是关键
#   路径, 丢一条比写员工本机好).


def _use_pg() -> bool:
    """生产 PG, 单测 sqlite (test fixture 通过 CATFISH_QUOTA_DB env override).

    返回:
      True  — 走 PG (CATFISH_DB_URL 配了 — 生产默认)
      False — 走 sqlite (CATFISH_QUOTA_DB 显式配了, 单测路径)

    BL-CENTRAL-EDGE-BOUNDARY (5/17): 两个 env 都没配 = 配置错误, 不再静默走
    默认 ~/.catfish/quota.db (员工本机违规). 返 True 让调用方走 PG 路径 +  # noqa: BOUNDARY
    在 _pg_conn 时撞 connect 错 fail-loud, 显式而不是默默写错地方.
    """
    if os.environ.get("CATFISH_QUOTA_DB"):
        return False  # 单测显式 sqlite 路径
    return True  # PG mode — 没配 CATFISH_DB_URL 也走 PG 路径, _pg_conn 时报错


def _audit_backend_configured() -> bool:
    """有任一 audit backend 配了 (PG 或 test sqlite). 没配 → 生产模式该报警."""
    return bool(os.environ.get("CATFISH_DB_URL", "").strip()) or bool(
        os.environ.get("CATFISH_QUOTA_DB")
    )


_PG_CONN_INFO: str | None = None  # 缓存连接字符串, 避免重读 env


def _pg_conninfo() -> str:
    """psycopg 连接字符串. lazy 缓存."""
    global _PG_CONN_INFO
    if _PG_CONN_INFO is not None:
        return _PG_CONN_INFO
    url = os.environ.get("CATFISH_DB_URL", "").strip()
    _PG_CONN_INFO = url
    return url


def _pg_conn():
    """开一个 psycopg sync 连接. 一次性, caller close.

    不用池: quota 写量不大 (每个 LLM 请求 1 次 INSERT), 连接开销可接受.
    后续要池化加 psycopg_pool.
    """
    import psycopg  # 懒 import, 没装 PG 也能跑 sqlite mode
    return psycopg.connect(_pg_conninfo())


def _quota_db_path() -> Path:
    """sqlite quota.db 路径 (只单元测试用, CATFISH_QUOTA_DB env 显式 override).

    BL-QUOTA-SQLITE-DEPRECATE (5/17) + BL-CENTRAL-EDGE-BOUNDARY:
      生产模式必走 PG (CATFISH_DB_URL). 此函数只在 _use_pg() == False 时调,
      也就是单测路径. 单测 fixture 必须设 CATFISH_QUOTA_DB 显式指 tmp_path.
      不再有 ~/.catfish/quota.db 默认 fallback (员工本机违反 boundary).  # noqa: BOUNDARY

    调用 unreachable 在生产 — 但代码层强校验, 没设环境变量 fail-loud,
    防开发期"忘了 setup" 静默写到 home dir.
    """
    custom = os.environ.get("CATFISH_QUOTA_DB")
    if custom:
        return Path(custom).expanduser()
    raise RuntimeError(
        "CATFISH_QUOTA_DB env 未设, 但 _use_pg() 返回 False 走到 sqlite 分支. "
        "BL-CENTRAL-EDGE-BOUNDARY 不再 fallback ~/.catfish/quota.db. "  # noqa: BOUNDARY
        "生产应配 CATFISH_DB_URL 走 PG; 单测应在 fixture 设 CATFISH_QUOTA_DB tmp 路径."
    )


def _quota_config_path() -> Path:
    """quotas.yaml 路径. CATFISH_QUOTAS_PATH env override.

    默认: <repo>/central/llm-gateway/config/quotas.yaml (跟 models.yaml 同目录).
    """
    custom = os.environ.get("CATFISH_QUOTAS_PATH")
    if custom:
        return Path(custom).expanduser()
    # quota.py → catfish_gateway/ → src/ → llm-gateway/  (3 个 parent)
    pkg_root = Path(__file__).resolve().parent.parent.parent
    return pkg_root / "config" / "quotas.yaml"


@dataclass
class UserQuota:
    tokens_per_minute: int = 100_000
    tokens_per_day: int = 1_000_000


@dataclass
class ModelQuota:
    tokens_per_day: int = 0  # 0 = 不限


@dataclass
class DepartmentQuota:
    tokens_per_day: int = 0  # 0 = 不限


@dataclass
class QuotaConfig:
    """所有 quota 配置 (defaults + overrides 合并)."""

    default_user: UserQuota
    user_overrides: dict[str, UserQuota]  # email → UserQuota
    model_quotas: dict[str, ModelQuota]   # model_name → ModelQuota
    department_quotas: dict[str, DepartmentQuota]  # dept_name → DeptQuota

    def per_user_for(self, email: str) -> UserQuota:
        return self.user_overrides.get(email, self.default_user)

    def per_model_for(self, model: str) -> ModelQuota:
        return self.model_quotas.get(model, ModelQuota(tokens_per_day=0))

    def per_department_for(self, dept: str) -> DepartmentQuota:
        return self.department_quotas.get(dept, DepartmentQuota(tokens_per_day=0))


def update_department_quota(department: str, tokens_per_day: int) -> bool:
    """改部门 quota → 写 quotas.yaml (overrides.departments). 五一 sprint 5/2 RBAC manager 用.

    行为:
    - 文件不存在 → 创建默认骨架
    - 已有 overrides.departments.<dept> → 更新
    - 没有 → 加进去
    - tokens_per_day=0 表示不限

    返 True 成功, False 失败 (yaml 写错 / 权限问题).

    线程安全: 简单文件锁 (不并发 manager 多人同时改 dev 单机够用),
    Phase 2 上 PG 后改成 PG 表 + UPSERT 更稳.
    """
    p = _quota_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)

    # 读现有
    data: dict
    if p.exists():
        try:
            with p.open(encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning("update_department_quota: yaml 解析失败 %s: %s", p, e)
            return False
    else:
        data = {}

    # 改 overrides.departments.<dept>
    # 注意: yaml 里 'overrides:' / 'departments:' 后只有注释时 safe_load 返 None,
    # setdefault 不会替换 None, 必须显式判断 (踩过坑).
    overrides = data.get("overrides")
    if not isinstance(overrides, dict):
        overrides = {}
        data["overrides"] = overrides

    depts = overrides.get("departments")
    if not isinstance(depts, dict):
        depts = {}
        overrides["departments"] = depts

    depts[department] = {"tokens_per_day": int(tokens_per_day)}

    # 写回 (utf-8, allow_unicode 保留中文部门名)
    try:
        with p.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        logger.info(
            "update_department_quota: %s tokens_per_day=%d (写 %s)",
            department, tokens_per_day, p,
        )
        return True
    except Exception as e:
        logger.warning("update_department_quota 写入失败 %s: %s", p, e)
        return False


def load_quota_config(path: Path | None = None) -> QuotaConfig:
    """从 yaml 加载. 文件不存在走全部默认 (per-user 限, model/dept 不限)."""
    p = path or _quota_config_path()
    if not p.exists():
        logger.info("quotas.yaml 不存在 (%s), 走全部默认", p)
        return QuotaConfig(
            default_user=UserQuota(),
            user_overrides={},
            model_quotas={},
            department_quotas={},
        )

    try:
        with p.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("quotas.yaml 解析失败 %s, 走默认: %s", p, e)
        return QuotaConfig(
            default_user=UserQuota(),
            user_overrides={},
            model_quotas={},
            department_quotas={},
        )

    defaults = data.get("defaults", {}) or {}
    overrides = data.get("overrides", {}) or {}

    # default_user
    pu = defaults.get("per_user", {}) or {}
    default_user = UserQuota(
        tokens_per_minute=int(pu.get("tokens_per_minute", 100_000)),
        tokens_per_day=int(pu.get("tokens_per_day", 1_000_000)),
    )

    # model quotas
    pm = defaults.get("per_model", {}) or {}
    model_quotas: dict[str, ModelQuota] = {}
    for model_name, cfg in pm.items():
        if not isinstance(cfg, dict):
            continue
        model_quotas[model_name] = ModelQuota(
            tokens_per_day=int(cfg.get("tokens_per_day", 0)),
        )

    # department quotas
    pd = defaults.get("per_department", {}) or {}
    department_quotas: dict[str, DepartmentQuota] = {}
    for dept_name, cfg in pd.items():
        if not isinstance(cfg, dict):
            continue
        department_quotas[dept_name] = DepartmentQuota(
            tokens_per_day=int(cfg.get("tokens_per_day", 0)),
        )

    # overrides
    user_overrides: dict[str, UserQuota] = {}
    for email, cfg in (overrides.get("users", {}) or {}).items():
        if not isinstance(cfg, dict):
            continue
        user_overrides[email] = UserQuota(
            tokens_per_minute=int(cfg.get("tokens_per_minute", default_user.tokens_per_minute)),
            tokens_per_day=int(cfg.get("tokens_per_day", default_user.tokens_per_day)),
        )

    for dept_name, cfg in (overrides.get("departments", {}) or {}).items():
        if not isinstance(cfg, dict):
            continue
        department_quotas[dept_name] = DepartmentQuota(
            tokens_per_day=int(cfg.get("tokens_per_day", 0)),
        )

    return QuotaConfig(
        default_user=default_user,
        user_overrides=user_overrides,
        model_quotas=model_quotas,
        department_quotas=department_quotas,
    )


# ── sqlite store ────────────────────────────────────────────


_SCHEMA = """
CREATE TABLE IF NOT EXISTS quota_events (
    ts INTEGER NOT NULL,
    user_email TEXT NOT NULL,
    department TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens_in INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quota_ts_user ON quota_events(ts, user_email);
CREATE INDEX IF NOT EXISTS idx_quota_ts_model ON quota_events(ts, model);
CREATE INDEX IF NOT EXISTS idx_quota_ts_dept ON quota_events(ts, department);
"""


def _get_conn() -> sqlite3.Connection:
    p = _quota_db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=2.0)
    conn.executescript(_SCHEMA)
    return conn


def record_usage(
    user_email: str,
    department: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
) -> None:
    """请求完成后记真实 token 用量. 失败静默不影响主流程.

    BL-QUOTA-SQLITE-DEPRECATE (5/17 鸿波): 老逻辑 PG 写失败 → fallback sqlite
    (写 ~/.catfish/quota.db, 员工本机文件). 违反 BL-CENTRAL-EDGE-BOUNDARY 规则.  # noqa: BOUNDARY
    现在: 生产 PG-only, PG 失败 → log warning + 丢这条 audit. 单测走 sqlite
    (CATFISH_QUOTA_DB env override).
    """
    ts_ms = int(time.time() * 1000)

    if _use_pg():
        try:
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO quota_events (ts_ms, user_email, department, model, tokens_in, tokens_out) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (ts_ms, user_email, department, model,
                         int(tokens_in), int(tokens_out)),
                    )
                conn.commit()
        except Exception as e:
            # BL-QUOTA-SQLITE-DEPRECATE (5/17): 不再 fallback sqlite. 丢这条
            # audit 比写员工本机好. ops 应该把 PG 报警接监控.
            logger.warning(
                "record_usage PG 失败, 丢这条 audit (不再 fallback sqlite, "
                "BL-CENTRAL-EDGE-BOUNDARY 规则): %s", e,
            )
        return

    # 单测路径: CATFISH_QUOTA_DB env 显式配了, 走 tmp sqlite (test fixture 设的)
    try:
        conn = _get_conn()
        with conn:
            conn.execute(
                "INSERT INTO quota_events VALUES (?, ?, ?, ?, ?, ?)",
                (ts_ms, user_email, department, model,
                 int(tokens_in), int(tokens_out)),
            )
        conn.close()
    except Exception as e:
        logger.warning("record_usage sqlite (test mode) 失败: %s", e)


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


@dataclass
class QuotaCheck:
    """check_quota 的结果."""

    allowed: bool
    dimension: str = ""  # per_user_minute / per_user_day / per_model_day / per_dept_day
    current: int = 0
    limit: int = 0
    reset_at: int = 0  # unix seconds


def estimate_tokens(text: str) -> int:
    """粗估 token 数: 4 字符 ≈ 1 token, 至少 1000.

    真实 token 在 LLM 响应完才知道, 这里 conservative 估高一点防绕过.
    """
    if not text:
        return 1000
    return max(len(text) // 4, 1000)


def check_quota(
    user_email: str,
    department: str,
    model: str,
    est_tokens: int,
    config: QuotaConfig | None = None,
    role: str | None = None,
) -> QuotaCheck:
    """请求来时调一次, 返 allowed=False 触发 429.

    检查顺序: per_user_minute → per_user_day → per_model_day → per_department_day.
    任一超 → 立刻拒, 不查后面的.

    BL-FIX39 (5/11): role in (admin, sysadmin) → 直接 allowed=True 跳所有检查.
    系统管理员场景: 演 demo / 应急处理 / 跨员工 debug 时不能被 quota 卡住.
    quota_events 仍会记录 (后续审计能看 admin 用了多少 token, 只是不拒).
    """
    # BL-FIX39: admin / sysadmin 跳 quota
    if role in ("admin", "sysadmin"):
        return QuotaCheck(allowed=True)

    if config is None:
        config = load_quota_config()

    now_ms = int(time.time() * 1000)
    now_sec = int(now_ms / 1000)

    # 1. per-user 1 minute
    user_q = config.per_user_for(user_email)
    used_min = sum_tokens_user_since(user_email, now_ms - 60_000)
    if user_q.tokens_per_minute > 0 and used_min + est_tokens > user_q.tokens_per_minute:
        return QuotaCheck(
            allowed=False,
            dimension="per_user_minute",
            current=used_min,
            limit=user_q.tokens_per_minute,
            reset_at=now_sec + 60,
        )

    # 2. per-user 1 day
    used_day = sum_tokens_user_since(user_email, now_ms - 86_400_000)
    if user_q.tokens_per_day > 0 and used_day + est_tokens > user_q.tokens_per_day:
        return QuotaCheck(
            allowed=False,
            dimension="per_user_day",
            current=used_day,
            limit=user_q.tokens_per_day,
            reset_at=now_sec + 86400,
        )

    # 3. per-model 1 day (model_q.tokens_per_day=0 表示不限)
    model_q = config.per_model_for(model)
    if model_q.tokens_per_day > 0:
        used_model = sum_tokens_model_since(model, now_ms - 86_400_000)
        if used_model + est_tokens > model_q.tokens_per_day:
            return QuotaCheck(
                allowed=False,
                dimension="per_model_day",
                current=used_model,
                limit=model_q.tokens_per_day,
                reset_at=now_sec + 86400,
            )

    # 4. per-department 1 day
    dept_q = config.per_department_for(department)
    if dept_q.tokens_per_day > 0:
        used_dept = sum_tokens_dept_since(department, now_ms - 86_400_000)
        if used_dept + est_tokens > dept_q.tokens_per_day:
            return QuotaCheck(
                allowed=False,
                dimension="per_dept_day",
                current=used_dept,
                limit=dept_q.tokens_per_day,
                reset_at=now_sec + 86400,
            )

    return QuotaCheck(allowed=True)


def friendly_quota_message(qc: QuotaCheck, user_email: str, model: str) -> str:
    """超额时给员工友好的提示. 不是 stack trace."""
    if qc.dimension == "per_user_minute":
        secs = max(qc.reset_at - int(time.time()), 1)
        return (
            f"你这分钟 token 用得太多 ({qc.current:,}/{qc.limit:,}). "
            f"等 {secs} 秒后再试, 或换 catfish-private-main (内网不限)."
        )
    if qc.dimension == "per_user_day":
        return (
            f"你今天 token quota 满了 ({qc.current:,}/{qc.limit:,}). "
            "明天重置. 急用找 manager 临时升 quota."
        )
    if qc.dimension == "per_model_day":
        return (
            f"模型 {model} 今天全员 quota 满了 ({qc.current:,}/{qc.limit:,}). "
            "换 catfish-private-main 或 catfish-public-qwen-flash."
        )
    if qc.dimension == "per_dept_day":
        return (
            f"你部门今天 quota 满了 ({qc.current:,}/{qc.limit:,}). "
            "manager 在仪表盘改部门配额."
        )
    return f"quota 超额 ({qc.dimension}): {qc.current:,}/{qc.limit:,}"


__all__ = [
    "QuotaCheck",
    "QuotaConfig",
    "UserQuota",
    "ModelQuota",
    "DepartmentQuota",
    "load_quota_config",
    "check_quota",
    "record_usage",
    "estimate_tokens",
    "friendly_quota_message",
    "sum_tokens_user_since",
    "sum_tokens_model_since",
    "sum_tokens_dept_since",
]
