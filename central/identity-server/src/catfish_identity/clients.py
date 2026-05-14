"""OAuth client registry — clients.yaml + bcrypt secret 验证.

# 设计 (BL-RBAC P0 + B sprint Day 1, 5/14)

跟 users.py 平级. users.yaml = **人**身份, clients.yaml = **服务**身份.
hermes-cli 等服务进程用 OAuth 2.0 client_credentials grant (RFC 6749 §4.4)
拿 access_token, 替代之前 dev_token hack.

详见 docs/RBAC-DESIGN.md §10 + §12.

# YAML schema

```yaml
clients:
  - client_id: hermes-cli
    client_secret_hash: $2b$12$...     # bcrypt
    name: Hermes CLI
    description: 鲶鱼 hermes 0.13 CLI
    allowed_grant_types:
      - client_credentials
    allowed_scopes:
      - chat.completions
      - audit.write
      - tools.invoke
    department: infra
    role: service                       # 固定, 跟 user role 体系隔离
    enabled: true
```

# secret 生成 (admin 加 client 时用)

```bash
python3 -c 'import bcrypt; print(bcrypt.hashpw(b"my_client_secret", bcrypt.gensalt()).decode())'
```

# 安全

- secret 只存 bcrypt hash, 永不存明文
- 跟 users 同款 timing-safe dummy hash 防 client_id 枚举 attack
- enabled=false 立即拒 (admin 可临时禁某个 client 不删 yaml)
- Phase 2 加: secret rotation endpoint, audit who-rotated, lockout 失败计数
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import bcrypt
import yaml

logger = logging.getLogger("catfish.identity.clients")

#: Timing-safe dummy hash. 跟 users.py 同款. 启动时算一次, 复用.
_TIMING_DUMMY_HASH: bytes = bcrypt.hashpw(
    b"client-timing-constant-placeholder", bcrypt.gensalt(rounds=12)
)

#: 默认 grant_types. 不显式配 = 只允许 client_credentials.
_DEFAULT_GRANT_TYPES: tuple[str, ...] = ("client_credentials",)

#: 白名单 grant_types. yaml 配的值不在这里直接拒 (防错配漏接 grant 类型).
#: 5/14 evening: 加 authorization_code — hermes 0.13 `hermes model` 走浏览器交互
#: 登录 (authorization_code flow), 不是 client_credentials. clients.yaml 的客户端要
#: 同时支持两种 grant 才能既给员工交互登录, 又给跑批 service 身份.
_KNOWN_GRANT_TYPES: frozenset[str] = frozenset(
    {"client_credentials", "authorization_code"}  # Phase 2+ 加: "refresh_token"
)

#: service role 是固定值 — clients.yaml 里 role 字段如果没配自动填这个.
#: 跟 user role (admin / manager / employee / sysadmin) 体系隔离, 鉴权链按
#: token_use=service 分流 (gateway oidc.py Day 2 实现).
SERVICE_ROLE: str = "service"


@dataclass
class IdentityClient:
    """OAuth client. 内部数据结构, 不直接 expose JSON.

    跟 IdentityUser 平级 — 一个走 user 鉴权链 (sub=email), 一个走 service 鉴权链
    (sub=client:<client_id>).
    """

    client_id: str
    client_secret_hash: str
    name: str = ""
    description: str = ""
    allowed_grant_types: list[str] = field(
        default_factory=lambda: list(_DEFAULT_GRANT_TYPES)
    )
    allowed_scopes: list[str] = field(default_factory=list)
    department: str = ""
    role: str = SERVICE_ROLE  # 固定 = "service", 防 yaml 误填别的
    enabled: bool = True

    def supports_grant(self, grant_type: str) -> bool:
        """这个 client 允许这个 grant_type 吗.

        空 allowed_grant_types 视为只允许 client_credentials (兼容老 yaml).
        """
        if not self.allowed_grant_types:
            return grant_type == "client_credentials"
        return grant_type in self.allowed_grant_types

    def filter_scopes(self, requested: list[str]) -> list[str]:
        """从 requested scope 里挑 client 允许的, 越权直接丢.

        requested 空 → 返 client.allowed_scopes 全集 (默认行为).
        requested 含越权项 → caller 应该返 OAuth invalid_scope, 不在这里 raise
        让 caller 决定 (这里只过滤).
        """
        if not requested:
            return list(self.allowed_scopes)
        allowed = set(self.allowed_scopes)
        return [s for s in requested if s in allowed]

    def has_unauthorized_scope(self, requested: list[str]) -> list[str]:
        """返 requested 里 client 没权限的 scope 列表 (空 = 都有权限)."""
        if not requested:
            return []
        allowed = set(self.allowed_scopes)
        return [s for s in requested if s not in allowed]

    def to_token_claims(self, scope: str) -> dict:
        """渲染成 JWT payload claim. 不含 secret hash.

        scope 是已经 filter 过的最终 scope (空格分隔, OAuth 标准格式).
        """
        return {
            "sub": f"client:{self.client_id}",  # service sub 用 client: 前缀
            "client_id": self.client_id,
            "token_use": "service",
            "scope": scope,
            "role": self.role or SERVICE_ROLE,
            "department": self.department,
        }


def _default_clients_path() -> Path:
    """默认 clients.yaml 位置. env CATFISH_IDENTITY_CLIENTS_PATH 覆盖."""
    if env := os.environ.get("CATFISH_IDENTITY_CLIENTS_PATH"):
        return Path(env).expanduser()
    return Path(__file__).resolve().parent.parent.parent / "config" / "clients.yaml"


class ClientRegistry:
    """OAuth client 注册表. 跟 UserRegistry 同模式.

    实例化时读 yaml 一次. clients.yaml 改了要重启 catfish-identity (Phase 2 加
    file watcher hot-reload, 跟 users 一起).
    """

    def __init__(self, clients_path: Path | None = None) -> None:
        self.clients_path = clients_path or _default_clients_path()
        self._clients: dict[str, IdentityClient] = {}
        self.reload()

    def reload(self) -> None:
        """从 yaml 重新加载 client 列表. 空文件 / 文件不存在 → 空注册表."""
        if not self.clients_path.exists():
            logger.info(
                "clients.yaml 不存在 (%s), client 注册表为空 — "
                "服务调用走 dev_token fallback (5/21 后 prod 拒).",
                self.clients_path,
            )
            self._clients = {}
            return

        with open(self.clients_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        raw_clients = data.get("clients", []) or []
        loaded: dict[str, IdentityClient] = {}
        for raw in raw_clients:
            if not isinstance(raw, dict):
                logger.warning("跳过非 dict 的 client 条目: %r", raw)
                continue
            client_id = str(raw.get("client_id") or "").strip()
            client_secret_hash = str(raw.get("client_secret_hash") or "")
            if not client_id or not client_secret_hash:
                logger.warning(
                    "跳过缺 client_id/client_secret_hash 的 client: %r", raw
                )
                continue

            grant_types = raw.get("allowed_grant_types") or list(_DEFAULT_GRANT_TYPES)
            if not isinstance(grant_types, list):
                grant_types = list(_DEFAULT_GRANT_TYPES)
            # 过滤未知 grant_type (防 yaml 错配偷接没实现的 grant)
            safe_grants: list[str] = []
            for g in grant_types:
                gs = str(g).strip().lower()
                if gs in _KNOWN_GRANT_TYPES:
                    safe_grants.append(gs)
                else:
                    logger.warning(
                        "client %s grant_type=%s 不在白名单, 跳过", client_id, gs
                    )
            if not safe_grants:
                # 全错配 → 兜底默认
                safe_grants = list(_DEFAULT_GRANT_TYPES)

            scopes = raw.get("allowed_scopes") or []
            if not isinstance(scopes, list):
                scopes = []

            # role 强制 = service. yaml 误填别的直接覆盖, 防越权配
            role = str(raw.get("role") or SERVICE_ROLE).strip().lower()
            if role != SERVICE_ROLE:
                logger.warning(
                    "client %s role=%s 不允许 (强制 service)", client_id, role
                )
                role = SERVICE_ROLE

            loaded[client_id] = IdentityClient(
                client_id=client_id,
                client_secret_hash=client_secret_hash,
                name=str(raw.get("name") or ""),
                description=str(raw.get("description") or ""),
                allowed_grant_types=safe_grants,
                allowed_scopes=[str(s) for s in scopes],
                department=str(raw.get("department") or ""),
                role=role,
                enabled=bool(raw.get("enabled", True)),
            )
        self._clients = loaded
        logger.info(
            "clients.yaml 加载: %d 个 client (path=%s)",
            len(loaded),
            self.clients_path,
        )

    def find(self, client_id: str) -> IdentityClient | None:
        """按 client_id 查 client. client_id 大小写敏感 (跟 OAuth 规范一致)."""
        if not client_id:
            return None
        return self._clients.get(client_id.strip())

    def verify_secret(
        self, client_id: str, client_secret: str
    ) -> IdentityClient | None:
        """验 client_id + client_secret. 通过返 client, 失败返 None.

        跟 UserRegistry.verify_password 同模式: timing-safe (即使 client_id 不存
        在也跑 bcrypt, 防 enumeration attack).

        disabled client → 拒 (即使密码对).
        """
        client = self.find(client_id)
        if client is None:
            # 不存在的 client 也跑一次 bcrypt 防 timing 暴露
            bcrypt.checkpw(b"dummy", _TIMING_DUMMY_HASH)
            return None
        if not client.enabled:
            bcrypt.checkpw(b"dummy", _TIMING_DUMMY_HASH)
            logger.info("client %s 已禁用, 拒绝", client_id)
            return None
        try:
            ok = bcrypt.checkpw(
                client_secret.encode("utf-8"),
                client.client_secret_hash.encode("utf-8"),
            )
        except (ValueError, TypeError):
            logger.warning("client %s 的 secret_hash 格式不对", client_id)
            return None
        return client if ok else None

    def __len__(self) -> int:
        return len(self._clients)

    def __contains__(self, client_id: str) -> bool:
        return self.find(client_id) is not None


__all__ = [
    "IdentityClient",
    "ClientRegistry",
    "SERVICE_ROLE",
]
