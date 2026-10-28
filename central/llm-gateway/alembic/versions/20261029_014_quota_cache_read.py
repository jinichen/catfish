"""10/29 — quota_events 加缓存命中 token (tokens_cache_read).

## 为什么

审计页 / 计费只读 quota_events, 而这张表只有 tokens_in / tokens_out。缓存命中数
网关从 5/19 起一直在抓 (gateway_audit.extra 的 cache_read_tokens), 但没进 quota_events,
计费就只能把整段输入按全价算 —— 本机 10/22–10/29 qwen-flash 输入 1.23 亿里 1.05 亿是
缓存命中, 上游按折扣价收, 门户上的成本因此明显偏高。

tokens_cache_read 是 tokens_in 的**子集** (OpenAI / DashScope / DeepSeek / Gemini 的
prompt_tokens 本来就含 cached_tokens), 不是另加的一份。

## 历史回填

quota_events 和 gateway_audit 是同一次请求先后写的两行, 没有共同 id。按
(user_email, model, tokens_in, tokens_out) 相同 + 时间差在 [-1s, +3s] 内配对
(gateway_audit.ts_ms 是秒取整), 取时间最近的一行。本机实测带缓存数的 audit 行
98.6% 的缓存 token 能配上; 配不上的是不记 quota 的后台调用 —— 员工本机记忆插件
(带 X-Catfish-Internal 头) 和网关自己的 internal:* 调用, quota_events 里本来就没有它们的行。

Revision ID: 20261029_014
Revises: 20261025_013
Create Date: 2026-10-29
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20261029_014"
down_revision: Union[str, None] = "20261025_013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE quota_events ADD COLUMN IF NOT EXISTS tokens_cache_read BIGINT NOT NULL DEFAULT 0")
    op.execute(
        """
        WITH ga AS (
            SELECT ts_ms, user_email, model, tokens_in, tokens_out,
                   (extra->>'cache_read_tokens')::bigint AS cr
            FROM gateway_audit
            WHERE status = 'ok' AND extra ? 'cache_read_tokens'
        ),
        pick AS (
            SELECT DISTINCT ON (q.id) q.id, ga.cr
            FROM quota_events q
            JOIN ga ON ga.user_email = q.user_email
                   AND ga.model = q.model
                   AND ga.tokens_in = q.tokens_in
                   AND ga.tokens_out = q.tokens_out
                   AND q.ts_ms BETWEEN ga.ts_ms - 1000 AND ga.ts_ms + 3000
            WHERE q.tokens_cache_read = 0
            ORDER BY q.id, abs(q.ts_ms - ga.ts_ms)
        )
        UPDATE quota_events q
           SET tokens_cache_read = LEAST(pick.cr, q.tokens_in)
          FROM pick
         WHERE q.id = pick.id AND pick.cr > 0
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE quota_events DROP COLUMN IF EXISTS tokens_cache_read")
