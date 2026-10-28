"""10/29 — quota_events 记后台任务 (background), 补回历史上漏记的调用.

## 为什么

带 X-Catfish-Internal 头 (token 有 background.tasks scope) 的调用 —— 员工本机记忆插件
(知识归类 / 生成 / 合并 / 记忆总结) 这类后台任务, 以及网关自己的 internal:* 调用 ——
从 BL-F17 (5/5) 起整条不写 quota_events。本意是不扣员工配额, 但审计页和成本只读这张表,
于是这些调用的花费在门户上根本不出现, 上游照样收钱。本机 10/22–10/29 qwen-flash:
网关记录 3613 次成功调用, quota_events 只有 2069 次, 漏掉的输出 token 占 38%。

现在照写、打 background 标记: 判超额 (sum_tokens_*) 和配额页排行排除它, 审计 / 成本照算。

## 历史回填

gateway_audit 每次调用都记了 (含后台任务)。把 status=ok、有 token、且在 quota_events 里
找不到同一次请求 (同人同模型同进出、时间差 ±30s) 的行补进来, 标 background。
配对窗口比 014 宽 (±30s), 宁可少补一两行也不把已记的请求重复记一遍。
gateway_audit 没有部门, 取该员工在 quota_events 里最近一次的部门。

Revision ID: 20261029_015
Revises: 20261029_014
Create Date: 2026-10-29
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20261029_015"
down_revision: Union[str, None] = "20261029_014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE quota_events ADD COLUMN IF NOT EXISTS background BOOLEAN NOT NULL DEFAULT false")
    op.execute(
        """
        INSERT INTO quota_events (ts_ms, user_email, department, model, tokens_in, tokens_out,
                                  tokens_cache_read, background)
        SELECT ga.ts_ms, ga.user_email,
               COALESCE((SELECT q2.department FROM quota_events q2
                          WHERE q2.user_email = ga.user_email
                          ORDER BY q2.ts_ms DESC LIMIT 1), ''),
               ga.model, ga.tokens_in, ga.tokens_out,
               LEAST(COALESCE((ga.extra->>'cache_read_tokens')::bigint, 0), ga.tokens_in),
               true
          FROM gateway_audit ga
         WHERE ga.status = 'ok'
           AND (ga.tokens_in > 0 OR ga.tokens_out > 0)
           AND ga.user_email <> ''
           AND NOT EXISTS (
                 SELECT 1 FROM quota_events q
                  WHERE q.user_email = ga.user_email
                    AND q.model = ga.model
                    AND q.tokens_in = ga.tokens_in
                    AND q.tokens_out = ga.tokens_out
                    AND q.ts_ms BETWEEN ga.ts_ms - 30000 AND ga.ts_ms + 30000)
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_quota_ts_bg ON quota_events(ts_ms) WHERE background")


def downgrade() -> None:
    op.execute("DELETE FROM quota_events WHERE background")
    op.execute("DROP INDEX IF EXISTS idx_quota_ts_bg")
    op.execute("ALTER TABLE quota_events DROP COLUMN IF EXISTS background")
