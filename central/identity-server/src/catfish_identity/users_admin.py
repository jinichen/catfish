"""UserRegistry 的 admin CRUD —— 建/改/锁/删/改密/审计日志。

8/15 从 users.py 搬出来 (832 行, 过了 CLAUDE.md §1 的 800 红线)。

# 为什么是 mixin 而不是独立函数

UserRegistry 是一个 756 行的类。要把一半方法搬走又不动任何调用点, mixin 是
唯一不改语义的做法:

  · `self.verify_password(...)` 这类兄弟方法调用照常 (MRO 兜住)
  · `registry.create_user(...)` 这类外部调用点一个字不用改
  · `monkeypatch.setattr(UserRegistry, "xxx", ...)` 这种打法也照常

改成模块级函数就得把 self 拆成参数, 那是**接口改动**, 不是拆分。

# 怎么分的这一刀

    users.py       载入与查询  __init__ / reload / seed_pg_from_yaml_if_empty
                              / reload_from_pg / find / verify_password / __len__
    本文件          admin CRUD  list_users / create_user / update_user / lock_user
                              / delete_user / reset_password / change_password
                              / list_audit

分界是"**谁在用**": 上面那组是 routes.py / routes_token.py 每次登录都要走的
热路径; 下面这组只有 admin_router.py 调, 是 IT 管理员的低频操作。两组共享的
状态只有 `self._users` 一个 dict, 和一个 `self.verify_password` 调用
(change_password 要先验旧密码)。

# ⚠ `from .db import get_pool` 必须留在函数体里

下面每个方法里都有一行:

    from .db import get_pool  # noqa: PLC0415

看着像是能提到模块顶部的样板代码, **不能提**。tests/test_change_password.py
是这么写的:

    monkeypatch.setattr("catfish_identity.db.get_pool", _fake_get_pool)

它打的是 **db 模块上的那个绑定**。函数体内 import 是在**调用时**才去 db 取,
所以取到的是被 patch 过的假货。提到模块顶部就变成了 import 时的快照, patch
再也影响不到。

⚠ 这个坑**在没装 asyncpg 的机器上是隐形的** —— 那里真 get_pool() 走 fallback
也返 None, 跟桩一样, 测试照样绿。只有在本机 (PG 跑在 5432) 才现形, 而且现形
的样子是一条看不出根因的 "another operation is in progress"。
所以拦它的是 tests/test_users_split_layering.py 里的**静态**守卫, 不是行为测试。

(这跟同一天在 catfish-cli 上踩的是同一条: `from X import name` 建的是新绑定
不是别名。那边是 patch 打错模块, 这边是 import 提错时机, 病根一样。)

`import json as _json` 同理留在原地 —— 那个是纯样板, 提上来没害处, 但既然
是纯搬运就不动。
"""
from __future__ import annotations

import logging

import bcrypt

from .users_models import IdentityUser

logger = logging.getLogger("catfish.identity.users")


class UserAdminMixin:
    """admin CRUD 的一半。别单独实例化 —— 它靠 UserRegistry 提供
    `self._users` 和 `self.verify_password`。"""

    # 让类型检查和读代码的人知道这两个是从哪来的 (由 UserRegistry 提供)。
    _users: dict[str, IdentityUser]

    async def list_users(
        self,
        *,
        include_deleted: bool = False,
        department_filter: str | None = None,
        role_filter: str | None = None,
    ) -> list[IdentityUser]:
        """列所有 user. PG 优先 (有完整字段), 内存 fallback (yaml only).

        admin UI 用, 支持按部门 / role 过滤.
        """
        from .db import get_pool  # noqa: PLC0415
        import json as _json  # noqa: PLC0415

        pool = await get_pool()
        if pool is not None:
            try:
                async with pool.acquire() as conn:
                    sql = (
                        "SELECT email, password_hash, name, department, tier, role, "
                        "managed_departments, locked, locked_at, locked_by, deleted_at, "
                        "created_at, created_by, last_login_at, must_change_password, "
                        # BL-RBAC-DAY3A (5/17) allowed_models + DAY4 allowed_tools
                        # + DAY5 allowed_skills (一行 SELECT 三个 RBAC 维度)
                        "allowed_models, allowed_tools, allowed_skills "
                        "FROM users"
                    )
                    where = []
                    params = []
                    if not include_deleted:
                        where.append("deleted_at IS NULL")
                    if department_filter:
                        where.append(f"department = ${len(params) + 1}")
                        params.append(department_filter)
                    if role_filter:
                        where.append(f"(role = ${len(params) + 1} OR (role = '' AND tier = ${len(params) + 1}))")
                        params.append(role_filter)
                    if where:
                        sql += " WHERE " + " AND ".join(where)
                    sql += " ORDER BY created_at DESC NULLS LAST, email"
                    rows = await conn.fetch(sql, *params)
            except Exception as e:
                logger.warning("list_users PG 失败 (fallback 内存): %s", e)
                rows = []
            if rows:
                out = []
                for r in rows:
                    managed = r["managed_departments"]
                    if isinstance(managed, str):
                        managed = _json.loads(managed)
                    if not isinstance(managed, list):
                        managed = []
                    # BL-RBAC-DAY3A: allowed_models 解析
                    am = r.get("allowed_models")
                    if isinstance(am, str):
                        try:
                            am = _json.loads(am)
                        except Exception:
                            am = None
                    if am is not None and not isinstance(am, list):
                        am = None
                    # BL-RBAC-DAY4: allowed_tools 解析
                    at = r.get("allowed_tools")
                    if isinstance(at, str):
                        try:
                            at = _json.loads(at)
                        except Exception:
                            at = None
                    if at is not None and not isinstance(at, list):
                        at = None
                    # BL-RBAC-DAY5: allowed_skills 解析
                    ask = r.get("allowed_skills")
                    if isinstance(ask, str):
                        try:
                            ask = _json.loads(ask)
                        except Exception:
                            ask = None
                    if ask is not None and not isinstance(ask, list):
                        ask = None
                    out.append(IdentityUser(
                        email=r["email"], password_hash=r["password_hash"],
                        name=r["name"] or "", department=r["department"] or "",
                        tier=r["tier"] or "employee", role=r["role"] or "",
                        managed_departments=[str(d) for d in managed],
                        locked=bool(r["locked"]),
                        locked_at=r["locked_at"].isoformat() if r["locked_at"] else None,
                        locked_by=r["locked_by"],
                        deleted_at=r["deleted_at"].isoformat() if r["deleted_at"] else None,
                        created_at=r["created_at"].isoformat() if r["created_at"] else None,
                        created_by=r["created_by"] or "system",
                        last_login_at=r["last_login_at"].isoformat() if r["last_login_at"] else None,
                        must_change_password=bool(r["must_change_password"]),
                        allowed_models=[str(m) for m in am] if isinstance(am, list) else None,
                        allowed_tools=[str(t) for t in at] if isinstance(at, list) else None,
                        allowed_skills=[str(s) for s in ask] if isinstance(ask, list) else None,
                    ))
                return out
        # 内存 fallback (yaml only, 没 PG 时)
        users = list(self._users.values())
        if department_filter:
            users = [u for u in users if u.department == department_filter]
        if role_filter:
            users = [u for u in users if u.effective_role() == role_filter]
        return users

    async def create_user(
        self,
        *,
        email: str,
        password: str,
        name: str = "",
        department: str = "",
        role: str = "employee",
        managed_departments: list[str] | None = None,
        created_by: str = "system",
        must_change_password: bool = True,
    ) -> tuple[bool, str]:
        """创建新 user. 写 PG + 内存. 返 (ok, error_msg).

        密码现场 bcrypt hash. 默认 must_change_password=True 强制首次改密.
        """
        from .db import get_pool  # noqa: PLC0415
        import json as _json  # noqa: PLC0415

        email = email.strip().lower()
        if not email or "@" not in email:
            return False, "email 不合法"
        if email in self._users:
            existing = self._users[email]
            if existing.deleted_at is None:
                return False, f"email {email} 已存在"
        if role and role not in ("sysadmin", "admin", "manager", "employee"):
            return False, f"role {role} 不在白名单"
        if not password or len(password) < 8:
            return False, "密码至少 8 位"

        password_hash = bcrypt.hashpw(
            password.encode("utf-8"), bcrypt.gensalt(rounds=12)
        ).decode("utf-8")
        managed = managed_departments or []
        # tier 跟 role 同步 (兼容老 OIDC claims)
        tier = role if role in ("sysadmin", "admin") else "employee"

        pool = await get_pool()
        if pool is not None:
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO users (email, password_hash, name, department, "
                        "tier, role, managed_departments, created_by, must_change_password) "
                        "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9)",
                        email, password_hash, name, department, tier, role,
                        _json.dumps(managed), created_by, must_change_password,
                    )
                    await conn.execute(
                        "INSERT INTO users_audit (ts_ms, action, target_email, by_email, meta) "
                        "VALUES ((EXTRACT(EPOCH FROM NOW()) * 1000)::BIGINT, "
                        "'create', $1, $2, $3::jsonb)",
                        email, created_by, _json.dumps({"role": role, "department": department}),
                    )
            except Exception as e:
                return False, f"PG 写失败: {e}"

        self._users[email] = IdentityUser(
            email=email, password_hash=password_hash, name=name,
            department=department, tier=tier, role=role,
            managed_departments=[str(d) for d in managed],
            created_by=created_by,
            must_change_password=must_change_password,
        )
        return True, ""

    async def update_user(
        self,
        target_email: str,
        *,
        by_email: str,
        name: str | None = None,
        department: str | None = None,
        role: str | None = None,
        managed_departments: list[str] | None = None,
        # BL-RBAC-DAY7 (5/17): per-user RBAC 维度 override (跟 user.allowed_*
        # field 对位). 传 None 跳过, [] = 用户级 override 解锁, [m1, m2] = 收紧.
        # 改写"unset" (回继承 dept) 用一个 sentinel: 不传该字段 = 不动,
        # 传 "__inherit__" = 改成 NULL (回继承 dept) — UI 显式触发.
        allowed_models: list[str] | str | None = None,
        allowed_tools: list[str] | str | None = None,
        allowed_skills: list[str] | str | None = None,
    ) -> tuple[bool, str]:
        """改 user 元信息. 不动密码 / 锁状态 — 那俩走专门 endpoint.

        BL-RBAC-DAY7 (5/17): allowed_models / allowed_tools / allowed_skills 三维
        可改, 跟 user.allowed_* field 对位. 传 None 跳过, "__inherit__" → NULL
        (回继承 dept), list → 写入 (空 list 也是 override 解锁).
        """
        from .db import get_pool  # noqa: PLC0415
        import json as _json  # noqa: PLC0415

        target_email = target_email.strip().lower()
        user = self._users.get(target_email)
        if user is None or user.deleted_at:
            return False, f"用户 {target_email} 不存在 / 已删"
        if role and role not in ("sysadmin", "admin", "manager", "employee"):
            return False, f"role {role} 不在白名单"

        sets = []
        params = []
        meta: dict = {}
        if name is not None and name != user.name:
            sets.append(f"name = ${len(params) + 1}")
            params.append(name)
            meta["name"] = name
            user.name = name
        if department is not None and department != user.department:
            sets.append(f"department = ${len(params) + 1}")
            params.append(department)
            meta["department"] = department
            user.department = department
        if role is not None and role != user.role:
            tier = role if role in ("sysadmin", "admin") else "employee"
            sets.append(f"role = ${len(params) + 1}")
            params.append(role)
            sets.append(f"tier = ${len(params) + 1}")
            params.append(tier)
            meta["role"] = role
            user.role = role
            user.tier = tier
        if managed_departments is not None:
            sets.append(f"managed_departments = ${len(params) + 1}::jsonb")
            params.append(_json.dumps(managed_departments))
            meta["managed_departments"] = managed_departments
            user.managed_departments = [str(d) for d in managed_departments]

        # BL-RBAC-DAY7 (5/17): allowed_* 三维更新.
        # None = 不动. "__inherit__" = NULL (回继承 dept). list = override.
        def _resolve_allowed(value, field_name):
            """返 (sql_value, py_value) 或 None (跳过)."""
            if value is None:
                return None
            if value == "__inherit__":
                return ("NULL", None)
            if isinstance(value, list):
                return (f"${len(params) + 1}::jsonb", [str(v) for v in value])
            return None  # 容错: 非法值跳过

        for field_name, raw_value in (
            ("allowed_models", allowed_models),
            ("allowed_tools", allowed_tools),
            ("allowed_skills", allowed_skills),
        ):
            resolved = _resolve_allowed(raw_value, field_name)
            if resolved is None:
                continue
            sql_val, py_val = resolved
            if sql_val == "NULL":
                sets.append(f"{field_name} = NULL")
                meta[field_name] = None
                setattr(user, field_name, None)
            else:
                sets.append(f"{field_name} = {sql_val}")
                params.append(_json.dumps(py_val))
                meta[field_name] = py_val
                setattr(user, field_name, py_val)

        if not sets:
            return True, ""

        sets.append("updated_at = NOW()")
        pool = await get_pool()
        if pool is not None:
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        f"UPDATE users SET {', '.join(sets)} WHERE email = ${len(params) + 1}",
                        *params, target_email,
                    )
                    await conn.execute(
                        "INSERT INTO users_audit (ts_ms, action, target_email, by_email, meta) "
                        "VALUES ((EXTRACT(EPOCH FROM NOW()) * 1000)::BIGINT, "
                        "'update', $1, $2, $3::jsonb)",
                        target_email, by_email, _json.dumps(meta),
                    )
            except Exception as e:
                return False, f"PG 写失败: {e}"
        return True, ""

    async def lock_user(
        self,
        target_email: str,
        *,
        by_email: str,
        locked: bool,
    ) -> tuple[bool, str]:
        """锁 / 解锁 user. locked=True 后 verify_password 拒登."""
        from .db import get_pool  # noqa: PLC0415
        import json as _json  # noqa: PLC0415

        target_email = target_email.strip().lower()
        user = self._users.get(target_email)
        if user is None or user.deleted_at:
            return False, f"用户 {target_email} 不存在"
        # 防自锁 sysadmin
        if user.effective_role() == "sysadmin" and locked:
            sysadmins = [u for u in self._users.values()
                         if u.effective_role() == "sysadmin" and not u.deleted_at and not u.locked]
            if len(sysadmins) <= 1 and target_email in [u.email for u in sysadmins]:
                return False, "不能锁最后一个 sysadmin (防自锁)"

        pool = await get_pool()
        if pool is not None:
            try:
                async with pool.acquire() as conn:
                    if locked:
                        await conn.execute(
                            "UPDATE users SET locked = TRUE, locked_at = NOW(), locked_by = $1 "
                            "WHERE email = $2",
                            by_email, target_email,
                        )
                    else:
                        await conn.execute(
                            "UPDATE users SET locked = FALSE, locked_at = NULL, locked_by = NULL "
                            "WHERE email = $1",
                            target_email,
                        )
                    await conn.execute(
                        "INSERT INTO users_audit (ts_ms, action, target_email, by_email, meta) "
                        "VALUES ((EXTRACT(EPOCH FROM NOW()) * 1000)::BIGINT, $1, $2, $3, $4::jsonb)",
                        "lock" if locked else "unlock",
                        target_email, by_email, _json.dumps({}),
                    )
            except Exception as e:
                return False, f"PG 写失败: {e}"
        user.locked = locked
        user.locked_by = by_email if locked else None
        return True, ""

    async def delete_user(
        self,
        target_email: str,
        *,
        by_email: str,
    ) -> tuple[bool, str]:
        """软删 user. 设 deleted_at, 不真删 row (audit 链不能断)."""
        from .db import get_pool  # noqa: PLC0415
        import json as _json  # noqa: PLC0415

        target_email = target_email.strip().lower()
        user = self._users.get(target_email)
        if user is None or user.deleted_at:
            return False, f"用户 {target_email} 不存在 / 已删"
        # 防删 sysadmin
        if user.effective_role() == "sysadmin":
            sysadmins = [u for u in self._users.values()
                         if u.effective_role() == "sysadmin" and not u.deleted_at]
            if len(sysadmins) <= 1:
                return False, "不能删最后一个 sysadmin (系统至少留一个)"

        pool = await get_pool()
        if pool is not None:
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE users SET deleted_at = NOW() WHERE email = $1",
                        target_email,
                    )
                    await conn.execute(
                        "INSERT INTO users_audit (ts_ms, action, target_email, by_email, meta) "
                        "VALUES ((EXTRACT(EPOCH FROM NOW()) * 1000)::BIGINT, "
                        "'delete', $1, $2, $3::jsonb)",
                        target_email, by_email, _json.dumps({}),
                    )
            except Exception as e:
                return False, f"PG 写失败: {e}"
        from datetime import datetime, timezone
        user.deleted_at = datetime.now(timezone.utc).isoformat()
        return True, ""

    async def reset_password(
        self,
        target_email: str,
        *,
        by_email: str,
        new_password: str,
        force_change: bool = True,
    ) -> tuple[bool, str]:
        """重置密码. admin / sysadmin 给员工换临时密码, 默认强制下次登录改."""
        from .db import get_pool  # noqa: PLC0415
        import json as _json  # noqa: PLC0415

        target_email = target_email.strip().lower()
        user = self._users.get(target_email)
        if user is None or user.deleted_at:
            return False, f"用户 {target_email} 不存在"
        if not new_password or len(new_password) < 8:
            return False, "新密码至少 8 位"

        new_hash = bcrypt.hashpw(
            new_password.encode("utf-8"), bcrypt.gensalt(rounds=12)
        ).decode("utf-8")

        pool = await get_pool()
        if pool is not None:
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE users SET password_hash = $1, password_changed_at = NOW(), "
                        "must_change_password = $2 WHERE email = $3",
                        new_hash, force_change, target_email,
                    )
                    await conn.execute(
                        "INSERT INTO users_audit (ts_ms, action, target_email, by_email, meta) "
                        "VALUES ((EXTRACT(EPOCH FROM NOW()) * 1000)::BIGINT, "
                        "'reset_password', $1, $2, $3::jsonb)",
                        target_email, by_email, _json.dumps({"force_change": force_change}),
                    )
            except Exception as e:
                return False, f"PG 写失败: {e}"
        user.password_hash = new_hash
        user.must_change_password = force_change
        return True, ""

    async def change_password(
        self,
        email: str,
        *,
        old_password: str,
        new_password: str,
    ) -> tuple[bool, str]:
        """BL-SELF-CHANGE-PASSWORD (7/20 鸿波 catch 达华 POC 员工无自主改密):
        员工自己改密码. 需验 old_password + hash new_password + 设
        must_change_password=False (首次登录改完不再强制).

        跟 reset_password (admin 干) 区别: 需 old_password verify. audit
        标 action='self_change_password' 便区分 (admin reset vs 员工自主).
        """
        from .db import get_pool  # noqa: PLC0415
        import json as _json  # noqa: PLC0415

        email = email.strip().lower()
        user = self._users.get(email)
        if user is None or user.deleted_at:
            return False, f"用户 {email} 不存在"

        # 1. verify old_password
        verified = self.verify_password(email, old_password)
        if verified is None:
            return False, "旧密码错"

        # 2. 校验 new_password 强度
        if not new_password or len(new_password) < 8:
            return False, "新密码至少 8 位"
        if new_password == old_password:
            return False, "新密码不能与旧密码相同"

        # 3. hash + 写 db
        new_hash = bcrypt.hashpw(
            new_password.encode("utf-8"), bcrypt.gensalt(rounds=12)
        ).decode("utf-8")

        pool = await get_pool()
        if pool is not None:
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE users SET password_hash = $1, password_changed_at = NOW(), "
                        "must_change_password = FALSE WHERE email = $2",
                        new_hash, email,
                    )
                    await conn.execute(
                        "INSERT INTO users_audit (ts_ms, action, target_email, by_email, meta) "
                        "VALUES ((EXTRACT(EPOCH FROM NOW()) * 1000)::BIGINT, "
                        "'self_change_password', $1, $2, $3::jsonb)",
                        email, email, _json.dumps({"self_service": True}),
                    )
            except Exception as e:
                return False, f"PG 写失败: {e}"

        # 4. 更新内存
        user.password_hash = new_hash
        user.must_change_password = False
        return True, ""

    async def list_audit(self, limit: int = 100) -> list[dict]:
        """查 users_audit 表 (admin 看历史). PG only."""
        from .db import get_pool  # noqa: PLC0415

        pool = await get_pool()
        if pool is None:
            return []
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT ts_ms, action, target_email, by_email, meta "
                    "FROM users_audit ORDER BY ts_ms DESC LIMIT $1",
                    limit,
                )
        except Exception as e:
            logger.warning("list_audit 失败: %s", e)
            return []
        out = []
        for r in rows:
            meta = r["meta"] or {}
            if isinstance(meta, str):
                import json as _json  # noqa: PLC0415
                try:
                    meta = _json.loads(meta)
                except Exception:
                    meta = {}
            out.append({
                "ts_ms": r["ts_ms"],
                "action": r["action"],
                "target_email": r["target_email"],
                "by_email": r["by_email"],
                "meta": meta,
            })
        return out
