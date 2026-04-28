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
from dataclasses import dataclass
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
    """注册用户. 内部数据结构, 不直接 expose JSON."""

    email: str
    password_hash: str
    name: str = ""
    department: str = ""
    tier: str = "employee"  # 'employee' | 'admin'

    def to_oidc_claims(self) -> dict:
        """渲染成 OIDC ID Token 的 claims (不含密码 hash)."""
        return {
            "email": self.email,
            "email_verified": True,
            "name": self.name or self.email.split("@")[0],
            "department": self.department,
            "tier": self.tier,
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
            loaded[email] = IdentityUser(
                email=email,
                password_hash=password_hash,
                name=raw.get("name", ""),
                department=raw.get("department", ""),
                tier=raw.get("tier", "employee"),
            )
        self._users = loaded
        logger.info(
            "users.yaml 加载: %d 个用户 (path=%s)", len(loaded), self.users_path
        )

    def find(self, email: str) -> IdentityUser | None:
        """按 email 查 user. email 大小写不敏感."""
        if not email:
            return None
        return self._users.get(email.strip().lower())

    def verify_password(self, email: str, password: str) -> IdentityUser | None:
        """验证 email + password. 通过返 user, 失败返 None.

        bcrypt 比对耗时常数 (~100ms 默认), 防 timing attack.
        Phase 2: 加 失败计数 + 锁账户.
        """
        user = self.find(email)
        if user is None:
            # 即使 user 不存在, 也走一次 bcrypt 比对 (用合法的 dummy hash),
            # 避免 timing attack 暴露 user 是否存在.
            bcrypt.checkpw(b"dummy", _TIMING_DUMMY_HASH)
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


def hash_password(password: str) -> str:
    """工具函数: 生成 bcrypt hash. IT 给员工改密码时用.

    用法 (CLI):
        python -c 'from catfish_identity.users import hash_password; print(hash_password("my_pwd"))'
    """
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
