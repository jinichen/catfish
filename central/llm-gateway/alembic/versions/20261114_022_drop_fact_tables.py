"""11/14 — 「政策同步」下线, 把它的四张表一起删掉。

后台「政策同步」页面和网关 /api/facts/* 在 9504977 整体删除 (5/26 起分析步骤拿不到模型,
半年没人用)。当时先留着表, 鸿波说要彻底清掉 —— 这四张表从此没有任何代码读写:

    fact_changes / fact_skill_impacts / fact_skill_patches / fact_audit   (20260510_002 建的)

不删 002 那个文件: 各处库的 alembic 历史都经过它, 删了链就断了。加这一条, 所有库跑到 head
之后状态一致, 不需要手工 SQL。

磁盘上 CATFISH_FACTS_DIR (默认 <网关 home>/.catfish/facts) 里管理员上传过的原始文件不归
迁移管, 网关也不再读它; 要清就直接删那个目录。

Revision ID: 20261114_022
Revises: 20261107_021
Create Date: 2026-11-14
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20261114_022"
down_revision: Union[str, None] = "20261107_021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 索引跟着表一起没。先删引用 fact_changes 的三张, 再删主表。
    for t in ("fact_audit", "fact_skill_patches", "fact_skill_impacts", "fact_changes"):
        op.execute(f"DROP TABLE IF EXISTS {t}")


def downgrade() -> None:
    # 不重建: 读写这些表的代码已经不存在, 空表回来也没人用。数据也恢复不了。
    pass
