"""9/10 — RoomLink 邮筒 (P50: 横向协同, 中央只中转不看内容).

## 为什么要一张表

同事 A 请 B 帮忙 (hermes 0.21 RoomLink) 需要三次握手:
A 发「请求」→ B 批准后回「授权 (grant)」→ A 拿 grant 直连 B:8642 派工。
前两步 A 和 B 互相不知道对方地址, 只有中央两边都认识 —— 所以中央当邮筒。

gateway 跑 4 个 worker, 进程内存不共享, 所以只能落 PG。

## 只中转, 不看内容

payload 是 TEXT 不是 JSONB —— 故意的。中央端**严禁**看员工端数据,
JSONB 会诱使以后有人在 SQL 里 `payload->>'room_id'` 做统计。TEXT 就是不透明字节串。
Phase 2 上 PKI 后 payload 直接换成密文, 表结构不用改。

## 取走即清空, 行留作审计

GET 一次就 `payload = NULL, consumed_at = now()`: 内容中央不留副本,
但 from/to/kind/时间 保留 —— 这是唯一允许中央看到的元数据, 也就是审计。
过期 (10 分钟) 没取的同样清空 payload。30 天后整行删。

Revision ID: 20260910_009
Revises: 20260801_008
Create Date: 2026-09-10
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260910_009"
down_revision: Union[str, None] = "20260801_008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS room_link_mailbox (
            id           BIGSERIAL PRIMARY KEY,
            from_sub     TEXT        NOT NULL,
            to_sub       TEXT        NOT NULL,
            kind         TEXT        NOT NULL,
            payload      TEXT,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at   TIMESTAMPTZ NOT NULL,
            consumed_at  TIMESTAMPTZ
        )
        """
    )
    # GET 只按收件人查未取走的
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_room_link_mailbox_inbox
            ON room_link_mailbox (to_sub, created_at)
            WHERE payload IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS room_link_mailbox")
