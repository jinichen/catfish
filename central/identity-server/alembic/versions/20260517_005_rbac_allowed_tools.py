"""RBAC Day 4: departments.allowed_tools + users.allowed_tools

BL-RBAC-DAY4 (5/17 上午):
跟 Day 3 (allowed_models) 同设计, 加 per-dept + per-user tool 白名单. gateway
tools_sanitizer 拿 user.effective_allowed_tools 在 LLM tool 列表里 drop 用户
不允许的工具. 销售部门不放 browser_* / 法务不放 catfish_search_web / 等等.

逻辑 (跟 allowed_models 完全一致):
  - departments.allowed_tools = [] → 全允许 (开放默认)
  - departments.allowed_tools = ['t1','t2'] → 只这俩 + ALWAYS_ON
  - users.allowed_tools = NULL → 继承 dept
  - users.allowed_tools = [] → 全允许 (用户级 override 解锁)
  - users.allowed_tools = ['t1'] → 只 t1 + ALWAYS_ON

注: gateway 侧 sanitizer 永远保留 _ALWAYS_ON_TOOLS (memory / execute_code /
read_file 等 LLM agent loop 底座), 这个白名单只过滤 always-on 之外的工具.

Revision ID: 20260517_005
Revises: 20260517_004
Create Date: 2026-05-17 09:30
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260517_005"
down_revision: Union[str, None] = "20260517_004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── departments.allowed_tools ─────────────────────────────────
    op.execute("""
        ALTER TABLE departments
        ADD COLUMN IF NOT EXISTS allowed_tools JSONB NOT NULL DEFAULT '[]'::jsonb
    """)
    op.execute(
        "COMMENT ON COLUMN departments.allowed_tools IS "
        "'[]=全允许 (开放默认); [t1,t2]=只这俩 + ALWAYS_ON 兜底'"
    )

    # 示范配置 (跟 allowed_models 的 4 部门一致):
    #   engineering — 全允许 (空 list)
    #   sales — 不放 catfish_browser_* / catfish_read_url, 销售不用浏览
    #   legal — 不放公网搜索 + browser, 合规要求不外联
    #   ops — 跟 engineering 类似全允许 (运维要全工具排错)
    op.execute("""
        UPDATE departments SET allowed_tools = '[
          "catfish_search_sessions",
          "catfish_list_my_outputs",
          "catfish_user_profile_get",
          "catfish_user_profile_propose",
          "catfish_user_profile_confirm",
          "catfish_run_skill",
          "search_skills",
          "memory",
          "execute_code",
          "read_file",
          "write_file",
          "edit_file",
          "list_dir",
          "search",
          "grep",
          "clarify",
          "delegate_task",
          "shell",
          "bash"
        ]'::jsonb
        WHERE name = 'sales'
    """)
    op.execute("""
        UPDATE departments SET allowed_tools = '[
          "catfish_search_sessions",
          "catfish_list_my_outputs",
          "catfish_user_profile_get",
          "catfish_user_profile_propose",
          "catfish_user_profile_confirm",
          "catfish_run_skill",
          "search_skills",
          "memory",
          "execute_code",
          "read_file",
          "write_file",
          "edit_file",
          "list_dir",
          "search",
          "grep",
          "clarify",
          "delegate_task"
        ]'::jsonb
        WHERE name = 'legal'
    """)

    # ── users.allowed_tools (per-user override) ───────────────────
    op.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS allowed_tools JSONB DEFAULT NULL
    """)
    op.execute(
        "COMMENT ON COLUMN users.allowed_tools IS "
        "'NULL=继承 dept, []=全允许 (override 解锁), [t1,t2]=收紧'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS allowed_tools")
    op.execute("ALTER TABLE departments DROP COLUMN IF EXISTS allowed_tools")
