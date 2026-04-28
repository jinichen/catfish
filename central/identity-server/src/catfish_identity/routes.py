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

from .jwt_signer import JwtSigner
from .users import IdentityUser, UserRegistry

logger = logging.getLogger("catfish.identity.routes")

#: authorization code 有效期 (秒)
_CODE_TTL_SECS = 300
#: access token / id token 有效期 (秒)
_TOKEN_TTL_SECS = 3600


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
) -> APIRouter:
    """构造 fastapi router. issuer 是 base URL (例 http://127.0.0.1:8998).

    设计: 把这几个依赖通过闭包传进去, 不用 fastapi global state, 测试可以单独
    构造小 router 验证.
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
        return HTMLResponse(content=html)

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
            return HTMLResponse(content=html, status_code=401)

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
        code: str = Form(...),
        redirect_uri: str = Form(...),
        client_id: str = Form(...),
        client_secret: str = Form(""),  # noqa: ARG001 — Phase 1B-1 不验 secret, Phase 2 加
    ) -> JSONResponse:
        """换 code 拿 id_token + access_token.

        Phase 1B-1: client_secret 不验 (因为 demo / 我们没实现 client 注册).
                   Phase 2 加 client registration + secret 验证.

        Returns:
            OAuth 2.0 token response: access_token, id_token, token_type, expires_in
        """
        if grant_type != "authorization_code":
            raise HTTPException(
                status_code=400, detail=f"unsupported grant_type: {grant_type}"
            )

        record = code_store.consume(code)
        if record is None:
            raise HTTPException(status_code=400, detail="invalid_grant: code 无效或已用过")

        if record.client_id != client_id:
            raise HTTPException(status_code=400, detail="invalid_grant: client_id 不匹配")
        if record.redirect_uri != redirect_uri:
            raise HTTPException(
                status_code=400, detail="invalid_grant: redirect_uri 不匹配"
            )

        # 组装 ID Token (含 user claims)
        id_claims = record.user.to_oidc_claims()
        if record.nonce:
            id_claims["nonce"] = record.nonce
        id_token = signer.sign_id_token(
            issuer=issuer,
            subject=record.user.email,
            audience=client_id,
            claims=id_claims,
            ttl_seconds=_TOKEN_TTL_SECS,
        )
        # access_token 也用 JWT (简化, Phase 2 改 opaque)
        access_token = signer.sign_id_token(
            issuer=issuer,
            subject=record.user.email,
            audience=client_id,
            claims={"scope": record.scope, "token_use": "access"},
            ttl_seconds=_TOKEN_TTL_SECS,
        )
        logger.info("token OK: user=%s client=%s", record.user.email, client_id)
        return JSONResponse(
            {
                "access_token": access_token,
                "id_token": id_token,
                "token_type": "Bearer",
                "expires_in": _TOKEN_TTL_SECS,
                "scope": record.scope,
            }
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
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>鲶鱼 · 登录</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Helvetica Neue', sans-serif;
           background: #0f1419; color: #e6e6e6; margin: 0;
           display: flex; min-height: 100vh; align-items: center; justify-content: center; }}
    .card {{ background: #1a1f26; border: 1px solid #2a3038; border-radius: 8px;
            padding: 32px; width: 360px; }}
    h1 {{ font-size: 20px; margin: 0 0 6px 0; color: #4eb3d3; }}
    .sub {{ color: #888; font-size: 13px; margin-bottom: 24px; }}
    label {{ display: block; font-size: 12px; color: #aaa; margin: 12px 0 4px; }}
    input {{ width: 100%; padding: 10px; border: 1px solid #2a3038; border-radius: 4px;
             background: #0f1419; color: #e6e6e6; box-sizing: border-box; font-size: 14px; }}
    button {{ width: 100%; margin-top: 20px; padding: 11px; background: #4eb3d3;
              color: #0f1419; border: 0; border-radius: 4px; font-size: 14px;
              font-weight: 600; cursor: pointer; }}
    button:hover {{ background: #6cc7e0; }}
    .error {{ color: #f87171; font-size: 12px; margin: 8px 0; }}
    .footer {{ color: #666; font-size: 11px; margin-top: 18px; text-align: center; }}
  </style>
</head>
<body>
  <form class="card" method="POST" action="/authorize">
    <h1>🐟 鲶鱼登录</h1>
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
