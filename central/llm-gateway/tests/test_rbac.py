"""测试 RBAC · 角色访问控制 (五一 sprint 5/2, BL-D8).

覆盖:
- 3 角色权限矩阵
- has_permission_for_department (manager 限 managed_departments)
- require_permission FastAPI dependency
- 默认 role=employee fallback
- admin 隐式全权
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from catfish_gateway.rbac import (
    Permission,
    ROLE_PERMISSIONS,
    has_permission,
    has_permission_for_department,
    require_permission,
)


# ── role 权限矩阵 ──────────────────────────────────────────


def test_admin_has_all_permissions() -> None:
    for perm in Permission:
        assert has_permission("admin", perm), f"admin 应该有 {perm.value}"


def test_employee_only_has_self_view() -> None:
    assert has_permission("employee", Permission.AUDIT_VIEW_SELF)
    assert has_permission("employee", Permission.QUOTA_VIEW_SELF)
    # employee 不能看部门 / 全员
    assert not has_permission("employee", Permission.AUDIT_VIEW_DEPARTMENT)
    assert not has_permission("employee", Permission.AUDIT_VIEW_ALL)
    assert not has_permission("employee", Permission.QUOTA_PATCH_DEPARTMENT)
    assert not has_permission("employee", Permission.QUOTA_PATCH_GLOBAL)
    assert not has_permission("employee", Permission.USER_MANAGE)
    assert not has_permission("employee", Permission.RBAC_PATCH)


def test_manager_can_view_own_department_audit() -> None:
    assert has_permission("manager", Permission.AUDIT_VIEW_DEPARTMENT)
    assert has_permission("manager", Permission.QUOTA_VIEW_DEPARTMENT)
    assert has_permission("manager", Permission.QUOTA_PATCH_DEPARTMENT)
    assert has_permission("manager", Permission.SKILL_PUBLISH_DEPARTMENT)


def test_manager_cannot_view_global_or_manage_users() -> None:
    assert not has_permission("manager", Permission.AUDIT_VIEW_ALL)
    assert not has_permission("manager", Permission.QUOTA_PATCH_GLOBAL)
    assert not has_permission("manager", Permission.USER_MANAGE)
    assert not has_permission("manager", Permission.RBAC_PATCH)


def test_unknown_role_denied_default() -> None:
    """未知 role → 默认拒 (防 yaml 误填导致提权)."""
    assert not has_permission("supervisor", Permission.AUDIT_VIEW_SELF)
    assert not has_permission("", Permission.AUDIT_VIEW_SELF)
    assert not has_permission("ADMIN", Permission.AUDIT_VIEW_SELF)  # 大小写敏感


# ── 部门维度 ───────────────────────────────────────────────


def test_admin_implicit_all_departments() -> None:
    """admin 不需要 managed_departments, 隐式访问任何部门."""
    user = {"role": "admin", "department": "研发部", "managed_departments": []}
    allowed, _ = has_permission_for_department(
        user, Permission.AUDIT_VIEW_DEPARTMENT, "销售部"
    )
    assert allowed


def test_manager_limited_to_managed_departments() -> None:
    """manager 只能访问 managed_departments 列出的部门."""
    user = {
        "role": "manager",
        "department": "研发部",
        "managed_departments": ["研发部", "测试部"],
    }
    # 自己部门 OK
    allowed, _ = has_permission_for_department(
        user, Permission.AUDIT_VIEW_DEPARTMENT, "研发部"
    )
    assert allowed
    # 也管的另一个部门 OK
    allowed, _ = has_permission_for_department(
        user, Permission.AUDIT_VIEW_DEPARTMENT, "测试部"
    )
    assert allowed
    # 不管的部门 拒
    allowed, reason = has_permission_for_department(
        user, Permission.AUDIT_VIEW_DEPARTMENT, "销售部"
    )
    assert not allowed
    assert "managed_departments" in reason


def test_employee_no_department_access() -> None:
    user = {"role": "employee", "department": "研发部"}
    allowed, reason = has_permission_for_department(
        user, Permission.AUDIT_VIEW_DEPARTMENT, "研发部"
    )
    assert not allowed


def test_manager_without_perm_denied() -> None:
    """manager 不持 RBAC_PATCH 权限, 即使部门匹配也拒."""
    user = {
        "role": "manager",
        "department": "研发部",
        "managed_departments": ["研发部"],
    }
    allowed, reason = has_permission_for_department(
        user, Permission.RBAC_PATCH, "研发部"
    )
    assert not allowed
    assert "manager" in reason


# ── FastAPI dependency ────────────────────────────────────


def _make_request(user: dict | None = None) -> MagicMock:
    """构造 FastAPI Request mock."""
    req = MagicMock()
    req.state.user = user
    return req


@pytest.mark.asyncio
async def test_require_permission_allow() -> None:
    user = {"role": "admin", "email": "alice@ffcs.cn"}
    req = _make_request(user)
    dep = require_permission(Permission.USER_MANAGE)
    result = await dep(req)
    assert result == user


@pytest.mark.asyncio
async def test_require_permission_deny_role() -> None:
    user = {"role": "employee", "email": "bob@ffcs.cn"}
    req = _make_request(user)
    dep = require_permission(Permission.USER_MANAGE)
    with pytest.raises(HTTPException) as exc:
        await dep(req)
    assert exc.value.status_code == 403
    assert "user.manage" in exc.value.detail


@pytest.mark.asyncio
async def test_require_permission_no_token_401() -> None:
    """request.state.user 为空 → 401 (没登录)."""
    req = _make_request(user=None)
    dep = require_permission(Permission.AUDIT_VIEW_SELF)
    with pytest.raises(HTTPException) as exc:
        await dep(req)
    assert exc.value.status_code == 401


# ── ROLE_PERMISSIONS 数据完整性 ───────────────────────────


def test_role_permissions_keys() -> None:
    # BL-ARCH1 P2 (5/10) 加了 sysadmin
    assert set(ROLE_PERMISSIONS.keys()) == {"admin", "sysadmin", "manager", "employee"}


def test_admin_ge_manager_ge_employee() -> None:
    """权限层级: admin ⊇ manager ⊇ employee."""
    admin_perms = ROLE_PERMISSIONS["admin"]
    manager_perms = ROLE_PERMISSIONS["manager"]
    employee_perms = ROLE_PERMISSIONS["employee"]
    assert manager_perms.issubset(admin_perms), "manager 权限 ⊆ admin"
    assert employee_perms.issubset(manager_perms), "employee 权限 ⊆ manager"
