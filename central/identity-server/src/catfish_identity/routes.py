"""OIDC 5 个端点的 fastapi route 实现.

# 端点

  GET  /.well-known/openid-configuration   discovery
  GET  /.well-known/jwks.json              公钥
  GET  /authorize                           登录页 + 发 code
  POST /authorize                           login form 提交 (验证密码 + 发 code)
  POST /token                               code → ID token
  GET  /userinfo                            access_token → claims

# 简化决定 (Phase 1B-1)

  - authorization code 存内存 dict (TTL 5 min). Phase 2 换 sqlite/redis.
  - access_token = 可逆短 JWT (RS256 签, sub=email, scope), Phase 2 加 opaque token.
  - 不实现 PKCE (Phase 1B-2 加, Companion 端用)
  - 不实现 refresh_token (Phase 1C 加)
  - login form 是简单 HTML, 不带 CSRF (Phase 2 加 — Phase 1B-1 demo 不要求)

# 安全声明 (代码审计时要看清楚的)

  - state 不强制 (Phase 2 加 CSRF 防御)
  - redirect_uri 不严格白名单, 接受 client 传的 (Phase 2 加 client 注册 + redirect 白名单)
  - 这是 demo / 开发 server, 生产部署需要 Phase 2 加固
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .clients import ClientRegistry
from .jwt_signer import JwtSigner
from .refresh_tokens import RefreshTokenStore
from .users import IdentityUser, UserRegistry

logger = logging.getLogger("catfish.identity.routes")

#: authorization code 有效期 (秒)
_CODE_TTL_SECS = 300
#: access token / id token 有效期 (秒)
_TOKEN_TTL_SECS = 3600
#: service token (client_credentials grant) 有效期 (秒)
#: 跟 user token 同 1h. 服务客户端每 expire 重换 — 1h 短到不需要 refresh_token.
_SERVICE_TOKEN_TTL_SECS = 3600
#: service token 的 audience. gateway 验签时 aud=catfish-gateway 才接受.
_SERVICE_TOKEN_AUDIENCE = "catfish-gateway"


@dataclass
class _AuthCode:
    """一次性 authorization code 的内存记录."""

    code: str
    user: IdentityUser
    client_id: str
    redirect_uri: str
    scope: str
    nonce: str
    issued_at: float
    used: bool = False  # 一次性, 用过就废


@dataclass
class _CodeStore:
    """简单内存 code 存储. Phase 2 换 sqlite/redis."""

    codes: dict[str, _AuthCode] = field(default_factory=dict)

    def issue(self, **kwargs) -> _AuthCode:
        code = secrets.token_urlsafe(32)
        record = _AuthCode(code=code, issued_at=time.time(), **kwargs)
        self.codes[code] = record
        # 清理过期 (lazy)
        now = time.time()
        expired = [
            c for c, r in self.codes.items() if now - r.issued_at > _CODE_TTL_SECS
        ]
        for c in expired:
            self.codes.pop(c, None)
        return record

    def consume(self, code: str) -> _AuthCode | None:
        """一次性使用. used=True 之后再调返 None."""
        record = self.codes.get(code)
        if record is None:
            return None
        if record.used:
            return None
        if time.time() - record.issued_at > _CODE_TTL_SECS:
            self.codes.pop(code, None)
            return None
        record.used = True
        return record


def make_router(
    *,
    issuer: str,
    signer: JwtSigner,
    registry: UserRegistry,
    code_store: _CodeStore,
    client_registry: ClientRegistry | None = None,
    refresh_token_store: RefreshTokenStore | None = None,
) -> APIRouter:
    """构造 fastapi router. issuer 是 base URL (例 http://127.0.0.1:8998).

    设计: 把这几个依赖通过闭包传进去, 不用 fastapi global state, 测试可以单独
    构造小 router 验证.

    BL-RBAC P0 + B sprint Day 1 (5/14): client_registry 可选, 为 None 时
    /token client_credentials grant 直接返 503 unsupported (cleanly degrade).

    BL-IDENTITY-REFRESH-TOKEN (5/15): refresh_token_store 可选. 为 None 时
    refresh_token grant 返 503 + authorization_code response 也不返 refresh_token
    (老客户端兼容, 1h 后重 catfish login).
    """
    router = APIRouter()

    # ============================================================
    # /.well-known/openid-configuration  (discovery)
    # ============================================================

    @router.get("/.well-known/openid-configuration")
    async def discovery() -> dict:
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/authorize",
            "token_endpoint": f"{issuer}/token",
            "userinfo_endpoint": f"{issuer}/userinfo",
            "jwks_uri": f"{issuer}/.well-known/jwks.json",
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "token_endpoint_auth_methods_supported": ["client_secret_post"],
            "scopes_supported": ["openid", "email", "profile"],
            "claims_supported": [
                "sub",
                "email",
                "email_verified",
                "name",
                "department",
                "tier",
                "iss",
                "aud",
                "iat",
                "exp",
            ],
        }

    # ============================================================
    # /.well-known/jwks.json  (公钥)
    # ============================================================

    @router.get("/.well-known/jwks.json")
    async def jwks() -> dict:
        return signer.jwks()

    # ============================================================
    # /authorize  (GET 登录页 / POST 验证)
    # ============================================================

    @router.get("/authorize", response_class=HTMLResponse)
    async def authorize_page(
        client_id: str,
        redirect_uri: str,
        response_type: str = "code",
        scope: str = "openid",
        state: str = "",
        nonce: str = "",
    ) -> HTMLResponse:
        """OIDC authorize 起步: GET 给登录页. POST 同 URL 提交表单.

        参数验证:
          - response_type 必须 = 'code' (Phase 1 只支持 code flow)
          - 其他参数透传到 form, 提交时一并验
        """
        if response_type != "code":
            raise HTTPException(
                status_code=400,
                detail=f"unsupported response_type: {response_type} (only 'code' supported)",
            )
        # 极简 HTML 登录页. 生产用 jinja template + CSRF (Phase 2)
        html = _render_login_page(
            client_id=client_id,
            redirect_uri=redirect_uri,
            scope=scope,
            state=state,
            nonce=nonce,
        )
        # 五一 sprint 5/3 BL-D11: 强制不缓存. 之前漏设 Cache-Control 头, 浏览器对登录页
        # 启发式缓存, 改 brand 后用户在 prod build 仍看老 HTML. no-store + must-revalidate
        # 双保险, 兼容老 IE / 国产浏览器.
        return HTMLResponse(
            content=html,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    @router.post("/authorize")
    async def authorize_submit(
        client_id: str = Form(...),
        redirect_uri: str = Form(...),
        scope: str = Form("openid"),
        state: str = Form(""),
        nonce: str = Form(""),
        email: str = Form(...),
        password: str = Form(...),
    ) -> RedirectResponse:
        """登录表单提交. 成功 → redirect_uri?code=...&state=..."""
        user = registry.verify_password(email, password)
        if user is None:
            # Phase 2: 失败计数 / 锁账户. 现在简单返 401 + login page 显错.
            html = _render_login_page(
                client_id=client_id,
                redirect_uri=redirect_uri,
                scope=scope,
                state=state,
                nonce=nonce,
                error="email 或密码错误",
            )
            # 同样不缓存 (失败重试也得拿最新 HTML)
            return HTMLResponse(
                content=html,
                status_code=401,
                headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )

        record = code_store.issue(
            user=user,
            client_id=client_id,
            redirect_uri=redirect_uri,
            scope=scope,
            nonce=nonce,
        )
        params = {"code": record.code}
        if state:
            params["state"] = state
        location = f"{redirect_uri}?{urlencode(params)}"
        logger.info(
            "authorize OK: user=%s client=%s code_prefix=%s",
            user.email, client_id, record.code[:8],
        )
        return RedirectResponse(url=location, status_code=status.HTTP_302_FOUND)

    # ============================================================
    # /token  (code → ID token)
    # ============================================================

    @router.post("/token")
    async def token(
        grant_type: str = Form(...),
        # authorization_code grant 字段 (用户走 SSO 用)
        code: str = Form(""),
        redirect_uri: str = Form(""),
        # 通用 client 字段
        client_id: str = Form(...),
        client_secret: str = Form(""),
        # client_credentials grant 字段 (服务调用用)
        scope: str = Form(""),
        # refresh_token grant 字段 (BL-IDENTITY-REFRESH 5/15)
        refresh_token: str = Form(""),
    ) -> JSONResponse:
        """OAuth 2.0 /token endpoint. 双 grant_type:

        # grant_type=authorization_code  (RFC 6749 §4.1)
            用户走 SSO 后浏览器拿 code, 换 id_token + access_token.
            参数: code, redirect_uri, client_id, [client_secret]
            返: id_token + access_token + token_type + expires_in + scope

        # grant_type=client_credentials  (RFC 6749 §4.4) — BL-RBAC Day 1 (5/14)
            服务进程 (hermes-cli 等) 拿 client_id + client_secret 换 service token.
            参数: client_id, client_secret, [scope]
            返: access_token (含 token_use=service) + token_type + expires_in + scope
            **不返 id_token** (服务调用没用户 sub).

        Phase 1B-1: authorization_code 的 client_secret 不验 (demo).
        Day 1 (5/14): client_credentials 的 client_secret **必须验** (服务身份硬要求).
        """
        if grant_type == "authorization_code":
            return await _handle_authorization_code(
                code=code,
                redirect_uri=redirect_uri,
                client_id=client_id,
                client_secret=client_secret,  # 不验, 占位接收
                code_store=code_store,
                signer=signer,
                issuer=issuer,
                refresh_token_store=refresh_token_store,
            )
        elif grant_type == "client_credentials":
            return _handle_client_credentials(
                client_id=client_id,
                client_secret=client_secret,
                scope=scope,
                client_registry=client_registry,
                signer=signer,
                issuer=issuer,
            )
        elif grant_type == "refresh_token":
            return await _handle_refresh_token(
                refresh_token_str=refresh_token,
                client_id=client_id,
                requested_scope=scope,
                refresh_token_store=refresh_token_store,
                registry=registry,
                signer=signer,
                issuer=issuer,
            )
        else:
            # OAuth 2.0 标准错误格式 (RFC 6749 §5.2)
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "unsupported_grant_type",
                    "error_description": (
                        f"grant_type={grant_type!r} 不支持. "
                        f"支持: authorization_code, client_credentials, refresh_token"
                    ),
                },
            )

    # ============================================================
    # /userinfo  (access_token → user claims)
    # ============================================================

    @router.get("/userinfo")
    async def userinfo(request: Request) -> dict:
        """OIDC /userinfo 端点. 客户端拿 access_token 来换 user claims.

        Authorization: Bearer <access_token>
        """
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            raise HTTPException(
                status_code=401, detail="missing or malformed Authorization header"
            )
        token_str = auth[7:].strip()
        try:
            payload = jwt.decode(
                token_str,
                signer._public_key,  # noqa: SLF001 — 测试用; 生产用 jwks
                algorithms=["RS256"],
                audience=None,
                options={"verify_aud": False},
            )
        except jwt.InvalidTokenError as e:
            raise HTTPException(status_code=401, detail=f"invalid token: {e}") from e

        sub = payload.get("sub", "")
        user = registry.find(sub)
        if user is None:
            # Edge case: token 签的时候 user 在, 现在 yaml 删了
            raise HTTPException(status_code=404, detail="user not found")

        return {"sub": sub, **user.to_oidc_claims()}

    return router


# ============================================================
# /token grant_type handlers (5/14 Day 1 拆出 — 双 grant 各自独立)
# ============================================================


async def _handle_authorization_code(
    *,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,  # noqa: ARG001 — Phase 1B-1 不验
    code_store: _CodeStore,
    signer: JwtSigner,
    issuer: str,
    refresh_token_store: RefreshTokenStore | None = None,
) -> JSONResponse:
    """authorization_code grant — 用户走 SSO 后浏览器换 id_token + access_token.

    Phase 1B-1 不验 client_secret. Phase 2 加 client registration + secret 验证.
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

    # 组装 ID Token (含 user claims, audience=client_id 表示这 token 给 client 看)
    # BL-RBAC-DAY3B (5/17): 用 async 版本拿 effective_allowed_models (合并 user+dept)
    id_claims = await record.user.to_oidc_claims_async()
    if record.nonce:
        id_claims["nonce"] = record.nonce
    id_token = signer.sign_id_token(
        issuer=issuer,
        subject=record.user.email,
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
        subject=record.user.email,
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
            sub=record.user.email,
            client_id=client_id,
            scope=record.scope,
        )
        response_body["refresh_token"] = rt.token
        response_body["refresh_expires_in"] = int(rt.expires_at - time.time())
    logger.info(
        "token OK (auth_code): user=%s client=%s refresh=%s",
        record.user.email, client_id,
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
        ttl_seconds=_SERVICE_TOKEN_TTL_SECS,
    )
    logger.info(
        "token OK (client_credentials): client=%s scope=%s dept=%s",
        client_id, final_scope_str, client.department,
    )
    return JSONResponse(
        {
            "access_token": access_token,
            # **不返 id_token** — service 调用没 user sub
            "token_type": "Bearer",
            "expires_in": _SERVICE_TOKEN_TTL_SECS,
            "scope": final_scope_str,
        }
    )


# ============================================================
# 简单登录页 HTML (内联避免 jinja 依赖)
# ============================================================


def _render_login_page(
    *,
    client_id: str,
    redirect_uri: str,
    scope: str,
    state: str,
    nonce: str,
    error: str = "",
) -> str:
    """极简登录页. Phase 2 改 jinja + CSS + CSRF."""
    error_html = (
        f'<div class="error">{_html_escape(error)}</div>' if error else ""
    )
    # 五一 sprint 5/3 BL-D11: 升级 SSO 登录页 brand
    #   - 占位 🐟 emoji → 内联 mark SVG (无需挂静态文件)
    #   - 配色全换墨青 #0E5F66 / 暖橙 #F47B3D / 暖米 #FAF1E4
    #   - 跟 BRAND.md 一致
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>鲶鱼 · 登录</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <!-- 五一 sprint 5/3 BL-D11: 内联 favicon (data: URI), 防 macOS 给本地端口配默认鱼 emoji.
       SVG 是 logo-mark 简化版, 跟登录卡片里的 mark 同源. -->
  <link rel="icon" type="image/svg+xml" href="data:image/svg+xml;utf8,
    <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 256 256'>
      <circle cx='128' cy='128' r='120' fill='%230E5F66'/>
      <path d='M 175 90 Q 125 70 90 100 Q 60 130 80 160' stroke='%23FAF1E4' stroke-width='14' fill='none' stroke-linecap='round'/>
      <circle cx='80' cy='160' r='14' fill='%23F47B3D'/>
      <circle cx='175' cy='128' r='16' fill='%23FAF1E4'/>
      <circle cx='178' cy='131' r='8' fill='%231A2E33'/>
    </svg>">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif;
           background: linear-gradient(180deg, #0E5F66 0%, #0A464C 100%); color: #FAF1E4; margin: 0;
           display: flex; min-height: 100vh; align-items: center; justify-content: center; }}
    .card {{ background: #1C2A2E; border: 1px solid #2E3F44; border-radius: 12px;
            padding: 36px 32px; width: 360px; box-shadow: 0 8px 32px rgba(0,0,0,0.25); }}
    .brand {{ display: flex; align-items: center; gap: 12px; margin: 0 0 6px 0; }}
    .brand svg {{ flex-shrink: 0; }}
    h1 {{ font-size: 22px; margin: 0; color: #FAF1E4; font-weight: 500; }}
    .sub {{ color: #8A9692; font-size: 13px; margin-bottom: 24px; }}
    label {{ display: block; font-size: 12px; color: #A8B0AD; margin: 14px 0 4px; }}
    input {{ width: 100%; padding: 11px; border: 1px solid #2E3F44; border-radius: 6px;
             background: #131C1F; color: #FAF1E4; box-sizing: border-box; font-size: 14px; }}
    input:focus {{ outline: none; border-color: #1A8A95; box-shadow: 0 0 0 3px rgba(26,138,149,0.2); }}
    button {{ width: 100%; margin-top: 22px; padding: 12px; background: #F47B3D;
              color: #FFFFFF; border: 0; border-radius: 6px; font-size: 14px;
              font-weight: 500; cursor: pointer; transition: background 0.15s; }}
    button:hover {{ background: #F89866; }}
    .error {{ color: #E59995; font-size: 12px; margin: 8px 0; }}
    .footer {{ color: #6B7775; font-size: 11px; margin-top: 18px; text-align: center; }}
  </style>
</head>
<body>
  <form class="card" method="POST" action="/authorize">
    <div class="brand">
      <!-- 内联 logo-mark SVG (跟 branding/logo-mark.svg 同源, 256→32 缩放) -->
      <svg width="32" height="32" viewBox="0 0 256 256" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <defs>
          <linearGradient id="lg" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stop-color="#1A8A95"/><stop offset="100%" stop-color="#0A464C"/>
          </linearGradient>
        </defs>
        <circle cx="128" cy="128" r="120" fill="url(#lg)"/>
        <path d="M 175 90 Q 125 70 90 100 Q 60 130 80 160" stroke="#FAF1E4" stroke-width="12" fill="none" stroke-linecap="round"/>
        <path d="M 175 165 Q 125 185 90 155 Q 60 125 80 95" stroke="#FAF1E4" stroke-width="12" fill="none" stroke-linecap="round" opacity="0.55"/>
        <circle cx="80" cy="160" r="11" fill="#F47B3D"/>
        <circle cx="175" cy="128" r="14" fill="#FAF1E4"/>
        <circle cx="178" cy="131" r="7" fill="#1A2E33"/>
      </svg>
      <h1>鲶鱼登录</h1>
    </div>
    <div class="sub">公司账号登录鲶鱼工作台</div>
    {error_html}
    <input type="hidden" name="client_id" value="{_html_escape(client_id)}">
    <input type="hidden" name="redirect_uri" value="{_html_escape(redirect_uri)}">
    <input type="hidden" name="scope" value="{_html_escape(scope)}">
    <input type="hidden" name="state" value="{_html_escape(state)}">
    <input type="hidden" name="nonce" value="{_html_escape(nonce)}">

    <label>邮箱</label>
    <input type="email" name="email" required autocomplete="username" autofocus>

    <label>密码</label>
    <input type="password" name="password" required autocomplete="current-password">

    <button type="submit">登录</button>

    <div class="footer">登录后会跳回 catfish 客户端</div>
  </form>
</body>
</html>"""


def _html_escape(s: str) -> str:
    """最小 HTML escape, 不依赖 jinja."""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )
