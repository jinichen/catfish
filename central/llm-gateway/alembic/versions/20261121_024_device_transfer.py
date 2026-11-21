"""11/21 — 换机直传 (docs/DEVICE-MIGRATION-DESIGN.md §12): 旧电脑把迁移包经中央「边传边取」送到新电脑。

迁移包在旧电脑上用新电脑现场生成的公钥 (age x25519, 新电脑屏幕上的二维码里) 加密, 中央只见密文、没有私钥。
中央不存整包: 只留一个滑动窗口 (默认 64MB) 的块, 新电脑取走一块就删一块; 两台都得在线。
半小时没动静就作废、删块。元数据 (谁、哪两台、多大、起止) 留 30 天审计。

Revision ID: 20261121_024
Revises: 20261117_023
Create Date: 2026-11-21
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20261121_024"
down_revision: Union[str, None] = "20261117_023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS device_transfer (
            id           TEXT PRIMARY KEY,
            owner_sub    TEXT NOT NULL,
            code         TEXT NOT NULL,
            recipient    TEXT NOT NULL,
            to_device    TEXT NOT NULL DEFAULT '',
            from_device  TEXT NOT NULL DEFAULT '',
            state        TEXT NOT NULL DEFAULT 'waiting',
            size         BIGINT,
            n            INTEGER,
            acked        INTEGER NOT NULL DEFAULT 0,
            relayed      BIGINT NOT NULL DEFAULT 0,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS device_transfer_owner ON device_transfer (owner_sub, state)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS device_transfer_chunks (
            transfer_id  TEXT NOT NULL REFERENCES device_transfer (id) ON DELETE CASCADE,
            i            INTEGER NOT NULL,
            data         BYTEA NOT NULL,
            PRIMARY KEY (transfer_id, i)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS device_transfer_chunks")
    op.execute("DROP TABLE IF EXISTS device_transfer")
