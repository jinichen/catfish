"""DevTokenProvider — dev 环境多角色测试 + 生产兜底.

# 用在哪里

  1. 本地开发 (CATFISH_ENV=dev, default): dev_users.yaml 配多账号,
     按 token 匹配返不同角色 (admin / manager / employee).
  2. 生产 SSO 配错救急 (CATFISH_ENV=prod + 显式设 CATFISH_DEV_TOKEN):
     客户 IT 第一次接 SSO 撞坑时, 临时用 dev_token 进 Companion 排查.
     Companion UI 必须显 warning banner "你在用 dev token, 不是真 SSO" (Phase 1C).
     audit log 必须标 auth_method='dev_token' (gateway 已支持 security_concern 字段).

# 决策对齐

决策 6 (docs/AUTH-DESIGN.md § 13): dev_token 保留作生产兜底.

# 行为

加载顺序:
  1. config/dev_users.yaml (五一 sprint 5/2 加, 多角色测试) — 优先
  2. env CATFISH_DEV_TOKEN (单 token, back-compat) — 兜底, role=admin

每次 verify 重新加载 yaml (lazy, 失败 fallback to env-only).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .base import AuthProvider, User

logger = logging.getLogger("catfish.gateway.auth.dev_token")

#: 默认 dev token. 生产环境员工应该 explicit 设 CATFISH_DEV_TOKEN, 不依赖默认.
_DEFAULT_DEV_TOKEN = "dev-token-local"


@dataclass
class _DevUser:
    """dev_users.yaml 的一行."""

    email: str
    token: str
    name: str = ""
    department: str = ""
    role: str = "employee"
    managed_departments: list[str] = field(default_factory=list)


@dataclass
class _DevConfig:
    """加载后的 dev_users.yaml 内容."""

    users: list[_DevUser] = field(default_factory=list)
    default: _DevUser | None = None


def _dev_users_path() -> Path:
    """dev_users.yaml 路径. CATFISH_DEV_USERS_PATH env override.

    默认: <repo>/central/llm-gateway/config/dev_users.yaml
    """
    custom = os.environ.get("CATFISH_DEV_USERS_PATH")
    if custom:
        return Path(custom).expanduser()
    # dev_token.py → auth/ → catfish_gateway/ → src/ → llm-gateway/  (4 个 parent)
    pkg_root = Path(__file__).resolve().parent.parent.parent.parent
    return pkg_root / "config" / "dev_users.yaml"


def _parse_user(d: dict[str, Any]) -> _DevUser | None:
    """从 yaml dict 解析 _DevUser. 返 None 表无效."""
    email = str(d.get("email", "")).strip()
    token = str(d.get("token", "")).strip()
    if not email or not token:
        return None
    managed = d.get("managed_departments", []) or []
    if isinstance(managed, str):
        managed = [s.strip() for s in managed.split(",") if s.strip()]
    return _DevUser(
        email=email,
        token=token,
        name=str(d.get("name", "")),
        department=str(d.get("department", "")),
        role=str(d.get("role", "employee")),
        managed_departments=[str(m) for m in managed],
    )


def _load_dev_config() -> _DevConfig:
    """每次 verify 都重 load — 改 yaml 后下一次请求生效."""
    path = _dev_users_path()
    if not path.exists():
        return _DevConfig()
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("dev_users.yaml 解析失败 (%s): %s", path, e)
        return _DevConfig()

    cfg = _DevConfig()
    for raw in data.get("users", []) or []:
        if isinstance(raw, dict):
            u = _parse_user(raw)
            if u is not None:
                cfg.users.append(u)

    default_raw = data.get("default")
    if isinstance(default_raw, dict):
        # default 没 token 字段也行, 默认走 _DEFAULT_DEV_TOKEN
        default_raw = {**default_raw, "token": default_raw.get("token", _DEFAULT_DEV_TOKEN)}
        cfg.default = _parse_user(default_raw)

    return cfg


def _user_from_dev(d: _DevUser, auth_method: str = "dev_token") -> User:
    return User(
        sub=d.email,
        department=d.department,
        tier="admin" if d.role == "admin" else "employee",
        role=d.role,
        managed_departments=list(d.managed_departments),
        auth_method=auth_method,
    )


def list_dev_users() -> list[dict[str, Any]]:
    """供 /api/dev/users 端点用. 返脱敏后的账号列表 (不暴露 token).

    Companion 切换器调这个查可选账号.
    """
    cfg = _load_dev_config()
    out: list[dict[str, Any]] = []
    for u in cfg.users:
        out.append({
            "email": u.email,
            "name": u.name or u.email,
            "department": u.department,
            "role": u.role,
            "managed_departments": list(u.managed_departments),
            "token": u.token,  # ★ dev 模式才暴露, 不是生产
        })
    return out


class DevTokenProvider(AuthProvider):
    """静态 token 验证.

    支持多账号 (dev_users.yaml) + 单 token env 兜底.
    """

    @property
    def name(self) -> str:
        return "dev_token"

    @property
    def is_strict(self) -> bool:
        # dev_token 是 lax — 客户 IT 配错 SSO 时救急用. UI 应该显 banner.
        return False

    def _env_token(self) -> str:
        """env CATFISH_DEV_TOKEN, back-compat."""
        return os.environ.get("CATFISH_DEV_TOKEN", _DEFAULT_DEV_TOKEN)

    def verify_bearer(self, authorization: str | None) -> User | None:
        if not authorization:
            return None
        if not authorization.lower().startswith("bearer "):
            return None
        token = authorization[7:].strip()
        if not token:
            return None

        # 1. yaml 多账号匹配 (五一 sprint 5/2)
        cfg = _load_dev_config()
        for u in cfg.users:
            if u.token == token:
                return _user_from_dev(u)

        # 2. 兜底: env CATFISH_DEV_TOKEN (老 'dev-token-local' 走这里)
        if token == self._env_token():
            if cfg.default is not None:
                return _user_from_dev(cfg.default)
            # 没 yaml 也没 default — 走最最老兼容 (admin / engineering)
            return User(
                sub="dev-user",
                department="engineering",
                tier="employee",
                role="admin",
                managed_departments=["engineering", "product"],
                auth_method="dev_token",
            )

        return None
