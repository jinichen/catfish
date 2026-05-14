"""ClientRegistry 单测 (BL-RBAC P0 + B sprint Day 1, 5/14).

跑法: cd central/identity-server && PYTHONPATH=src python -m pytest tests/test_clients.py -q

覆盖:
  - clients.yaml load + 默认值兜底
  - verify_secret 通过 / 失败 / 未知 client / disabled client (timing-safe)
  - allowed_grant_types 白名单过滤 (yaml 错配未知 grant 跳过)
  - filter_scopes / has_unauthorized_scope 越权检测
  - to_token_claims 输出格式 (sub=client:<id> + token_use=service)
"""
from __future__ import annotations

from pathlib import Path

import bcrypt
import pytest

from catfish_identity.clients import (
    ClientRegistry,
    IdentityClient,
    SERVICE_ROLE,
)


def _hash(secret: str) -> str:
    """生成 bcrypt hash (rounds=4 给测试快, 生产用 12)."""
    return bcrypt.hashpw(secret.encode(), bcrypt.gensalt(rounds=4)).decode()


@pytest.fixture
def clients_yaml(tmp_path) -> Path:
    """造一个 clients.yaml 含 3 个 client (常规 / 禁用 / 多 scope)."""
    p = tmp_path / "clients.yaml"
    p.write_text(f"""
clients:
  - client_id: hermes-cli
    client_secret_hash: {_hash("hermes-secret")}
    name: Hermes CLI
    description: 鲶鱼 hermes 0.13 CLI
    allowed_grant_types:
      - client_credentials
    allowed_scopes:
      - chat.completions
      - audit.write
      - tools.invoke
    department: infra
    role: service
    enabled: true

  - client_id: skills-hub
    client_secret_hash: {_hash("hub-secret")}
    name: Skills Hub
    allowed_scopes:
      - skills.read
    department: infra
    enabled: true

  - client_id: legacy-bot
    client_secret_hash: {_hash("legacy-secret")}
    name: Legacy Bot
    allowed_scopes:
      - chat.completions
    department: legacy
    enabled: false   # 临时禁
""", encoding="utf-8")
    return p


@pytest.fixture
def registry(clients_yaml: Path) -> ClientRegistry:
    return ClientRegistry(clients_path=clients_yaml)


# ── load + 默认值 ──────────────────────────────────────────


def test_load_clients(registry: ClientRegistry):
    assert len(registry) == 3
    assert "hermes-cli" in registry
    assert "skills-hub" in registry
    assert "legacy-bot" in registry


def test_load_no_file(tmp_path):
    """clients.yaml 不存在 → 空注册表 (cleanly degrade)"""
    r = ClientRegistry(clients_path=tmp_path / "nope.yaml")
    assert len(r) == 0
    assert r.find("anything") is None


def test_load_default_grant_types_when_missing(tmp_path):
    """yaml 没配 allowed_grant_types → 默认 [client_credentials]"""
    p = tmp_path / "clients.yaml"
    p.write_text(f"""
clients:
  - client_id: minimal
    client_secret_hash: {_hash("x")}
""", encoding="utf-8")
    c = ClientRegistry(clients_path=p).find("minimal")
    assert c.allowed_grant_types == ["client_credentials"]


def test_load_skips_unknown_grant_type(tmp_path):
    """yaml 配未知 grant_type 跳过, 全错配兜底默认"""
    p = tmp_path / "clients.yaml"
    p.write_text(f"""
clients:
  - client_id: bad-grant
    client_secret_hash: {_hash("x")}
    allowed_grant_types:
      - password           # 未实现
      - device_code        # 未实现
""", encoding="utf-8")
    c = ClientRegistry(clients_path=p).find("bad-grant")
    # 全错配 → 兜底 ["client_credentials"]
    assert c.allowed_grant_types == ["client_credentials"]


def test_role_forced_to_service(tmp_path):
    """yaml 误填 role=admin 应被强制覆盖回 service (防越权配)"""
    p = tmp_path / "clients.yaml"
    p.write_text(f"""
clients:
  - client_id: tries-admin
    client_secret_hash: {_hash("x")}
    role: admin            # 不允许, 应被覆盖
""", encoding="utf-8")
    c = ClientRegistry(clients_path=p).find("tries-admin")
    assert c.role == SERVICE_ROLE


def test_skip_missing_required_fields(tmp_path):
    """缺 client_id / client_secret_hash 跳过, 不挂"""
    p = tmp_path / "clients.yaml"
    p.write_text(f"""
clients:
  - name: 缺 client_id
    client_secret_hash: {_hash("x")}
  - client_id: 缺 hash
    name: nope
  - client_id: ok
    client_secret_hash: {_hash("y")}
""", encoding="utf-8")
    r = ClientRegistry(clients_path=p)
    assert len(r) == 1
    assert r.find("ok") is not None


# ── verify_secret ──────────────────────────────────────────


def test_verify_secret_ok(registry: ClientRegistry):
    c = registry.verify_secret("hermes-cli", "hermes-secret")
    assert c is not None
    assert c.client_id == "hermes-cli"


def test_verify_secret_wrong(registry: ClientRegistry):
    assert registry.verify_secret("hermes-cli", "wrong") is None


def test_verify_secret_unknown_client(registry: ClientRegistry):
    """未知 client → None (timing-safe: 内部仍跑一次 bcrypt 防 enumeration)"""
    assert registry.verify_secret("nope", "anything") is None


def test_verify_secret_disabled_client(registry: ClientRegistry):
    """legacy-bot enabled=false → 即使密码对也拒"""
    assert registry.verify_secret("legacy-bot", "legacy-secret") is None


def test_verify_secret_empty_client_id(registry: ClientRegistry):
    assert registry.verify_secret("", "any") is None


# ── grant / scope 检查 ────────────────────────────────────


def test_supports_grant(registry: ClientRegistry):
    c = registry.find("hermes-cli")
    assert c.supports_grant("client_credentials") is True
    assert c.supports_grant("authorization_code") is False
    assert c.supports_grant("password") is False


def test_filter_scopes_subset(registry: ClientRegistry):
    c = registry.find("hermes-cli")
    # 部分越权: tools.invoke 在白名单, admin.god 不在 → 只剩 tools.invoke
    assert c.filter_scopes(["tools.invoke", "admin.god"]) == ["tools.invoke"]


def test_filter_scopes_empty_returns_all(registry: ClientRegistry):
    """空 requested → 给 client 全集 (默认行为)"""
    c = registry.find("hermes-cli")
    assert set(c.filter_scopes([])) == {
        "chat.completions", "audit.write", "tools.invoke",
    }


def test_has_unauthorized_scope(registry: ClientRegistry):
    c = registry.find("hermes-cli")
    assert c.has_unauthorized_scope(["chat.completions"]) == []
    assert c.has_unauthorized_scope(["chat.completions", "admin.god"]) == ["admin.god"]
    assert c.has_unauthorized_scope([]) == []


# ── to_token_claims ────────────────────────────────────────


def test_to_token_claims_format(registry: ClientRegistry):
    c = registry.find("hermes-cli")
    claims = c.to_token_claims("chat.completions audit.write")
    assert claims == {
        "sub": "client:hermes-cli",
        "client_id": "hermes-cli",
        "token_use": "service",
        "scope": "chat.completions audit.write",
        "role": "service",
        "department": "infra",
    }


def test_to_token_claims_default_role(tmp_path):
    """role 字段没配 → 默认 service"""
    p = tmp_path / "clients.yaml"
    p.write_text(f"""
clients:
  - client_id: norole
    client_secret_hash: {_hash("x")}
""", encoding="utf-8")
    c = ClientRegistry(clients_path=p).find("norole")
    claims = c.to_token_claims("")
    assert claims["role"] == "service"
    assert claims["sub"] == "client:norole"
