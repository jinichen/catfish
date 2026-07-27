"""OIDCProvider — 验 IdP 签的 RS256 JWT (含 catfish-identity / 客户自建 SSO).

# 流程

  Companion / 浏览器
    ↓ 走 OIDC authorization code flow 拿 id_token
  gateway 收到 Bearer <id_token>
    ↓ OIDCProvider.verify_bearer
    1. 解 token header 拿 kid
    2. 从 jwks_uri 拉公钥 (PyJWKClient 自带缓存)
    3. RS256 验签
    4. 验 iss + aud + exp (PyJWT 自动)
    5. 提 claims → User

# 跟 catfish-identity 配合

catfish-identity (port 8998) 签 id_token, gateway (port 8999) 验. issuer 必须一致:
  gateway .env: CATFISH_OIDC_ISSUER=http://127.0.0.1:8998
  catfish-identity .env: CATFISH_IDENTITY_ISSUER=http://127.0.0.1:8998

# 客户切自己 SSO 时

改 gateway .env:
  CATFISH_OIDC_ISSUER=https://sso.client.com
  CATFISH_OIDC_AUDIENCE=catfish-prod
  CATFISH_OIDC_JWKS_URI=https://sso.client.com/.well-known/jwks.json (可选)
重启 gateway, catfish-identity 不启动. 30 秒切换.

# 决策对齐 (docs/AUTH-DESIGN.md § 13)

  - 决策 2: gateway 直接验 IdP JWT (本 provider 即此, 不经 catfish-sso 中间层)
  - 决策 3: User.sub = email
  - 决策 5: JWT stateless
"""

from __future__ import annotations

import logging
from typing import Any

import jwt

from .base import AuthProvider, User

logger = logging.getLogger("catfish.gateway.auth.oidc")


class OIDCProvider(AuthProvider):
    """验 OIDC IdP 签的 RS256 JWT.

    使用:
        provider = OIDCProvider(
            issuer="http://127.0.0.1:8998",
            audience="catfish-companion",
        )
        user = provider.verify_bearer("Bearer eyJh...")
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str | list[str],
        jwks_uri: str | None = None,
        cache_ttl: int = 600,
        algorithms: list[str] | None = None,
        # 测试 hook: 直接传 jwks dict, 跳过 HTTP 拉 (生产代码别用)
        jwks_for_testing: dict | None = None,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        # BL-COMPANION-AUTH (5/15 凌晨): audience 接 list, PyJWT.decode 原生支持
        # 多 audience (token 的 aud 在 list 任一就过). 修我今晚把 access_token aud
        # 从 catfish-companion (老) 改 catfish-gateway (新, RFC 9068) 后, 老 Companion
        # 已存的 token (aud=catfish-companion) 被拒的副作用. 现在两边都接.
        if isinstance(audience, str):
            # 允许逗号分隔 env 配置, e.g. CATFISH_OIDC_AUDIENCE="catfish-gateway,catfish-companion"
            self.audience: list[str] = [a.strip() for a in audience.split(",") if a.strip()]
        else:
            self.audience = list(audience)
        self.jwks_uri = jwks_uri or f"{self.issuer}/.well-known/jwks.json"
        self.algorithms = algorithms or ["RS256"]
        self._jwks_for_testing = jwks_for_testing
        self._jwks_client: jwt.PyJWKClient | None = None

        if jwks_for_testing is None:
            # 生产路径: PyJWKClient 自带 LRU 缓存.
            # PyJWT >= 2.10 支持 lifespan 参数; 旧版没有 — 兜底不传.
            try:
                self._jwks_client = jwt.PyJWKClient(
                    self.jwks_uri,
                    lifespan=cache_ttl,
                )
            except TypeError:
                self._jwks_client = jwt.PyJWKClient(self.jwks_uri)
        logger.info(
            "OIDCProvider 初始化: issuer=%s aud=%s jwks_uri=%s",
            self.issuer, self.audience, self.jwks_uri,
        )

    @property
    def name(self) -> str:
        return f"oidc:{self.issuer}"

    @property
    def is_strict(self) -> bool:
        # 真 SSO, 配错应该 fail (不像 dev_token lax)
        return True

    def _get_signing_key(self, token: str) -> Any:
        """根据 token header 的 kid 找对应公钥. 不抛 (返 None 让 caller 拒绝)."""
        try:
            headers = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as e:
            logger.debug("token header 解析失败: %s", e)
            return None
        kid = headers.get("kid")

        if self._jwks_for_testing is not None:
            # 测试路径: 从注入 dict 找
            for jwk_dict in self._jwks_for_testing.get("keys", []):
                if jwk_dict.get("kid") == kid:
                    return jwt.PyJWK(jwk_dict).key
            logger.debug("test jwks 里没 kid=%s", kid)
            return None

        # 生产路径
        try:
            return self._jwks_client.get_signing_key_from_jwt(token).key
        except (jwt.PyJWKClientError, jwt.InvalidTokenError) as e:
            logger.debug("拉 jwks 或找 kid 失败 (issuer=%s): %s", self.issuer, e)
            return None

    def verify_bearer(self, authorization: str | None) -> User | None:
        if not authorization:
            return None
        if not authorization.lower().startswith("bearer "):
            return None
        token = authorization[7:].strip()
        if not token:
            return None

        key = self._get_signing_key(token)
        if key is None:
            return None

        try:
            payload = jwt.decode(
                token,
                key,
                algorithms=self.algorithms,
                audience=self.audience,
                issuer=self.issuer,
                # PyJWT 自动验 exp / iat / nbf
            )
        except jwt.InvalidTokenError as e:
            logger.debug("OIDC 验签 / claims 校验失败: %s", e)
            return None

        sub = payload.get("sub", "").strip()
        if not sub:
            logger.warning("OIDC token 通过验签但缺 sub claim, 拒绝")
            return None

        # BL-RBAC Day 2 + BL-IDENTITY-REFRESH (5/15): 三种 token_use 分流.
        # catfish-identity 5/14 起 access_token 也含完整 user claims (RFC 9068).
        #
        #   token_use 缺省 / "id"   → id_token (老 OIDC 客户端)
        #   token_use=access        → access_token (用户身份, sub=email) ← catfish login 用
        #   token_use=service       → service token (服务身份, sub=client:<id>) ← hermes-cli/cron 用
        #   token_use=其他          → 拒 (防奇怪 use claim)
        token_use = payload.get("token_use")
        if token_use not in (None, "id", "access", "service"):
            logger.debug("拒绝未知 token_use=%r (sub=%s)", token_use, sub)
            return None

        # 五一 sprint 5/2 RBAC: 从 OIDC claims 读 role + managed_departments.
        # catfish-identity IdentityUser.to_oidc_claims 已透传 (5/2 改).
        # service token 5/14 起也透传 (clients.py to_token_claims 含 role=service).
        managed_raw = payload.get("managed_departments", [])
        if isinstance(managed_raw, str):
            managed_list = [d.strip() for d in managed_raw.split(",") if d.strip()]
        elif isinstance(managed_raw, list):
            managed_list = [str(d) for d in managed_raw if d]
        else:
            managed_list = []
        # BL-RBAC-DAY3B (5/17): effective_allowed_models 从 OIDC claim 拿
        # (identity to_oidc_claims_async 合并了 user.allowed_models + dept.allowed_models)
        eam_raw = payload.get("effective_allowed_models", [])
        if isinstance(eam_raw, list):
            eam_list = [str(m) for m in eam_raw if m]
        else:
            eam_list = []
        # BL-RBAC-DAY4 (5/17): effective_allowed_tools 同套路
        eat_raw = payload.get("effective_allowed_tools", [])
        if isinstance(eat_raw, list):
            eat_list = [str(t) for t in eat_raw if t]
        else:
            eat_list = []
        # BL-RBAC-DAY5 (5/17): effective_allowed_skills (glob pattern list)
        eas_raw = payload.get("effective_allowed_skills", [])
        if isinstance(eas_raw, list):
            eas_list = [str(s) for s in eas_raw if s]
        else:
            eas_list = []
        # BL-PLUGIN-AUTH-FIX (7/27 鸿波): OAuth scope claim → User.scopes.
        # identity `routes_token.py:143/267/357` 都写 access_token_claims["scope"]
        # (空格分隔字符串, RFC 6749 §3.3). app.py is_internal_call 用它判 background.tasks.
        # 注 · id_token 没这个 claim (你的 config.yaml model.api_key 就是 id_token,
        # aud=catfish-companion + 无 token_use + 无 scope) → 拿到空 list, 行为跟老代码一致.
        scope_raw = payload.get("scope", "")
        scope_list = scope_raw.split() if isinstance(scope_raw, str) else []
        return User(
            sub=sub,
            department=payload.get("department", ""),
            tier=payload.get("tier", "employee"),
            # role: service token 是 "service", 用户 token 是 admin/manager/employee
            role=payload.get("role", "employee"),
            managed_departments=managed_list,
            auth_method=self.name,
            effective_allowed_models=eam_list,
            effective_allowed_tools=eat_list,
            effective_allowed_skills=eas_list,
            scopes=scope_list,
        )
