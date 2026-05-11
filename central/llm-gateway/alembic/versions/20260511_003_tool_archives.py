"""BL-Q3-ARCHIVE — tool message archive 表 (5/11)

修 context overflow 真根因. tool message content > 4KB 走 lossless archive,
prompt 里替换成 ref + 头尾 + haiku 摘要. LLM 主动 catfish_read_tool_archive
召回中段.

跟 facts_db / mcp-registry / skills-hub 同 _use_pg() 模式. PG 失败 → jsonl
兜底.

设计文档: docs/CATFISH-Q3-ARCHIVE-DESIGN.md

Revision ID: 20260511_003
Revises: 20260510_002
Create Date: 2026-05-11
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260511_003"
down_revision: Union[str, None] = "20260510_002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── tool_archives: tool message 全量 archive (lossless) ────────
    # 一行 = 一条超过阈值的 role=tool message. content 列存全文 (TEXT),
    # 后续 LLM 调 catfish_read_tool_archive 按 ref 查回, 支持 grep / line_range
    # 召回片段.
    #
    # ref = sha256(content + ":" + tool_call_id)[:16]
    # session_id = "<user_email>:<conversation_id>" 或 "<user_email>:<date>" 兜底
    op.execute("""
        CREATE TABLE IF NOT EXISTS tool_archives (
            ref             VARCHAR(16) PRIMARY KEY,
            session_id      VARCHAR(128) NOT NULL,
            user_email      VARCHAR(255) NOT NULL,
            tool_call_id    VARCHAR(128),
            tool_name       VARCHAR(128),
            content         TEXT NOT NULL,
            content_bytes   INTEGER NOT NULL,
            lines           INTEGER NOT NULL,
            summary         TEXT,
            summary_model   VARCHAR(64),
            summary_at      TIMESTAMP,
            summary_error   TEXT,
            created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
            expires_at      TIMESTAMP NOT NULL DEFAULT (NOW() + INTERVAL '14 days')
        )
    """)

    # session 索引: 按 user 拉 archive 列表用
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_tool_archives_session
        ON tool_archives(session_id)
    """)

    # user 索引: 鉴权 (read tool 要校验 archive.user_email == 当前 user)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_tool_archives_user
        ON tool_archives(user_email)
    """)

    # GC 用: expires_at < NOW() 扫表
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_tool_archives_expires
        ON tool_archives(expires_at)
    """)

    # summary worker 扫表用: WHERE summary IS NULL ORDER BY created_at
    # partial index 省空间 (绝大部分行都有 summary)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_tool_archives_summary_null
        ON tool_archives(created_at)
        WHERE summary IS NULL AND summary_error IS NULL
    """)

    # created_at 时间序列查询 (admin UI 按时间倒序)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_tool_archives_created_at
        ON tool_archives(created_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_tool_archives_created_at")
    op.execute("DROP INDEX IF EXISTS ix_tool_archives_summary_null")
    op.execute("DROP INDEX IF EXISTS ix_tool_archives_expires")
    op.execute("DROP INDEX IF EXISTS ix_tool_archives_user")
    op.execute("DROP INDEX IF EXISTS ix_tool_archives_session")
    op.execute("DROP TABLE IF EXISTS tool_archives")
