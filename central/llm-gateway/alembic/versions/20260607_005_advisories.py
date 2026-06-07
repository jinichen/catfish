"""BL-MANIFESTO-ADVISORY-PHASE2 — advisories 表 (6/7).

中央服务 publish advisory 持久化. Phase 1 (6/7) 用 yaml + git push,
Phase 2 (本次) 走 PG admin POST + 客户端 pull feed.

跟 catfish-central-manifesto 公理 4 一致 — advisory 是中央 publish 内容,
不是员工对话, 中央存合规 (员工对话仍 0 出端).

Revision ID: 20260607_005
Revises: 20260511_004
Create Date: 2026-06-07
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260607_005"
down_revision: Union[str, None] = "20260511_004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS advisories (
            id VARCHAR(64) PRIMARY KEY,
            severity VARCHAR(16) NOT NULL,
            category VARCHAR(32) NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            recommendation TEXT,
            target_json JSONB,
            references_json JSONB,
            tags_json JSONB,
            remediation_actions_json JSONB,
            published TIMESTAMPTZ NOT NULL,
            expires TIMESTAMPTZ,
            published_by VARCHAR(255) NOT NULL,
            revoked_at TIMESTAMPTZ,
            revoked_by VARCHAR(255)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_advisories_published
        ON advisories(published DESC)
    """)
    # active = revoked_at IS NULL AND (expires IS NULL OR expires > now())
    # PG 不支持 now() 在 partial index, 所以只能 partial on revoked_at
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_advisories_active
        ON advisories(severity) WHERE revoked_at IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_advisories_active")
    op.execute("DROP INDEX IF EXISTS ix_advisories_published")
    op.execute("DROP TABLE IF EXISTS advisories")
