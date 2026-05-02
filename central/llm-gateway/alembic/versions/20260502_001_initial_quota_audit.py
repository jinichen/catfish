"""Initial: quota_events + gateway_audit (五一 sprint 5/2 收尾)

替代 db.py 老 idempotent CREATE TABLE. PG 主存储, sqlite 仍走旧路径作 fallback.

跟 catfish-identity 共享同一 PG database. 表名前缀防冲突 (quota_events / gateway_audit
跟 users / registry_agents 同 schema 但不同表).

Revision ID: 20260502_001
Revises:
Create Date: 2026-05-02
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260502_001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── quota_events: token 用量滑动窗口 ──────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS quota_events (
            id          BIGSERIAL PRIMARY KEY,
            ts_ms       BIGINT NOT NULL,
            user_email  TEXT NOT NULL,
            department  TEXT NOT NULL DEFAULT '',
            model       TEXT NOT NULL,
            tokens_in   BIGINT NOT NULL DEFAULT 0,
            tokens_out  BIGINT NOT NULL DEFAULT 0
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_quota_ts_user  ON quota_events(ts_ms, user_email)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_quota_ts_model ON quota_events(ts_ms, model)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_quota_ts_dept  ON quota_events(ts_ms, department)")

    # ── gateway_audit: 中央 metadata audit (无对话内容) ───────
    # 跟 metrics.py 老 jsonl 格式对齐: ts / user_id / model / tokens / latency / status / error
    # 结构化 PG 比 jsonl 查询快 100x (按 user / 按 model / 按时间范围).
    # 边界: 永远不存 prompt / completion / tool args.
    op.execute("""
        CREATE TABLE IF NOT EXISTS gateway_audit (
            id              BIGSERIAL PRIMARY KEY,
            ts_ms           BIGINT NOT NULL,
            user_email      TEXT NOT NULL DEFAULT '',
            department      TEXT NOT NULL DEFAULT '',
            model           TEXT NOT NULL DEFAULT '',
            tokens_in       BIGINT NOT NULL DEFAULT 0,
            tokens_out      BIGINT NOT NULL DEFAULT 0,
            tokens_total    BIGINT NOT NULL DEFAULT 0,
            latency_ms      INTEGER NOT NULL DEFAULT 0,
            ttft_ms         INTEGER,
            status          TEXT NOT NULL DEFAULT 'ok',
            error_code      TEXT NOT NULL DEFAULT '',
            error_msg       TEXT NOT NULL DEFAULT '',
            auth_method     TEXT NOT NULL DEFAULT 'unknown',
            security_concerns JSONB NOT NULL DEFAULT '[]'::jsonb,
            extra           JSONB NOT NULL DEFAULT '{}'::jsonb
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts_user   ON gateway_audit(ts_ms, user_email)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts_model  ON gateway_audit(ts_ms, model)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts_status ON gateway_audit(ts_ms, status)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_audit_ts_status")
    op.execute("DROP INDEX IF EXISTS idx_audit_ts_model")
    op.execute("DROP INDEX IF EXISTS idx_audit_ts_user")
    op.execute("DROP TABLE IF EXISTS gateway_audit")
    op.execute("DROP INDEX IF EXISTS idx_quota_ts_dept")
    op.execute("DROP INDEX IF EXISTS idx_quota_ts_model")
    op.execute("DROP INDEX IF EXISTS idx_quota_ts_user")
    op.execute("DROP TABLE IF EXISTS quota_events")
