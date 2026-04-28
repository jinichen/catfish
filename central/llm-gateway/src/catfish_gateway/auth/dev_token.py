"""DevTokenProvider — dev 环境用静态 token + 生产兜底.

# 用在哪里

  1. 本地开发 (CATFISH_ENV=dev, default): 你跑 catfish_gateway 测试时用
  2. 生产 SSO 配错救急 (CATFISH_ENV=prod 但显式设了 CATFISH_DEV_TOKEN):
     客户 IT 第一次接 SSO 撞坑时, 临时用 dev_token 进 Companion 排查.
     Companion UI 必须显 warning banner "你在用 dev token, 不是真 SSO" (Phase 1C).
     audit log 必须标 auth_method='dev_token' (gateway 已支持 security_concern 字段).

# 决策对齐

决策 6 (docs/AUTH-DESIGN.md § 13): dev_token 保留作生产兜底.

# 行为兼容

跟 Phase 1A 之前的 auth.py 100% 一致:
  - 静态 token 来自 env CATFISH_DEV_TOKEN (默认 'dev-token-local')
  - 验过的 user 字段固定: sub='dev-user', dept='engineering', tier='employee'
  - 每次 verify 读 env, 支持 pytest monkeypatch.setenv 测试改 token

新加: User.auth_method='dev_token' (旧版没这个字段, 默认 'unknown'). audit log 用.
"""

from __future__ import annotations

import logging
import os

from .base import AuthProvider, User

logger = logging.getLogger("catfish.gateway.auth.dev_token")

#: 默认 dev token. 生产环境员工应该 explicit 设 CATFISH_DEV_TOKEN, 不依赖默认.
_DEFAULT_DEV_TOKEN = "dev-token-local"

#: dev_token 的 user 固定为这个. 测试 / dev 都用同一身份.
_DEV_USER_SUB = "dev-user"
_DEV_USER_DEPT = "engineering"
_DEV_USER_TIER = "employee"


class DevTokenProvider(AuthProvider):
    """静态 token 验证.

    Stateless — 每次 verify_bearer 重读 env, 支持 monkeypatch.
    Thread-safe — 没有共享 mutable state.
    """

    @property
    def name(self) -> str:
        return "dev_token"

    @property
    def is_strict(self) -> bool:
        # dev_token 是 lax — 客户 IT 配错 SSO 时救急用. UI 应该显 banner.
        return False

    def _expected_token(self) -> str:
        """每次读 env, 支持测试 monkeypatch."""
        return os.environ.get("CATFISH_DEV_TOKEN", _DEFAULT_DEV_TOKEN)

    def verify_bearer(self, authorization: str | None) -> User | None:
        if not authorization:
            return None
        if not authorization.lower().startswith("bearer "):
            return None
        token = authorization[7:].strip()
        if not token:
            return None
        if token != self._expected_token():
            return None
        return User(
            sub=_DEV_USER_SUB,
            department=_DEV_USER_DEPT,
            tier=_DEV_USER_TIER,
            auth_method="dev_token",
        )
