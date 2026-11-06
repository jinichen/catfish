"""11/06 — 公司级设置 (下发给员工 Companion 的那几个数), 先只有「群聊记录保留天数」(docs/GROUP-CHAT-PLAN.md §4.2).

一行一个设置: key → value (字符串)。只有管理员能改; 员工 Companion 每次同步顺带取。不涉员工内容。

Revision ID: 20261106_020
Revises: 20261106_019
Create Date: 2026-11-06
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20261106_020"
down_revision: Union[str, None] = "20261106_019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS org_policy (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by  TEXT
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS org_policy")
