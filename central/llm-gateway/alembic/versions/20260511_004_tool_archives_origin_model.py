"""BL-Q3-ARCHIVE fix2 — tool_archives 加 origin_model 列 (5/11).

鸿波: '不可能再用小模型, summary 直接用 chat 同款模型'.

私有部署下 token 不要钱, 用 chat 同款模型 = 数据走向一致 + GPU 同 shard +
不用维护 catalog tool_summarizer tag. summary_worker 读这列优先调.

Revision ID: 20260511_004
Revises: 20260511_003
Create Date: 2026-05-11
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260511_004"
down_revision: Union[str, None] = "20260511_003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE tool_archives
        ADD COLUMN IF NOT EXISTS origin_model VARCHAR(128)
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE tool_archives DROP COLUMN IF EXISTS origin_model")
