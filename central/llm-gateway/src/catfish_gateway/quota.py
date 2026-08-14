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

# P3.5.93 (6/23 鸿波): yaml 写回保 comments.
#
# 老逻辑 update_department_quota 用 yaml.safe_dump → 5/2 ship 起 6 周来吹掉了
# yaml 顶部 6 行 BL-FIX38 / BL-CHENHONGBO-DEV-QUOTA 历史注释 (鸿波每次走
# manager UI 改部门 quota 都丢一些, 直到一行不剩).
#
# ruamel.yaml round_trip 模式可保 comments + 字段顺序. 仅 _quotas_yaml_*
# helper 用, 其他 yaml 仍走 pyyaml (不必要改动).
from ruamel.yaml import YAML

logger = logging.getLogger("catfish.gateway.quota")

# P3.5.93: 单实例 round_trip parser, 模块级即可 (无状态)
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


def _resolve_friendly_model(role_str: str) -> str:
    """超额错误文案里"建议换的 model 名" — 文案降级 chain (不是路由!).

    P3.5.29 Phase 2 (6/17 鸿波): error message 用 role 动态 resolve, 不 hardcode.
    客户改 roles.yaml → error message 跟着走.

    P3.5.139 (6/29 鸿波"都要去除硬编码"): chain 升级:
      1. roles.yaml (gateway startup load 成功 — 正常情况, 99% 走这)
      2. .env CATFISH_FALLBACK_{ROLE.upper()} — roles 没 load 时兜底
         (e.g. roles.yaml 语法错 / 文件不在). 客户改 .env 跟着走.
      3. 空字符串 — env 也没配 → 文案渲染成 "换 (内网不限)" 略丑,
         dev 启动 gateway 没 .env 时可见. 不应该出现在正常 production.

    注意: 这是文案降级, 不影响路由. 路由 chain (picker/role/yaml) 在 Companion,
    gateway 自己只有 roles.yaml 一个 truth source. 文案兜底字面值全删.
    """
    # 1. roles.yaml
    try:
        from . import roles as roles_module
        m = roles_module.resolve_or_none(role_str)
        if m:
            return m
    except Exception:
        pass
    # 2. .env CATFISH_FALLBACK_{ROLE.upper()} (e.g. CATFISH_FALLBACK_CHAT_DEFAULT)
    env_key = f"CATFISH_FALLBACK_{role_str.upper()}"
    val = os.environ.get(env_key, "").strip()
    if val:
        return val
    # 3. 空字符串 — 文案降级, 不该出现在正常 production
    return ""


def friendly_quota_message(qc: QuotaCheck, user_email: str, model: str) -> str:
    """超额时给员工友好的提示. 不是 stack trace."""
    if qc.dimension == "per_user_minute":
        secs = max(qc.reset_at - int(time.time()), 1)
        # P3.5.29 Phase 2: chat_default 主力内网, 客户改 yaml 跟着改, 不 hardcode.
        # P3.5.139 (6/29 鸿波): roles 没 load 走 .env, .env 没配走空字符串文案降级.
        chat_default = _resolve_friendly_model("chat_default")
        return (
            f"你这分钟 token 用得太多 ({qc.current:,}/{qc.limit:,}). "
            f"等 {secs} 秒后再试, 或换 {chat_default} (内网不限)."
        )
    if qc.dimension == "per_user_day":
        return (
            f"你今天 token quota 满了 ({qc.current:,}/{qc.limit:,}). "
            "明天重置. 急用找 manager 临时升 quota."
        )
    if qc.dimension == "per_model_day":
        # P3.5.29 Phase 2: chat_default + public_flash dynamic resolve.
        # P3.5.139 (6/29 鸿波): 同上 chain roles.yaml → .env → 空字符串.
        chat_default = _resolve_friendly_model("chat_default")
        public_flash = _resolve_friendly_model("public_flash")
        return (
            f"模型 {model} 今天全员 quota 满了 ({qc.current:,}/{qc.limit:,}). "
            f"换 {chat_default} 或 {public_flash}."
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


# ── 8/15: 配置 / 用量聚合切出去了, 这里 re-export 回来 ──
#
# 全仓 26 个符号是通过 `from . import quota` + `quota.X` 访问的 (app.py 十几处
# 懒 import, admin router, 测试)。搬走函数会让那些访问静默变成 AttributeError
# —— Python 没类型检查, 要跑到那一行才发现。
#
# 放在文件末尾: 上面的 check_quota 用到 load_quota_config / sum_tokens_*,
# 而 Python 的模块级名字在**调用时**才解析, 所以写在后面不影响。
from .quota_audit import (  # noqa: E402,F401
    audit_period_totals,
    audit_summary_dept_since,
    audit_summary_global_since,
    audit_summary_user_since,
    sum_tokens_dept_since,
    sum_tokens_model_since,
    sum_tokens_user_since,
    top_departments,
    top_users_in_department,
)
from .quota_base import (  # noqa: E402,F401
    _audit_backend_configured,
    _get_conn,
    _pg_conn,
    _quota_config_path,
    _quota_db_path,
    _use_pg,
)
from .quota_config import (  # noqa: E402,F401
    DepartmentQuota,
    ModelQuota,
    QuotaConfig,
    UserQuota,
    delete_dept_override,
    delete_per_department,
    delete_per_model,
    delete_user_override,
    get_full_config_dict,
    load_quota_config,
    put_dept_override,
    put_per_department,
    put_per_model,
    put_user_override,
    update_default_per_user,
    update_department_quota,
)
