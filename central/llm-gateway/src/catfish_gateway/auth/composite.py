"""CompositeProvider — 串联多个 AuthProvider.

# 设计

按构造顺序遍历 providers, 第一个返 User 的就用. 用于:

  prod: CompositeProvider([OIDCProvider, DevTokenProvider])
        优先 OIDC; OIDC 验失败 (token 不是 OIDC 格式 / iss 不对) 时 dev_token 兜底.
        决策 6: dev_token 在 prod 也保留作 SSO 配错救急.

  multi-IdP (Phase 2+): CompositeProvider([飞书OIDC, 钉钉OIDC, OIDCProvider])
        多家 IdP 并存, 各自验各自的 token (kid / iss 不同会自然区分).

# verify_bearer 顺序

按 list 顺序. 不并发, 因为 verify_bearer 大部分时间是 jwks 缓存命中, 同步即可.

# is_strict 语义

任一 provider strict → composite strict (高水位). 因为 caller 用 is_strict 决定
"是不是真 SSO", 既然组合里有真 SSO, 整体就该当 strict.
"""

from __future__ import annotations

import logging

from .base import AuthProvider, User

logger = logging.getLogger("catfish.gateway.auth.composite")


class CompositeProvider(AuthProvider):
    """串联多个 AuthProvider, 顺序敏感, 第一个返 User 的就用."""

    def __init__(self, providers: list[AuthProvider]) -> None:
        if not providers:
            raise ValueError("CompositeProvider 至少需要 1 个 provider")
        self.providers = list(providers)
        names = [p.name for p in self.providers]
        logger.info("CompositeProvider 初始化, 顺序: %s", " → ".join(names))

    @property
    def name(self) -> str:
        return f"composite[{', '.join(p.name for p in self.providers)}]"

    @property
    def is_strict(self) -> bool:
        # 任一 strict 就 strict (高水位)
        return any(p.is_strict for p in self.providers)

    def verify_bearer(self, authorization: str | None) -> User | None:
        for provider in self.providers:
            user = provider.verify_bearer(authorization)
            if user is not None:
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "CompositeProvider: %s 通过验证 user.sub=%s",
                        provider.name, user.sub,
                    )
                return user
        return None
