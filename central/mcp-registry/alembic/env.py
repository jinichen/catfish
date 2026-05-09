"""alembic env.py — mcp-registry 跟 catfish-gateway 同模式 (env CATFISH_DB_URL 注入).

migrations/ 里的脚本由 `alembic revision -m "..."` 生成, `alembic upgrade head`
跑迁移. dev / 单测走 sqlite fallback 不用 alembic.
"""
from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

# env CATFISH_DB_URL 优先于 alembic.ini 里的占位
db_url = os.environ.get("CATFISH_DB_URL", "").strip()
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=None,
        literal_binds=True, dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
