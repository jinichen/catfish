"""8/1 — 拆分「供应商」与「模型」.

## 为什么拆

7 个模型拆出 6 组 upstream 配置 (实查 models.yaml 7/30):

    internal-qwen-main / internal-qwen-vision / internal-bge-m3
        三个不同端点, 但共用 INTERNAL_LLM_KEY
    dashscope / deepseek     各一个模型
    gemini                   **两个模型共用**, 且都不填 api_base

今天真正的重复只有 Gemini 那一对 —— 拆分的收益主要不在消除现有重复,
而在"以后加模型 / 加供应商 / 换 key"这三件事, 以及它是把 key 移出环境变量
的前提。

加一个 Gemini 模型现在要把端点和 key 变量名再敲一遍, 敲错了要等员工调用
失败才发现。而客户现场加一家新供应商更是做不到 —— 见 docs/DESIGN-PROVIDER-
SPLIT-20260730.md §2: docker-compose.yml 是**显式列名**转发 env 的, 新变量
容器根本看不见。

## 这次只做 DDL, 数据搬迁在启动时跑

`upgrade()` 只建表。把现有模型拆成 provider 的那步放在
`provider_store.migrate_models_to_providers()`, 启动时幂等执行 —— 跟
`restore_env_placeholders` / `enforce_single_default` 同一个套路 (7/30)。

理由是可测: 拆分要读 JSONB、去重、生成可读 id、回写, 用 Python 写能逐条
钉测试并注入故障验证; 写成 SQL 只能靠肉眼。

## downgrade 必须把字段合并回去

**不能只 DROP TABLE。** 模型的 payload 里 upstream 会变成
`{"model": ..., "provider": "gemini", "timeout": 60}` —— 表一删, api_base 和
api_key_env 就没了, 回滚等于把模型配置删了一半, 而且 gateway 起来之后
每个模型都调用失败。

所以 downgrade 先 UPDATE 把 provider 的两个字段内联回 payload, 再 DROP。

Revision ID: 20260801_008
Revises: 20260730_007
Create Date: 2026-08-01
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260801_008"
down_revision: Union[str, None] = "20260730_007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gateway_providers (
            id           TEXT PRIMARY KEY,
            display_name TEXT        NOT NULL,
            -- NULL = 用 SDK 默认端点 (Gemini 就是这样, 不填 api_base)
            api_base     TEXT,
            -- key 的两条来源, 迁移期并存:
            --   api_key_env  读环境变量 (存变量名, 老路)
            --   api_key_enc  Fernet 密文 (新路, 第二步才启用)
            -- 两个都空 = 这个供应商还没配 key, 用它的模型不可用。
            api_key_env  TEXT,
            api_key_enc  BYTEA,
            timeout      INTEGER     NOT NULL DEFAULT 60,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by   TEXT
        )
        """
    )


def downgrade() -> None:
    # ⚠ 顺序不能反: 先把 provider 的字段内联回模型, 再删表。
    #
    # 只 DROP TABLE 的话, 模型 payload 里的 upstream 只剩
    # {"model": ..., "provider": "gemini", "timeout": 60} —— api_base 和
    # api_key_env 没了, 回滚等于把模型配置删掉一半, gateway 起来后每个模型
    # 调用都失败, 而且从配置上看不出少了什么。
    #
    # `- 'provider'` 去掉引用键; jsonb_build_object 把两个字段塞回去。
    # api_base 为 NULL 时得到 "api_base": null, 正是 UpstreamConfig 里
    # `str | None` 期望的形态。
    op.execute(
        """
        UPDATE gateway_models m
        SET payload = jsonb_set(
                m.payload,
                '{upstream}',
                ((m.payload -> 'upstream') - 'provider')
                    || jsonb_build_object(
                        'api_base', p.api_base,
                        'api_key_env', COALESCE(p.api_key_env, 'INTERNAL_LLM_KEY')
                    )
            )
        FROM gateway_providers p
        WHERE m.payload -> 'upstream' ->> 'provider' = p.id
        """
    )
    # 回滚也算一次配置变更 —— 不 bump 的话 4 个 worker 的缓存看不到这次改动,
    # 会继续用带 provider 引用的旧配置直到 TTL 到期 (见 007 里为什么要代次)。
    op.execute("UPDATE gateway_config_meta SET revision = revision + 1 WHERE id = 1")
    op.execute("DROP TABLE IF EXISTS gateway_providers")
