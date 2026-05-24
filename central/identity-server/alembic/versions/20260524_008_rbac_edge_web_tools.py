"""RBAC: 给所有部门加 web_search + web_extract (BL-EDGE-TOOL-KEY 5/24)

# 背景

之前 ~/.hermes/.env 里的 TAVILY_API_KEY 是员工自己手贴, 50 人部署不可持续.
中央 catfish-gateway 现在通过 /v1/edge/tool-config/{tool_name} 派发 key, RBAC
拦截走 user.can_use_tool(tool_name) → effective_allowed_tools claim. 这个
claim 由 identity-server 合并 (user.allowed_tools ∪ dept.allowed_tools) 签进
JWT, 所以**部门 RBAC 不放 web_search, 中央就不发 key**.

这是新接入的能力 — sales/legal 的 005/006 dept allowed_tools 是在 web_search
中央派发**之前**写的, 当时 web_search 还是员工自己配 Tavily key (中央没法
拦, 拦了也没用因为 hermes 直连 Tavily). 现在中央派发上线了, RBAC 才真有控
制力, 所以这次 seed 把所有部门都打开作为**起步默认**.

# 决策: 全员开 (5/24 鸿波)

跟 005 的 "legal 合规要求不外联" 表面冲突, 但实际逻辑变了:
  - 005 时代: 不放 web_search ≠ 真不能搜 (员工自己配 Tavily 走 hermes 直连)
  - 008 时代 (这个 migration): 不放 web_search = **真不能搜** (中央不发 key,
    员工 ~/.hermes/.env 里就没 TAVILY_API_KEY).
  - 因此 005 的 RBAC 实际从未生效过, 现在 008 让它**真生效**.
  - 起步 default = 全员开, admin 想给某部门关在 admin UI 单独 unset 就行.

(管 legal 真的要"不外联", 之后 admin UI 单独 unset web_search/web_extract
即可 — 关一行不是这个 migration 的事.)

# Scope

加 web_search + web_extract. 不加 web_crawl (5/24 还没在中央 registry, 等下
轮 sprint 一起做).

# Revision

Revision ID: 20260524_008
Revises: 20260517_007
Create Date: 2026-05-24
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260524_008"
down_revision: Union[str, None] = "20260517_007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 新加的 tool 名 — 来自 gateway/edge_tool_config.py EDGE_TOOL_REGISTRY
_NEW_TOOLS = ["web_search", "web_extract"]


def upgrade() -> None:
    """给所有部门的 allowed_tools 追加 web_search + web_extract.

    Postgres JSONB '||' 合并 + ARRAY 操作没有内置去重, 用子查询过滤已存在的:
      - 现有 list 是 [] (engineering/ops) → 不动, 走 "空 = 全允许" 路径
      - 现有 list 是 [t1, t2, ...] (sales/legal/...) → 追加缺的 tool name
    """
    # 注: 我们用 '|| jsonb_build_array(...)' 不去重, 但因为这个 migration 只
    # 运行一次, 不会重复追加. 如果手动重跑了 downgrade 再 upgrade 会去重 (因
    # 为 downgrade 把 tool 名移走了).
    op.execute("""
        UPDATE departments
        SET allowed_tools = allowed_tools || '["web_search", "web_extract"]'::jsonb
        WHERE jsonb_array_length(allowed_tools) > 0   -- 跳过 [] (空 = 全允许)
          AND NOT (allowed_tools @> '["web_search"]'::jsonb)  -- 已有就不追加
    """)

    # 对 [] 的部门 (engineering / ops) 不动 — 空 list = 全允许 = 已经默认含
    # web_search/web_extract, 不需要显式列.


def downgrade() -> None:
    """从所有部门移除 web_search + web_extract.

    用 jsonb 减法 (PostgreSQL 12+ 的 jsonb - text[] 形式). 对 [] 的部门是 no-op.
    """
    op.execute("""
        UPDATE departments
        SET allowed_tools = allowed_tools - 'web_search' - 'web_extract'
        WHERE jsonb_array_length(allowed_tools) > 0
    """)
