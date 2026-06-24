"""P3.5.93 (6/23 鸿波): drop departments.quota_models_day — 治 6 周 dead UI

# 真因 (P3.5.93 audit-5)

20260517_004 (BL-RBAC-DAY7 Day 3a) 加了 departments.quota_models_day 字段
(BIGINT NOT NULL DEFAULT 0) + AccessPage UI 编辑入口. 但 gateway check_quota
真生效路径走 quotas.yaml (overrides.departments.<dept>.tokens_per_day), 完全
不读这字段:

  grep "quota_models_day" central/llm-gateway/src/  → 0 行 hit

5/17 ship 到现在 6 周, 鸿波每次通过 AccessPage 改部门 quota 都不生效 (真生效
靠 5/24 那次手动 vim quotas.yaml 改 chenhongbo override).

# 修法 (P3.5.93 B 方案合并)

砍 identity-server 这个 dead 字段, 部门 quota 编辑收口到新 /admin/quota 走
yaml (gateway 真读的源). 一处编辑, 一处生效.

# Revision

Revision ID: 20260623_009
Revises: 20260524_008
Create Date: 2026-06-23

# Rollback

downgrade 加回 column DEFAULT 0. 但生产数据已无意义 (yaml 才是源), 不必跑.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260623_009"
down_revision: Union[str, None] = "20260524_008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 砍 departments.quota_models_day (6 周来 dead UI 写入但 gateway 不读)
    op.execute("ALTER TABLE departments DROP COLUMN IF EXISTS quota_models_day")


def downgrade() -> None:
    # 加回不破存量数据 — DEFAULT 0 自动填. 但 yaml 才是真源, 加回也没用.
    op.execute("""
        ALTER TABLE departments
        ADD COLUMN IF NOT EXISTS quota_models_day BIGINT NOT NULL DEFAULT 0
    """)
