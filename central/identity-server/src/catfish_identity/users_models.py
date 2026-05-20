"""IdentityUser dataclass + to_oidc_claims — 抽自 users.py (5/21 拆分).

UserRegistry CRUD 在 users.py 主文件. 这里只是 user 数据模型.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IdentityUser:
    """注册用户. 内部数据结构, 不直接 expose JSON.

    五一 sprint 5/2 加 RBAC (BL-D8): role + managed_departments.
    BL-ARCH1 P1 (5/10) 加 admin 管理字段: locked / deleted_at / created_by /
    last_login_at / must_change_password.
    跟旧 tier 字段并存 (老配置兼容), 优先级 role > tier.
    """

    email: str
    password_hash: str
    name: str = ""
    department: str = ""
    tier: str = "employee"  # 旧字段 'employee' | 'admin' | 'sysadmin', 兼容老 yaml
    # ── RBAC (5/2 加, BL-D8) ──
    role: str = ""  # sysadmin / admin / manager / employee. 空 = 用 tier 兜底
    managed_departments: list[str] = field(default_factory=list)
    # ── admin 管理字段 (5/10 加, BL-ARCH1 P1) ──
    locked: bool = False
    locked_at: str | None = None  # ISO datetime, None = 未锁
    locked_by: str | None = None  # 谁锁的 (admin email)
    deleted_at: str | None = None  # 软删时间, None = 活跃
    created_at: str | None = None
    created_by: str = "system"
    last_login_at: str | None = None
    must_change_password: bool = False
    # ── RBAC Day 3a (5/17 加, BL-RBAC-DAY3A) ──
    # None = 继承 department.allowed_models; [] = 用户级 override 解锁全允许;
    # [m1, m2] = 用户级 override 收紧只允许这俩.
    # gateway 拿到 user → 走 get_effective_allowed_models() 决议.
    allowed_models: list[str] | None = None
    # ── RBAC Day 4 (5/17 加, BL-RBAC-DAY4) ──
    # 跟 allowed_models 同套语义, tools 维度. None = 继承 dept; [] = 全允许 override;
    # [t1, t2] = 收紧 (+ gateway 端 ALWAYS_ON_TOOLS 兜底 LLM agent loop 底座).
    allowed_tools: list[str] | None = None
    # ── RBAC Day 5 (5/17 加, BL-RBAC-DAY5) ──
    # skill 维度, glob pattern (catfish:* / hermes:bundled:* / hermes:github:owner/*).
    # None = 继承 dept; [] = 全允许; [g1, g2] = 收紧到匹配的 skill.
    allowed_skills: list[str] | None = None

    def effective_role(self) -> str:
        """实际生效的 role. 优先 role 字段, 兜底 tier.

        BL-ARCH1 P1 (5/10): tier='sysadmin' 视为 role='sysadmin' (最高权限).
        """
        if self.role:
            return self.role
        if self.tier == "sysadmin":
            return "sysadmin"
        if self.tier == "admin":
            return "admin"
        return "employee"

    def to_oidc_claims(self) -> dict:
        """渲染成 OIDC ID Token 的 claims (不含密码 hash). **sync 路径**, 不含 RBAC
        Day 3b 的 effective_allowed_models (需要 async 决议 dept).

        加 RBAC 字段 role / managed_departments, gateway / Companion 直接验.

        BL-RBAC-DAY3B (5/17): 老 caller 仍用这个, 但发 ID Token 应该走异步版
        `to_oidc_claims_async()` 拿 effective_allowed_models. 见 routes.py.
        """
        role = self.effective_role()
        managed = self.managed_departments if role == "manager" else []
        return {
            "email": self.email,
            "email_verified": True,
            "name": self.name or self.email.split("@")[0],
            "department": self.department,
            "tier": self.tier,
            "role": role,
            "managed_departments": managed,
            # BL-RBAC-DAY3B: 同步版本返的是 raw user.allowed_models, 不合并 dept.
            # gateway 应该走异步版的 effective_allowed_models claim.
            "allowed_models_raw": self.allowed_models,
            # BL-RBAC-DAY4 (5/17): allowed_tools raw (gateway 走异步版拿 effective)
            "allowed_tools_raw": self.allowed_tools,
            # BL-RBAC-DAY5 (5/17): allowed_skills raw (gateway 走异步版拿 effective)
            "allowed_skills_raw": self.allowed_skills,
        }

    async def to_oidc_claims_async(self) -> dict:
        """异步版本, 含 effective_allowed_models (合并 user + dept).

        BL-RBAC-DAY3B (5/17): /token + /authorize 发 ID Token 时走这个, gateway
        验 token 后拿 effective_allowed_models claim 直接过滤 catalog + chat.
        """
        from .departments import (  # noqa: PLC0415
            get_effective_allowed_models,
            get_effective_allowed_skills,
            get_effective_allowed_tools,
        )

        base = self.to_oidc_claims()
        effective = await get_effective_allowed_models(
            user_email=self.email,
            user_allowed_models=self.allowed_models,
            user_department=self.department,
        )
        base["effective_allowed_models"] = effective
        # BL-RBAC-DAY4 (5/17): 同步骤拿 effective_allowed_tools, gateway 验
        # token 后直接拿这个 claim 过滤 LLM tool 列表 (tools_sanitizer).
        effective_tools = await get_effective_allowed_tools(
            user_email=self.email,
            user_allowed_tools=self.allowed_tools,
            user_department=self.department,
        )
        base["effective_allowed_tools"] = effective_tools
        # BL-RBAC-DAY5 (5/17): effective_allowed_skills, gateway skills_inject 过滤
        effective_skills = await get_effective_allowed_skills(
            user_email=self.email,
            user_allowed_skills=self.allowed_skills,
            user_department=self.department,
        )
        base["effective_allowed_skills"] = effective_skills
        return base


