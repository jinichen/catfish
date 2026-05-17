"""Admin 管理 endpoints (BL-ARCH1 P1, 5/10).

给 catfish-web /admin/users 调. RBAC:
  - sysadmin: 全权 (含创建 admin / sysadmin / 改 sysadmin 自己)
  - admin: 管 admin 之下 (employee / manager), 不能动 sysadmin 也不能创建 admin
  - manager / employee: 没权限调本 router

鉴权: 跟 mcp-registry / skills-hub 同模式 — 信任 X-Catfish-User-Sub / -Role header
(gateway 反代时注入). 不接受外部 Bearer.

# 端点

  GET    /admin/users                列 user (含 deleted_at IS NULL 默认)
  POST   /admin/users                创建 user
  GET    /admin/users/{email}        单 user 详情
  PUT    /admin/users/{email}        改 user (name/department/role/managed_dept)
  DELETE /admin/users/{email}        软删
  POST   /admin/users/{email}/lock   锁 / 解锁
  POST   /admin/users/{email}/reset-password   admin 给员工换临时密码
  GET    /admin/users-audit          users_audit 历史 (谁改了谁)
"""
from __future__ import annotations

import logging
import urllib.parse
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, EmailStr, Field

from .users import IdentityUser, UserRegistry

logger = logging.getLogger("catfish.identity.admin")


def _decode_header(value: str | None) -> str:
    """gateway proxy percent-encode 中文 header 反向."""
    if not value:
        return ""
    try:
        return urllib.parse.unquote(value)
    except Exception:
        return value


class CallerContext:
    """从 X-Catfish-User-* header 解出来的调用方身份."""

    def __init__(self, sub: str, dept: str, role: str):
        self.sub = sub
        self.dept = dept
        self.role = role

    @property
    def is_sysadmin(self) -> bool:
        return self.role == "sysadmin"

    @property
    def is_admin_or_above(self) -> bool:
        return self.role in ("sysadmin", "admin")


def require_caller(
    x_catfish_user_sub: str | None = Header(default=None, alias="X-Catfish-User-Sub"),
    x_catfish_user_dept: str | None = Header(default=None, alias="X-Catfish-User-Dept"),
    x_catfish_user_role: str | None = Header(default=None, alias="X-Catfish-User-Role"),
) -> CallerContext:
    sub = _decode_header(x_catfish_user_sub)
    if not sub:
        raise HTTPException(
            status_code=401,
            detail="缺 X-Catfish-User-Sub (必经 gateway 反代)",
        )
    return CallerContext(
        sub=sub,
        dept=_decode_header(x_catfish_user_dept),
        role=_decode_header(x_catfish_user_role) or "employee",
    )


def require_admin_or_above(
    caller: CallerContext = Depends(require_caller),
) -> CallerContext:
    if not caller.is_admin_or_above:
        raise HTTPException(
            status_code=403,
            detail=f"admin / sysadmin only (你是 {caller.role})",
        )
    return caller


def require_sysadmin(
    caller: CallerContext = Depends(require_caller),
) -> CallerContext:
    if not caller.is_sysadmin:
        raise HTTPException(
            status_code=403,
            detail=f"sysadmin only (你是 {caller.role})",
        )
    return caller


# ── Pydantic schemas ────────────────────────────────────────────


class UserBrief(BaseModel):
    email: str
    name: str
    department: str
    role: str
    managed_departments: list[str]
    locked: bool
    locked_at: str | None = None
    deleted_at: str | None = None
    created_at: str | None = None
    last_login_at: str | None = None
    must_change_password: bool = False
    # BL-RBAC-DAY7 (5/17): per-user override. null = 继承 dept, [] = 解锁全允许.
    allowed_models: list[str] | None = None
    allowed_tools: list[str] | None = None
    allowed_skills: list[str] | None = None


def _to_brief(u: IdentityUser) -> UserBrief:
    return UserBrief(
        email=u.email,
        name=u.name,
        department=u.department,
        role=u.effective_role(),
        managed_departments=u.managed_departments,
        locked=u.locked,
        locked_at=u.locked_at,
        deleted_at=u.deleted_at,
        created_at=u.created_at,
        last_login_at=u.last_login_at,
        must_change_password=u.must_change_password,
        allowed_models=u.allowed_models,
        allowed_tools=u.allowed_tools,
        allowed_skills=u.allowed_skills,
    )


class CreateUserReq(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = ""
    department: str = ""
    role: str = "employee"  # employee / manager / admin / sysadmin
    managed_departments: list[str] = Field(default_factory=list)
    must_change_password: bool = True


class UpdateUserReq(BaseModel):
    name: str | None = None
    department: str | None = None
    role: str | None = None
    managed_departments: list[str] | None = None
    # BL-RBAC-DAY7 (5/17): per-user RBAC override 三维. None = 不动,
    # "__inherit__" = NULL (回继承 dept), list = override (空 list = 解锁全允许).
    allowed_models: list[str] | str | None = None
    allowed_tools: list[str] | str | None = None
    allowed_skills: list[str] | str | None = None


class LockUserReq(BaseModel):
    locked: bool


class ResetPasswordReq(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)
    force_change: bool = True


# ── Router ──────────────────────────────────────────────────────


def make_admin_router(registry: UserRegistry) -> APIRouter:
    """创建 admin router. 用 closure 持 registry."""
    router = APIRouter(prefix="/admin", tags=["admin"])

    @router.get("/users")
    async def list_users(
        include_deleted: bool = False,
        department: str | None = None,
        role: str | None = None,
        caller: CallerContext = Depends(require_admin_or_above),
    ) -> dict[str, Any]:
        """列 user. admin 看不到 sysadmin (除非自己是 sysadmin)."""
        users = await registry.list_users(
            include_deleted=include_deleted,
            department_filter=department,
            role_filter=role,
        )
        # admin 屏蔽 sysadmin 行 (sysadmin 看全)
        if caller.role != "sysadmin":
            users = [u for u in users if u.effective_role() != "sysadmin"]
        return {
            "users": [_to_brief(u).model_dump() for u in users],
            "count": len(users),
            "caller_role": caller.role,
        }

    @router.post("/users", status_code=201)
    async def create_user(
        req: CreateUserReq,
        caller: CallerContext = Depends(require_admin_or_above),
    ) -> dict[str, Any]:
        """创建 user. role=admin/sysadmin 只 sysadmin 能创建."""
        if req.role in ("admin", "sysadmin") and caller.role != "sysadmin":
            raise HTTPException(
                status_code=403,
                detail=f"创建 {req.role} 角色只 sysadmin 能做 (你是 {caller.role})",
            )
        ok, err = await registry.create_user(
            email=req.email,
            password=req.password,
            name=req.name,
            department=req.department,
            role=req.role,
            managed_departments=req.managed_departments,
            created_by=caller.sub,
            must_change_password=req.must_change_password,
        )
        if not ok:
            raise HTTPException(status_code=400, detail=err)
        u = registry.find(req.email)
        return {"ok": True, "user": _to_brief(u).model_dump() if u else None}

    @router.get("/users/{email}")
    async def get_user(
        email: str,
        caller: CallerContext = Depends(require_admin_or_above),
    ) -> UserBrief:
        u = registry.find(email)
        if u is None:
            raise HTTPException(status_code=404, detail=f"user {email} 不存在")
        if u.effective_role() == "sysadmin" and caller.role != "sysadmin":
            raise HTTPException(status_code=403, detail="只 sysadmin 能看 sysadmin")
        return _to_brief(u)

    @router.put("/users/{email}")
    async def update_user(
        email: str,
        req: UpdateUserReq,
        caller: CallerContext = Depends(require_admin_or_above),
    ) -> dict[str, Any]:
        target = registry.find(email)
        if target is None:
            raise HTTPException(status_code=404, detail=f"user {email} 不存在")
        if target.effective_role() == "sysadmin" and caller.role != "sysadmin":
            raise HTTPException(status_code=403, detail="只 sysadmin 能改 sysadmin")
        if req.role in ("admin", "sysadmin") and caller.role != "sysadmin":
            raise HTTPException(
                status_code=403,
                detail=f"提升到 {req.role} 角色只 sysadmin 能做",
            )
        ok, err = await registry.update_user(
            email,
            by_email=caller.sub,
            name=req.name,
            department=req.department,
            role=req.role,
            managed_departments=req.managed_departments,
            # BL-RBAC-DAY7 (5/17): 三维 RBAC override 透传
            allowed_models=req.allowed_models,
            allowed_tools=req.allowed_tools,
            allowed_skills=req.allowed_skills,
        )
        if not ok:
            raise HTTPException(status_code=400, detail=err)
        u = registry.find(email)
        return {"ok": True, "user": _to_brief(u).model_dump() if u else None}

    @router.delete("/users/{email}")
    async def delete_user(
        email: str,
        caller: CallerContext = Depends(require_admin_or_above),
    ) -> dict[str, Any]:
        target = registry.find(email)
        if target is None:
            raise HTTPException(status_code=404, detail=f"user {email} 不存在")
        if target.effective_role() == "sysadmin" and caller.role != "sysadmin":
            raise HTTPException(status_code=403, detail="只 sysadmin 能删 sysadmin")
        if email.lower() == caller.sub.lower():
            raise HTTPException(status_code=400, detail="不能删自己")
        ok, err = await registry.delete_user(email, by_email=caller.sub)
        if not ok:
            raise HTTPException(status_code=400, detail=err)
        return {"ok": True}

    @router.post("/users/{email}/lock")
    async def lock_user(
        email: str,
        req: LockUserReq,
        caller: CallerContext = Depends(require_admin_or_above),
    ) -> dict[str, Any]:
        target = registry.find(email)
        if target is None:
            raise HTTPException(status_code=404, detail=f"user {email} 不存在")
        if target.effective_role() == "sysadmin" and caller.role != "sysadmin":
            raise HTTPException(status_code=403, detail="只 sysadmin 能锁 sysadmin")
        if email.lower() == caller.sub.lower() and req.locked:
            raise HTTPException(status_code=400, detail="不能锁自己")
        ok, err = await registry.lock_user(
            email, by_email=caller.sub, locked=req.locked,
        )
        if not ok:
            raise HTTPException(status_code=400, detail=err)
        return {"ok": True, "locked": req.locked}

    @router.post("/users/{email}/reset-password")
    async def reset_password(
        email: str,
        req: ResetPasswordReq,
        caller: CallerContext = Depends(require_admin_or_above),
    ) -> dict[str, Any]:
        target = registry.find(email)
        if target is None:
            raise HTTPException(status_code=404, detail=f"user {email} 不存在")
        if target.effective_role() == "sysadmin" and caller.role != "sysadmin":
            raise HTTPException(status_code=403, detail="只 sysadmin 能重置 sysadmin")
        ok, err = await registry.reset_password(
            email,
            by_email=caller.sub,
            new_password=req.new_password,
            force_change=req.force_change,
        )
        if not ok:
            raise HTTPException(status_code=400, detail=err)
        return {"ok": True, "force_change": req.force_change}

    @router.get("/users-audit")
    async def list_users_audit(
        limit: int = 100,
        caller: CallerContext = Depends(require_admin_or_above),
    ) -> dict[str, Any]:
        events = await registry.list_audit(limit=limit)
        return {"events": events, "limit": limit, "caller_role": caller.role}

    @router.get("/me-as-admin")
    async def me_as_admin(
        caller: CallerContext = Depends(require_admin_or_above),
    ) -> dict[str, Any]:
        """admin 自己的身份 + 权限速览, web 启动后调一次决定显隐 admin tab."""
        return {
            "sub": caller.sub,
            "dept": caller.dept,
            "role": caller.role,
            "is_sysadmin": caller.is_sysadmin,
            "permissions": {
                "list_users": True,
                "create_admin": caller.is_sysadmin,
                "manage_sysadmin": caller.is_sysadmin,
            },
        }

    # ── BL-RBAC-DAY7 (5/17): departments endpoints ─────────────────
    # admin / sysadmin 看 + 改 dept 配置 (allowed_models / allowed_tools /
    # allowed_skills / quota_models_day / description). manager 只读自己管
    # 的 dept (managed_departments). employee 没权限.

    @router.get("/departments")
    async def list_departments(
        caller: CallerContext = Depends(require_admin_or_above),
    ) -> dict[str, Any]:
        """列所有 dept. admin / sysadmin 看全, manager 只看 managed_departments."""
        from .departments import get_global_registry  # noqa: PLC0415

        depts = await get_global_registry().list_all()
        # manager filter — 但 manager 没过 require_admin_or_above, 不会到这.
        # 留口子给 future role=manager 改成 manager_or_above
        return {"departments": [d.to_dict() for d in depts]}

    @router.get("/departments/{name}")
    async def get_department(
        name: str,
        caller: CallerContext = Depends(require_admin_or_above),
    ) -> dict[str, Any]:
        from .departments import get_global_registry  # noqa: PLC0415

        dept = await get_global_registry().get(name)
        if dept is None:
            raise HTTPException(status_code=404, detail=f"dept {name} 不存在")
        return {"department": dept.to_dict()}

    @router.put("/departments/{name}")
    async def update_department(
        name: str,
        req: "UpdateDeptReq",
        caller: CallerContext = Depends(require_admin_or_above),
    ) -> dict[str, Any]:
        from .departments import get_global_registry  # noqa: PLC0415

        reg = get_global_registry()
        ok, err = await reg.update(
            name,
            by_email=caller.sub,
            allowed_models=req.allowed_models,
            allowed_tools=req.allowed_tools,
            allowed_skills=req.allowed_skills,
            quota_models_day=req.quota_models_day if req.quota_models_day is not None else -1,
            description=req.description,
        )
        if not ok:
            raise HTTPException(status_code=400, detail=err)
        dept = await reg.get(name)
        return {"ok": True, "department": dept.to_dict() if dept else None}

    return router


class UpdateDeptReq(BaseModel):
    """BL-RBAC-DAY7 (5/17): admin /admin/departments/{name} PUT body.

    所有字段 optional, None 跳过. allowed_* 是 list[str] (空 = 全允许).
    quota_models_day 0 = 不限, -1 sentinel 跳过 (但 client 应该不传 -1, 直接不传).
    """
    allowed_models: list[str] | None = None
    allowed_tools: list[str] | None = None
    allowed_skills: list[str] | None = None
    quota_models_day: int | None = None
    description: str | None = None
