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
  ├─ .env 持 CATFISH_WEB_BACKEND (默认 parallel) + 可选该家 key (admin 改这里, 改完 reload)
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

只覆盖 hermes 的 web 工具组 (web_search / web_extract / web_crawl), 共用
一个 backend (一份 key, 一行 yaml; 10/2 起 backend 可配, 见下面 WEB_BACKENDS). 其他外部 key 工具 (image_generate /
x_search / 等) 沿用同套架构, 后续 sprint 加 registry 行就行.

# 不在 scope

- 不返 backend 配额信息 (gateway 不代理配额, 那是各家自己 dashboard 的事).
- 不做 per-user key rotation (生产 admin 通过改 .env + reload 全局轮换, 单
  员工粒度暂不支持).
- 不缓存 endpoint 输出 (env 是运行时读, 改完 .env 重启 gateway 立刻生效).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


# ── web backend (10/2 改) ──────────────────────────────────────────
#
# 5/24 写死 Tavily。hermes 后来把 web 后端全迁成插件 (plugins/web/), Tavily 没迁
# 过去 —— 现在的 hermes 里根本没有这个名字。而 hermes 对 web.backend 是 strict
# 的: 存的名字原样用, 不探可用性、不回退 (tools/web_tools.py _get_backend)。于是
# 中央每 50 分钟 (ai.catfish.token-refresh) 把 `web.backend: tavily` 刷回每个
# 员工的 config.yaml, web_search / web_extract 全挂, 员工手改也活不过 50 分钟。
#
# 现在 backend 由 gateway env `CATFISH_WEB_BACKEND` 定, 默认 parallel:
# parallel / exa 有免费匿名通道 (hermes plugins/web/keyless_mcp.py), 不配 key
# 也能用 (10/2 实测 search 5/5); admin 买了 key 就在 gateway .env 里配对应的
# 环境变量, 跟原来 Tavily 一样下发。名字必须是现在 hermes 真有的插件。

@dataclass(frozen=True)
class WebBackend:
    name: str              # hermes web.backend 的值 (= plugins/web 里注册的名字)
    key_env: str           # 这家的 key 在 gateway 和员工 ~/.hermes/.env 里的变量名  # noqa: BOUNDARY (描述 edge 行为)
    keyless: bool          # 不配 key 时 hermes 能不能走免费匿名通道


WEB_BACKENDS: dict[str, WebBackend] = {
    b.name: b
    for b in (
        WebBackend("parallel", "PARALLEL_API_KEY", keyless=True),
        WebBackend("exa", "EXA_API_KEY", keyless=True),
        WebBackend("brave-free", "BRAVE_SEARCH_API_KEY", keyless=False),
        WebBackend("firecrawl", "FIRECRAWL_API_KEY", keyless=False),
        WebBackend("keenable", "KEENABLE_API_KEY", keyless=False),
        WebBackend("searxng", "SEARXNG_URL", keyless=False),
    )
}
DEFAULT_WEB_BACKEND = "parallel"


def web_backend_name() -> str:
    """gateway env CATFISH_WEB_BACKEND, 没配 = parallel。不认识的名字原样返, build_response 报 503。"""
    return (os.environ.get("CATFISH_WEB_BACKEND") or DEFAULT_WEB_BACKEND).strip().lower()


# ── tool registry ──────────────────────────────────────────────────


@dataclass(frozen=True)
class EdgeToolConfig:
    """单个 hermes 边缘工具的中央派发描述.

    tool_group 相同的工具共享 env_vars + yaml_block (web_search / web_extract /
    web_crawl 都属 'web', 共用一个 web.backend)。
    """

    tool_name: str          # hermes 真实 tool 名 (web_search / web_extract / 等)
    tool_group: str          # 配置组名 (web / image / ...) — 同组共享 env 和 yaml


# 加新 tool 流程 (e.g. image_generate):
#   1. 这里加一行, build_response 里补这个 group 的 provider / key 解析
#   2. alembic seed 给部门加 RBAC 行
#   3. (无需改 CLI, 它按 list_supported_tools 自动迭代)
EDGE_TOOL_REGISTRY: dict[str, EdgeToolConfig] = {
    name: EdgeToolConfig(tool_name=name, tool_group="web")
    for name in ("web_search", "web_extract", "web_crawl")
}


def group_provider(tool_group: str) -> tuple[str, str]:
    """(provider, key_env) —— 给 /v1/edge/tool-config 列表的元信息用。"""
    name = web_backend_name()
    b = WEB_BACKENDS.get(name)
    return name, (b.key_env if b else "")


# ── 公开 API ─────────────────────────────────────────────────────


def is_supported_tool(tool_name: str) -> bool:
    """该 tool 是否走中央派发. 不支持 → endpoint 返 404."""
    return tool_name in EDGE_TOOL_REGISTRY


def list_supported_tools() -> list[str]:
    """给 CLI 用 — 拉一次 list 知道要迭代哪些 endpoint."""
    return sorted(EDGE_TOOL_REGISTRY.keys())


def get_env_value(tool_name: str) -> str | None:
    """从 gateway 进程 env 读当前 web backend 的 key。没配 / 空白 / 不认识的 backend → None。"""
    if tool_name not in EDGE_TOOL_REGISTRY:
        return None
    b = WEB_BACKENDS.get(web_backend_name())
    if b is None:
        return None
    raw = os.environ.get(b.key_env, "")
    if not isinstance(raw, str):
        return None
    val = raw.strip()
    return val or None


def build_response(tool_name: str) -> tuple[int, dict[str, Any]]:
    """组装 endpoint 响应. 返 (http_status, body).

    - 404: 不在 registry
    - 503: CATFISH_WEB_BACKEND 不是现在 hermes 有的插件; 或这家必须有 key 而 gateway 没配
    - 200: OK, body 含 env_vars (keyless 且没配 key 时为空) + yaml_block
    """
    cfg = EDGE_TOOL_REGISTRY.get(tool_name)
    if cfg is None:
        return 404, {
            "error": f"tool {tool_name!r} 不在中央派发 registry",
            "supported": list_supported_tools(),
        }

    name = web_backend_name()
    backend = WEB_BACKENDS.get(name)
    base = {"tool_name": tool_name, "tool_group": cfg.tool_group, "provider": name}
    if backend is None:
        return 503, {
            **base,
            "error": (
                f"CATFISH_WEB_BACKEND={name!r} 不是 hermes 现有的 web 插件 "
                f"(可选: {', '.join(sorted(WEB_BACKENDS))}) — admin 改 central/llm-gateway/.env 后 reload."
            ),
        }

    env_value = get_env_value(tool_name)
    if env_value is None and not backend.keyless:
        return 503, {
            **base,
            "error": (
                f"web 后端 {name} 必须有 key, gateway 进程 env 没配 {backend.key_env} — "
                f"admin 要去 central/llm-gateway/.env 加这条然后 reload gateway."
            ),
        }

    return 200, {
        **base,
        "env_vars": {backend.key_env: env_value} if env_value else {},
        "yaml_block": {"web": {"backend": name}},
    }
