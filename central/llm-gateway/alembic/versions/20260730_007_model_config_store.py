"""7/30 — 模型配置进库, 支持运行时增删改.

在这之前模型只能改 models.yaml 再重启 gateway, 而那个文件在容器里是
**只读挂载** (docker-compose: ./llm-gateway/config:/app/config:ro), 所以
根本没有"在界面上改模型参数"这条路。

## 为什么 payload 用 JSONB 整存, 不拆列

ModelConfig 是个嵌套结构 (upstream / fallback / rate_limits 三层子模型),
而且一直在长字段 —— max_output_tokens、supports_vision、rate_limits 都是
后加的。拆成列意味着每加一个字段就要一次 DDL 迁移, 而客户现场跑的版本
未必跟得上。

JSONB 整存 + 在 API 边界用 pydantic 校验, 该有的约束一个不少 (写进来的
必须能过 ModelConfig.model_validate), 但库结构不用跟着字段走。
name 单独提出来做主键是因为它是业务唯一键, 要靠库来防重。

## 为什么要单独的 revision 计数器, 不用 max(updated_at)

gateway 跑 4 个 worker (GATEWAY_WORKERS:-4), 各自缓存配置、靠"源的代次
变没变"决定要不要重新加载。代次必须**单调递增**。

max(updated_at) 不满足: 删掉最近更新的那一行, max 会**倒退**。倒退的代次
会让某些 worker 的陈旧缓存看起来"和当前一致", 于是永远不刷新 —— 表现为
"改了配置, 4 个 worker 里有 1 个死活不生效", 且哪个不生效是随机的。

单调计数器没有这个问题: 任何写操作 (含删除) 都 +1, 只增不减。

Revision ID: 20260730_007
Revises: 20260622_006
Create Date: 2026-07-30
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260730_007"
down_revision: Union[str, None] = "20260622_006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gateway_models (
            name        TEXT PRIMARY KEY,
            payload     JSONB       NOT NULL,
            sort_order  INTEGER     NOT NULL DEFAULT 0,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by  TEXT
        )
        """
    )
    # 列表展示按 sort_order 再按 name, 给个索引免得每次排序全表
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_gateway_models_order "
        "ON gateway_models(sort_order, name)"
    )

    # 单行表: 配置代次。CHECK(id=1) 保证只可能有一行 —— 多行的话
    # "当前代次是多少" 就有歧义, 而这个值是缓存正确性的唯一依据。
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gateway_config_meta (
            id       SMALLINT PRIMARY KEY,
            revision BIGINT NOT NULL DEFAULT 0,
            CONSTRAINT gateway_config_meta_single_row CHECK (id = 1)
        )
        """
    )
    op.execute(
        "INSERT INTO gateway_config_meta (id, revision) VALUES (1, 0) "
        "ON CONFLICT (id) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_gateway_models_order")
    op.execute("DROP TABLE IF EXISTS gateway_models")
    op.execute("DROP TABLE IF EXISTS gateway_config_meta")
