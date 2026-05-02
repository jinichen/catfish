"""Alembic env — gateway schema migration runner.

URL 优先级 (跟 catfish_gateway.db.db_url 对齐):
  1. env CATFISH_DB_URL
  2. central/llm-gateway/config/database.yaml 的 url 字段
  3. 都没 → 报错退出 (alembic 必须有 PG, sqlite fallback 不走 alembic)

注意: 不走 alembic.ini 的 configparser, 直接 create_engine(url) 避开
%XX 插值问题 (URL 里 URL-encoded password 含 %21 等).
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# 加 src 到 path 复用 catfish_gateway.db.db_url
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from catfish_gateway.db import db_url  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

url = db_url()
if not url:
    raise SystemExit(
        "alembic 需要 PG URL. 设 CATFISH_DB_URL 或填 config/database.yaml 的 url 字段."
    )

# SQLAlchemy 默认 'postgresql://' → psycopg2, 我们装的是 psycopg3 (psycopg[binary]).
# 把 URL scheme 改成 'postgresql+psycopg://' 让 SQLAlchemy 走 psycopg3.
# psycopg2 在 macOS 装麻烦 (要 libpq), psycopg3 binary 自带, 选 psycopg3.
if url.startswith("postgresql://"):
    url = "postgresql+psycopg://" + url[len("postgresql://"):]
elif url.startswith("postgres://"):
    url = "postgresql+psycopg://" + url[len("postgres://"):]

# 没用 SQLAlchemy ORM (gateway 直接 SQL), 所以 metadata = None.
target_metadata = None


def run_migrations_offline() -> None:
    """生成 SQL 不连 DB ('offline' 模式)."""
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """连 DB 直接跑 ('online' 模式, 默认).

    用 create_engine(url) 绕开 alembic.ini configparser, 防 URL 里 %XX
    被当成变量插值 (PG 密码 URL-encoded 后含 %21 等会炸).
    """
    connectable = create_engine(url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
