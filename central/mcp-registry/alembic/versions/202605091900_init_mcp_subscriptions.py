"""init mcp_subscriptions + mcp_audit (BL-D3 Phase 2, 5/9)

跟 catfish-gateway / catfish-identity 共享 PG, 表前缀 mcp_ 防冲突.

Revision ID: 20260509_init
Revises:
Create Date: 2026-05-09
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260509_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_subscriptions",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("user_sub", sa.Text, nullable=False),
        sa.Column("connector_id", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("oauth_token_ref", sa.Text, nullable=True),
        sa.Column(
            "subscribed_at", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.text("NOW()"),
        ),
        sa.Column("oauth_state", sa.Text, nullable=True),
        sa.UniqueConstraint(
            "user_sub", "connector_id",
            name="uq_mcp_sub_user_connector",
        ),
    )
    op.create_index("idx_mcp_sub_user", "mcp_subscriptions", ["user_sub"])
    op.create_index("idx_mcp_sub_connector", "mcp_subscriptions", ["connector_id"])

    op.create_table(
        "mcp_audit",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_sub", sa.Text, nullable=False),
        sa.Column("connector_id", sa.Text, nullable=True),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column(
            "ts", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.text("NOW()"),
        ),
        sa.Column("meta", postgresql.JSONB, nullable=True),
    )
    op.create_index("idx_mcp_audit_user", "mcp_audit", ["user_sub"])
    op.create_index(
        "idx_mcp_audit_ts", "mcp_audit", [sa.text("ts DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_mcp_audit_ts", table_name="mcp_audit")
    op.drop_index("idx_mcp_audit_user", table_name="mcp_audit")
    op.drop_table("mcp_audit")
    op.drop_index("idx_mcp_sub_connector", table_name="mcp_subscriptions")
    op.drop_index("idx_mcp_sub_user", table_name="mcp_subscriptions")
    op.drop_table("mcp_subscriptions")
