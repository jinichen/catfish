"""9/23 晚 — 撤掉 gateway_roles (010 建的, 活了两个小时).

## 为什么建了又撤

010 把 roles.yaml 搬进库, 想让"删被角色指着的模型"有出路。写完之后鸿波问了
一句: "不是都是跟随用户的选择吗, 为什么还要做角色指定?" —— 于是逐个追了
七个角色在代码里的消费者 (结论见 roles.py 开头): 三个零消费者, 一个只是
文案里的一个词, 一个网关自己就会算, 一个是 8/9 已经定过"不许有第二个来源"
的那种第二来源, 只有 embedding 真需要一个指定 —— 而它跟 chat 一样用模型上
的 `default` 标志就够了 (一个 mode 一个默认)。

所以整张表、roles.yaml、角色页都不需要。库里的角色一行都不用搬: 它们就是
从 roles.yaml 播种进去的出厂值, 而模型页的「默认」徽章已经是这份信息。

## 为什么不直接删掉 010 的文件

鸿波本机的 alembic_version 已经指到 20260923_010。文件一删, alembic 找不到
当前版本, 连 `upgrade head` 都跑不了。留着 010 + 加这条 011, 所有库
(本机 / 未来客户现场) 跑到 head 之后状态一致, 不需要任何手工 SQL。

Revision ID: 20260923_011
Revises: 20260923_010
Create Date: 2026-09-23
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260923_011"
down_revision: Union[str, None] = "20260923_010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS gateway_roles")
    # 配置变更 (虽然只是删了一张没人读的表) —— 跟 007/008 一样 bump, 保持惯例。
    op.execute("UPDATE gateway_config_meta SET revision = revision + 1 WHERE id = 1")


def downgrade() -> None:
    # 回到 010 的状态: 表在但空。没有数据要恢复 —— 那张表里只有出厂种子。
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gateway_roles (
            role        TEXT PRIMARY KEY,
            model       TEXT        NOT NULL,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by  TEXT
        )
        """
    )
