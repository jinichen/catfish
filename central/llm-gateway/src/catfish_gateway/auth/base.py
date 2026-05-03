"""AuthProvider 抽象基类 + User dataclass.

# 设计

AuthProvider 是认证策略入口. 不同 IdP / 策略实现各自的 verify_bearer.

实现:
  - DevTokenProvider (auth/dev_token.py): dev 环境用静态 token
  - OIDCProvider (auth/oidc.py): Phase 1B 加, 验 IdP 签的 JWT
  - CompositeProvider (auth/composite.py): Phase 1B 加, 串联多个 provider

verify_bearer 契约:
  - 输入: Authorization header 全文 (含 'Bearer ' 前缀), 或 None
  - 返回: User 或 None
  - 不抛异常 (除非真严重 bug, 不让 caller 区分各种"无效" 子类型)
  - caller 拿到 None 自己决定是 401 还是 anonymous

# 决策对齐 (docs/AUTH-DESIGN.md § 13)

  - User.sub = email (决策 3, 跨 IdP 通用)
  - JWT stateless (决策 5, 中央零状态)
  - dev_token 保留 + warning banner + audit 标 auth_method=dev_token (决策 6)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class User:
    """认证后的 caller. 字段对应 SSO claim 子集.

    Attributes:
        sub: SSO user id. Phase 1 用 email (决策 3).
        department: 部门. Phase 2 RBAC 用.
        tier: 'employee' | 'admin'. (legacy, Phase 2 改用 role)
        role: 'admin' | 'manager' | 'employee'. 五一 sprint 5/2 加, 替代 tier.
        managed_departments: manager 管的部门列表 (空表示啥都不管, manager 必须配).
        auth_method: 'dev_token' | 'oidc' | 等. 给 audit log 看.
                     Phase 1A: 现有调用都不传, 默认 'unknown', 兼容旧行为.
    """

    sub: str
    department: str = ""
    tier: str = "employee"
    role: str = "employee"
    managed_departments: list[str] = None  # type: ignore[assignment]
    auth_method: str = "unknown"

    def __post_init__(self) -> None:
        if self.managed_departments is None:
            self.managed_departments = []

    def can_access(self, model) -> bool:
        # P0: 任何认证用户能调任何模型
        # Phase 2: 部门 + 模型敏感度 (RBAC)
        return True

    def is_admin(self) -> bool:
        return self.role == "admin"

    def is_manager(self) -> bool:
        return self.role == "manager"

    def can_manage_department(self, dept: str) -> bool:
        """RBAC: manager 限 managed_departments, admin 全权."""
        if self.is_admin():
            return True
        if self.is_manager():
            return dept in (self.managed_departments or [])
        return False


class AuthProvider(ABC):
    """认证策略基类. 子类提供具体实现.

    设计: 子类必须实现 verify_bearer + name. is_strict 默认 True, dev_token
    类的 lax provider override 成 False 以传递语义给 caller (例: UI banner).
    """

    @abstractmethod
    def verify_bearer(self, authorization: str | None) -> User | None:
        """验 Authorization header 全文. 返 User 或 None (不抛)."""
        raise NotImplementedError

    @property
    @abstractmethod
    def name(self) -> str:
        """provider 名字. 给 log / metric / Companion UI banner 看.
        例: 'dev_token' / 'oidc:feishu' / 'oidc:custom'
        """
        raise NotImplementedError

    @property
    def is_strict(self) -> bool:
        """是不是严格鉴权.

        - True (默认): 真正的 SSO, 配错应该 fail. 例: OIDCProvider
        - False: lax (配错也勉强能用). 例: DevTokenProvider — 允许本地开发,
                也允许生产 IT 配错 SSO 时救急 (决策 6). UI 应该显 banner 警告.
        """
        return True
