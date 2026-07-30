"""wiki-hub 反向代理 (P3.3.18, 6/10).

gateway 收 `/v1/wiki/*` → 透传到 catfish-wiki-hub upstream (默认 :8998),
跟 skills_hub_proxy 同模式:
  - gateway 端 OIDC 验真员工身份
  - 注入 X-Catfish-User-Sub / -Dept / -Role 到上游
  - 上游 hub 信任 header 不再自己验

# 端点

  GET   /v1/wiki/healthz                                  健康
  GET   /v1/wiki/documents                                列已发布
  GET   /v1/wiki/documents/{ns}/{file_id}                 单条详情 + body
  POST  /v1/wiki/documents/{ns}                           发布 / 重发
  POST  /v1/wiki/documents/{ns}/{file_id}/unpublish       撤回 (员工 self / admin)
  GET   /v1/wiki/audit                                    admin 审计

# Manifesto

公理 2 (员工主动 push 例外) / 公理 3 (中央无 push) / 公理 4 (unpublish 不动员工本机).
详 CATFISH-CENTRAL-MANIFESTO.md.
"""
from __future__ import annotations

import logging
import urllib.parse
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .auth import User, get_current_user
from .config import get_config

logger = logging.getLogger("catfish.gateway.wiki_hub_proxy")

router = APIRouter(prefix="/v1/wiki", tags=["wiki-hub"])


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
    "host",
    "content-length",
}


def _safe_header_value(value: str) -> str:
    if not value:
        return ""
    if all(ord(c) < 128 for c in value):
        return value
    return urllib.parse.quote(value, safe="@.-_/+")


def _filter_request_headers(headers: Any) -> dict[str, str]:
    out = {}
    for k, v in headers.items():
        kl = k.lower()
        if kl in _HOP_BY_HOP_HEADERS:
            continue
        if kl == "authorization":
            continue
        if kl in _FORWARD_HEADERS or kl.startswith("x-"):
            out[k] = v
    return out


def _filter_response_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        k: v for k, v in headers.items()
        if k.lower() not in _HOP_BY_HOP_HEADERS
    }


async def _proxy(
    request: Request,
    upstream_path: str,
    user: User,
) -> Response:
    config = get_config()
    cfg = config.wiki_hub

    if not cfg.enabled:
        raise HTTPException(
            status_code=503,
            detail="wiki-hub 未启用 (gateway config.wiki_hub.enabled=false)",
        )

    upstream_url = f"{cfg.upstream_url.rstrip('/')}{upstream_path}"
    headers = _filter_request_headers(request.headers)
    headers["X-Catfish-User-Sub"] = _safe_header_value(user.sub or "")
    headers["X-Catfish-User-Dept"] = _safe_header_value(user.department or "")
    headers["X-Catfish-User-Role"] = _safe_header_value(user.role or "employee")

    body = await request.body()
    client: httpx.AsyncClient = request.app.state.wiki_hub_client

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
        logger.warning("wiki-hub 连接失败: %s (%s)", upstream_url, e)
        raise HTTPException(
            status_code=502,
            detail=(
                f"wiki-hub 不可达 ({cfg.upstream_url}). 是否启动? "
                f"`python -m catfish_wiki_hub.app`"
            ),
        ) from e
    except httpx.TimeoutException as e:
        logger.warning("wiki-hub 超时: %s (%ds)", upstream_url, cfg.timeout)
        raise HTTPException(status_code=504, detail="wiki-hub 上游超时") from e

    return Response(
        content=upstream_resp.content,
        status_code=upstream_resp.status_code,
        headers=_filter_response_headers(upstream_resp.headers),
        media_type=upstream_resp.headers.get("content-type"),
    )


# ── 公开 endpoints (所有员工可读) ─────────────────────────────


@router.get("/healthz")
async def healthz(
    request: Request,
    user: User = Depends(get_current_user),
) -> Response:
    return await _proxy(request, "/healthz", user)


@router.get("/documents")
async def list_documents(
    request: Request,
    user: User = Depends(get_current_user),
) -> Response:
    return await _proxy(request, "/wiki/documents", user)


@router.get("/documents/{namespace}/{file_id}")
async def get_document(
    namespace: str,
    file_id: str,
    request: Request,
    user: User = Depends(get_current_user),
) -> Response:
    return await _proxy(request, f"/wiki/documents/{namespace}/{file_id}", user)


# ── 写 endpoints ─────────────────────────────────────────────


@router.post("/documents/{namespace}")
async def publish_document(
    namespace: str,
    request: Request,
    user: User = Depends(get_current_user),
) -> Response:
    """发布 wiki (JSON body). 上游用 X-Catfish-User-Sub 当 published_by."""
    return await _proxy(request, f"/wiki/documents/{namespace}", user)


@router.post("/documents/{namespace}/{file_id}/unpublish")
async def unpublish_document(
    namespace: str,
    file_id: str,
    request: Request,
    user: User = Depends(get_current_user),
) -> Response:
    """撤回 wiki — 员工只能撤自己 publish 的, admin 例外."""
    return await _proxy(
        request, f"/wiki/documents/{namespace}/{file_id}/unpublish", user,
    )


@router.get("/audit")
async def audit(
    request: Request,
    user: User = Depends(get_current_user),
) -> Response:
    return await _proxy(request, "/wiki/audit", user)
