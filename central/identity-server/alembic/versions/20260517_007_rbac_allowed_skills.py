"""RBAC Day 5: departments.allowed_skills + users.allowed_skills

BL-RBAC-DAY5 (5/17 下午):
跟 Day 3 (allowed_models) + Day 4 (allowed_tools) 同设计, 加 per-dept + per-user
skill 白名单. 但 skill 不像 tool 是单一 name, 有命名空间维度:

  catfish:*                          — catfish 自家工程审定 skill (CATFISH_SKILLS_DIR)
  hermes:bundled:*                   — hermes 装机自带的 (~/.hermes/hermes-agent/skills/)
  hermes:github:<owner>/<repo>       — 用户 git clone 进 ~/.hermes/skills/ 的 GitHub skill
  hermes:hf:<owner>/<name>           — hermes 0.14 #26219 huggingface/skills tap 拉的
  hermes:local:<name>                — 员工本机自写 / catfish_teach_start 凝固的

glob 匹配:
  []                                  = 全允许 (开放默认 / 无 dept 配置)
  ["catfish:*"]                       = 只 catfish 工程审定 skill
  ["catfish:*", "hermes:bundled:*"]   = + hermes 自带
  ["hermes:github:zarazhangrui/*"]    = 只 zarazhangrui 这个 GitHub user 发的 skill
  禁单条: hermes 自带 generic 但不放 hermes-yuanbao → 不能用 glob 排除, 只能列正面.
         真要 ship 后加 deny_skills 字段补.

namespace 推导 (catfish 实施层):
  catfish:*       — skill_md_path 在 CATFISH_SKILLS_DIR 子树
  hermes:bundled  — 在 hermes-agent 源码目录 (跟着 hermes 一起装)
  hermes:github   — 在 ~/.hermes/skills/, 含 .git/, remote.origin.url 是 github.com
  hermes:hf       — 在 ~/.hermes/skills/, metadata 标 hf (hermes 0.14 装的)
  hermes:local    — 在 ~/.hermes/skills/, 既没 .git 也没 hf 标 = 员工本机写

Revision ID: 20260517_007
Revises: 20260517_006
Create Date: 2026-05-17 11:00
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260517_007"
down_revision: Union[str, None] = "20260517_006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── departments.allowed_skills (namespace glob list) ──────────
    op.execute("""
        ALTER TABLE departments
        ADD COLUMN IF NOT EXISTS allowed_skills JSONB NOT NULL DEFAULT '[]'::jsonb
    """)
    op.execute(
        "COMMENT ON COLUMN departments.allowed_skills IS "
        "'[]=全允许 (开放默认); [<ns>:<glob>]=只允许命名空间下匹配的 skill. "
        "命名空间: catfish:* / hermes:bundled:* / hermes:github:<owner>/<repo> / "
        "hermes:hf:<owner>/<name> / hermes:local:*'"
    )

    # 示范配置 (跟 allowed_models + allowed_tools 的 4 部门对位):
    #   engineering — 全允许 (空 list)
    #   sales — 只 catfish 自家工程审定 skill (合规, 不让员工跑外部 skill)
    #   legal — 同 sales, 合规要求 + 加 hermes bundled (hermes 自带的够用)
    #   ops — 全允许 (运维要全工具排错)
    op.execute("""
        UPDATE departments SET allowed_skills = '[
          "catfish:*"
        ]'::jsonb
        WHERE name = 'sales'
    """)
    op.execute("""
        UPDATE departments SET allowed_skills = '[
          "catfish:*",
          "hermes:bundled:*"
        ]'::jsonb
        WHERE name = 'legal'
    """)
    # engineering / ops 仍 [] (全允许), 不动

    # ── users.allowed_skills (per-user override) ──────────────────
    op.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS allowed_skills JSONB DEFAULT NULL
    """)
    op.execute(
        "COMMENT ON COLUMN users.allowed_skills IS "
        "'NULL=继承 dept, []=全允许 (override 解锁), [<ns>:<glob>]=收紧'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS allowed_skills")
    op.execute("ALTER TABLE departments DROP COLUMN IF EXISTS allowed_skills")
