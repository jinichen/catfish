"""10/21 — 记下「出厂模型已经播种过」, 之后升级不再把删掉的模型 / 供应商加回来.

## 病

鸿波 10/21: 每次升级中央端, 已经删掉的供应商和模型又回来了。

model_store 文件头写的设计是「首次启动播种一次, 之后 yaml 不再参与模型列表 ——
否则升级包换了 yaml 会把客户现场的改动冲掉」, 但实现是**每次启动**都跑
seed_from_yaml, 规则是 `ON CONFLICT (name) DO NOTHING` (库里没有同名的就插)。
删掉的模型在库里没有了, 于是每次重启 (升级必然重启) 都被当成「缺失的出厂模型」
插回去; 插回来的是 yaml 里的老形态 (自带 api_base / api_key_env), 紧接着
migrate_models_to_providers 看到它没挂供应商, 又按端点把删掉的供应商建回来。

## 修

库里记一个「播种过了」的时间戳。seed_from_yaml 只在它为空时播, 播完写上。

不能用「gateway_models 是空的」来判断: 客户把模型全删了 (例如只用自己接的内网模型、
还没加上) 也是空的, 那时把出厂模型灌回去同样是这个 bug。

## 已经在跑的现场

这条迁移跑在新版 gateway 启动之前 (Dockerfile: alembic upgrade head && python -m …)。
库里已经有任何模型或供应商 = 早就播种过 → 直接标记, 新代码不会再补。
只有真正的空库 (全新安装) 才保持为空, 让第一次启动播种。

已经被以前的升级补回来的那些, 这次不会自动消失 —— 删一次就不会再回来了。

Revision ID: 20261021_012
Revises: 20260923_011
Create Date: 2026-10-21
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20261021_012"
down_revision: Union[str, None] = "20260923_011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE gateway_config_meta ADD COLUMN IF NOT EXISTS seeded_at TIMESTAMPTZ")
    op.execute(
        """
        UPDATE gateway_config_meta SET seeded_at = now()
         WHERE id = 1 AND seeded_at IS NULL
           AND (EXISTS (SELECT 1 FROM gateway_models) OR EXISTS (SELECT 1 FROM gateway_providers))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE gateway_config_meta DROP COLUMN IF EXISTS seeded_at")
