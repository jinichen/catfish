"""alembic env.py — wiki-hub. 复刻 skills-hub env.py 改 _VERSION_TABLE.

URL 优先级:
  1. env CATFISH_DB_URL (推荐, 跨 service 一处配)
  2. alembic.ini sqlalchemy.url (占位, 仅 fallback)
  3. 都没 → 报错退出 (alembic 必须有 PG)

driver 自动归一化:
  postgresql://...  → postgresql+psycopg://...
  postgres://...    → postgresql+psycopg://...
"""
from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config


def _resolve_url() -> str:
    url = os.environ.get("CATFISH_DB_URL", "").strip()
    if not url:
        raise SystemExit(
            "alembic 需要 PG URL. 设 CATFISH_DB_URL=postgresql://user:pwd@host/db"
        )
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    elif url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    return url


# 跟 skills-hub / mcp-registry / gateway / identity 同命名约定, version_table
# 加 _wiki_hub 后缀防撞.
_VERSION_TABLE = "alembic_version_wiki_hub"


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
