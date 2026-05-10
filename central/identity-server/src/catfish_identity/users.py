"""User registry — YAML 配置文件 + bcrypt 密码验证.

# 设计

Phase 1B-1: 静态 YAML 文件加载用户. 5 月 demo 演示够 (写死 3-5 个 user).

Phase 2 加:
  - LDAP 接入
  - 飞书 / 钉钉 / 企微 接入
  - 客户自己 SSO 接入 (那时候 catfish-identity 自己关掉, 走客户)

# YAML schema

```yaml
users:
  - email: chenhongbo@ffcs.cn
    password_hash: $2b$12$...    # bcrypt
    name: 陈鸿波
    department: engineering
    tier: admin
```

# 密码生成 (员工 / IT 用)

```bash
python -c 'import bcrypt; print(bcrypt.hashpw(b"my_password", bcrypt.gensalt()).decode())'
```

# 安全

  - 密码永远不存明文, 只存 bcrypt hash
  - 失败计数 / 锁账户 Phase 2 加
  - 改密码 Phase 2 加 (现在 IT 改 yaml 文件)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import bcrypt
import yaml

logger = logging.getLogger("catfish.identity.users")

#: Timing-safe dummy hash. 已知 user 时跑真 bcrypt 验证, 未知 user 也跑同样
#: 耗时的 bcrypt 验证 (这个 dummy hash), 防 attacker 通过响应时间推断 user 是否存在.
#: 启动时 bcrypt.hashpw 一次 (~100ms 一次, 仅启动时), 之后复用.
_TIMING_DUMMY_HASH: bytes = bcrypt.hashpw(
    b"timing-constant-placeholder", bcrypt.gensalt(rounds=12)
)


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
        """渲染成 OIDC ID Token 的 claims (不含密码 hash).

        加 RBAC 字段 role / managed_departments, gateway / Companion 直接验.
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
        }


def _default_users_path() -> Path:
    """默认 users.yaml 位置. 客户可以 env CATFISH_IDENTITY_USERS_PATH 覆盖."""
    if env := os.environ.get("CATFISH_IDENTITY_USERS_PATH"):
        return Path(env).expanduser()
    # 开发 / 默认: 项目 config 目录
    return Path(__file__).resolve().parent.parent.parent / "config" / "users.yaml"


class UserRegistry:
    """用户注册表 + 验证.

    每次实例化重读 YAML, 不缓存到内存 (改 yaml 不用重启 — 但实际用 watcher
    监听文件 mtime 更优, Phase 2 加).

    实际上每次 verify_password 重读 YAML 太慢 (有几十用户 + bcrypt 100ms 验证).
    这里折中: 实例化时读一次, 持久缓存. 重启进程才生效新 yaml.
    Phase 2 加 file watcher hot-reload.
    """

    def __init__(self, users_path: Path | None = None) -> None:
        self.users_path = users_path or _default_users_path()
        self._users: dict[str, IdentityUser] = {}
        self.reload()

    def reload(self) -> None:
        """从 YAML 重新加载用户列表."""
        if not self.users_path.exists():
            logger.warning(
                "users.yaml 不存在 (%s), 注册表为空. 启动 catfish-identity 之前请确认.",
                self.users_path,
            )
            self._users = {}
            return

        with open(self.users_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        raw_users = data.get("users", []) or []
        loaded: dict[str, IdentityUser] = {}
        for raw in raw_users:
            if not isinstance(raw, dict):
                logger.warning("跳过非 dict 的 user 条目: %r", raw)
                continue
            email = raw.get("email", "").strip().lower()
            password_hash = raw.get("password_hash", "")
            if not email or not password_hash:
                logger.warning("跳过缺 email/password_hash 的 user: %r", raw)
                continue
            # role 安全: 只接受白名单值, 防 yaml 误填
            # BL-ARCH1 P1 (5/10): + sysadmin (系统管理员一档, 超 admin)
            role = str(raw.get("role", "")).strip().lower()
            if role and role not in ("sysadmin", "admin", "manager", "employee"):
                logger.warning(
                    "user %s role=%s 不在白名单, fallback 到 employee", email, role
                )
                role = ""
            managed = raw.get("managed_departments") or []
            if not isinstance(managed, list):
                managed = []
            loaded[email] = IdentityUser(
                email=email,
                password_hash=password_hash,
                name=raw.get("name", ""),
                department=raw.get("department", ""),
                tier=raw.get("tier", "employee"),
                role=role,
                managed_departments=[str(d) for d in managed],
            )
        self._users = loaded
        logger.info(
            "users.yaml 加载: %d 个用户 (path=%s)", len(loaded), self.users_path
        )

    async def seed_pg_from_yaml_if_empty(self) -> int:
        """首次启动 PG 是空的, 把 yaml 加载的 users 灌进 PG.

        返插入了多少条. 0 = PG 已有数据 (跳过) / PG 不可用. 幂等.
        """
        from .db import get_pool  # noqa: PLC0415

        pool = await get_pool()
        if pool is None:
            return 0
        try:
            async with pool.acquire() as conn:
                count = await conn.fetchval("SELECT COUNT(*) FROM users")
                if count and count > 0:
                    return 0  # PG 已有数据, 不动

                if not self._users:
                    return 0  # yaml 也空

                import json as _json  # noqa: PLC0415
                for u in self._users.values():
                    await conn.execute(
                        "INSERT INTO users (email, password_hash, name, department, "
                        "tier, role, managed_departments) "
                        "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)",
                        u.email, u.password_hash, u.name, u.department,
                        u.tier, u.role, _json.dumps(u.managed_departments),
                    )
                logger.info("PG users 首次 seed: 从 yaml 灌 %d 条", len(self._users))
                return len(self._users)
        except Exception as e:
            logger.warning("seed_pg_from_yaml 失败: %s", e)
            return 0

    async def reload_from_pg(self) -> bool:
        """从 PG users 表加载, 覆盖现有内存 dict.

        catfish-identity app startup 时调一次. PG 没配置 / 失败时返 False,
        保留 yaml 加载的结果.

        五一 sprint 5/4 (BL-D17 部分): 中央用户存 PG, yaml 留 dev/test fallback.
        """
        from .db import get_pool  # noqa: PLC0415

        pool = await get_pool()
        if pool is None:
            return False
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT email, password_hash, name, department, tier, role, "
                    "managed_departments, locked, locked_at, locked_by, deleted_at, "
                    "created_at, created_by, last_login_at, must_change_password "
                    "FROM users"
                )
        except Exception as e:
            logger.warning("PG users 加载失败, 保留 yaml: %s", e)
            return False

        loaded: dict[str, IdentityUser] = {}
        for row in rows:
            managed = row["managed_departments"]
            # asyncpg 的 jsonb 字段返 list 或 str, 兼容
            if isinstance(managed, str):
                import json as _json  # noqa: PLC0415
                managed = _json.loads(managed)
            if not isinstance(managed, list):
                managed = []
            email = (row["email"] or "").strip().lower()
            if not email or not row["password_hash"]:
                continue
            loaded[email] = IdentityUser(
                email=email,
                password_hash=row["password_hash"],
                name=row["name"] or "",
                department=row["department"] or "",
                tier=row["tier"] or "employee",
                role=row["role"] or "",
                managed_departments=[str(d) for d in managed],
                # BL-ARCH1 P1 (5/10) admin 字段
                locked=bool(row.get("locked", False)),
                locked_at=row["locked_at"].isoformat() if row.get("locked_at") else None,
                locked_by=row.get("locked_by"),
                deleted_at=row["deleted_at"].isoformat() if row.get("deleted_at") else None,
                created_at=row["created_at"].isoformat() if row.get("created_at") else None,
                created_by=row.get("created_by") or "system",
                last_login_at=row["last_login_at"].isoformat() if row.get("last_login_at") else None,
                must_change_password=bool(row.get("must_change_password", False)),
            )
        self._users = loaded
        logger.info("PG users 加载: %d 个用户 (覆盖 yaml)", len(loaded))
        return True

    def find(self, email: str) -> IdentityUser | None:
        """按 email 查 user. email 大小写不敏感."""
        if not email:
            return None
        return self._users.get(email.strip().lower())

    def verify_password(self, email: str, password: str) -> IdentityUser | None:
        """验证 email + password. 通过返 user, 失败返 None.

        bcrypt 比对耗时常数 (~100ms 默认), 防 timing attack.
        BL-ARCH1 P1 (5/10): 加 locked / deleted_at 检查.
        """
        user = self.find(email)
        if user is None:
            # 即使 user 不存在, 也走一次 bcrypt 比对 (用合法的 dummy hash),
            # 避免 timing attack 暴露 user 是否存在.
            bcrypt.checkpw(b"dummy", _TIMING_DUMMY_HASH)
            return None
        if user.locked:
            bcrypt.checkpw(b"dummy", _TIMING_DUMMY_HASH)
            logger.info("用户 %s 已锁, 拒绝登录", email)
            return None
        if user.deleted_at:
            bcrypt.checkpw(b"dummy", _TIMING_DUMMY_HASH)
            logger.info("用户 %s 已删除, 拒绝登录", email)
            return None
        try:
            ok = bcrypt.checkpw(
                password.encode("utf-8"), user.password_hash.encode("utf-8")
            )
        except (ValueError, TypeError):
            # password_hash 格式坏了 (yaml 配错)
            logger.warning("用户 %s 的 password_hash 格式不对", email)
            return None
        return user if ok else None

    def __len__(self) -> int:
        return len(self._users)

    # ── BL-ARCH1 P1 (5/10) admin CRUD ──────────────────────────────

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
                        "created_at, created_by, last_login_at, must_change_password "
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
    ) -> tuple[bool, str]:
        """改 user 元信息. 不动密码 / 锁状态 — 那俩走专门 endpoint."""
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


def hash_password(password: str) -> str:
    """工具函数: 生成 bcrypt hash. IT 给员工改密码时用.

    用法 (CLI):
        python -c 'from catfish_identity.users import hash_password; print(hash_password("my_pwd"))'
    """
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
