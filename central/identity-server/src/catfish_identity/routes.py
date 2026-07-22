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
import time
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .clients import ClientRegistry
from .code_store import AuthCodeRecord, CodeStore
from .jwt_signer import JwtSigner
from .refresh_tokens import RefreshTokenStore
from .users import UserRegistry

# 6/9 BL-F11.P2 (鸿波): _CodeStore 从 in-memory dict 移到 sqlite 持久化, 让 multi-worker
# 部署能跨 worker 共享 authorization_code state. 老 in-memory 版本旧测试 5 处用
# `_CodeStore()` 实例化, 这里给老符号一个 alias 保持源码兼容 (测试加 db_path=tmp 改一下).
_CodeStore = CodeStore
_AuthCode = AuthCodeRecord

logger = logging.getLogger("catfish.identity.routes")

#: authorization code 有效期 (秒)
_CODE_TTL_SECS = 300
#: access token / id token 有效期 (秒)
_TOKEN_TTL_SECS = 3600
#: service token (client_credentials grant) 默认有效期 (秒).
#: 5/18 BL-HERMES-AUTH-LONGLIVED 改: per-client 可在 clients.yaml 配
#: `service_token_ttl_seconds` 覆盖. hermes-cli 这种 long-lived 服务建议 30 天
#: (2592000), 单机部署没 secret rotation 压力. 客户端没指定走默认 1h.
_SERVICE_TOKEN_TTL_SECS_DEFAULT = 3600
#: service token 上限: 365 天. 防 clients.yaml 写 99999 年这种长期凭据. 客户
#: 真要更长应该走 catfish-identity admin 走 audit 流, 不是 yaml 一行改完.
_SERVICE_TOKEN_TTL_SECS_MAX = 365 * 24 * 3600
#: service token 的 audience. gateway 验签时 aud=catfish-gateway 才接受.
_SERVICE_TOKEN_AUDIENCE = "catfish-gateway"


# 6/9 BL-F11.P2: _AuthCode + _CodeStore 移到 code_store.py (sqlite 持久化, 跨
# worker 共享 authorization_code state). 上面 import 给了 _CodeStore / _AuthCode
# 兼容别名, 测试源码不破.


def make_router(
    *,
    issuer: str,
    signer: JwtSigner,
    registry: UserRegistry,
    code_store: CodeStore,
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

        # 6/9 BL-F11.P2: 改存 user_email (sqlite 不存对象). consume 后 routes_token
        # 用 registry.find(user_email) 回查 user.
        record = code_store.issue(
            user_email=user.email,
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
                registry=registry,  # 6/9 BL-F11.P2: handler 用 user_email 回查 user
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

    # ============================================================
    # BL-SELF-CHANGE-PASSWORD (7/20 鸿波 catch): 员工自主改密码
    # ============================================================
    # POST /me/password
    # Authorization: Bearer <access_token>
    # body: {"old_password": "...", "new_password": "..."}
    #
    # 员工首次登录用 admin 给的临时密码 · 需自主改.
    # must_change_password=true 触发前端弹强制改密 modal.
    # ============================================================

    @router.post("/me/password")
    async def change_own_password(request: Request) -> dict:
        """员工自主改密码 · 需验旧密码 + 强度 8 位."""
        # 1. verify access_token · 拿 sub (员工 email)
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            raise HTTPException(
                status_code=401, detail="missing or malformed Authorization header"
            )
        token_str = auth[7:].strip()
        try:
            payload = jwt.decode(
                token_str,
                signer._public_key,  # noqa: SLF001
                algorithms=["RS256"],
                audience=None,
                options={"verify_aud": False},
            )
        except jwt.InvalidTokenError as e:
            raise HTTPException(status_code=401, detail=f"invalid token: {e}") from e

        sub = payload.get("sub", "")
        if not sub or ":" in sub:
            # service token (sub=client:hermes-cli) 不能改密 · 拒
            raise HTTPException(
                status_code=403, detail="仅员工可改密码 · service token 不支持"
            )

        # 2. parse body
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="JSON body required")

        old_password = str(body.get("old_password", "")).strip()
        new_password = str(body.get("new_password", "")).strip()

        if not old_password or not new_password:
            raise HTTPException(
                status_code=400,
                detail="old_password 和 new_password 都必填"
            )

        # 3. 调 UserRegistry.change_password
        # 严守军规 7/20: registry 就是 UserRegistry 实例 · 不是 wrapper ·
        # 无 registry._users_store 层级 (verify_password / reset_password /
        # find / list_users 都直接挂 registry 上, users.py:68 class UserRegistry).
        # 之前 getattr _users_store 是瞎猜的抽象.
        ok, msg = await registry.change_password(
            email=sub,
            old_password=old_password,
            new_password=new_password,
        )
        if not ok:
            # 400 for validation errors (weak password, wrong old) · 500 for db
            code = 400 if "至少" in msg or "错" in msg or "相同" in msg else 500
            raise HTTPException(status_code=code, detail=msg)

        # 4. 军规 7/20 鸿波 catch "改完密码不重新登录" — revoke user 所有活
        # refresh_token. 用法与 admin lock_user 完全对齐 (refresh_tokens.py:293
        # revoke_all_for_sub). 语义:
        #   - refresh_token 已 revoke · 员工端 access_token 过期后 refresh 拿新
        #     token 就 401 · Companion 走 auth_login 弹 SSO 输新密码
        #   - 未过期的 access_token (JWT stateless · TTL 1h) 剩余时间内还有效 ·
        #     但下次 refresh 时挂 · 攻击者持泄露密码 + 已抓到 refresh_token 场
        #     景下能续 30 天 (refresh_token TTL) 的问题被堵住
        # 前端应在改密成功响应后立即调 auth_logout · 主动清 Keychain · 不用等
        # access_token 自然过期
        revoked_count = 0
        if refresh_token_store is not None:
            revoked_count = refresh_token_store.revoke_all_for_sub(sub)

        logger.info(
            "self_change_password OK: user=%s (must_change_password 清 · "
            "revoked %d refresh_tokens)",
            sub, revoked_count,
        )
        return {
            "success": True,
            "message": "密码已修改 · 请用新密码重新登录",
            "revoked_sessions": revoked_count,
        }

    return router



# ============================================================
# /token grant_type handlers (5/20 拆: 400 行抽到 routes_token.py)
# ============================================================

from .routes_token import (  # noqa: F401
    _handle_authorization_code,
    _handle_client_credentials,
    _handle_refresh_token,
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
