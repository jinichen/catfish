"""配额查询与管理路由 —— 从 app.py 拆出 (8/15)。

员工侧 /api/quota/me · 部门 /api/quota/department/{d} · 全局 /api/quota/global
管理侧 /api/admin/quota/* (config / defaults / per_model / per_department /
overrides) · 运维侧 /api/inflight

`api_inflight` 归到这里不是因为它是配额, 是因为它跟这一组共用
`_require_sysadmin` —— 扫过 app.py 全部顶层函数, 用那个守卫的只有本组
再加它一个。留在 app.py 就得两边各一份 (或者跨模块引), 不如一起走。
语义上也说得通: 两者都是 sysadmin 的运维视角。

五个 pydantic body 模型 (_DeptQuotaBody 等) 只被本组用, 一并搬。
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel as _BaseModel

from .auth import User, get_current_user, resolve_effective_user_email
# `api_quota_global` 用 admin 守卫 (不是 sysadmin) —— 那个函数跟审计那组
# 一起搬去了 audit_router。共用一份而不是再抄一份: 它俩的 403 文案本来
# 就是同一句 ("不能访问全局聚合"), 跟 facts/advisory 那两份不同 ——
# 那两份是各写各的领域文案, 所以维持独立; 这里是真·同一个东西。
from .audit_router import _require_admin  # noqa: F401



# 这几个是**无状态**的守卫 / body 模型 —— 只有路由本身需要 app,
# 它们不需要, 所以留在模块级。放进 register() 里就成了局部定义,
# 别的模块 import 不到 (quota_router 要引 audit_router._require_admin)。
class _DeptQuotaUpdate(_BaseModel):
    tokens_per_day: int

def _require_sysadmin(user: User) -> None:
    """quota 全局编辑只 sysadmin 能用 (改 default 影响全员)."""
    if user.role != "sysadmin":
        raise HTTPException(
            status_code=403,
            detail=f"role={user.role} 不能编辑 quota 配置 (sysadmin only)",
        )

class _PerUserDefaults(_BaseModel):
    tokens_per_minute: int
    tokens_per_day: int

class _ModelQuotaBody(_BaseModel):
    tokens_per_day: int

class _DeptQuotaBody(_BaseModel):
    tokens_per_day: int

class _UserOverrideBody(_BaseModel):
    tokens_per_minute: int
    tokens_per_day: int


def register_quota_routes(app) -> None:
    """把 quota 这一组路由挂到 app 上。

    用 register(app) 而不是 APIRouter —— 跟 admin_models_router /
    admin_providers_router 同一套 (app.py:621 那段注释定的规矩:
    "路由要挂在同一个 app 上, 没法用 from X import *, 所以改用显式 register")。
    """
    @app.get("/api/inflight")
    async def api_inflight(user: User = Depends(get_current_user)) -> dict[str, Any]:
        """当前在途的 SSE 流 —— 卡住时用来看"到底哪条卡着、卡了多久"。

        # 为什么补这个 (8/15)

        inflight_streams 从 5/12 起就在记每条流 (request_id / user / model /
        message_count / started_at), 它的 docstring 写的是 "cancel UI / ops 调试用",
        但**从来没有接出来**。于是 8/15 现场一条 deepseek 流卡在首 chunk 之前时,
        这份数据就在进程内存里躺着, 排查的人拿不到 —— 只能从日志里数"哪条请求没有
        收尾行"。信息在, 但看不见。

        elapsed_secs 是这里唯一新算的字段, 也正是卡住时最想知道的那个。
        按它倒序排, 最久的排最前。

        # 权限

        sysadmin only: 记录里带 user (员工邮箱) 和 model, 属于跨员工的运行状态。
        普通员工看自己的对话不需要这个接口。

        # 边界

        纯内存, 单实例 (见 inflight_streams 模块头)。gateway 重启后清零, 多 pod
        时只反映当前这个 pod —— 这两条都是那个模块本来就有的性质, 不是这里引入的。
        """
        _require_sysadmin(user)
        from . import inflight_streams  # noqa: PLC0415

        now = time.time()
        rows = []
        for r in inflight_streams.list_inflight():
            row = dict(r)
            started = row.get("started_at")
            row["elapsed_secs"] = round(now - started, 1) if isinstance(started, (int, float)) else None
            rows.append(row)
        rows.sort(key=lambda x: x.get("elapsed_secs") or 0, reverse=True)
        return {"count": len(rows), "now_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "inflight": rows}
    @app.get("/api/quota/me")
    async def quota_me(
        user: User = Depends(get_current_user),
        x_catfish_user: str | None = Header(default=None, alias="X-Catfish-User"),
    ) -> dict[str, Any]:
        from . import quota  # 懒 import 避免顶层循环

        # 5/23 BL-QUOTA-EFFECTIVE-USER (鸿波): chat 写 quota_events 时按 effective_user_email
        # (resolve 后真员工 chenhongbo@ffcs.cn). quota_me 之前用 user.sub 查, hermes service
        # token 时 sub=client:hermes-cli, 查到 0 → dashboard 永远显示 0/不限. 改用同款 resolve
        # 让"读"按"写"一致.
        effective_user = resolve_effective_user_email(user, x_catfish_user)

        config = quota.load_quota_config()
        user_q = config.per_user_for(effective_user)

        now_ms = int(time.time() * 1000)
        minute_cutoff = now_ms - 60_000
        day_cutoff = now_ms - 86_400_000

        used_minute = quota.sum_tokens_user_since(effective_user, minute_cutoff)
        used_day = quota.sum_tokens_user_since(effective_user, day_cutoff)

        dept_used_day = 0
        dept_limit_day = 0
        if user.department:
            dept_used_day = quota.sum_tokens_dept_since(user.department, day_cutoff)
            dept_q = config.department_quotas.get(user.department)
            if dept_q is not None:
                dept_limit_day = dept_q.tokens_per_day

        return {
            "user_email": effective_user,
            "department": user.department,
            "minute": {
                "used": used_minute,
                "limit": user_q.tokens_per_minute,  # 0 = 不限
            },
            "day": {
                "used": used_day,
                "limit": user_q.tokens_per_day,
            },
            "department_day": {
                "used": dept_used_day,
                "limit": dept_limit_day,
            },
        }
    @app.get("/api/quota/department/{department}")
    async def api_quota_department(
        department: str,
        user: User = Depends(get_current_user),
    ) -> dict[str, Any]:
        """部门级 quota 聚合 — manager 改 / 看本部门."""
        from . import quota  # 懒 import

        if not user.can_manage_department(department):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"role={user.role} 无权访问部门 {department} 的 quota. "
                    f"managed_departments={user.managed_departments}"
                ),
            )

        config = quota.load_quota_config()
        now_ms = int(time.time() * 1000)
        day_cutoff = now_ms - 86_400_000

        dept_used_day = quota.sum_tokens_dept_since(department, day_cutoff)
        dept_q = config.department_quotas.get(department)
        dept_limit_day = dept_q.tokens_per_day if dept_q else 0

        # Top 员工 (按今日用量)
        top_users = quota.top_users_in_department(department, day_cutoff, limit=10)

        return {
            "department": department,
            "day": {
                "used": dept_used_day,
                "limit": dept_limit_day,  # 0 = 不限
            },
            "top_users": top_users,  # [{user_email, tokens_used}]
            "viewer_role": user.role,
        }
    @app.put("/api/quota/department/{department}")
    async def api_quota_department_update(
        department: str,
        body: _DeptQuotaUpdate,
        user: User = Depends(get_current_user),
    ) -> dict[str, Any]:
        """改部门日 quota. RBAC: admin 全权 / manager 限 managed_departments."""
        from . import quota

        if not user.can_manage_department(department):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"role={user.role} 无权改部门 {department} quota. "
                    f"managed_departments={user.managed_departments}"
                ),
            )

        if body.tokens_per_day < 0:
            raise HTTPException(status_code=400, detail="tokens_per_day 不能负")

        ok = quota.update_department_quota(department, body.tokens_per_day)
        if not ok:
            raise HTTPException(status_code=500, detail="写 quotas.yaml 失败, 看 gateway log")

        return {
            "department": department,
            "tokens_per_day": body.tokens_per_day,
            "updated_by": user.sub,
            "ok": True,
        }
    @app.get("/api/admin/quota/config")
    async def api_admin_quota_config(
        user: User = Depends(get_current_user),
    ) -> dict[str, Any]:
        """返完整 quotas.yaml dict (defaults + overrides). sysadmin only."""
        from . import quota
        _require_sysadmin(user)
        return {
            "config": quota.get_full_config_dict(),
            "viewer_role": user.role,
        }
    @app.put("/api/admin/quota/defaults/per_user")
    async def api_admin_quota_default_per_user(
        body: _PerUserDefaults,
        user: User = Depends(get_current_user),
    ) -> dict[str, Any]:
        """改全员默认 per_user (defaults.per_user). sysadmin only."""
        from . import quota
        _require_sysadmin(user)
        ok, msg = quota.update_default_per_user(body.tokens_per_minute, body.tokens_per_day)
        if not ok:
            raise HTTPException(500, detail=msg or "写 quotas.yaml 失败")
        return {"ok": True, "updated_by": user.sub, **body.model_dump()}
    @app.put("/api/admin/quota/per_model/{name}")
    async def api_admin_quota_put_per_model(
        name: str,
        body: _ModelQuotaBody,
        user: User = Depends(get_current_user),
    ) -> dict[str, Any]:
        """加/改单 model quota (defaults.per_model.<name>). sysadmin only."""
        from . import quota
        _require_sysadmin(user)
        ok, msg = quota.put_per_model(name, body.tokens_per_day)
        if not ok:
            raise HTTPException(500, detail=msg or "写 quotas.yaml 失败")
        return {"ok": True, "name": name, "tokens_per_day": body.tokens_per_day}
    @app.delete("/api/admin/quota/per_model/{name}")
    async def api_admin_quota_delete_per_model(
        name: str,
        user: User = Depends(get_current_user),
    ) -> dict[str, Any]:
        """删 model quota. Idempotent. sysadmin only."""
        from . import quota
        _require_sysadmin(user)
        ok, msg = quota.delete_per_model(name)
        if not ok:
            raise HTTPException(500, detail=msg or "写失败")
        return {"ok": True, "name": name}
    @app.put("/api/admin/quota/per_department/{name}")
    async def api_admin_quota_put_per_department(
        name: str,
        body: _DeptQuotaBody,
        user: User = Depends(get_current_user),
    ) -> dict[str, Any]:
        """加/改部门默认 quota (defaults.per_department.<name>). sysadmin only."""
        from . import quota
        _require_sysadmin(user)
        ok, msg = quota.put_per_department(name, body.tokens_per_day)
        if not ok:
            raise HTTPException(500, detail=msg or "写 quotas.yaml 失败")
        return {"ok": True, "name": name, "tokens_per_day": body.tokens_per_day}
    @app.delete("/api/admin/quota/per_department/{name}")
    async def api_admin_quota_delete_per_department(
        name: str,
        user: User = Depends(get_current_user),
    ) -> dict[str, Any]:
        """删部门默认 quota. Idempotent. sysadmin only."""
        from . import quota
        _require_sysadmin(user)
        ok, msg = quota.delete_per_department(name)
        if not ok:
            raise HTTPException(500, detail=msg or "写失败")
        return {"ok": True, "name": name}
    @app.put("/api/admin/quota/overrides/users/{email}")
    async def api_admin_quota_put_user_override(
        email: str,
        body: _UserOverrideBody,
        user: User = Depends(get_current_user),
    ) -> dict[str, Any]:
        """加/改用户 override (overrides.users.<email>). sysadmin only."""
        from . import quota
        _require_sysadmin(user)
        ok, msg = quota.put_user_override(
            email, body.tokens_per_minute, body.tokens_per_day,
        )
        if not ok:
            raise HTTPException(400 if "email" in msg or "格式" in msg else 500, detail=msg)
        return {"ok": True, "email": email, **body.model_dump()}
    @app.delete("/api/admin/quota/overrides/users/{email}")
    async def api_admin_quota_delete_user_override(
        email: str,
        user: User = Depends(get_current_user),
    ) -> dict[str, Any]:
        """删用户 override. Idempotent. sysadmin only."""
        from . import quota
        _require_sysadmin(user)
        ok, msg = quota.delete_user_override(email)
        if not ok:
            raise HTTPException(500, detail=msg or "写失败")
        return {"ok": True, "email": email}
    @app.put("/api/admin/quota/overrides/departments/{name}")
    async def api_admin_quota_put_dept_override(
        name: str,
        body: _DeptQuotaBody,
        user: User = Depends(get_current_user),
    ) -> dict[str, Any]:
        """加/改部门 override (overrides.departments.<name>). 优先级高于 defaults."""
        from . import quota
        _require_sysadmin(user)
        ok, msg = quota.put_dept_override(name, body.tokens_per_day)
        if not ok:
            raise HTTPException(500, detail=msg or "写 quotas.yaml 失败")
        return {"ok": True, "name": name, "tokens_per_day": body.tokens_per_day}
    @app.delete("/api/admin/quota/overrides/departments/{name}")
    async def api_admin_quota_delete_dept_override(
        name: str,
        user: User = Depends(get_current_user),
    ) -> dict[str, Any]:
        """删部门 override. Idempotent. sysadmin only."""
        from . import quota
        _require_sysadmin(user)
        ok, msg = quota.delete_dept_override(name)
        if not ok:
            raise HTTPException(500, detail=msg or "写失败")
        return {"ok": True, "name": name}
    @app.get("/api/quota/global")
    async def api_quota_global(
        user: User = Depends(get_current_user),
    ) -> dict[str, Any]:
        """全员 quota 聚合 — admin 看 top 部门 / top 用户 / 总用量."""
        from . import quota
        _require_admin(user)

        now_ms = int(time.time() * 1000)
        day_cutoff = now_ms - 86_400_000

        return {
            "since_ms": day_cutoff,
            "top_departments": quota.top_departments(day_cutoff, limit=10),
            "viewer_role": user.role,
        }
