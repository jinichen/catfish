"""Gateway 数据库连接 — PostgreSQL 主存储, sqlite/jsonl fallback.

# 切换逻辑

读 env `CATFISH_DB_URL` (跟 catfish-identity 共享同一个 PG 实例 / URL 决定后端):
- 有值 (例 `postgresql://catfish:xxx@localhost:5432/catfish`) → 走 PG
- 无值 → 走老 sqlite (quota_events) / jsonl (audit) — dev / 单机模式

# 共享 PG 实例 + 不同表

跟 catfish-identity 共用同一个 PG 实例 + 表前缀防冲突:
- catfish-identity: `users`, `registry_agents`
- gateway:          `quota_events`, `gateway_audit`

每个进程独立维护连接池. 连接 < 10 个/进程, asyncpg 池.

# Schema migration

由 alembic 管理 (alembic/ 目录). 启动时不再 CREATE TABLE,
要求部署前先跑 `alembic upgrade head` (五一 sprint 5/2 收尾改).

dev / sqlite fallback 仍然走老 idempotent CREATE (quota.py 内嵌).
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import yaml

logger = logging.getLogger("catfish.gateway.db")


# asyncpg 懒 import: 没装 / 没 PG 配置时静默走 sqlite/jsonl fallback.
_POOL = None  # type: ignore[var-annotated]
_CONFIG_CACHE: dict | None = None


def _load_config() -> dict:
    """读 config/database.yaml. 失败 / 文件不存在返空 dict."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    custom = os.environ.get("CATFISH_DB_CONFIG", "").strip()
    if custom:
        path = Path(custom).expanduser()
    else:
        # config/database.yaml (跟 models.yaml 同目录)
        path = Path(__file__).resolve().parent.parent.parent / "config" / "database.yaml"

    if not path.exists():
        _CONFIG_CACHE = {}
        return _CONFIG_CACHE

    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        _CONFIG_CACHE = data.get("database", {}) or {}
        return _CONFIG_CACHE
    except Exception as e:
        logger.warning("database.yaml 解析失败 %s: %s", path, e)
        _CONFIG_CACHE = {}
        return _CONFIG_CACHE


def db_url() -> str | None:
    """优先级: env CATFISH_DB_URL > config/database.yaml > None.

    None 时上层 fallback sqlite/jsonl.
    """
    env_url = os.environ.get("CATFISH_DB_URL", "").strip()
    if env_url:
        return env_url

    config = _load_config()
    yaml_url = (config.get("url") or "").strip()
    if yaml_url:
        return yaml_url

    return None


def db_pool_config() -> dict:
    """asyncpg.create_pool 的 kwargs. yaml 覆盖默认 (min=1 max=10 timeout=10)."""
    config = _load_config()
    pool_cfg = config.get("pool", {}) or {}
    return {
        "min_size": int(pool_cfg.get("min_size", 1)),
        "max_size": int(pool_cfg.get("max_size", 10)),
        "timeout": float(pool_cfg.get("timeout_seconds", 10.0)),
        "command_timeout": float(pool_cfg.get("command_timeout_seconds", 10.0)),
    }


async def get_pool():
    """全局连接池. 没配 DB URL / 没装 asyncpg 返 None. 第一次调用 lazy 创建.

    PG 失败 (连不上 / 认证错) 也返 None, 上层 fallback sqlite/jsonl,
    保证 demo / dev 不需要 PG 也能跑.
    """
    global _POOL
    if _POOL is not None:
        return _POOL

    url = db_url()
    if not url:
        return None

    try:
        import asyncpg  # lazy
    except ImportError:
        logger.warning("asyncpg 未装, gateway fallback sqlite/jsonl. (pip install asyncpg)")
        return None

    try:
        pool_cfg = db_pool_config()
        _POOL = await asyncpg.create_pool(url, **pool_cfg)
        logger.info(
            "gateway PG 池创建成功: %s (min=%d max=%d)",
            _mask_url(url), pool_cfg["min_size"], pool_cfg["max_size"],
        )
        return _POOL
    except Exception as e:
        logger.warning("gateway PG 池创建失败 (%s), fallback sqlite/jsonl: %s",
                       _mask_url(url), e)
        return None


async def close_pool() -> None:
    """优雅关闭. 进程退出调."""
    global _POOL
    if _POOL is not None:
        await _POOL.close()
        _POOL = None


async def fetch_user_metadata(email: str) -> dict | None:
    """查 catfish-identity `users` 表拿真员工 metadata.

    BL-AUTH-DECOUPLE-A1-API-ME-FIX (6/1 鸿波): service token + X-Catfish-User
    场景下, gateway 拿不到 on-behalf user 的 department/role (这些在
    identity 仓 users 表, gateway 跟 identity 共用 PG). 显示 API (/api/me)
    需要真员工元数据让 Companion conditional render 对.

    返 dict: {department, role, managed_departments, must_change_password} 或 None.
    None 含义: PG 没配 (dev/单机 sqlite fallback) / asyncpg 没装 / email 不存在
    / 软删. caller 应该 fallback 到 service token 自己的元数据 (graceful).
    """
    import json
    pool = await get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT department, role, managed_departments, must_change_password "
                "FROM users WHERE email = $1 AND deleted_at IS NULL",
                email,
            )
            if row is None:
                return None
            # managed_departments 在 identity 仓 schema 是 JSONB (db.py:167,
            # alembic 20260502_001). asyncpg 默认不自动 JSON decode JSONB —
            # 返字符串. 没装 JSON codec 时手动 json.loads. 已装 codec 拿到
            # list 直接用. None / 空串 → [].
            mds_raw = row["managed_departments"]
            if mds_raw is None:
                mds = []
            elif isinstance(mds_raw, str):
                try:
                    mds = json.loads(mds_raw) if mds_raw.strip() else []
                except json.JSONDecodeError:
                    logger.warning(
                        "managed_departments not valid JSON for email=%s: %r",
                        email, mds_raw[:100],
                    )
                    mds = []
            elif isinstance(mds_raw, list):
                mds = mds_raw
            else:
                mds = []
            return {
                "department": row["department"],
                "role": row["role"],
                "managed_departments": mds,
                "must_change_password": bool(row["must_change_password"]),
            }
    except Exception:
        logger.exception("fetch_user_metadata failed for email=%s", email)
        return None


def _mask_url(url: str) -> str:
    """日志里隐藏密码."""
    return re.sub(r"(://[^:]+:)([^@]+)(@)", r"\1***\3", url)


__all__ = ["get_pool", "close_pool", "db_url", "fetch_user_metadata"]
