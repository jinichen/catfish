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
        role: 'sysadmin' | 'admin' | 'manager' | 'employee'.
              五一 sprint 5/2 加 manager/employee, 5/10 BL-ARCH1 P1 加 sysadmin.
              继承: sysadmin > admin > manager > employee.
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
    # BL-RBAC-DAY3B (5/17): allowed_models from OIDC effective_allowed_models claim.
    # [] = 全允许 (开放默认 / 无 dept 配置). [m1, m2] = 收紧只允许这俩.
    # 从 OIDCProvider 验 JWT 后塞 effective_allowed_models claim (identity 已经
    # 合并 user.allowed_models + dept.allowed_models 决议过).
    effective_allowed_models: list[str] = None  # type: ignore[assignment]
    # BL-RBAC-DAY4 (5/17): allowed_tools from OIDC effective_allowed_tools claim.
    # 跟 allowed_models 同语义. gateway tools_sanitizer 用它过滤 LLM tool 列表.
    # ALWAYS_ON_TOOLS 永远兜底, 不被这个列表 drop.
    effective_allowed_tools: list[str] = None  # type: ignore[assignment]
    # BL-RBAC-DAY5 (5/17): allowed_skills glob list from OIDC effective_allowed_skills.
    # 跟 allowed_tools 同语义但 glob 匹配 (namespace:pattern, fnmatch). gateway
    # skills_inject 用它过滤 LLM 看到的 skill catalog.
    effective_allowed_skills: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.managed_departments is None:
            self.managed_departments = []
        if self.effective_allowed_models is None:
            self.effective_allowed_models = []
        if self.effective_allowed_tools is None:
            self.effective_allowed_tools = []
        if self.effective_allowed_skills is None:
            self.effective_allowed_skills = []

    def can_access(self, model) -> bool:
        """BL-RBAC-DAY3B (5/17): 真 RBAC. effective_allowed_models 空 = 全允许.

        sysadmin 永远全允许 (绕过 RBAC, 用于运维 / 排错).
        """
        if self.is_sysadmin():
            return True
        if not self.effective_allowed_models:
            return True  # 空 list = 全允许 (无 dept 配置 / 开发期默认)
        # model 可以是 ModelConfig 或 model name str
        model_name = getattr(model, "name", None) or str(model)
        return model_name in self.effective_allowed_models

    def can_use_tool(self, tool_name: str) -> bool:
        """BL-RBAC-DAY4 (5/17): 真 RBAC. effective_allowed_tools 空 = 全允许.

        跟 can_access(model) 同语义.

        注: 此方法只判 RBAC 白名单. 不判 ALWAYS_ON_TOOLS — 那个由
        tools_sanitizer 在调用前作为兜底层, 保证 LLM agent loop 底座工具
        (memory / execute_code / read_file 等) 不会被 dept RBAC drop.
        """
        if self.is_sysadmin():
            return True
        if not self.effective_allowed_tools:
            return True  # 空 list = 全允许 (无 dept 配置 / 开发期默认)
        if not isinstance(tool_name, str) or not tool_name:
            return False
        return tool_name in self.effective_allowed_tools

    def can_use_skill(self, skill_name: str) -> bool:
        """BL-RBAC-DAY5 (5/17): skill 维度 RBAC, glob 匹配.

        skill_name 是 namespaced (`catfish:weekly-report` /
        `hermes:github:zarazhangrui/frontend-slides` / 等), 由 skills_loader
        推导. effective_allowed_skills 是 glob pattern list.

        sysadmin 永远绕过. 空 list = 全允许.
        """
        if self.is_sysadmin():
            return True
        if not self.effective_allowed_skills:
            return True  # 空 = 全允许
        if not isinstance(skill_name, str) or not skill_name:
            return False
        import fnmatch  # noqa: PLC0415
        for pattern in self.effective_allowed_skills:
            if fnmatch.fnmatchcase(skill_name, pattern):
                return True
        return False

    def is_sysadmin(self) -> bool:
        """BL-ARCH1 P1 (5/10): 超级管理员, identity-server users.yaml tier=sysadmin."""
        return self.role == "sysadmin"

    def is_admin(self) -> bool:
        """admin 或更高 (sysadmin) — BL-ARCH1 P2 (5/10) 让 sysadmin 继承 admin 权限.

        ⚠ 'admin 或更高'语义 (跟 catfish-web RoleGate 一致), 不是'恰好 admin'.
        如要严格 admin 用 ``role == "admin"``.
        """
        return self.role in ("admin", "sysadmin")

    def is_manager(self) -> bool:
        """恰好 manager (不含 admin / sysadmin). 沿用历史语义 (5/2 BL-D8 引入)."""
        return self.role == "manager"

    def can_manage_department(self, dept: str) -> bool:
        """RBAC: manager 限 managed_departments, admin / sysadmin 全权."""
        if self.is_admin():  # 包含 sysadmin
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
