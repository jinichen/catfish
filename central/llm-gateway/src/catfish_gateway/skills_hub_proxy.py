"""skills-hub 反向代理 (BL-D2 5/10).

gateway 收 `/v1/hub/*` → 透传到 catfish-skills-hub upstream (默认 :8997),
跟 mcp-registry 反代同模式 (BL-D3 Phase 1):
  - gateway 端 OIDC 验真员工身份
  - 注入 X-Catfish-User-Sub / -Dept / -Role 到上游
  - 上游 hub 信任 header 不再自己验 dev_token (BL-FIX29 关掉了那条)

跟 mcp_registry_proxy 几乎一样, 唯一区别: prefix `/v1/hub` + 上游 path `/skills/*`.

# 端点

  GET   /v1/hub/skills                          列已发布
  GET   /v1/hub/skills/{ns}/{name}              skill 元 + 最新版
  GET   /v1/hub/skills/{ns}/{name}/{version}    指定版本元
  GET   /v1/hub/skills/{ns}/{name}/{ver}/files/{path}  下载文件
  POST  /v1/hub/skills/{namespace}              发布新版 (multipart, 透传)
  DELETE /v1/hub/skills/{ns}/{name}/{version}   删 (上游 admin only 拦截)
  GET   /v1/hub/audit                           audit (admin)
  GET   /v1/hub/healthz                         健康
"""
from __future__ import annotations

import logging
import urllib.parse
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .auth import User, get_current_user
from .config import get_config

logger = logging.getLogger("catfish.gateway.skills_hub_proxy")

router = APIRouter(prefix="/v1/hub", tags=["skills-hub"])


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
    """非 ASCII (中文) percent-encode 走 HTTP/1.1 安全 (跟 mcp_registry_proxy 同款)."""
    if not value:
        return ""
    if all(ord(c) < 128 for c in value):
        return value
    return urllib.parse.quote(value, safe="@.-_/+")


def _filter_request_headers(headers: Any) -> dict[str, str]:
    """过滤请求 headers — 透传安全的, 跳 hop-by-hop + Authorization."""
    out = {}
    for k, v in headers.items():
        kl = k.lower()
        if kl in _HOP_BY_HOP_HEADERS:
            continue
        if kl == "authorization":
            continue  # 不透传 JWT, 上游 hub 信任 gateway X-Catfish-User-* 注入
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
    """统一代理. 透传到 hub upstream + 注入员工身份 header."""
    config = get_config()
    cfg = config.skills_hub

    if not cfg.enabled:
        raise HTTPException(
            status_code=503,
            detail="skills-hub 未启用 (gateway config.skills_hub.enabled=false)",
        )

    upstream_url = f"{cfg.upstream_url.rstrip('/')}{upstream_path}"
    headers = _filter_request_headers(request.headers)
    headers["X-Catfish-User-Sub"] = _safe_header_value(user.sub or "")
    headers["X-Catfish-User-Dept"] = _safe_header_value(user.department or "")
    headers["X-Catfish-User-Role"] = _safe_header_value(user.role or "employee")
    # 10/18 专家包审核要知道 manager 管哪些部门 (跟 wiki_hub_proxy 同写法)
    headers["X-Catfish-User-Managed-Depts"] = ",".join(
        urllib.parse.quote(d, safe="") for d in (user.managed_departments or []) if d
    )

    body = await request.body()
    client: httpx.AsyncClient = request.app.state.skills_hub_client

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
        logger.warning("skills-hub 连接失败: %s (%s)", upstream_url, e)
        raise HTTPException(
            status_code=502,
            detail=f"skills-hub 不可达 ({cfg.upstream_url}). 是否启动? "
            f"`python -m catfish_skills_hub.app`",
        ) from e
    except httpx.TimeoutException as e:
        logger.warning("skills-hub 超时: %s (%ds)", upstream_url, cfg.timeout)
        raise HTTPException(status_code=504, detail="skills-hub 上游超时") from e

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


@router.get("/skills")
async def list_skills(
    request: Request,
    user: User = Depends(get_current_user),
) -> Response:
    """列已发布 skill (按 ns/name, 各取最新版)."""
    return await _proxy(request, "/skills", user)


@router.get("/skills/{namespace}/{name}")
async def get_skill_latest(
    namespace: str,
    name: str,
    request: Request,
    user: User = Depends(get_current_user),
) -> Response:
    return await _proxy(request, f"/skills/{namespace}/{name}", user)


@router.get("/skills/{namespace}/{name}/{version}")
async def get_skill_version(
    namespace: str,
    name: str,
    version: str,
    request: Request,
    user: User = Depends(get_current_user),
) -> Response:
    return await _proxy(request, f"/skills/{namespace}/{name}/{version}", user)


@router.get("/skills/{namespace}/{name}/{version}/files/{file_path:path}")
async def download_file(
    namespace: str,
    name: str,
    version: str,
    file_path: str,
    request: Request,
    user: User = Depends(get_current_user),
) -> Response:
    return await _proxy(
        request,
        f"/skills/{namespace}/{name}/{version}/files/{file_path}",
        user,
    )


# ── 写 endpoints (员工 publish, admin delete) ─────────────────


@router.post("/skills/{namespace}")
async def publish_skill(
    namespace: str,
    request: Request,
    user: User = Depends(get_current_user),
) -> Response:
    """发布 skill (multipart files). 上游 storage 用 X-Catfish-User-Sub 当 published_by."""
    return await _proxy(request, f"/skills/{namespace}", user)


@router.delete("/skills/{namespace}/{name}/{version}")
async def delete_skill_version(
    namespace: str,
    name: str,
    version: str,
    request: Request,
    user: User = Depends(get_current_user),
) -> Response:
    """删 (上游 require_admin 拦 manager / employee)."""
    return await _proxy(request, f"/skills/{namespace}/{name}/{version}", user)


@router.get("/audit")
async def audit(
    request: Request,
    user: User = Depends(get_current_user),
) -> Response:
    """admin 看 audit (上游拦)."""
    return await _proxy(request, "/audit", user)


# ── 专家包 (10/18, docs/EXPERT-PACK-PLAN.md §5): 提交 / 市场 / 审核 / 下载 ─────────
# 谁能看、谁能审由 hub 按部门判; 下载这里再加一道: identity 的 allowed_skills 对 `expert/<id>` 生效
# (5/17 存了这个字段一直没人执行; 专家包是第一个按它拦的)。


@router.get("/experts")
async def experts_market(request: Request, user: User = Depends(get_current_user)) -> Response:
    return await _proxy(request, "/experts", user)


@router.get("/experts/mine")
async def experts_mine(request: Request, user: User = Depends(get_current_user)) -> Response:
    return await _proxy(request, "/experts/mine", user)


@router.get("/experts/review")
async def experts_review_list(request: Request, user: User = Depends(get_current_user)) -> Response:
    return await _proxy(request, "/experts/review", user)


@router.get("/experts/signing-key")
async def experts_signing_key(request: Request, user: User = Depends(get_current_user)) -> Response:
    return await _proxy(request, "/experts/signing-key", user)


@router.post("/experts")
async def experts_submit(request: Request, user: User = Depends(get_current_user)) -> Response:
    return await _proxy(request, "/experts", user)


@router.get("/experts/{pack_id}/{version}")
async def experts_detail(pack_id: str, version: str, request: Request, user: User = Depends(get_current_user)) -> Response:
    return await _proxy(request, f"/experts/{pack_id}/{version}", user)


@router.post("/experts/{pack_id}/{version}/review")
async def experts_review(pack_id: str, version: str, request: Request, user: User = Depends(get_current_user)) -> Response:
    return await _proxy(request, f"/experts/{pack_id}/{version}/review", user)


@router.post("/experts/{pack_id}/{version}/withdraw")
async def experts_withdraw(pack_id: str, version: str, request: Request, user: User = Depends(get_current_user)) -> Response:
    return await _proxy(request, f"/experts/{pack_id}/{version}/withdraw", user)


@router.get("/experts/{pack_id}/{version}/download")
async def experts_download(pack_id: str, version: str, request: Request, user: User = Depends(get_current_user)) -> Response:
    if not user.can_use_skill(f"expert/{pack_id}"):
        raise HTTPException(status_code=403, detail=f"你的账号没有开通专家包 {pack_id} (allowed_skills 不含 expert/{pack_id})")
    return await _proxy(request, f"/experts/{pack_id}/{version}/download", user)
