"""11/01 — 手机中转站: 配对关系 + 连接记录 (docs/MOBILE-CLIENT-DESIGN.md §3).

只有元数据: 哪个员工、哪台手机 / 电脑、何时连上断开、转了多少字节。没有内容列。
连接记录 30 天删 (relay_db.PgStore.conn_start 写入时顺手清)。

Revision ID: 20261101_017
Revises: 20261031_016
Create Date: 2026-11-01
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20261101_017"
down_revision: Union[str, None] = "20261031_016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mobile_pair (
            sub          TEXT NOT NULL,
            phone_device TEXT NOT NULL,
            pc_device    TEXT NOT NULL,
            phone_name   TEXT,
            paired_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (sub, phone_device, pc_device)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS relay_connection (
            id          BIGSERIAL PRIMARY KEY,
            sub         TEXT NOT NULL,
            device_id   TEXT NOT NULL,
            role        TEXT NOT NULL,
            device_name TEXT,
            started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
            ended_at    TIMESTAMPTZ,
            bytes_in    BIGINT NOT NULL DEFAULT 0,
            bytes_out   BIGINT NOT NULL DEFAULT 0
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS relay_connection_online ON relay_connection (sub, last_seen) "
        "WHERE ended_at IS NULL"
    )
    op.execute("CREATE INDEX IF NOT EXISTS relay_connection_started ON relay_connection (started_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS relay_connection")
    op.execute("DROP TABLE IF EXISTS mobile_pair")
