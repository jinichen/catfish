"""RBAC · 角色访问控制 — 五一 sprint 5/2 (BL-D8).

# 设计 (docs/RBAC-DESIGN.md v0.1)

3 角色: admin / manager / employee. 跟 SAP / Oracle 的国央企标准一致.

JWT 解析后的 user dict 已含 role / department / managed_departments
(catfish-identity 5/2 改的, IdentityUser.to_oidc_claims 透传).

# 用法

```python
from catfish_gateway.rbac import Permission, require_permission

@app.get("/v1/audit/department")
async def audit_department(
    user = Depends(require_permission(Permission.AUDIT_VIEW_DEPARTMENT)),
):
    department = user.get("department")
    if user.get("role") == "manager":
        managed = user.get("managed_departments") or []
        if department not in managed:
            raise HTTPException(403, "无权访问该部门 audit")
    ...
```

# 演进

- Phase 2 (Q3) — 加 fine-grained ACL (BL-Q3-RBAC2)
- Phase 3 (Q4) — 跨部门 / 跨子公司 federation RBAC
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from fastapi import HTTPException, Request, status

logger = logging.getLogger("catfish.gateway.rbac")


class Permission(str, Enum):
    """gateway 暴露的所有受保护操作.

    命名: <资源>.<动作>. 客户 IT 一看就懂.
    """

    # audit
    AUDIT_VIEW_SELF = "audit.view_self"
    AUDIT_VIEW_DEPARTMENT = "audit.view_department"
    AUDIT_VIEW_ALL = "audit.view_all"

    # quota
    QUOTA_VIEW_SELF = "quota.view_self"
    QUOTA_VIEW_DEPARTMENT = "quota.view_department"
    QUOTA_PATCH_DEPARTMENT = "quota.patch_department"
    QUOTA_PATCH_GLOBAL = "quota.patch_global"

    # user / RBAC
    USER_MANAGE = "user.manage"
    RBAC_PATCH = "rbac.patch"

    # skills (Phase 2.5 / Phase 3 共享 skill 时用)
    SKILL_PUBLISH_DEPARTMENT = "skill.publish_department"
    SKILL_DELETE_DEPARTMENT = "skill.delete_department"


#: 角色 → 拥有的 Permission 集合.
ROLE_PERMISSIONS: dict[str, set[Permission]] = {
    "admin": set(Permission),  # 全权
    "manager": {
        Permission.AUDIT_VIEW_SELF,
        Permission.AUDIT_VIEW_DEPARTMENT,
        Permission.QUOTA_VIEW_SELF,
        Permission.QUOTA_VIEW_DEPARTMENT,
        Permission.QUOTA_PATCH_DEPARTMENT,
        Permission.SKILL_PUBLISH_DEPARTMENT,
        Permission.SKILL_DELETE_DEPARTMENT,
    },
    "employee": {
        Permission.AUDIT_VIEW_SELF,
        Permission.QUOTA_VIEW_SELF,
    },
}


def has_permission(role: str, perm: Permission) -> bool:
    """role 是否有 perm. 未知 role → False (默认拒)."""
    return perm in ROLE_PERMISSIONS.get(role, set())


def has_permission_for_department(
    user: dict[str, Any], perm: Permission, target_department: str
) -> tuple[bool, str]:
    """除了角色检查, 还验"manager 限自己部门" 约束.

    返 (allowed, deny_reason).
    """
    role = user.get("role", "employee")

    # 1. role 维度
    if not has_permission(role, perm):
        return False, f"role={role} 没有 {perm.value} 权限"

    # 2. department 维度 (admin 隐式全权, manager 限 managed_departments)
    if role == "admin":
        return True, ""
    if role == "manager":
        managed = user.get("managed_departments") or []
        if target_department not in managed:
            return False, (
                f"role=manager 只能访问 managed_departments={managed}, "
                f"不能访问 {target_department}"
            )
        return True, ""
    # employee 已在 1. 拦截 (employee 不持 *_DEPARTMENT 权限)
    return False, "employee 无部门级访问权限"


# ── FastAPI dependency ──────────────────────────────────────


def _user_from_request(request: Request) -> dict[str, Any]:
    """从 request.state 拿 OIDC middleware 解析的 user.

    request.state.user 由 catfish-gateway/auth 中间件设置, 含 OIDC claims
    (sub / email / role / department / managed_departments).
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无 OIDC token, 请登录",
        )
    return user


def require_permission(perm: Permission):
    """FastAPI dependency factory: 验当前 user 有 perm 权限.

    Usage:
        @app.get(...)
        async def x(user = Depends(require_permission(Permission.AUDIT_VIEW_ALL))):
            ...
    """

    async def dep(request: Request) -> dict[str, Any]:
        user = _user_from_request(request)
        role = user.get("role", "employee")
        if not has_permission(role, perm):
            logger.info(
                "RBAC deny: user=%s role=%s perm=%s",
                user.get("email", "?"),
                role,
                perm.value,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role={role} 无 {perm.value} 权限. 联系 IT 升级角色.",
            )
        return user

    return dep


def require_self_or_department_admin():
    """通用 dependency: 必须是自己或 manager/admin.

    给"看部门聚合" / "改部门 quota" 类 endpoint 用.
    """

    async def dep(request: Request) -> dict[str, Any]:
        user = _user_from_request(request)
        role = user.get("role", "employee")
        if role not in ("admin", "manager"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role={role} 不能访问部门级资源",
            )
        return user

    return dep


__all__ = [
    "Permission",
    "ROLE_PERMISSIONS",
    "has_permission",
    "has_permission_for_department",
    "require_permission",
    "require_self_or_department_admin",
]
