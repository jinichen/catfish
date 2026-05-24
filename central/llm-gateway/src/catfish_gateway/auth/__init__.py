"""Auth subsystem 入口.

跟 Phase 1A 之前的单文件 auth.py **100% 行为兼容**. 现在拆成 ABC + 多实现,
后面加 OIDCProvider 不需要改 caller.

# 公开 API

  - User                       (dataclass)
  - AuthProvider               (ABC)
  - DevTokenProvider           (dev / 兜底实现)
  - make_auth_provider()       (工厂, 根据 env 返不同 provider)
  - get_current_user           (FastAPI dependency, 强鉴权)
  - get_current_user_optional  (FastAPI dependency, 可选鉴权)

# 兼容旧 auth.py 的导入

之前: `from catfish_gateway.auth import User, get_current_user`
现在: 同一行不需要改. auth/ 包替换 auth.py 后 `from .auth import` 仍然 work.

# Phase 演进

  Phase 1A (本提交): DevTokenProvider 单实现, 行为兼容
  Phase 1B: OIDCProvider + CompositeProvider (prod env 优先 OIDC, dev_token 兜底)
  Phase 1C: Companion 端 OAuth flow + Keychain token 存

# 测试 hook

  _set_provider_for_testing(provider) 显式注入 mock provider, 不要在生产代码用.
"""

from __future__ import annotations

import logging
import os
import re

from fastapi import Header, HTTPException

from .base import AuthProvider, User
from .composite import CompositeProvider
from .dev_token import DevTokenProvider
from .oidc import OIDCProvider

__all__ = [
    "User",
    "AuthProvider",
    "DevTokenProvider",
    "OIDCProvider",
    "CompositeProvider",
    "make_auth_provider",
    "get_current_user",
    "get_current_user_optional",
    # BL-AUTH-DECOUPLE-A1 (5/19)
    "SERVICE_CLIENTS_ALLOWING_USER_OVERRIDE",
    "SERVICE_SUB_PREFIX",
    "X_CATFISH_USER_HEADER",
    "is_service_principal",
    "service_client_id",
    "resolve_effective_user_email",
]


# ────────────────────────────────────────────────────────────────────────
# BL-AUTH-DECOUPLE-A1 (5/19): service token + X-Catfish-User header.
#
# 背景: hermes daemon 调 gateway 用员工的 user JWT (1h) 跨小时被 OAuth refresh
# 覆盖, 中间 401 race. 治本: hermes 改用 service token (sub=client:hermes-cli,
# 30 天), 然后用 X-Catfish-User header 标识"代表哪个 user 调用". gateway 验
# service token, 同时取 header 的 email 作为 quota / RBAC / audit 的 effective
# user. 只白名单的 client_id 允许这个 override, 防别的 service 越权.
#
# 详见 A1 工单 + docs/AUTH-DESIGN.md (后续补 § 14).
# ────────────────────────────────────────────────────────────────────────

#: service token sub 的前缀 (catfish-identity clients.py to_token_claims 约定).
SERVICE_SUB_PREFIX = "client:"

#: 允许通过 X-Catfish-User header 覆盖 effective_user 的 service client_id 白名单.
#: 写死在代码里 (不走 yaml) — 防 ops 误配某个 service 拿到 user-impersonation 能力.
#: 加新 client 要改这里 + 走 code review.
SERVICE_CLIENTS_ALLOWING_USER_OVERRIDE: frozenset[str] = frozenset({"hermes-cli"})

#: HTTP header name. 跟 hermes / Companion 约定 (A2 / A3 实现侧用同一个名).
X_CATFISH_USER_HEADER = "X-Catfish-User"

#: X-Catfish-User 值的形状校验 — 必须长得像 email.
#: 真正的"user 是否存在"由后端业务 (quota / facts / etc) 自己处理 — gateway
#: 只防 garbage / injection / 不像 email 的字符串.
#: 容忍长度 254 (RFC 5321 local 64 + @ + domain 255 cap).
_EMAIL_SHAPE_RE = re.compile(
    r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,}$"
)


def is_service_principal(user: User | None) -> bool:
    """user 是不是 service token 派生的 (sub 以 'client:' 开头).

    判 sub 形状不判 role — role=service 是 catfish-identity 约定的, 但万一
    yaml 错配 / 别的 IdP 不带这字段, sub 前缀更不可绕过 (oidc.py 验签后 sub
    直接来自 JWT, 不经过 yaml).
    """
    if user is None:
        return False
    return isinstance(user.sub, str) and user.sub.startswith(SERVICE_SUB_PREFIX)


def service_client_id(user: User | None) -> str | None:
    """从 service token user 提 client_id. 非 service token 返 None."""
    if not is_service_principal(user):
        return None
    return user.sub[len(SERVICE_SUB_PREFIX):] or None


def resolve_effective_user_email(
    user: User,
    x_catfish_user_header: str | None,
) -> str:
    """决定本次请求"代表谁"的 email — quota / RBAC / audit 用这个.

    规则:
      1. 普通 user token (sub=email): effective = sub. X-Catfish-User **完全忽略**
         (防普通员工冒充别人).
      2. Service token (sub=client:<id>) 且 client_id 在白名单:
         必须带 X-Catfish-User header + 内容看着像 email → effective = header.
         缺 header → 400 (caller 应该传).
         header 格式不对 → 401.
      3. Service token 但 client_id **不在白名单**: X-Catfish-User 忽略,
         effective = sub (e.g. "client:something"). 这样不允许 impersonation
         的 service 也能跑 (例如 cron 跑批 quota 归到自己头上).

    返: 字符串 (email 或 "client:..."), 永不返 None. raise HTTPException 表错.
    """
    # 普通 user token
    if not is_service_principal(user):
        return user.sub

    # service token
    cid = service_client_id(user)
    if cid not in SERVICE_CLIENTS_ALLOWING_USER_OVERRIDE:
        # 不允许 override 的 service: 自己当 effective user
        return user.sub

    # 允许 override 的 service token (e.g. hermes-cli)
    raw = (x_catfish_user_header or "").strip()
    if not raw:
        raise HTTPException(
            status_code=400,
            detail=(
                f"service token (sub={user.sub}) requires "
                f"{X_CATFISH_USER_HEADER} header to identify on-behalf-of user"
            ),
        )
    if not _EMAIL_SHAPE_RE.match(raw):
        # 不像 email — 拒 (防 garbage / injection)
        raise HTTPException(
            status_code=401,
            detail=f"{X_CATFISH_USER_HEADER} value is not a valid email",
        )
    return raw

logger = logging.getLogger("catfish.gateway.auth")

#: Module-level singleton, lazy init.
#: 测试可以调 `_set_provider_for_testing` 注入 mock.
_provider: AuthProvider | None = None


def _env() -> str:
    return os.environ.get("CATFISH_ENV", "dev")


def make_auth_provider() -> AuthProvider:
    """工厂: 根据 env 返合适的 AuthProvider.

    决策对齐 (docs/AUTH-DESIGN.md § 13):
      - 决策 1: 飞书 + 自建 OIDC (Phase 1B-1 自建 catfish-identity 已 ship)
      - 决策 2: gateway 直接验 IdP JWT (OIDCProvider 即此)
      - 决策 6: dev_token 在 prod 保留作兜底 (CompositeProvider 串 OIDC + dev_token)

    行为:
      env=dev:  DevTokenProvider 单一 (本地开发)
      env=prod: 看 CATFISH_OIDC_ISSUER:
                设了  → CompositeProvider([OIDC, DevToken]) — 优先 OIDC + 兜底
                没设 → DevTokenProvider + warning (生产配置缺失, 应该立即告警)
      其他 env: DevTokenProvider 兜底 + warning
    """
    env = _env()
    if env == "dev":
        logger.info("auth: env=dev → DevTokenProvider")
        return DevTokenProvider()

    if env == "prod":
        oidc_issuer = os.environ.get("CATFISH_OIDC_ISSUER", "").strip()
        if oidc_issuer:
            # BL-COMPANION-AUTH (5/15 凌晨): 默认接两个 audience —
            #   catfish-companion: Companion id_token (catfish-identity 老默认 aud=client_id)
            #   catfish-gateway:   service token + RFC 9068 access_token (新, aud=resource server)
            # env CATFISH_OIDC_AUDIENCE 逗号分隔可覆盖, e.g. "audA,audB"
            # 修我 5/14 RBAC Day 2 把 access_token aud 改 catfish-gateway 后, Companion
            # 老 token (aud=catfish-companion) 被拒 → quota 卡 401 的副作用.
            audience = os.environ.get(
                "CATFISH_OIDC_AUDIENCE",
                "catfish-companion,catfish-gateway",
            )
            jwks_uri_env = os.environ.get("CATFISH_OIDC_JWKS_URI", "").strip()
            oidc = OIDCProvider(
                issuer=oidc_issuer,
                audience=audience,
                jwks_uri=jwks_uri_env or None,
            )
            # Composite: 优先 OIDC (真 SSO), 失败 fallback dev_token (生产兜底).
            # dev_token 启用条件: CATFISH_DEV_TOKEN env 显式设 (非默认).
            # Phase 1C 加: dev_token 在 prod 用时 audit 标 auth_method='dev_token',
            # Companion UI 显 warning banner.
            logger.info(
                "auth: env=prod → Composite[OIDC(%s), DevToken]", oidc_issuer
            )
            return CompositeProvider([oidc, DevTokenProvider()])

        logger.warning(
            "auth: env=prod 但 CATFISH_OIDC_ISSUER 没设, fallback DevTokenProvider. "
            "生产部署应该配 OIDC."
        )
        return DevTokenProvider()

    logger.warning("auth: 未知 CATFISH_ENV=%r, fallback DevTokenProvider", env)
    return DevTokenProvider()


def _get_provider() -> AuthProvider:
    """Lazy singleton. 第一次访问初始化."""
    global _provider
    if _provider is None:
        _provider = make_auth_provider()
    return _provider


def _set_provider_for_testing(provider: AuthProvider | None) -> None:
    """测试 hook. 注入 mock 或 reset (None) 让 singleton 重新 lazy init.

    生产代码不要用. 测试里用法:
        _set_provider_for_testing(MyMockProvider())
        ... 测试 ...
        _set_provider_for_testing(None)  # 复原
    """
    global _provider
    _provider = provider


async def get_current_user(
    authorization: str | None = Header(default=None),
) -> User:
    """强鉴权 dependency: 没带 / 带错都 401.

    用在敏感端点 (chat / embeddings 等). 行为跟 Phase 1A 之前一致:
      - 没 Authorization header → 401 missing
      - 不是 'Bearer ...' 格式  → 401 must be Bearer
      - token 不对             → 401 invalid token
      - 通过                   → 返 User

    Phase 1A 改动: 验证逻辑委托给 _get_provider().verify_bearer(...).
    旧版"env != dev 时直接抛 501"已删 — 决策 6 dev_token 保留作 prod 兜底.
    """
    if not authorization:
        # BL-DEBUG-401 (5/24 鸿波): 临时加, 定位 Companion silent refresh 后仍 401 的真因
        logger.warning("BL-DEBUG-401: missing Authorization header")
        raise HTTPException(status_code=401, detail="missing Authorization header")
    if not authorization.lower().startswith("bearer "):
        # BL-DEBUG-401: 头不对
        logger.warning(
            "BL-DEBUG-401: not Bearer prefix, got: %r (len=%d)",
            authorization[:30], len(authorization),
        )
        raise HTTPException(
            status_code=401,
            detail="Authorization must be a Bearer token",
        )

    user = _get_provider().verify_bearer(authorization)
    if user is None:
        # BL-DEBUG-401 (5/24): 升级版 — decode JWT claims, 打 iss/aud/exp/iat
        # 让一眼能看出: exp 过期? aud 不匹配? iss 不匹配? kid 不在 JWKS?
        # decode 不验签 (验签已经在上面 verify_bearer 失败了, 这里只是拆 claims).
        import json as _json
        import base64 as _b64
        import time as _time
        token_part = authorization[7:]  # 去掉 "Bearer "
        preview = token_part[:40] if len(token_part) >= 40 else token_part
        # 拆 header.payload.sig
        parts = token_part.split(".")
        decoded_info = ""
        try:
            if len(parts) >= 2:
                def _b64d(s: str) -> dict:
                    s += "=" * (-len(s) % 4)
                    return _json.loads(_b64.urlsafe_b64decode(s))
                hdr = _b64d(parts[0])
                pl = _b64d(parts[1])
                now = int(_time.time())
                exp = pl.get("exp")
                iat = pl.get("iat")
                exp_status = "?"
                if isinstance(exp, int):
                    diff = exp - now
                    exp_status = (
                        f"PAST EXPIRY by {-diff}s ({-diff/60:.1f}min)"
                        if diff <= 0
                        else f"valid {diff}s ({diff/60:.1f}min) left"
                    )
                decoded_info = (
                    f" alg={hdr.get('alg')} kid={hdr.get('kid')}"
                    f" iss={pl.get('iss')!r} aud={pl.get('aud')!r}"
                    f" sub={pl.get('sub')!r}"
                    f" iat={iat} exp={exp} now={now} → {exp_status}"
                )
        except Exception as _e:
            decoded_info = f" (decode 失败: {_e})"
        logger.warning(
            "BL-DEBUG-401: invalid token; preview=%r len=%d%s",
            preview, len(token_part), decoded_info,
        )
        raise HTTPException(status_code=401, detail="invalid token")
    return user


async def get_current_user_optional(
    authorization: str | None = Header(default=None),
) -> User | None:
    """可选鉴权: 没带 / 带错都 None, 不抛.

    用在匿名浏览场景 (例: 员工启动 Companion 时拉 catalog 看有哪些模型,
    还没填 token).
    """
    return _get_provider().verify_bearer(authorization)
