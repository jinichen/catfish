"""Auth middleware.

P0: Dev mode accepts a static token.
P1: Will validate real SSO JWT (OIDC).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import Header, HTTPException


@dataclass
class User:
    """Represents the authenticated caller of the gateway."""

    sub: str  # unique subject id (SSO user id)
    department: str = ""  # for P1 RBAC
    tier: str = "employee"  # employee | admin

    def can_access(self, model) -> bool:
        # P0: every authenticated user can access every model
        # P1: introduce department-level and sensitivity-level gates
        return True


def _dev_token() -> str:
    return os.environ.get("CATFISH_DEV_TOKEN", "dev-token-local")


def _env() -> str:
    return os.environ.get("CATFISH_ENV", "dev")


def _verify_bearer(authorization: str | None) -> User | None:
    """Core 验证逻辑，不抛异常，只返回 User 或 None。

    抽出来的目的：既给严格鉴权（get_current_user）用，也给可选鉴权
    （get_current_user_optional）用，两者逻辑不会漂。
    """
    if not authorization:
        return None
    if not authorization.lower().startswith("bearer "):
        return None
    token = authorization[7:].strip()

    if _env() == "dev":
        if token != _dev_token():
            return None
        return User(sub="dev-user", department="engineering", tier="employee")

    # P1: validate real JWT here, return None on bad token.
    return None


async def get_current_user(
    authorization: str | None = Header(default=None),
) -> User:
    """强鉴权依赖：没带 / 带错都返 401。用于敏感端点。"""
    if not authorization:
        raise HTTPException(status_code=401, detail="missing Authorization header")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Authorization must be a Bearer token",
        )

    if _env() != "dev":
        # P1: 真正的 JWT 校验
        raise HTTPException(
            status_code=501,
            detail="production SSO not yet implemented (scheduled for P1)",
        )

    user = _verify_bearer(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid dev token")
    return user


async def get_current_user_optional(
    authorization: str | None = Header(default=None),
) -> User | None:
    """可选鉴权依赖：没带 / 带错都返 None，不抛。

    用在匿名浏览场景（比如员工第一次启动 Hermes，还没填 token 时
    需要先知道有哪些模型可选）。
    """
    return _verify_bearer(authorization)
