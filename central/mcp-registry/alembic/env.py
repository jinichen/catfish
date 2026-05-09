"""alembic env.py — mcp-registry 跟 catfish-gateway / catfish-identity 同模式.

URL 优先级:
  1. env CATFISH_DB_URL (推荐, 跨 service 一处配)
  2. alembic.ini sqlalchemy.url (占位, 仅 fallback)
  3. 都没 → 报错退出 (alembic 必须有 PG)

BL-D3 fix4 (5/9): 之前用 set_main_option 走 configparser, 密码含 '%' (RFC 3986
percent-encoding) 触发 ValueError: invalid interpolation syntax. 改跟 gateway/
identity 同款 — 直接传 url 给 context.configure / engine.create_engine, 绕开
configparser interpolation 完全无 escape 问题.

driver 自动归一化:
  postgresql://...  → postgresql+psycopg://... (强制用 psycopg3, 不要 psycopg2)
  postgres://...    → postgresql+psycopg://... (兼容老格式)
"""
from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config


def _resolve_url() -> str:
    """从 env 拿 URL + 自动归一化为 psycopg3 driver."""
    url = os.environ.get("CATFISH_DB_URL", "").strip()
    if not url:
        raise SystemExit(
            "alembic 需要 PG URL. 设 CATFISH_DB_URL=postgresql://user:pwd@host/db"
        )
    # 强制用 psycopg3 driver (跟 db.py 用的 psycopg 一致, 不引 psycopg2)
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    elif url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    return url


# BL-D3 fix3 (5/9): version_table 加 _mcp_registry 后缀, 跟 gateway/identity 同
# 命名约定 (PG 看就知道哪张表是哪个 service 的迁移).
_VERSION_TABLE = "alembic_version_mcp_registry"


def run_migrations_offline() -> None:
    url = _resolve_url()
    context.configure(
        url=url, target_metadata=None,
        literal_binds=True, dialect_opts={"paramstyle": "named"},
        version_table=_VERSION_TABLE,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _resolve_url()
    # 直接 create_engine 不走 engine_from_config(configparser),
    # URL 含 '%' percent-encoding 不再撞 interpolation
    connectable = create_engine(url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=None,
            version_table=_VERSION_TABLE,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
