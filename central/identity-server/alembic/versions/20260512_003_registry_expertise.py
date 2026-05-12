"""BL-FED2.2 (5/12 鸿波拍板) — registry_agents 加 expertise 列, 黄页用.

跟 capabilities 区分:
  - capabilities: 协议层 ("a2a.ask"), 是 agent 间协议互通 capability
  - expertise:   业务层 ("资质", "外勤报销"), 给员工"找谁问"用

数据来源:
  - 各 employee 自己机器 ~/.catfish/expertise.yaml 里 status=confirmed 的 tag
  - gateway self_register 上报时带过来 (隐私边界: 只 confirmed, 不带 evidence)

Revision ID: 20260512_003
Revises: 20260510_002
Create Date: 2026-05-12
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa  # noqa: F401  (alembic 习惯 import)


revision: str = "20260512_003"
down_revision: Union[str, None] = "20260510_002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 加 expertise JSONB, 默认 [] (老 agent 没上报视为无 confirmed 专长)
    op.execute("""
        ALTER TABLE registry_agents
        ADD COLUMN IF NOT EXISTS expertise JSONB NOT NULL DEFAULT '[]'::jsonb
    """)
    # GIN 索引 — by-expertise 查 tag 用 (tag 数 < 100 小集合, GIN 比 btree 适合)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_registry_expertise_gin
        ON registry_agents USING GIN (expertise)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_registry_expertise_gin")
    op.execute("ALTER TABLE registry_agents DROP COLUMN IF EXISTS expertise")
