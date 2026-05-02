"""Initial: users + registry_agents (五一 sprint 5/2 收尾)

替代 db.py 老 SCHEMA_SQL idempotent CREATE TABLE. 跟 gateway 对齐, 各服务自管 alembic.

跟 gateway alembic 共享同一 PG database, 不同表前缀:
  - identity-server: users, registry_agents (本 migration)
  - gateway:         quota_events, gateway_audit (gateway alembic 管)

Revision ID: 20260502_001
Revises:
Create Date: 2026-05-02
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260502_001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users (员工身份) ──────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS users (
            email                TEXT PRIMARY KEY,
            password_hash        TEXT NOT NULL,
            name                 TEXT NOT NULL DEFAULT '',
            department           TEXT NOT NULL DEFAULT '',
            tier                 TEXT NOT NULL DEFAULT 'employee',
            role                 TEXT NOT NULL DEFAULT '',
            managed_departments  JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # ── registry_agents (Plan D Federation 注册表) ────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS registry_agents (
            sub               TEXT PRIMARY KEY,
            catfish_endpoint  TEXT NOT NULL,
            jwks_uri          TEXT NOT NULL,
            public_pem        TEXT NOT NULL DEFAULT '',
            department        TEXT NOT NULL DEFAULT '',
            capabilities      JSONB NOT NULL DEFAULT '[]'::jsonb,
            last_seen         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_registry_last_seen ON registry_agents(last_seen)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_registry_last_seen")
    op.execute("DROP TABLE IF EXISTS registry_agents")
    op.execute("DROP TABLE IF EXISTS users")
