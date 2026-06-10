"""init wiki_documents + wiki_audit (P3.3.18, 6/10)

跟 skills-hub / mcp-registry / gateway 共享 PG, 表前缀 wiki_ 防冲突.
元数据进 PG (索引 / join 友好), markdown 文件内容 + frontmatter 进 PG (text 字段,
跟 skill 那种二进制目录不同 — wiki 就是单个 markdown 文件, 没有"装上后跑代码"的概念,
PG 存全文也合理). 也写一份到 FS `~/.catfish-hub/wiki/{ns}/{file_id}.md` 做 fallback.

Schema 设计要点:
- file_id: UUID, 员工 publish 时分配, 不依赖 namespace/name 唯一性 (wiki 文件可能
  同名重复 publish, e.g. 两员工都写"CSMM-4 评估" 笔记)
- stale_after_unpublish: BL-MANIFESTO 公理 4 — 中央 unpublish 后, 已 pull 员工
  本机副本不动. 本字段标"原 publisher 已撤回", 客户端拉时知道显 stale 标.
- 没有 version 概念: wiki 是知识笔记不是工具包, 改动直接更新 published_at (员工
  可以重 publish, 中央覆盖). 如果将来要历史版本, 加 wiki_revisions 表.

Revision ID: 20260610_init_wiki
Revises:
Create Date: 2026-06-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260610_init_wiki"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── wiki_documents: 已发布 wiki 笔记的元数据 + 全文 ─────────────
    op.create_table(
        "wiki_documents",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("namespace", sa.Text, nullable=False),  # dept/finance, dept/sales, ...
        sa.Column("file_id", sa.Text, nullable=False),  # UUID 员工本机分配
        sa.Column("filename", sa.Text, nullable=False),  # 原始 wiki rel_path, e.g. "wiki/entities/老李.md"
        sa.Column("title", sa.Text, nullable=False, server_default=""),  # frontmatter title
        sa.Column("kind", sa.Text, nullable=False, server_default="entity"),  # entity | concept | query
        sa.Column("description_preview", sa.Text, nullable=False, server_default=""),  # body 前 200 字
        sa.Column("frontmatter_yaml", sa.Text, nullable=False, server_default=""),  # 完整 frontmatter
        sa.Column("body_md", sa.Text, nullable=False, server_default=""),  # 完整 body
        sa.Column("published_by", sa.Text, nullable=False),
        sa.Column(
            "published_at", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.text("NOW()"),
        ),
        sa.Column("size_bytes", sa.BigInteger, nullable=False, server_default="0"),
        # 公理 4 兼容: 中央 unpublish 时仅标 stale=true, 客户端拉时看到, 自己决定卸不卸
        sa.Column("stale_after_unpublish", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column(
            "unpublished_at", sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column("unpublished_reason", sa.Text, nullable=True),
        sa.UniqueConstraint(
            "namespace", "file_id",
            name="uq_wiki_ns_file_id",
        ),
    )
    op.create_index("idx_wiki_namespace", "wiki_documents", ["namespace"])
    op.create_index("idx_wiki_published_by", "wiki_documents", ["published_by"])
    op.create_index(
        "idx_wiki_published_at", "wiki_documents",
        [sa.text("published_at DESC")],
    )
    op.create_index("idx_wiki_kind", "wiki_documents", ["kind"])
    op.create_index("idx_wiki_stale", "wiki_documents", ["stale_after_unpublish"])

    # ── wiki_audit: 发布 / 撤回 / 拉取 audit ───────────────────────
    op.create_table(
        "wiki_audit",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts_ms", sa.BigInteger, nullable=False),
        sa.Column("action", sa.Text, nullable=False),  # publish | unpublish | install
        sa.Column("namespace", sa.Text, nullable=True),
        sa.Column("file_id", sa.Text, nullable=True),
        sa.Column("by_user", sa.Text, nullable=False),
        sa.Column("meta", postgresql.JSONB, nullable=True),
    )
    op.create_index("idx_wiki_audit_ts", "wiki_audit", [sa.text("ts_ms DESC")])
    op.create_index("idx_wiki_audit_user", "wiki_audit", ["by_user"])
    op.create_index("idx_wiki_audit_action", "wiki_audit", ["action"])


def downgrade() -> None:
    op.drop_index("idx_wiki_audit_action", table_name="wiki_audit")
    op.drop_index("idx_wiki_audit_user", table_name="wiki_audit")
    op.drop_index("idx_wiki_audit_ts", table_name="wiki_audit")
    op.drop_table("wiki_audit")
    op.drop_index("idx_wiki_stale", table_name="wiki_documents")
    op.drop_index("idx_wiki_kind", table_name="wiki_documents")
    op.drop_index("idx_wiki_published_at", table_name="wiki_documents")
    op.drop_index("idx_wiki_published_by", table_name="wiki_documents")
    op.drop_index("idx_wiki_namespace", table_name="wiki_documents")
    op.drop_table("wiki_documents")
