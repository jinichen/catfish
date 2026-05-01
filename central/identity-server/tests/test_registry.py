"""测试 Plan D · catfish-identity registry (五一 sprint Day 4).

覆盖:
- register 写 yaml 成功
- lookup 已注册 / 未注册
- list 全部
- last_seen 在线检测
- yaml 持久化 (保存/加载往返)
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from catfish_identity.registry import (
    OFFLINE_AFTER_SECONDS,
    RegistryEntry,
    build_registry_router,
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    """每个测试独立 yaml store + isolated FastAPI client."""
    registry_path = tmp_path / "registry.yaml"
    monkeypatch.setenv("CATFISH_REGISTRY_PATH", str(registry_path))
    app = FastAPI()
    app.include_router(build_registry_router())
    return TestClient(app)


# ── register ────────────────────────────────────────────────────


def test_register_basic(client: TestClient) -> None:
    resp = client.post(
        "/registry/register",
        json={
            "sub": "alice@ffcs.cn",
            "catfish_endpoint": "http://alice:8999",
            "jwks_uri": "http://alice:8998/.well-known/jwks.json",
            "department": "研发部",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["total_agents"] == 1
    assert "T" in data["last_seen"]  # ISO format


def test_register_multiple(client: TestClient) -> None:
    for sub in ["alice@ffcs.cn", "bob@ffcs.cn", "charlie@ffcs.cn"]:
        client.post(
            "/registry/register",
            json={
                "sub": sub,
                "catfish_endpoint": f"http://{sub.split('@')[0]}:8999",
                "jwks_uri": f"http://{sub.split('@')[0]}:8998/jwks",
            },
        )
    resp = client.post(
        "/registry/register",
        json={
            "sub": "alice@ffcs.cn",  # 重复 register 是更新, 不增加
            "catfish_endpoint": "http://alice-new:8999",
            "jwks_uri": "http://alice:8998/jwks",
        },
    )
    assert resp.json()["total_agents"] == 3  # 还是 3 个不变


def test_register_missing_required(client: TestClient) -> None:
    """缺 sub / endpoint → 400."""
    resp = client.post("/registry/register", json={"sub": "alice@ffcs.cn"})
    # 缺 catfish_endpoint
    assert resp.status_code in (400, 422)


# ── B 方案: per-agent public_pem + jwks endpoint ──────────────


def _gen_test_keypair() -> tuple[str, str]:
    """生成 RSA 测试 keypair, 返 (private_pem, public_pem)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def test_register_with_public_pem(client: TestClient) -> None:
    """register 带 public_pem 字段, jwks endpoint 暴露."""
    _, public_pem = _gen_test_keypair()
    resp = client.post(
        "/registry/register",
        json={
            "sub": "alice@ffcs.cn",
            "catfish_endpoint": "http://alice:8999",
            "public_pem": public_pem,
        },
    )
    assert resp.status_code == 200
    # lookup 看 jwks_uri 自动算的
    lookup = client.get("/registry/lookup?sub=alice@ffcs.cn").json()
    assert "agents/alice@ffcs.cn/jwks.json" in lookup["jwks_uri"]


def test_per_agent_jwks_endpoint(client: TestClient) -> None:
    """暴露 per-agent jwks, 返 RSA 公钥结构."""
    _, public_pem = _gen_test_keypair()
    client.post(
        "/registry/register",
        json={
            "sub": "alice@ffcs.cn",
            "catfish_endpoint": "http://alice:8999",
            "public_pem": public_pem,
        },
    )

    resp = client.get("/registry/agents/alice@ffcs.cn/jwks.json")
    assert resp.status_code == 200
    data = resp.json()
    assert "keys" in data
    assert len(data["keys"]) == 1
    jwk = data["keys"][0]
    assert jwk["kty"] == "RSA"
    assert jwk["alg"] == "RS256"
    assert jwk["kid"] == "alice@ffcs.cn"
    assert jwk["use"] == "sig"
    assert "n" in jwk
    assert "e" in jwk


def test_per_agent_jwks_unknown_sub(client: TestClient) -> None:
    resp = client.get("/registry/agents/eve@ffcs.cn/jwks.json")
    assert resp.status_code == 404


def test_per_agent_jwks_no_public_pem(client: TestClient) -> None:
    """register 没传 public_pem → jwks endpoint 拒绝."""
    client.post(
        "/registry/register",
        json={
            "sub": "alice@ffcs.cn",
            "catfish_endpoint": "http://alice:8999",
            # public_pem 缺
        },
    )
    resp = client.get("/registry/agents/alice@ffcs.cn/jwks.json")
    assert resp.status_code == 404
    assert "public_pem" in resp.json()["detail"]


def test_per_agent_jwks_malformed_pem(client: TestClient) -> None:
    """public_pem 格式错 → jwks endpoint 500."""
    client.post(
        "/registry/register",
        json={
            "sub": "alice@ffcs.cn",
            "catfish_endpoint": "http://alice:8999",
            "public_pem": "not a valid PEM",
        },
    )
    resp = client.get("/registry/agents/alice@ffcs.cn/jwks.json")
    assert resp.status_code == 500


# ── lookup ──────────────────────────────────────────────────────


def test_lookup_registered(client: TestClient) -> None:
    client.post(
        "/registry/register",
        json={
            "sub": "alice@ffcs.cn",
            "catfish_endpoint": "http://alice:8999",
            "jwks_uri": "http://alice:8998/jwks",
            "department": "研发部",
            "capabilities": ["a2a.ask", "a2a.skill_share"],
        },
    )
    resp = client.get("/registry/lookup?sub=alice@ffcs.cn")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sub"] == "alice@ffcs.cn"
    assert data["catfish_endpoint"] == "http://alice:8999"
    assert data["department"] == "研发部"
    assert "a2a.ask" in data["capabilities"]
    assert data["online"] is True  # 刚 register, last_seen < 2 min


def test_lookup_not_registered(client: TestClient) -> None:
    resp = client.get("/registry/lookup?sub=eve@ffcs.cn")
    assert resp.status_code == 404


# ── list ────────────────────────────────────────────────────────


def test_list_empty(client: TestClient) -> None:
    resp = client.get("/registry/list")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_with_agents(client: TestClient) -> None:
    for sub in ["alice@ffcs.cn", "bob@ffcs.cn"]:
        client.post(
            "/registry/register",
            json={
                "sub": sub,
                "catfish_endpoint": f"http://{sub.split('@')[0]}",
                "jwks_uri": "x",
            },
        )
    resp = client.get("/registry/list")
    data = resp.json()
    assert len(data) == 2
    subs = {a["sub"] for a in data}
    assert subs == {"alice@ffcs.cn", "bob@ffcs.cn"}


# ── 在线检测 ────────────────────────────────────────────────────


def test_online_check_fresh() -> None:
    e = RegistryEntry(
        sub="x",
        catfish_endpoint="x",
        jwks_uri="x",
        last_seen_iso=datetime.now(timezone.utc).isoformat(),
    )
    assert e.is_online() is True


def test_online_check_stale() -> None:
    """超过 2 分钟 last_seen → 离线."""
    stale_ts = (
        datetime.now(timezone.utc) - timedelta(seconds=OFFLINE_AFTER_SECONDS + 10)
    ).isoformat()
    e = RegistryEntry(
        sub="x",
        catfish_endpoint="x",
        jwks_uri="x",
        last_seen_iso=stale_ts,
    )
    assert e.is_online() is False


def test_online_check_no_last_seen() -> None:
    e = RegistryEntry(sub="x", catfish_endpoint="x", jwks_uri="x")
    assert e.is_online() is False


# ── 持久化 ──────────────────────────────────────────────────────


def test_persistence_yaml(tmp_path: Path, monkeypatch) -> None:
    """yaml 真写到磁盘 + reload 一致."""
    registry_path = tmp_path / "registry.yaml"
    monkeypatch.setenv("CATFISH_REGISTRY_PATH", str(registry_path))

    app = FastAPI()
    app.include_router(build_registry_router())
    client1 = TestClient(app)
    client1.post(
        "/registry/register",
        json={
            "sub": "alice@ffcs.cn",
            "catfish_endpoint": "http://alice:8999",
            "jwks_uri": "http://alice:8998/jwks",
            "department": "研发部",
        },
    )

    # 文件存在
    assert registry_path.exists()
    content = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    assert "agents" in content
    assert "alice@ffcs.cn" in content["agents"]

    # 起新 client (同 yaml) 也能 lookup
    app2 = FastAPI()
    app2.include_router(build_registry_router())
    client2 = TestClient(app2)
    resp = client2.get("/registry/lookup?sub=alice@ffcs.cn")
    assert resp.status_code == 200
    assert resp.json()["catfish_endpoint"] == "http://alice:8999"
