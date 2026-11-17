"""11/17 — 出图 / 出视频: quota_events 加 media_units, 新表 media_jobs。

## media_units

文生图 / 图生图按张、文生视频 / 图生视频按秒计费 (上游就是这么收的), 跟 token 不是一回事。
这类请求 tokens_in / tokens_out 记 0, 张数或秒数记在 media_units, 审计页按模型的
price_per_unit (元 / 张、元 / 秒) 算成本。对话 / 向量模型的行这一列是 0, 老数据不用回填。

## media_jobs

视频要几分钟才出来, 上游是「提交任务 → 轮询」。员工端提交后拿到的是网关的任务号, 网关记下:
谁提交的 (只有本人能查 / 取)、哪个模型、上游任务号、状态、出来后的临时下载地址 (上游 24 小时过期)、
算没算过费 (metered —— 员工端会反复轮询, 成功那次只能记一次账)。

只存元数据, 不存提示词、不存视频内容。30 天前的行在提交新任务时顺手删。

Revision ID: 20261117_023
Revises: 20261114_022
Create Date: 2026-11-17
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20261117_023"
down_revision: Union[str, None] = "20261114_022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE quota_events ADD COLUMN IF NOT EXISTS media_units DOUBLE PRECISION NOT NULL DEFAULT 0")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS media_jobs (
            id            TEXT PRIMARY KEY,
            ts_ms         BIGINT NOT NULL,
            updated_ms    BIGINT NOT NULL,
            user_email    TEXT NOT NULL,
            department    TEXT NOT NULL DEFAULT '',
            model         TEXT NOT NULL,
            mode          TEXT NOT NULL,
            upstream_task TEXT NOT NULL,
            status        TEXT NOT NULL,
            units         DOUBLE PRECISION NOT NULL DEFAULT 0,
            result_url    TEXT NOT NULL DEFAULT '',
            error         TEXT NOT NULL DEFAULT '',
            metered       BOOLEAN NOT NULL DEFAULT false
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_media_jobs_user_ts ON media_jobs(user_email, ts_ms)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS media_jobs")
    op.execute("ALTER TABLE quota_events DROP COLUMN IF EXISTS media_units")
