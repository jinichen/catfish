"""9/23 — 角色映射 (roles.yaml 的 roles 段) 搬进库.

## 为什么

7/30 (007) 把模型从 models.yaml 搬进库, 理由写在 model_store.py 开头:
"那个文件在容器里是只读挂载, 所以'在界面上改模型'这条路根本不存在"。

roles.yaml 是同一个只读挂载目录里的另一个文件, **当时没跟着搬**。结果是
一边 (模型) 点一下就改, 另一边 (角色) 连 API 都写不了。8/14 加的那道
"删模型前查 roles 引用"保护正卡在这个缝里: 它拦下来之后给人的出路是
"SSH 上去改文件、重启网关" —— 9/22 鸿波在现场删一个 gemini 模型就撞上了。

这张表让角色跟模型站到同一边: 库是运行时事实源, yaml 只是出厂种子。

## 表里只有 roles 段

roles.yaml 原来还有两段, 这次一起处理掉, 不进库:

    fallback_chain          死配置。fallback.py 根本没 import roles, 全仓
                            唯一的消费者是 roles.py 自己算一遍塞进 /v1/roles,
                            而 /v1/roles 的三个客户端都只读 `roles` 字段。
    rbac_default_allowed    一次性播种脚本 (scripts/seed_rbac_from_roles.py)
                            的输入, 而那个脚本没有任何地方调用它; 部门权限
                            早就在 /admin/access 界面上管了。脚本和这段一起删。

## 代次共用 gateway_config_meta

改角色也 bump 同一个 revision —— 角色变了, catalog 里"默认模型"那一列
(chat_default) 也跟着变, 让 config 缓存一起失效正好。

Revision ID: 20260923_010
Revises: 20260910_009
Create Date: 2026-09-23
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260923_010"
down_revision: Union[str, None] = "20260910_009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gateway_roles (
            role        TEXT PRIMARY KEY,
            model       TEXT        NOT NULL,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- 谁改的。"seed:roles.yaml" = 出厂; "<人> (删除 X 时自动改指)" =
            -- 删模型时软角色被自动挪走 —— 角色页要把这个原样显示出来,
            -- 否则"summarize 怎么变成 qwen 了"没人答得上来。
            updated_by  TEXT
        )
        """
    )


def downgrade() -> None:
    # 回滚后 roles.py 会退回读 yaml。库里的改动丢掉 —— 跟 007 一样, 这是
    # 回滚的含义, 不是 bug。bump 代次让 4 个 worker 都重新加载。
    op.execute("UPDATE gateway_config_meta SET revision = revision + 1 WHERE id = 1")
    op.execute("DROP TABLE IF EXISTS gateway_roles")
