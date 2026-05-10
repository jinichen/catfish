"""init skills_versions + skills_audit (BL-D2 Phase 2 PG 统一, 5/10)

跟 catfish-gateway / catfish-identity / catfish-mcp-registry 共享 PG, 表前缀
skills_ 防冲突. 元数据进 PG (索引 / join 友好), 文件内容继续 FS
(~/.catfish-hub/skills/<ns>/<n>/<v>/), 真上线再切对象存储 S3/MinIO.

Revision ID: 20260510_init_skills
Revises:
Create Date: 2026-05-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260510_init_skills"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── skills_versions: 已发布 skill 的版本元数据 ──────────────────
    # PK: (namespace, name, version) — 一个 skill 多版本共存
    # content_dir: FS 路径 (CATFISH_HUB_ROOT 下相对路径), 真上线切对象存储
    # 时改成 s3://bucket/key 或类似.
    op.create_table(
        "skills_versions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("namespace", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("version", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("published_by", sa.Text, nullable=False),
        sa.Column(
            "published_at", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.text("NOW()"),
        ),
        sa.Column("content_dir", sa.Text, nullable=False),
        sa.Column("file_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_bytes", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("deprecated", sa.Boolean, nullable=False, server_default=sa.text("false")),
        # P1 字段, 留位 (评分 / 订阅数 / 标签)
        sa.Column("subscribe_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rating_avg", sa.Float, nullable=True),
        sa.Column("rating_count", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint(
            "namespace", "name", "version",
            name="uq_skills_ns_name_version",
        ),
    )
    op.create_index("idx_skills_namespace", "skills_versions", ["namespace"])
    op.create_index("idx_skills_published_by", "skills_versions", ["published_by"])
    op.create_index(
        "idx_skills_published_at", "skills_versions",
        [sa.text("published_at DESC")],
    )

    # ── skills_audit: 发布 / 删除 / 下载 audit ───────────────────────
    op.create_table(
        "skills_audit",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts_ms", sa.BigInteger, nullable=False),
        sa.Column("action", sa.Text, nullable=False),  # publish | delete | download
        sa.Column("namespace", sa.Text, nullable=True),
        sa.Column("name", sa.Text, nullable=True),
        sa.Column("version", sa.Text, nullable=True),
        sa.Column("by_user", sa.Text, nullable=False),
        sa.Column("meta", postgresql.JSONB, nullable=True),
    )
    op.create_index("idx_skills_audit_ts", "skills_audit", [sa.text("ts_ms DESC")])
    op.create_index("idx_skills_audit_user", "skills_audit", ["by_user"])
    op.create_index("idx_skills_audit_action", "skills_audit", ["action"])


def downgrade() -> None:
    op.drop_index("idx_skills_audit_action", table_name="skills_audit")
    op.drop_index("idx_skills_audit_user", table_name="skills_audit")
    op.drop_index("idx_skills_audit_ts", table_name="skills_audit")
    op.drop_table("skills_audit")
    op.drop_index("idx_skills_published_at", table_name="skills_versions")
    op.drop_index("idx_skills_published_by", table_name="skills_versions")
    op.drop_index("idx_skills_namespace", table_name="skills_versions")
    op.drop_table("skills_versions")
