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



# 5/21 拆: IdentityUser dataclass 抽到 users_models.py
from .users_admin import UserAdminMixin
from .users_models import IdentityUser  # noqa: F401

def _default_users_path() -> Path:
    """默认 users.yaml 位置. 客户可以 env CATFISH_IDENTITY_USERS_PATH 覆盖."""
    if env := os.environ.get("CATFISH_IDENTITY_USERS_PATH"):
        return Path(env).expanduser()
    # 开发 / 默认: 项目 config 目录
    return Path(__file__).resolve().parent.parent.parent / "config" / "users.yaml"


class UserRegistry(UserAdminMixin):
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
                    "created_at, created_by, last_login_at, must_change_password, "
                    # BL-RBAC-DAY3A (5/17): allowed_models per-user 白名单
                    "allowed_models, "
                    # BL-RBAC-DAY4 (5/17): allowed_tools per-user 白名单
                    "allowed_tools, "
                    # BL-RBAC-DAY5 (5/17): allowed_skills per-user 白名单
                    "allowed_skills "
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
            # BL-RBAC-DAY3A (5/17): allowed_models 解析. NULL → None (继承 dept).
            am = row.get("allowed_models")
            if isinstance(am, str):
                import json as _json  # noqa: PLC0415
                try:
                    am = _json.loads(am)
                except Exception:
                    am = None
            if am is not None and not isinstance(am, list):
                am = None
            # BL-RBAC-DAY4 (5/17): allowed_tools 解析 (同 allowed_models 套路)
            at = row.get("allowed_tools")
            if isinstance(at, str):
                import json as _json  # noqa: PLC0415
                try:
                    at = _json.loads(at)
                except Exception:
                    at = None
            if at is not None and not isinstance(at, list):
                at = None
            # BL-RBAC-DAY5 (5/17): allowed_skills 解析
            ask = row.get("allowed_skills")
            if isinstance(ask, str):
                import json as _json  # noqa: PLC0415
                try:
                    ask = _json.loads(ask)
                except Exception:
                    ask = None
            if ask is not None and not isinstance(ask, list):
                ask = None
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
                # BL-RBAC-DAY3A: per-user model 白名单 (None = 继承 dept)
                allowed_models=[str(m) for m in am] if isinstance(am, list) else None,
                # BL-RBAC-DAY4: per-user tools 白名单 (None = 继承 dept)
                allowed_tools=[str(t) for t in at] if isinstance(at, list) else None,
                # BL-RBAC-DAY5: per-user skills 白名单 (None = 继承 dept)
                allowed_skills=[str(s) for s in ask] if isinstance(ask, list) else None,
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

    # ── admin CRUD 在 users_admin.UserAdminMixin (8/15 拆) ──────────
    #
    # list_users / create_user / update_user / lock_user / delete_user /
    # reset_password / change_password / list_audit 八个方法搬去了那边。
    # 分界是"谁在用": 本文件剩下的是每次登录都走的热路径 (routes.py /
    # routes_token.py), 搬走的那组只有 admin_router.py 调。
    #
    # 用 mixin 不用模块级函数, 是为了 `registry.create_user(...)` 这些调用点
    # 一个字都不用改。详见 users_admin.py 的模块 docstring。


def hash_password(password: str) -> str:
    """工具函数: 生成 bcrypt hash. IT 给员工改密码时用.

    用法 (CLI):
        python -c 'from catfish_identity.users import hash_password; print(hash_password("my_pwd"))'
    """
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
