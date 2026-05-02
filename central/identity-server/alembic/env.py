"""Alembic env — identity-server schema migration runner.

URL 优先级 (跟 catfish_identity.db.db_url 对齐):
  1. env CATFISH_DB_URL
  2. central/identity-server/config/database.yaml 的 url 字段
  3. 都没 → 报错退出 (alembic 必须 PG, yaml fallback 不走 alembic)

跟 gateway alembic/env.py 一致, 用 create_engine(url) 绕开 alembic.ini configparser
防 URL 里 %XX (URL-encoded password) 被当变量插值.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# 加 src 到 path 复用 catfish_identity.db.db_url
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from catfish_identity.db import db_url  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

url = db_url()
if not url:
    raise SystemExit(
        "alembic 需要 PG URL. 设 CATFISH_DB_URL 或填 config/database.yaml 的 url 字段."
    )

# psycopg3 driver (跟 gateway 一致, psycopg2 在 macOS 装麻烦)
if url.startswith("postgresql://"):
    url = "postgresql+psycopg://" + url[len("postgresql://"):]
elif url.startswith("postgres://"):
    url = "postgresql+psycopg://" + url[len("postgres://"):]

target_metadata = None  # 没用 ORM, 手写 op.execute


# 各服务独立 version table 防共享 PG 时 alembic_version 撞.
# identity-server 用 alembic_version_identity, gateway 用 alembic_version_gateway.
_VERSION_TABLE = "alembic_version_identity"


def run_migrations_offline() -> None:
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=_VERSION_TABLE,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """绕开 configparser 防 URL %XX 插值炸 (gateway 已踩过)."""
    connectable = create_engine(url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=_VERSION_TABLE,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
