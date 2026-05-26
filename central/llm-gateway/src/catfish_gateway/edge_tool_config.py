"""edge_tool_config — 中央派发 hermes 边缘工具的 backend key.

# 背景 (BL-EDGE-TOOL-KEY 5/24 鸿波)

之前员工要用 web_search, 得自己在 ~/.hermes/.env 里贴 TAVILY_API_KEY. 50 人  # noqa: BOUNDARY (docstring 描述员工本地配置, 非真访问)
部署后这个不可持续:
  - 出账分不清 (每人一个 key)
  - 离职员工 key 没回收
  - 改 key 要 50 台 ssh 跑一遍
  - 没 RBAC (销售部能搜, 法务部默认也能搜)

哲学债务参考 docs/LOCAL-DATA-LAYOUT.md "所有第三方 API key 中央管理" 承诺.

# 架构

```
catfish-gateway (中央)
  ├─ .env 持 TAVILY_API_KEY (admin 改这里, 改完 reload)
  └─ GET /v1/edge/tool-config/{tool_name}
      ├─ JWT 验 (复用 get_current_user)
      ├─ RBAC: user.can_use_tool(tool_name) — 走 effective_allowed_tools claim
      └─ 200 返 {tool_group, env_vars, yaml_block}
          ↓
catfish-cli (边缘, refresh-hermes 命令)
  ├─ 对每个已知 tool 调一次 endpoint
  ├─ env_vars 合并写 ~/.hermes/.env (per-key update, 保留无关行)  # noqa: BOUNDARY (描述 edge 行为)
  └─ yaml_block 合并写 ~/.hermes/config.yaml (preserve sibling keys)  # noqa: BOUNDARY (描述 edge 行为)
```

# 当前 scope (5/24 first ship)

只覆盖 hermes 0.14 的 web 工具组 (web_search / web_extract / web_crawl), 共用
Tavily backend (一份 key, 一行 yaml). 其他外部 key 工具 (image_generate /
x_search / 等) 沿用同套架构, 后续 sprint 加 registry 行就行.

# 不在 scope

- 不返 Tavily 配额信息 (gateway 不代理 Tavily quota, 那是 Tavily 自己 dashboard 的事).
- 不做 per-user key rotation (生产 admin 通过改 .env + reload 全局轮换, 单
  员工粒度暂不支持).
- 不缓存 endpoint 输出 (env 是运行时读, 改完 .env 重启 gateway 立刻生效).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


# ── tool registry ──────────────────────────────────────────────────


@dataclass(frozen=True)
class EdgeToolConfig:
    """单个 hermes 边缘工具的中央派发描述.

    一个 tool_name → 一个 EdgeToolConfig. tool_group 相同的工具共享 env_vars
    + yaml_block (例: web_search/web_extract/web_crawl 都属 'web', 共用
    TAVILY_API_KEY + web.backend yaml 段).
    """

    tool_name: str          # hermes 真实 tool 名 (web_search / web_extract / 等)
    tool_group: str          # 配置组名 (web / image / ...) — 同组共享 env 和 yaml
    provider: str            # 当前选的 provider (tavily / firecrawl / 等)
    env_var_name: str        # 边缘 .env 文件里的 key 名
    env_var_source: str      # 中央 gateway 进程读 key 的 env 名 (通常 = env_var_name)
    yaml_block: dict[str, Any]  # 写进 ~/.hermes/config.yaml 的 yaml 段  # noqa: BOUNDARY (描述 edge 行为)

    def to_response(self, env_value: str) -> dict[str, Any]:
        """生成给 CLI 的 JSON 响应. 真 key 值在这一步注入."""
        return {
            "tool_name": self.tool_name,
            "tool_group": self.tool_group,
            "provider": self.provider,
            "env_vars": {self.env_var_name: env_value},
            "yaml_block": self.yaml_block,
        }


# 5/24 first ship: web 工具组 (Tavily). 一份 key, 3 个 tool 共用.
#
# 加新 tool 流程 (e.g. image_generate):
#   1. 在 .env 里加 IMAGE_PROVIDER_API_KEY
#   2. 这里加一行 "image_generate": EdgeToolConfig(..., env_var_source="IMAGE_PROVIDER_API_KEY", yaml_block={"image": {"backend": "..."}})
#   3. alembic seed 给部门加 image_generate RBAC 行
#   4. (无需改 CLI, 它已经按 _SUPPORTED_TOOLS 自动迭代)
#
# 选 Tavily 而不是 hermes default Firecrawl: 5/24 demo 用户在中国, Firecrawl
# 国内访问不稳; Tavily 1k 搜索/月免费够 demo 用. 后续也可换 SearXNG 自托管
# (零月费) 但要先架 docker 容器.
_TAVILY_WEB_YAML = {
    # hermes 0.14 web tool config schema (NousResearch/hermes-agent 官方文档确认):
    #   web.backend = single backend for all 3 web tools, 或
    #   web.search_backend / web.extract_backend split (per-capability)
    # 我们选 single backend mode — 3 个 tool 都走 Tavily, 简单一致.
    "web": {"backend": "tavily"},
}

EDGE_TOOL_REGISTRY: dict[str, EdgeToolConfig] = {
    "web_search": EdgeToolConfig(
        tool_name="web_search",
        tool_group="web",
        provider="tavily",
        env_var_name="TAVILY_API_KEY",
        env_var_source="TAVILY_API_KEY",
        yaml_block=_TAVILY_WEB_YAML,
    ),
    "web_extract": EdgeToolConfig(
        tool_name="web_extract",
        tool_group="web",
        provider="tavily",
        env_var_name="TAVILY_API_KEY",
        env_var_source="TAVILY_API_KEY",
        yaml_block=_TAVILY_WEB_YAML,
    ),
    "web_crawl": EdgeToolConfig(
        tool_name="web_crawl",
        tool_group="web",
        provider="tavily",
        env_var_name="TAVILY_API_KEY",
        env_var_source="TAVILY_API_KEY",
        yaml_block=_TAVILY_WEB_YAML,
    ),
}


# ── 公开 API ─────────────────────────────────────────────────────


def is_supported_tool(tool_name: str) -> bool:
    """该 tool 是否走中央派发. 不支持 → endpoint 返 404."""
    return tool_name in EDGE_TOOL_REGISTRY


def list_supported_tools() -> list[str]:
    """给 CLI 用 — 拉一次 list 知道要迭代哪些 endpoint."""
    return sorted(EDGE_TOOL_REGISTRY.keys())


def get_env_value(tool_name: str) -> str | None:
    """从 gateway 进程 env 读真 key 值. 不存在或空白返 None.

    返 None → endpoint 返 503 (admin 还没配 .env). 不返 500, 因为这不是 bug.
    """
    cfg = EDGE_TOOL_REGISTRY.get(tool_name)
    if cfg is None:
        return None
    raw = os.environ.get(cfg.env_var_source, "")
    if not isinstance(raw, str):
        return None
    val = raw.strip()
    return val or None


def build_response(tool_name: str) -> tuple[int, dict[str, Any]]:
    """组装 endpoint 响应. 返 (http_status, body).

    - 404: 不在 registry
    - 503: 在 registry 但 gateway env 没配 key
    - 200: OK, body 含 env_vars + yaml_block
    """
    cfg = EDGE_TOOL_REGISTRY.get(tool_name)
    if cfg is None:
        return 404, {
            "error": f"tool {tool_name!r} 不在中央派发 registry",
            "supported": list_supported_tools(),
        }

    env_value = get_env_value(tool_name)
    if env_value is None:
        return 503, {
            "error": (
                f"gateway 进程 env 没配 {cfg.env_var_source} — "
                f"admin 要去 central/llm-gateway/.env 加这条然后 reload gateway."
            ),
            "tool_name": tool_name,
            "tool_group": cfg.tool_group,
            "provider": cfg.provider,
        }

    return 200, cfg.to_response(env_value)
