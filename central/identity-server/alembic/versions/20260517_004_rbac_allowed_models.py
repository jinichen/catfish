"""RBAC Day 3a: departments 表 + users.allowed_models 字段

BL-RBAC-DAY3A (5/17 凌晨, 原 sprint plan 5/16 但 memory 主线占整天 → 推迟):

加 per-dept + per-user model 白名单, gateway 接 user.allowed_models 过滤
chat completion / catalog 返回. 销售部门只允许 deepseek-flash 省钱 / 公关部门
只允许私有 Qwen 合规, 等等.

逻辑:
  - departments.allowed_models = [] → 全允许 (开放默认)
  - departments.allowed_models = ['m1','m2'] → 只允许这俩
  - users.allowed_models = NULL → 继承 dept (默认)
  - users.allowed_models = [] → 全允许 (用户级 override 解锁)
  - users.allowed_models = ['m1'] → 只允许 m1 (用户级 override 收紧)

quota_models_day 也顺手加 (RBAC sprint 后续 Day 用), 默认 0 = 不限,
跟 BL-F14 internal_models 兼容.

Day 3b (gateway 接入) 留周日清醒时. 这个 migration 只装 schema, 不破 gateway.

Revision ID: 20260517_004
Revises: 20260512_003
Create Date: 2026-05-17 05:30
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260517_004"
down_revision: Union[str, None] = "20260512_003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── departments (RBAC per-dept model 白名单 + quota) ─────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS departments (
            name              TEXT PRIMARY KEY,
            allowed_models    JSONB NOT NULL DEFAULT '[]'::jsonb,
            quota_models_day  BIGINT NOT NULL DEFAULT 0,
            description       TEXT NOT NULL DEFAULT '',
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    # 加 4 个示范部门: engineering 全允许, sales 只 deepseek-flash, legal 只私有,
    # ops 私有 + 公网通义. 真客户接入时改这表.
    op.execute("""
        INSERT INTO departments (name, allowed_models, description) VALUES
          ('engineering', '[]'::jsonb, '研发: 默认全允许, fallback chain 自由'),
          ('sales',
           '["catfish-public-deepseek-flash"]'::jsonb,
           '销售: 限便宜模型省钱, 不调内网主力'),
          ('legal',
           '["catfish-private-main", "catfish-private-vision"]'::jsonb,
           '法务: 数据合规要求高, 不上公网'),
          ('ops',
           '["catfish-private-main", "catfish-public-qwen-flash"]'::jsonb,
           '运维: 内网主力 + 公网兜底')
        ON CONFLICT (name) DO NOTHING
    """)

    # ── users.allowed_models (per-user 覆盖 dept 默认) ────────────
    op.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS allowed_models JSONB DEFAULT NULL
    """)
    # NULL 是默认, 含义"继承 department 的 allowed_models", 不强制 INSERT 时改.

    op.execute(
        "COMMENT ON COLUMN users.allowed_models IS "
        "'NULL=继承 dept, []=全允许 (override 解锁), [m1,m2]=收紧'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS allowed_models")
    op.execute("DROP TABLE IF EXISTS departments")
