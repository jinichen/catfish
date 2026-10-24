"""10/25 — 员工本机「数据外发记录」的每日指纹 (锚点).

## 为什么中央要存这个

员工本机 ~/.catfish/outbound_log.db 记下所有带内容出本机的请求, 连内容, 每行带指纹链
(改 / 删中间任何一行都对不上)。但删掉**末尾**几行本机自己查不出 —— 所以 Companion 每天把
链头 (last_id, last_hash) 报上来。有争议时拿中央这份对: 本机对不上 = 被动过。

## 只有指纹, 没有内容

这里只存一个 sha256 和一个序号, 从它推不出任何内容。中央看不到员工发了什么,
只能证明「那天的记录后来没被改」。

Revision ID: 20261025_013
Revises: 20261021_012
Create Date: 2026-10-25
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20261025_013"
down_revision: Union[str, None] = "20261021_012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS egress_anchor (
            id           BIGSERIAL PRIMARY KEY,
            sub          TEXT        NOT NULL,
            last_id      BIGINT      NOT NULL,
            last_hash    TEXT        NOT NULL,
            row_count    BIGINT      NOT NULL DEFAULT 0,
            received_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_egress_anchor_sub ON egress_anchor (sub, received_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS egress_anchor")
