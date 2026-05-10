"""alembic env.py — skills-hub 跟 catfish-gateway / catfish-identity / mcp-registry 同模式.

URL 优先级:
  1. env CATFISH_DB_URL (推荐, 跨 service 一处配)
  2. alembic.ini sqlalchemy.url (占位, 仅 fallback)
  3. 都没 → 报错退出 (alembic 必须有 PG)

BL-D2 Phase 2 (5/10): 复刻 mcp-registry alembic env.py (BL-D3 fix4 模板).
绕开 configparser interpolation, URL 含 '%' percent-encoded 密码不撞 ValueError.

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


# BL-D2 Phase 2 (5/10): version_table 加 _skills_hub 后缀, 跟其他 service 同
# 命名约定 (PG 看 \dt 就知道哪张是哪个 service 的迁移).
# alembic_version_identity / _gateway / _mcp_registry / _skills_hub 共存.
_VERSION_TABLE = "alembic_version_skills_hub"


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
