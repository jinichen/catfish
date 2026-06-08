"""Identity-server /token endpoint grant_type handlers — 抽自 routes.py (5/20 拆分).

3 个 grant 各自独立:
  _handle_authorization_code: OIDC 标准 code → id_token + access_token + refresh_token
  _handle_refresh_token: refresh_token → 新 access_token
  _handle_client_credentials: 服务身份 (RFC 6749 §4.4) → 长 TTL access_token
"""
from __future__ import annotations

import logging
import secrets
import time
from urllib.parse import urlencode

import jwt
from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse

from .clients import ClientRegistry
from .code_store import CodeStore
from .jwt_signer import JwtSigner
from .refresh_tokens import RefreshTokenStore
from .users import UserRegistry

logger = logging.getLogger("catfish.identity.routes")

_CODE_TTL_SECS = 300
_TOKEN_TTL_SECS = 3600
_SERVICE_TOKEN_AUDIENCE = "catfish-gateway"
_SERVICE_TOKEN_TTL_SECS_DEFAULT = 3600
_SERVICE_TOKEN_TTL_SECS_MAX = 365 * 24 * 3600
_SERVICE_TOKEN_TTL_SECS_MIN = 60

# 5/20 拆: 这些常量在主文件 routes.py 也定义 — 保持两边同步, 主文件不导入这里
# (handlers 用本地常量, 防 cross-module import 引入 cycle).

# ============================================================
# /token grant_type handlers (5/14 Day 1 拆出 — 双 grant 各自独立)
# ============================================================


async def _handle_authorization_code(
    *,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,  # noqa: ARG001 — Phase 1B-1 不验
    code_store: CodeStore,
    registry: UserRegistry,
    signer: JwtSigner,
    issuer: str,
    refresh_token_store: RefreshTokenStore | None = None,
) -> JSONResponse:
    """authorization_code grant — 用户走 SSO 后浏览器换 id_token + access_token.

    Phase 1B-1 不验 client_secret. Phase 2 加 client registration + secret 验证.

    6/9 BL-F11.P2: code_store 从 in-memory 改 sqlite, record 不再带 user 对象只带
    user_email. 这里 consume 后用 registry.find(user_email) 回查 user.
    """
    if not code:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_request",
                "error_description": "code is required for authorization_code grant",
            },
        )
    if not redirect_uri:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_request",
                "error_description": "redirect_uri is required for authorization_code grant",
            },
        )

    record = code_store.consume(code)
    if record is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_grant",
                "error_description": "code 无效或已用过",
            },
        )

    if record.client_id != client_id:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_grant",
                "error_description": "client_id 不匹配",
            },
        )
    if record.redirect_uri != redirect_uri:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_grant",
                "error_description": "redirect_uri 不匹配",
            },
        )

    # 6/9 BL-F11.P2: 回查 user — sqlite 不存对象, 只存 email. user 可能在 code 颁发
    # 后被 admin 删 / 锁, 检查.
    user = registry.find(record.user_email)
    if user is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_grant",
                "error_description": "user 不存在或已删除",
            },
        )

    # 组装 ID Token (含 user claims, audience=client_id 表示这 token 给 client 看)
    # BL-RBAC-DAY3B (5/17): 用 async 版本拿 effective_allowed_models (合并 user+dept)
    id_claims = await user.to_oidc_claims_async()
    if record.nonce:
        id_claims["nonce"] = record.nonce
    id_token = signer.sign_id_token(
        issuer=issuer,
        subject=user.email,
        audience=client_id,
        claims=id_claims,
        ttl_seconds=_TOKEN_TTL_SECS,
    )
    # access_token 也用 JWT (RFC 9068 JWT Profile for OAuth 2.0 Access Tokens).
    # BL-LEAN-CHAT + BL-IDENTITY-REFRESH (5/15 凌晨): access_token 也带完整 user
    # claims (email/name/department/role/managed_departments) — 这样 gateway 拿
    # access_token 当 Bearer 时能直接还原用户身份, 不用再回查 /userinfo.
    #
    # **修正 5/14 之前的错配**: 老版本 access_token 只有 {scope, token_use=access},
    # gateway 的 oidc.py 看 token_use=access 直接拒 (line 159-162). 导致 catfish
    # login 拿的 access_token 用作 Bearer 时 401 invalid token. 现在 access_token
    # 含完整 claims, gateway 接受逻辑也跟着改 (RBAC Day 2 那边).
    #
    # access_token 跟 id_token 区别:
    #   - id_token: aud=client_id (告诉 client "用户是谁")
    #   - access_token: aud=catfish-gateway (调用 protected resource server)
    access_token_claims = dict(id_claims)
    access_token_claims["scope"] = record.scope
    access_token_claims["token_use"] = "access"
    access_token = signer.sign_id_token(
        issuer=issuer,
        subject=user.email,
        audience=_SERVICE_TOKEN_AUDIENCE,  # catfish-gateway, 跟 service token 一致
        claims=access_token_claims,
        ttl_seconds=_TOKEN_TTL_SECS,
    )
    # BL-IDENTITY-REFRESH-TOKEN (5/15): 如果配了 refresh_token_store, 同时签 refresh_token.
    # 客户端 (catfish login CLI / hermes) 拿 refresh_token 在 access 过期时无感续.
    response_body: dict = {
        "access_token": access_token,
        "id_token": id_token,
        "token_type": "Bearer",
        "expires_in": _TOKEN_TTL_SECS,
        "scope": record.scope,
    }
    if refresh_token_store is not None:
        rt = refresh_token_store.issue(
            sub=user.email,
            client_id=client_id,
            scope=record.scope,
        )
        response_body["refresh_token"] = rt.token
        response_body["refresh_expires_in"] = int(rt.expires_at - time.time())
    logger.info(
        "token OK (auth_code): user=%s client=%s refresh=%s",
        user.email, client_id,
        "yes" if refresh_token_store is not None else "no",
    )
    return JSONResponse(response_body)


async def _handle_refresh_token(
    *,
    refresh_token_str: str,
    client_id: str,
    requested_scope: str,
    refresh_token_store: RefreshTokenStore | None,
    registry: UserRegistry,
    signer: JwtSigner,
    issuer: str,
) -> JSONResponse:
    """refresh_token grant — 拿 refresh_token 换新 access_token + 新 refresh_token.

    OAuth 2.0 RFC 6749 §6. Rotation 模式 (一次性使用):
      1. 验旧 refresh_token (exists / not revoked / not expired)
      2. 验 client_id 跟旧 token 一致
      3. 标旧 token revoked (一次性)
      4. 签新 access_token + 新 refresh_token (chain 到旧 token via parent_token)
      5. 返新对

    requested_scope 可空 (= 复用旧 scope) 或 = 旧 scope 子集. 不允许扩.
    """
    if refresh_token_store is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "unsupported_grant_type",
                "error_description": "refresh_token grant 未启用 (catfish-identity 未配 refresh_token_store)",
            },
        )

    if not refresh_token_str:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_request",
                "error_description": "refresh_token is required for refresh_token grant",
            },
        )

    record = refresh_token_store.find(refresh_token_str)
    if record is None:
        logger.warning("refresh_token grant 失败: token 不存在 (client=%s)", client_id)
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_grant", "error_description": "refresh_token 无效"},
        )
    if record.is_revoked():
        # 一次性使用 — 拿过的 token 再来 = 可能被回放. 触发 chain 全 revoke (Phase 2).
        logger.warning(
            "refresh_token grant 失败: token 已 revoked (sub=%s client=%s, 可能被回放)",
            record.sub, record.client_id,
        )
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_grant", "error_description": "refresh_token 已用过 / 已吊销"},
        )
    if record.is_expired():
        logger.info("refresh_token grant 失败: token 过期 (sub=%s)", record.sub)
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_grant", "error_description": "refresh_token 已过期, 重新登录"},
        )
    if record.client_id != client_id:
        logger.warning(
            "refresh_token grant 失败: client_id 不匹配 (token client=%s vs request client=%s)",
            record.client_id, client_id,
        )
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_grant", "error_description": "client_id 不匹配 refresh_token"},
        )

    # scope 不许扩, 默认复用旧 scope
    final_scope = record.scope
    if requested_scope:
        requested = set(requested_scope.split())
        original = set(record.scope.split())
        if not requested.issubset(original):
            extra = requested - original
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_scope",
                    "error_description": f"refresh 不允许扩 scope, 越权: {', '.join(sorted(extra))}",
                },
            )
        final_scope = " ".join(sorted(requested))

    # 找 user — refresh 跟原 sub 关联. user 可能被 admin 锁了/删了, 检查.
    user = registry.find(record.sub)
    if user is None:
        # 原 user 没了 — 拒绝 refresh. revoke 这条防再用.
        refresh_token_store.revoke(record.token)
        logger.warning("refresh: sub=%s 已 不在 registry, 拒 + revoke", record.sub)
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_grant", "error_description": "user 不存在或已删除"},
        )
    if user.locked or user.deleted_at:
        refresh_token_store.revoke(record.token)
        logger.warning("refresh: sub=%s locked/deleted, 拒 + revoke", record.sub)
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_grant", "error_description": "user 已锁定或删除"},
        )

    # 标旧 token revoked (一次性使用)
    refresh_token_store.revoke(record.token)

    # 签新 access_token (跟 authorization_code 流程同模式 — 含 user claims, RFC 9068)
    # BL-RBAC-DAY3B (5/17): async 版本拿 effective_allowed_models
    user_claims = await user.to_oidc_claims_async()
    access_token_claims = dict(user_claims)
    access_token_claims["scope"] = final_scope
    access_token_claims["token_use"] = "access"
    access_token = signer.sign_id_token(
        issuer=issuer,
        subject=user.email,
        audience=_SERVICE_TOKEN_AUDIENCE,
        claims=access_token_claims,
        ttl_seconds=_TOKEN_TTL_SECS,
    )

    # 签新 refresh_token (chain to 旧)
    new_rt = refresh_token_store.issue(
        sub=user.email,
        client_id=client_id,
        scope=final_scope,
        parent_token=record.token,
    )

    logger.info(
        "token OK (refresh): user=%s client=%s scope=%s",
        user.email, client_id, final_scope,
    )
    return JSONResponse({
        "access_token": access_token,
        "refresh_token": new_rt.token,
        "refresh_expires_in": int(new_rt.expires_at - time.time()),
        "token_type": "Bearer",
        "expires_in": _TOKEN_TTL_SECS,
        "scope": final_scope,
    })


def _handle_client_credentials(
    *,
    client_id: str,
    client_secret: str,
    scope: str,
    client_registry: ClientRegistry | None,
    signer: JwtSigner,
    issuer: str,
) -> JSONResponse:
    """client_credentials grant — 服务进程换 service token (BL-RBAC Day 1, 5/14).

    OAuth 2.0 RFC 6749 §4.4. 跟 hermes-cli 等服务用. 严格验:
      - client_registry 必须配 (没配返 503 — 部署没启用)
      - client_id + client_secret 必须 bcrypt 验过
      - client.enabled 必须 true
      - client.allowed_grant_types 必须含 "client_credentials"
      - 请求 scope 必须 ⊆ client.allowed_scopes (越权返 invalid_scope)

    返: access_token (token_use=service) + token_type + expires_in + scope.
    **不返 id_token** — 服务调用没用户 sub.
    """
    if client_registry is None:
        # 部署没装 client_credentials 支持. cleanly degrade 不挂.
        # 5/21 之后 prod env 启动时会强制要求装 client_registry, 这里只是 dev fallback.
        raise HTTPException(
            status_code=503,
            detail={
                "error": "unsupported_grant_type",
                "error_description": (
                    "client_credentials grant 未启用 — "
                    "catfish-identity 启动时未配 client_registry. "
                    "见 docs/RBAC-DESIGN.md §10."
                ),
            },
        )

    if not client_secret:
        # invalid_client (RFC 6749 §5.2) — 缺凭据
        raise HTTPException(
            status_code=401,
            detail={
                "error": "invalid_client",
                "error_description": "client_secret is required for client_credentials grant",
            },
        )

    client = client_registry.verify_secret(client_id, client_secret)
    if client is None:
        # invalid_client — client_id / secret / disabled 任一不过, 统一这个错
        # (不暴露具体哪个原因, 防 enumeration)
        logger.warning(
            "client_credentials 失败: client_id=%s (unknown / wrong secret / disabled)",
            client_id,
        )
        raise HTTPException(
            status_code=401,
            detail={
                "error": "invalid_client",
                "error_description": "client 认证失败 (client_id / secret 错或 client 已禁用)",
            },
        )

    # client 必须显式允许 client_credentials grant
    if not client.supports_grant("client_credentials"):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unauthorized_client",
                "error_description": (
                    f"client {client_id!r} 未在 allowed_grant_types 里包含 client_credentials"
                ),
            },
        )

    # scope 越权检查 — requested 必须 ⊆ allowed_scopes
    requested_scopes = [s for s in scope.split() if s] if scope else []
    bad = client.has_unauthorized_scope(requested_scopes)
    if bad:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_scope",
                "error_description": (
                    f"client {client_id!r} 无权请求 scope: {', '.join(bad)}. "
                    f"允许 scope: {', '.join(client.allowed_scopes)}"
                ),
            },
        )

    # 最终 scope (空 = 给 client 全集)
    final_scopes = client.filter_scopes(requested_scopes)
    final_scope_str = " ".join(final_scopes)

    # 5/18 BL-HERMES-AUTH-LONGLIVED: 每个 client 可在 clients.yaml 配
    # service_token_ttl_seconds 覆盖默认 1h. hermes-cli 设 30 天 (2592000) 单机省心.
    # cap 365 天上限防"100 年过期"this种凭据 (真要更长走 admin audit 不走 yaml).
    ttl = getattr(client, "service_token_ttl_seconds", None) or _SERVICE_TOKEN_TTL_SECS_DEFAULT
    ttl = max(60, min(int(ttl), _SERVICE_TOKEN_TTL_SECS_MAX))

    # 签 access_token (RS256, 跟 user token 同公钥 — gateway 不区分验签)
    claims = client.to_token_claims(final_scope_str)
    # 注意: signer.sign_id_token 会自己加 iat / exp / iss / sub / aud, 这里 claims
    # 里的 sub 会被 signer 的 subject 参数覆盖 (传 client.to_token_claims 里的 sub).
    access_token = signer.sign_id_token(
        issuer=issuer,
        subject=claims["sub"],  # client:hermes-cli
        audience=_SERVICE_TOKEN_AUDIENCE,  # catfish-gateway
        claims={
            "client_id": claims["client_id"],
            "token_use": claims["token_use"],
            "scope": claims["scope"],
            "role": claims["role"],
            "department": claims["department"],
        },
        ttl_seconds=ttl,
    )
    logger.info(
        "token OK (client_credentials): client=%s scope=%s dept=%s ttl=%ds",
        client_id, final_scope_str, client.department, ttl,
    )
    return JSONResponse(
        {
            "access_token": access_token,
            # **不返 id_token** — service 调用没 user sub
            "token_type": "Bearer",
            "expires_in": ttl,
            "scope": final_scope_str,
        }
    )


