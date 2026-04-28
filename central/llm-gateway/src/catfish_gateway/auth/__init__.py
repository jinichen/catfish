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
from .dev_token import DevTokenProvider

__all__ = [
    "User",
    "AuthProvider",
    "DevTokenProvider",
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

    Phase 1A: 一律返 DevTokenProvider (env=dev 用 / env=prod 也兜底用,
              因为决策 6 dev_token 在 prod 也保留作 SSO 配错救急).
    Phase 1B: env=prod 返 CompositeProvider([OIDCProvider, DevTokenProvider]),
              优先 OIDC, dev_token 兜底.
    """
    env = _env()
    if env == "dev":
        logger.info("auth: env=dev → DevTokenProvider")
        return DevTokenProvider()

    if env == "prod":
        # Phase 1B 上 OIDC 后这里改成 CompositeProvider.
        # 当前 dev_token 兜底, 加 warning log 让 ops 知道 OIDC 还没接.
        logger.warning(
            "auth: env=prod 但 OIDCProvider 还没 ship (Phase 1B), "
            "暂用 DevTokenProvider 兜底. UI 应显 warning banner."
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
