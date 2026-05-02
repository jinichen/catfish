"""中央数据库连接 — PostgreSQL 主存储, yaml/sqlite 老路径作 fallback.

# 切换逻辑

读 env `CATFISH_DB_URL` 决定后端:
- 有值 (例 `postgresql://catfish:xxx@localhost:5432/catfish`) → 走 PG
- 无值 → 走老 yaml / sqlite (单元测试 / 单机 dev mode 用)

# 共享组件

catfish-identity 跟 gateway 共用同一个 PG 实例 + 不同表:
- catfish-identity: `users`, `registry_agents`
- gateway: `quota_events`, `gateway_audit`

每个进程独立维护连接池. 连接 < 10 个/进程, asyncpg 池.

# Schema migration

启动时自动 `CREATE TABLE IF NOT EXISTS` (轻量 idempotent).
真生产用 alembic / sqitch, Phase 2 加. Phase 1 简化版够 demo.
"""

from __future__ import annotations

import logging
import os
from typing import Any, AsyncIterator

from pathlib import Path

import asyncpg
import yaml

logger = logging.getLogger("catfish.db")


_POOL: asyncpg.Pool | None = None
_CONFIG_CACHE: dict | None = None


def _load_config() -> dict:
    """读 config/database.yaml. 失败 / 文件不存在返空 dict."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    # 优先级: env CATFISH_DB_CONFIG > 标准位置
    custom = os.environ.get("CATFISH_DB_CONFIG", "").strip()
    if custom:
        path = Path(custom).expanduser()
    else:
        # config/database.yaml (跟 users.yaml 同目录)
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

    None 时上层 fallback yaml/sqlite (单测 / 单机 dev 模式).
    """
    # 1. env (跨进程统一最优先, docker-compose / k8s 用)
    env_url = os.environ.get("CATFISH_DB_URL", "").strip()
    if env_url:
        return env_url

    # 2. config/database.yaml
    config = _load_config()
    yaml_url = (config.get("url") or "").strip()
    if yaml_url:
        return yaml_url

    # 3. 都没 → yaml/sqlite fallback
    return None


def db_pool_config() -> dict:
    """返 asyncpg.create_pool 的 kwargs (min_size / max_size / timeout 等).

    yaml 配的覆盖默认值. 默认: min=1, max=10, timeout=10s.
    """
    config = _load_config()
    pool_cfg = config.get("pool", {}) or {}
    return {
        "min_size": int(pool_cfg.get("min_size", 1)),
        "max_size": int(pool_cfg.get("max_size", 10)),
        "timeout": float(pool_cfg.get("timeout_seconds", 10.0)),
        "command_timeout": float(pool_cfg.get("command_timeout_seconds", 10.0)),
    }


async def get_pool() -> asyncpg.Pool | None:
    """全局连接池. 没配 DB URL 返 None.

    第一次调用时 lazy 创建, 后续复用.
    """
    global _POOL
    if _POOL is not None:
        return _POOL

    url = db_url()
    if not url:
        return None

    try:
        pool_cfg = db_pool_config()
        _POOL = await asyncpg.create_pool(url, **pool_cfg)
        logger.info(
            "PG 连接池创建成功: %s (min=%d max=%d)",
            _mask_url(url), pool_cfg["min_size"], pool_cfg["max_size"],
        )
        return _POOL
    except Exception as e:
        logger.warning("PG 连接池创建失败 (%s), fallback yaml/sqlite: %s", _mask_url(url), e)
        return None


async def close_pool() -> None:
    """优雅关闭连接池. 进程退出时调."""
    global _POOL
    if _POOL is not None:
        await _POOL.close()
        _POOL = None


def _mask_url(url: str) -> str:
    """日志里隐藏密码."""
    import re
    return re.sub(r"(://[^:]+:)([^@]+)(@)", r"\1***\3", url)


# ── 启动时创建 schema (idempotent) ──────────────────────────


SCHEMA_SQL = """
-- Users (catfish-identity)
CREATE TABLE IF NOT EXISTS users (
    email           TEXT PRIMARY KEY,
    password_hash   TEXT NOT NULL,
    name            TEXT NOT NULL DEFAULT '',
    department      TEXT NOT NULL DEFAULT '',
    tier            TEXT NOT NULL DEFAULT 'employee',
    role            TEXT NOT NULL DEFAULT '',
    managed_departments JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Registry agents (Plan D Federation)
CREATE TABLE IF NOT EXISTS registry_agents (
    sub             TEXT PRIMARY KEY,
    catfish_endpoint TEXT NOT NULL,
    jwks_uri        TEXT NOT NULL,
    public_pem      TEXT NOT NULL DEFAULT '',
    department      TEXT NOT NULL DEFAULT '',
    capabilities    JSONB NOT NULL DEFAULT '[]'::jsonb,
    last_seen       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_registry_last_seen ON registry_agents(last_seen);

-- Quota events (gateway)
CREATE TABLE IF NOT EXISTS quota_events (
    id              BIGSERIAL PRIMARY KEY,
    ts_ms           BIGINT NOT NULL,
    user_email      TEXT NOT NULL,
    department      TEXT NOT NULL DEFAULT '',
    model           TEXT NOT NULL,
    tokens_in       BIGINT NOT NULL DEFAULT 0,
    tokens_out      BIGINT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_quota_ts_user  ON quota_events(ts_ms, user_email);
CREATE INDEX IF NOT EXISTS idx_quota_ts_model ON quota_events(ts_ms, model);
CREATE INDEX IF NOT EXISTS idx_quota_ts_dept  ON quota_events(ts_ms, department);
"""


async def init_schema() -> bool:
    """启动时跑一次. 没 PG 配置静默跳过.

    返 True = schema 已就绪, False = PG 未配置或失败.
    """
    pool = await get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as conn:
            # asyncpg 一次只能跑一个语句, 拆 SQL
            for stmt in [s.strip() for s in SCHEMA_SQL.split(";") if s.strip()]:
                await conn.execute(stmt)
        logger.info("PG schema 初始化完成")
        return True
    except Exception as e:
        logger.warning("PG schema 初始化失败: %s", e)
        return False


__all__ = ["get_pool", "close_pool", "init_schema", "db_url"]
