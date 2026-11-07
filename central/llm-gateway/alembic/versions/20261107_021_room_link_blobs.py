"""11/07 — 群里发大文件: 加密文件只在中央暂存一份, 收件人各自来取 (docs/GROUP-CHAT-PLAN.md §4.9).

原来群文件切成 80KB 一块、给每个收件人各投一份进邮筒 (room_link_mailbox), 一条消息合计 5MB 封顶 ——
再大, 中央要存「人数 × 文件大小」, 收件人一次取件要一口气拿完所有块。这里改成:

- 发件人电脑先用随机密钥加密文件, 1MB 一块传上来 —— 中央只有密文, 密钥在发给每个人的那封
  (端到端加密的) 群消息里, 中央拿不到。
- 收件人按块来取; 都取完了 (每人 POST taken) 就删内容, 最晚到期 (7 天) 删。只留元数据 30 天做审计。

Revision ID: 20261107_021
Revises: 20261106_020
Create Date: 2026-11-07
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20261107_021"
down_revision: Union[str, None] = "20261106_020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS room_link_blobs (
            id          TEXT PRIMARY KEY,
            owner_sub   TEXT NOT NULL,
            recipients  TEXT[] NOT NULL,
            taken_by    TEXT[] NOT NULL DEFAULT '{}',
            size        BIGINT NOT NULL,
            n           INTEGER NOT NULL,
            complete    BOOLEAN NOT NULL DEFAULT false,
            purged      BOOLEAN NOT NULL DEFAULT false,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at  TIMESTAMPTZ NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS room_link_blobs_owner ON room_link_blobs (owner_sub) WHERE NOT purged")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS room_link_blob_chunks (
            blob_id  TEXT NOT NULL REFERENCES room_link_blobs (id) ON DELETE CASCADE,
            i        INTEGER NOT NULL,
            data     BYTEA NOT NULL,
            PRIMARY KEY (blob_id, i)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS room_link_blob_chunks")
    op.execute("DROP TABLE IF EXISTS room_link_blobs")
