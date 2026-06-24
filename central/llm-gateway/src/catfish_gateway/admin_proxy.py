"""identity-server admin 反代 (BL-ARCH1 P1 5/10).

gateway 收 `/api/admin/*` → 透传到 catfish-identity :8998 + 注入 X-Catfish-User-*.
跟 skills_hub_proxy / mcp_registry_proxy 同模板.

# 端点 (透传, 上游 admin_router 处理)

  GET    /api/admin/users
  POST   /api/admin/users
  GET    /api/admin/users/{email}
  PUT    /api/admin/users/{email}
  DELETE /api/admin/users/{email}
  POST   /api/admin/users/{email}/lock
  POST   /api/admin/users/{email}/reset-password
  GET    /api/admin/users-audit
  GET    /api/admin/me-as-admin
"""
from __future__ import annotations

import logging
import urllib.parse
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .auth import User, get_current_user

logger = logging.getLogger("catfish.gateway.admin_proxy")

router = APIRouter(prefix="/api/admin", tags=["admin"])


_HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}
_FORWARD_HEADERS = {"accept", "accept-encoding", "accept-language", "content-type", "user-agent"}


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
        if kl in _HOP_BY_HOP_HEADERS or kl == "authorization":
            continue
        if kl in _FORWARD_HEADERS or kl.startswith("x-"):
            out[k] = v
    return out


def _filter_response_headers(headers: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP_HEADERS}


async def _proxy(
    request: Request,
    upstream_path: str,
    user: User,
) -> Response:
    """透传到 identity-server admin_router. 注入 X-Catfish-User-* header."""
    # identity-server URL 配置 — 默认 dev 8998. 真生产 env CATFISH_IDENTITY_URL.
    import os as _os
    identity_url = _os.environ.get(
        "CATFISH_IDENTITY_URL", "http://127.0.0.1:8998",
    ).rstrip("/")

    upstream_url = f"{identity_url}{upstream_path}"
    headers = _filter_request_headers(request.headers)
    headers["X-Catfish-User-Sub"] = _safe_header_value(user.sub or "")
    headers["X-Catfish-User-Dept"] = _safe_header_value(user.department or "")
    headers["X-Catfish-User-Role"] = _safe_header_value(user.role or "employee")

    body = await request.body()
    # 复用 mcp_registry_client / skills_hub_client 同款 lifespan httpx client
    # 这里简化用 per-request client (admin endpoints 调用频率低, 不必单例)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            upstream_resp = await client.request(
                method=request.method,
                url=upstream_url,
                headers=headers,
                params=request.query_params,
                content=body if body else None,
            )
    except httpx.ConnectError as e:
        logger.warning("identity-server 连接失败: %s (%s)", upstream_url, e)
        raise HTTPException(
            status_code=502,
            detail=f"identity-server 不可达 ({identity_url}). 是否启动?",
        ) from e
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="identity-server 上游超时")

    return Response(
        content=upstream_resp.content,
        status_code=upstream_resp.status_code,
        headers=_filter_response_headers(upstream_resp.headers),
        media_type=upstream_resp.headers.get("content-type"),
    )


# ── 路由 (透传, 上游 RBAC 拦截) ──────────────────────────────────


@router.get("/users")
async def list_users(request: Request, user: User = Depends(get_current_user)) -> Response:
    return await _proxy(request, "/admin/users", user)


@router.post("/users")
async def create_user(request: Request, user: User = Depends(get_current_user)) -> Response:
    return await _proxy(request, "/admin/users", user)


@router.get("/users/{email}")
async def get_user(email: str, request: Request, user: User = Depends(get_current_user)) -> Response:
    return await _proxy(request, f"/admin/users/{email}", user)


@router.put("/users/{email}")
async def update_user(email: str, request: Request, user: User = Depends(get_current_user)) -> Response:
    return await _proxy(request, f"/admin/users/{email}", user)


@router.delete("/users/{email}")
async def delete_user(email: str, request: Request, user: User = Depends(get_current_user)) -> Response:
    return await _proxy(request, f"/admin/users/{email}", user)


@router.post("/users/{email}/lock")
async def lock_user(email: str, request: Request, user: User = Depends(get_current_user)) -> Response:
    return await _proxy(request, f"/admin/users/{email}/lock", user)


@router.post("/users/{email}/reset-password")
async def reset_password(email: str, request: Request, user: User = Depends(get_current_user)) -> Response:
    return await _proxy(request, f"/admin/users/{email}/reset-password", user)


@router.get("/users-audit")
async def users_audit(request: Request, user: User = Depends(get_current_user)) -> Response:
    return await _proxy(request, "/admin/users-audit", user)


@router.get("/me-as-admin")
async def me_as_admin(request: Request, user: User = Depends(get_current_user)) -> Response:
    return await _proxy(request, "/admin/me-as-admin", user)


# ── P3.5.95 (6/23 鸿波): /departments 透传 — 治 5/17 BL-RBAC-DAY7 漏的 dead UI
#
# identity-server admin_router 真有 3 个 endpoint, 但 gateway admin_proxy 没透传.
# 6 周来 AccessPage 列表 → 404 静默吃 → "0 个部门". 修法只在 gateway 这层补:
#   GET    /departments        → 列所有
#   GET    /departments/{name}  → 单个详情
#   PUT    /departments/{name}  → 改 allowed_models / allowed_tools / allowed_skills
# 没 POST / DELETE — 部门是 alembic 20260517_004 seed 进去的 4 个 RBAC dept,
# 不允许 CRUD (跟现实匹配: 部门是 HR 流程产物, 不是中央门户 self-service).


@router.get("/departments")
async def list_departments(request: Request, user: User = Depends(get_current_user)) -> Response:
    return await _proxy(request, "/admin/departments", user)


@router.get("/departments/{name}")
async def get_department(name: str, request: Request, user: User = Depends(get_current_user)) -> Response:
    return await _proxy(request, f"/admin/departments/{name}", user)


@router.put("/departments/{name}")
async def update_department(name: str, request: Request, user: User = Depends(get_current_user)) -> Response:
    return await _proxy(request, f"/admin/departments/{name}", user)
