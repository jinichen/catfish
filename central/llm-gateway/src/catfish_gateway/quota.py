"""Quota · 三维配额限流 — 五一 sprint 5/3 (BL-D9).

# 设计 (docs/QUOTA-DESIGN.md v0.1)

三维:
- per-user: 员工每分钟 / 每天 token 上限
- per-model: 模型粒度全员每天上限 (公网 LLM 控成本用)
- per-department: 部门聚合每天上限 (manager 改)

# Sliding window

sqlite 存近 1h / 近 7 天 token 用量 events. 每分钟 / 每天窗口实时累加.
~/.catfish/quota.db, 每查 < 1ms.

# 估算 vs 真实

请求来时用 estimated tokens (粗估 4 字符 = 1 token, 至少 1000) 检查 quota.
请求完成后用 response.usage 真实 tokens 记到 sqlite.
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


def _quota_db_path() -> Path:
    """quota.db 路径. CATFISH_QUOTA_DB env override (单元测试用)."""
    custom = os.environ.get("CATFISH_QUOTA_DB")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".catfish" / "quota.db"


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
    """请求完成后记真实 token 用量. 失败静默不影响主流程."""
    try:
        conn = _get_conn()
        with conn:
            conn.execute(
                "INSERT INTO quota_events VALUES (?, ?, ?, ?, ?, ?)",
                (
                    int(time.time() * 1000),
                    user_email,
                    department,
                    model,
                    int(tokens_in),
                    int(tokens_out),
                ),
            )
        conn.close()
    except Exception as e:
        logger.warning("record_usage 失败: %s", e)


def _sum_tokens(where_clause: str, params: tuple[Any, ...]) -> int:
    """通用 sum 查询. cutoff_ms 在 params 里."""
    try:
        conn = _get_conn()
        cur = conn.execute(
            f"SELECT COALESCE(SUM(tokens_in + tokens_out), 0) FROM quota_events WHERE {where_clause}",
            params,
        )
        result = cur.fetchone()[0]
        conn.close()
        return int(result or 0)
    except Exception as e:
        logger.warning("_sum_tokens 失败 (where=%s): %s", where_clause, e)
        return 0


def sum_tokens_user_since(user_email: str, cutoff_ms: int) -> int:
    return _sum_tokens(
        "user_email = ? AND ts >= ?",
        (user_email, cutoff_ms),
    )


def sum_tokens_model_since(model: str, cutoff_ms: int) -> int:
    return _sum_tokens(
        "model = ? AND ts >= ?",
        (model, cutoff_ms),
    )


def sum_tokens_dept_since(department: str, cutoff_ms: int) -> int:
    return _sum_tokens(
        "department = ? AND ts >= ?",
        (department, cutoff_ms),
    )


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
) -> QuotaCheck:
    """请求来时调一次, 返 allowed=False 触发 429.

    检查顺序: per_user_minute → per_user_day → per_model_day → per_department_day.
    任一超 → 立刻拒, 不查后面的.
    """
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
