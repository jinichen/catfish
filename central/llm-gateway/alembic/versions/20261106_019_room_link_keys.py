"""11/06 — 同事协作邮筒的端到端加密: 员工电脑的公钥目录 (docs/GROUP-CHAT-PLAN.md §4.1).

每台员工电脑一对 X25519 密钥, 私钥只在那台电脑上; 这里只存**公钥** (不是员工内容)。
发件端按收件人查公钥、在本机加密后再投进 room_link_mailbox —— 中央库里只剩密文。

Revision ID: 20261106_019
Revises: 20261101_018
Create Date: 2026-11-06
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20261106_019"
down_revision: Union[str, None] = "20261101_018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS room_link_keys (
            sub         TEXT NOT NULL,
            device_id   TEXT NOT NULL,
            pubkey      TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (sub, device_id)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS room_link_keys")
