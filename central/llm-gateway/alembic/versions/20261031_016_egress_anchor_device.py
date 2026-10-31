"""10/31 — 外发记录锚点加 device_id (换机迁移, docs/DEVICE-MIGRATION-DESIGN.md §6.2).

## 为什么

锚点原来只按人 (sub) 记。员工换机后, 新机接着旧机那条链往下写; 旧机如果导出后还在用,
它也接着写自己那份并报锚点 —— 中央就收到两条互相对不上的链头, 校验报「被动过」。
带上设备号以后, 本机校验只对「自己」和「迁来的那台在迁移点之前」的锚点。

device_id 是 Companion 本机随机生成的 UUID, 只是元数据, 不涉及任何内容。老锚点为空。

Revision ID: 20261031_016
Revises: 20261029_015
Create Date: 2026-10-31
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20261031_016"
down_revision: Union[str, None] = "20261029_015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE egress_anchor ADD COLUMN IF NOT EXISTS device_id TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE egress_anchor DROP COLUMN IF EXISTS device_id")
