"""Alembic env — gateway schema migration runner.

URL 优先级 (跟 catfish_gateway.db.db_url 对齐):
  1. env CATFISH_DB_URL
  2. central/llm-gateway/config/database.yaml 的 url 字段
  3. 都没 → 报错退出 (alembic 必须有 PG, sqlite fallback 不走 alembic)
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 加 src 到 path 复用 catfish_gateway.db.db_url
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from catfish_gateway.db import db_url  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 用 catfish_gateway 的 db_url 配置 alembic, 优先 env, 否则 yaml
url = db_url()
if not url:
    raise SystemExit(
        "alembic 需要 PG URL. 设 CATFISH_DB_URL 或填 config/database.yaml 的 url 字段."
    )
config.set_main_option("sqlalchemy.url", url)

# 没用 SQLAlchemy ORM (gateway 直接 SQL), 所以 metadata = None.
# 自动 autogenerate 不可用, 手写 op.execute 即可.
target_metadata = None


def run_migrations_offline() -> None:
    """生成 SQL 不连 DB ('offline' 模式)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """连 DB 直接跑 ('online' 模式, 默认)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
