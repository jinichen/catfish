"""RBAC Day 4 follow-up: 修 sales/legal allowed_tools 用 hermes 0.14 真 tool name

BL-HERMES-014-UPGRADE (5/17 上午客户机实测):
migration 005 dept seed 写的 sales/legal allowed_tools 用了 hermes 0.13 老名字
(shell/bash/edit_file/list_dir/search/grep/todo_tool). hermes 0.14 这些**不再
注册**, registry.get_all_tool_names() 实测拉 71 个真名, 0.13 老名一个不在.

老名 → 0.14 真名映射:
  shell, bash               → terminal
  edit_file                 → patch
  search, grep, list_dir    → search_files
  todo_tool                 → todo
  screenshot                → browser_snapshot / browser_get_images (不放 sales/legal)

修后效果: sales/legal dept 升 hermes 0.14 后, 员工调 catfish_run_skill 等
工具不受影响 (catfish 原生 always-on), 但调 0.14 真正存在的 terminal/patch/
search_files/todo 也能正常走 RBAC.

不动 sales/legal 不放的工具集 (browser_* / web_* / x_search / kanban / 等), dept
admin 要给 sales 放 web 搜索就在 admin UI 单独加.

Revision ID: 20260517_006
Revises: 20260517_005
Create Date: 2026-05-17 10:00
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260517_006"
down_revision: Union[str, None] = "20260517_005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # sales: 销售常用 — 文件 / 代码 / memory / skill / cron / todo / 跨问 / 派任务
    # 不放 browser_* / x_search / web_* / computer_use / 大量平台集成 (省 token + 不让销售跑代码 agent)
    op.execute("""
        UPDATE departments SET allowed_tools = '[
          "catfish_search_sessions",
          "catfish_list_my_outputs",
          "catfish_user_profile_get",
          "catfish_user_profile_propose",
          "catfish_user_profile_confirm",
          "catfish_run_skill",
          "skill_view",
          "skills_list",
          "memory",
          "execute_code",
          "read_file",
          "write_file",
          "patch",
          "search_files",
          "terminal",
          "clarify",
          "delegate_task",
          "todo",
          "cronjob",
          "session_search"
        ]'::jsonb
        WHERE name = 'sales'
    """)

    # legal: 法务 — 文件 / 代码 / memory / skill / 跨问 / 派任务 / todo / kanban
    # 不放 browser / web 搜索 (合规要求不外联) / computer_use / 任何 messaging 平台
    op.execute("""
        UPDATE departments SET allowed_tools = '[
          "catfish_search_sessions",
          "catfish_list_my_outputs",
          "catfish_user_profile_get",
          "catfish_user_profile_propose",
          "catfish_user_profile_confirm",
          "catfish_run_skill",
          "skill_view",
          "skills_list",
          "memory",
          "execute_code",
          "read_file",
          "write_file",
          "patch",
          "search_files",
          "clarify",
          "delegate_task",
          "todo",
          "kanban_create",
          "kanban_list",
          "kanban_show",
          "kanban_comment",
          "kanban_complete",
          "session_search"
        ]'::jsonb
        WHERE name = 'legal'
    """)

    # engineering 仍 [] (全允许), ops 仍 [] — 不动


def downgrade() -> None:
    # 回滚: 把 sales/legal 改回 005 的 0.13 老名字 list (不解决 forward-compat 问题
    # 但保持 migration 链可逆).
    op.execute("""
        UPDATE departments SET allowed_tools = '[
          "catfish_search_sessions","catfish_list_my_outputs",
          "catfish_user_profile_get","catfish_user_profile_propose","catfish_user_profile_confirm",
          "catfish_run_skill","search_skills","memory","execute_code","read_file","write_file",
          "edit_file","list_dir","search","grep","clarify","delegate_task","shell","bash"
        ]'::jsonb
        WHERE name = 'sales'
    """)
    op.execute("""
        UPDATE departments SET allowed_tools = '[
          "catfish_search_sessions","catfish_list_my_outputs",
          "catfish_user_profile_get","catfish_user_profile_propose","catfish_user_profile_confirm",
          "catfish_run_skill","search_skills","memory","execute_code","read_file","write_file",
          "edit_file","list_dir","search","grep","clarify","delegate_task"
        ]'::jsonb
        WHERE name = 'legal'
    """)
