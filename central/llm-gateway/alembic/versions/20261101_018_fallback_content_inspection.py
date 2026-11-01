"""11/01 — 公网模型被内容审核拦下时换模型重试 (触发词「content inspection」, 见 fallback.py)。

## 为什么

百炼 qwen 的内容审核误判时报 400 data_inspection_failed。同一段内容重发还会被拦,
而 on_errors 里没有对应的触发词, fallback 链停在原地, 员工只看到报错。鸿波 11/01 拍板:
这类错换模型重试。

## 改谁

只改「公网 → 公网」的模型: on_errors 里已经有 500 和 "upstream error" 的那些
(出厂配置里只有公网模型这样配; 内网模型故意不配 500, 防内网内容外流, 这里不碰)。
只追加一个触发词, 不改备用链 —— 备用链是空的模型照样不会切, 要在管理控制台配。

Revision ID: 20261101_018
Revises: 20261101_017
Create Date: 2026-11-01
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20261101_018"
down_revision: Union[str, None] = "20261101_017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TRIGGER = "content inspection"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE gateway_models
           SET payload = jsonb_set(payload, '{{fallback,on_errors}}',
                                   (payload->'fallback'->'on_errors') || '["{_TRIGGER}"]'::jsonb)
         WHERE jsonb_typeof(payload->'fallback'->'on_errors') = 'array'
           AND (payload->'fallback'->'on_errors') @> '[500]'::jsonb
           AND (payload->'fallback'->'on_errors') @> '["upstream error"]'::jsonb
           AND NOT (payload->'fallback'->'on_errors') @> '["{_TRIGGER}"]'::jsonb
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE gateway_models
           SET payload = jsonb_set(payload, '{{fallback,on_errors}}',
                                   (payload->'fallback'->'on_errors') - '{_TRIGGER}')
         WHERE (payload->'fallback'->'on_errors') @> '["{_TRIGGER}"]'::jsonb
        """
    )
