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
]

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
            audience = os.environ.get("CATFISH_OIDC_AUDIENCE", "catfish-companion")
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
        raise HTTPException(status_code=401, detail="missing Authorization header")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Authorization must be a Bearer token",
        )

    user = _get_provider().verify_bearer(authorization)
    if user is None:
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
