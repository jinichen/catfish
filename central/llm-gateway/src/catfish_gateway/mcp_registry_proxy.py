"""mcp-registry 反向代理 (BL-D3 Phase 1 收尾, 5/9 ship).

gateway 收 `/v1/mcp/*` → 透传到 mcp-registry upstream, 并从 JWT 抽 dept
加到 `X-Catfish-User-Dept` header. mcp-registry 据此做部门权限过滤.

为啥反代不让 Companion 直连:
    1. Companion 走单一 origin (gateway) 简化 CORS / 鉴权
    2. dept 必须 gateway 注入 — Companion 自己改 dept 就绕权限了 (员工
       理论上能 fetch + 改 X-Catfish-User-Dept: admin 看不该看的连接器).
       服务端注入 = 真权限.
    3. mcp-registry 部署可随时换地址 (yaml 配), Companion 不用重新打包.

Phase 2+ 会加更多 endpoint (/v1/mcp/subscribe POST/DELETE), 这层透明转发,
不需要改.
"""
from __future__ import annotations

import logging
import urllib.parse
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .auth import User, get_current_user
from .config import get_config

logger = logging.getLogger("catfish.gateway.mcp_registry_proxy")

router = APIRouter(prefix="/v1/mcp", tags=["mcp-registry"])

# 单例 httpx client (lifespan 起的, app.state.mcp_registry_client)
# 这里只读 app.state, 不持自己的连接池.

_FORWARD_HEADERS = {
    "accept",
    "accept-encoding",
    "accept-language",
    "content-type",
    "user-agent",
}
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",  # 必须重写, httpx 自己加
    "content-length",  # httpx 自己算
}


def _safe_header_value(value: str) -> str:
    """BL-D3 fix2 (5/9): HTTP header 值 ASCII-safe.

    catfish 部门 / 用户名可能含中文 (e.g. '企业发展与风控部'), HTTP/1.1
    header 标准只允许 latin-1, httpx 严格用 ASCII. 全 ASCII 则原样, 否则
    URL-encode (RFC 3986 percent-encoding, UTF-8 字节). 上游 mcp-registry
    端读 header 时 urllib.parse.unquote 还原.

    safe='@.-_/' — 让常见 ASCII 字符不被 escape, 可读性更好.
    """
    if not value:
        return ""
    if all(ord(c) < 128 for c in value):
        return value
    return urllib.parse.quote(value, safe="@.-_/+")


def _filter_request_headers(headers: Any) -> dict[str, str]:
    """过滤请求 headers — 只透传安全的, 跳 hop-by-hop + Authorization
    (上游不需要原始 JWT, 我们改加 X-Catfish-User-* header)."""
    out = {}
    for k, v in headers.items():
        kl = k.lower()
        if kl in _HOP_BY_HOP_HEADERS:
            continue
        if kl == "authorization":
            continue  # 不透传 JWT, 上游 mcp-registry 信任 gateway
        if kl in _FORWARD_HEADERS or kl.startswith("x-"):
            out[k] = v
    return out


def _filter_response_headers(headers: httpx.Headers) -> dict[str, str]:
    """过滤响应 headers — 跳 hop-by-hop, 透传其他."""
    return {
        k: v for k, v in headers.items()
        if k.lower() not in _HOP_BY_HOP_HEADERS
    }


async def _proxy(
    request: Request,
    upstream_path: str,
    user: User,
) -> Response:
    """统一代理函数 — 把 request 透传到 mcp-registry upstream + 注入员工身份."""
    config = get_config()
    cfg = config.mcp_registry

    if not cfg.enabled:
        raise HTTPException(
            status_code=503,
            detail="mcp-registry 未启用 (gateway config.mcp_registry.enabled=false)",
        )

    upstream_url = f"{cfg.upstream_url.rstrip('/')}{upstream_path}"
    headers = _filter_request_headers(request.headers)
    # 注入员工身份 — 不让 Companion 自己伪造 dept 绕权限.
    # BL-D3 fix2 (5/9): user.department / sub 可能含中文 (例 '企业发展与风控部'),
    # HTTP header 默认 ASCII (httpx 严格), 必须 percent-encode (RFC 5987 风格).
    # mcp-registry 端读 header 时 urllib.parse.unquote 还原.
    headers["X-Catfish-User-Sub"] = _safe_header_value(user.sub or "")
    headers["X-Catfish-User-Dept"] = _safe_header_value(user.department or "")
    headers["X-Catfish-User-Role"] = _safe_header_value(user.role or "employee")

    body = await request.body()
    client: httpx.AsyncClient = request.app.state.mcp_registry_client

    try:
        upstream_resp = await client.request(
            method=request.method,
            url=upstream_url,
            headers=headers,
            params=request.query_params,
            content=body if body else None,
            timeout=cfg.timeout,
        )
    except httpx.ConnectError as e:
        logger.warning("mcp-registry 连接失败: %s (%s)", upstream_url, e)
        raise HTTPException(
            status_code=502,
            detail=f"mcp-registry 不可达 ({cfg.upstream_url}). 是否启动? "
            f"`python -m catfish_mcp_registry.app`",
        ) from e
    except httpx.TimeoutException as e:
        logger.warning("mcp-registry 超时: %s (%ds)", upstream_url, cfg.timeout)
        raise HTTPException(status_code=504, detail="mcp-registry 上游超时") from e

    return Response(
        content=upstream_resp.content,
        status_code=upstream_resp.status_code,
        headers=_filter_response_headers(upstream_resp.headers),
        media_type=upstream_resp.headers.get("content-type"),
    )


@router.get("/registry")
async def list_registry(
    request: Request,
    user: User = Depends(get_current_user),
) -> Response:
    """列可订阅 MCP 连接器 — 透传 GET /v1/mcp/registry, 注入员工 dept."""
    return await _proxy(request, "/v1/mcp/registry", user)


@router.get("/manifest/{connector_id}")
async def get_manifest(
    connector_id: str,
    request: Request,
    user: User = Depends(get_current_user),
) -> Response:
    """单连接器详情 — 透传 GET /v1/mcp/manifest/{id}, 注入 dept (上游做 403)."""
    return await _proxy(request, f"/v1/mcp/manifest/{connector_id}", user)


# ── Phase 2+ endpoints (留位, 透传逻辑一样) ──────────────────────


@router.post("/subscribe")
async def subscribe(
    request: Request,
    user: User = Depends(get_current_user),
) -> Response:
    """订阅 (Phase 2 接通)."""
    return await _proxy(request, "/v1/mcp/subscribe", user)


@router.delete("/subscribe/{subscription_id}")
async def unsubscribe(
    subscription_id: str,
    request: Request,
    user: User = Depends(get_current_user),
) -> Response:
    """取消订阅 (Phase 2 接通)."""
    return await _proxy(
        request, f"/v1/mcp/subscribe/{subscription_id}", user
    )


@router.get("/subscribed")
async def my_subscriptions(
    request: Request,
    user: User = Depends(get_current_user),
) -> Response:
    """我订阅的 (Phase 2 接通)."""
    return await _proxy(request, "/v1/mcp/subscribed", user)
