"""/token client_credentials grant 端到端单测 (BL-RBAC P0 + B sprint Day 1, 5/14).

跑法: cd central/identity-server && PYTHONPATH=src python -m pytest tests/test_client_credentials.py -q

覆盖 (RFC 6749 §4.4 + §5.2):
  - 成功路径: 默认 scope / 部分 scope / 越权 scope
  - 错误路径: invalid_client (4 种 sub-case) / unauthorized_client / invalid_scope
  - JWT 内容: sub=client:<id>, aud=catfish-gateway, token_use=service
  - 不返 id_token (服务调用没 user sub, OIDC 概念不适用)
  - authorization_code grant 仍正常 (regression)
  - client_registry=None 返 503 cleanly
"""
from __future__ import annotations

from pathlib import Path

import bcrypt
import jwt as pyjwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from catfish_identity.clients import ClientRegistry
from catfish_identity.jwt_signer import JwtSigner
from catfish_identity.routes import _CodeStore, make_router
from catfish_identity.users import UserRegistry, hash_password


def _hash(secret: str) -> str:
    return bcrypt.hashpw(secret.encode(), bcrypt.gensalt(rounds=4)).decode()


@pytest.fixture
def app(tmp_path) -> FastAPI:
    """构造独立 app, 含 user + client registry."""
    # users
    users_yaml = tmp_path / "users.yaml"
    users_yaml.write_text(f"""
users:
  - email: alice@x.com
    password_hash: {hash_password("password123")}
    name: Alice
    department: sales
    tier: employee
""")
    # clients
    clients_yaml = tmp_path / "clients.yaml"
    clients_yaml.write_text(f"""
clients:
  - client_id: hermes-cli
    client_secret_hash: {_hash("hermes-secret")}
    name: Hermes CLI
    allowed_grant_types:
      - client_credentials
    allowed_scopes:
      - chat.completions
      - audit.write
      - tools.invoke
    department: infra
    role: service
    enabled: true

  - client_id: disabled-bot
    client_secret_hash: {_hash("disabled-secret")}
    allowed_scopes:
      - chat.completions
    department: legacy
    enabled: false
""", encoding="utf-8")

    keys_dir = tmp_path / "keys"
    signer = JwtSigner(key_dir=keys_dir)
    registry = UserRegistry(users_path=users_yaml)
    code_store = _CodeStore()
    client_registry = ClientRegistry(clients_path=clients_yaml)

    fastapi_app = FastAPI()
    fastapi_app.include_router(
        make_router(
            issuer="http://test:8998",
            signer=signer,
            registry=registry,
            code_store=code_store,
            client_registry=client_registry,
        )
    )
    fastapi_app.state.signer = signer
    return fastapi_app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ── 成功路径 ─────────────────────────────────────────────


def test_client_credentials_success_default_scope(client: TestClient):
    """正确 secret + 不传 scope → 200 + access_token + 默认 scope (client 全集)"""
    r = client.post("/token", data={
        "grant_type": "client_credentials",
        "client_id": "hermes-cli",
        "client_secret": "hermes-secret",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 3600
    # 不返 id_token (服务调用没 user sub)
    assert "id_token" not in body
    assert "access_token" in body
    # scope 是 client 全集
    assert set(body["scope"].split()) == {
        "chat.completions", "audit.write", "tools.invoke",
    }


def test_client_credentials_success_partial_scope(client: TestClient):
    """传部分 scope → 200 + 限定到这些 scope"""
    r = client.post("/token", data={
        "grant_type": "client_credentials",
        "client_id": "hermes-cli",
        "client_secret": "hermes-secret",
        "scope": "chat.completions audit.write",
    })
    assert r.status_code == 200
    assert r.json()["scope"] == "chat.completions audit.write"


def test_client_credentials_jwt_claims(client: TestClient, app: FastAPI):
    """返的 access_token 含正确 service claims (验签 + 解 payload)"""
    r = client.post("/token", data={
        "grant_type": "client_credentials",
        "client_id": "hermes-cli",
        "client_secret": "hermes-secret",
    })
    token = r.json()["access_token"]

    # 公钥验签 (跟 gateway 走的同模式 — 真生产从 jwks_uri 拉, 单测直接拿对象)
    from cryptography.hazmat.primitives import serialization
    signer = app.state.signer
    pub_pem = signer._public_key.public_bytes(  # noqa: SLF001 — 单测用
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    payload = pyjwt.decode(
        token,
        pub_pem,
        algorithms=["RS256"],
        audience="catfish-gateway",
    )
    assert payload["sub"] == "client:hermes-cli"
    assert payload["aud"] == "catfish-gateway"
    assert payload["token_use"] == "service"
    assert payload["client_id"] == "hermes-cli"
    assert payload["role"] == "service"
    assert payload["department"] == "infra"
    assert "scope" in payload
    assert payload["iss"] == "http://test:8998"
    # iat / exp 合理 (ttl=3600)
    assert payload["exp"] - payload["iat"] == 3600


# ── 错误路径 (RFC 6749 §5.2) ─────────────────────────────


def test_missing_client_secret_returns_invalid_client(client: TestClient):
    """没传 client_secret → 401 invalid_client"""
    r = client.post("/token", data={
        "grant_type": "client_credentials",
        "client_id": "hermes-cli",
    })
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "invalid_client"


def test_wrong_client_secret_returns_invalid_client(client: TestClient):
    r = client.post("/token", data={
        "grant_type": "client_credentials",
        "client_id": "hermes-cli",
        "client_secret": "WRONG",
    })
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "invalid_client"


def test_unknown_client_returns_invalid_client(client: TestClient):
    """未知 client_id → 401 invalid_client (跟错 secret 同 error 防 enumeration)"""
    r = client.post("/token", data={
        "grant_type": "client_credentials",
        "client_id": "nope",
        "client_secret": "anything",
    })
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "invalid_client"


def test_disabled_client_returns_invalid_client(client: TestClient):
    """enabled=false 的 client 即使密码对也拒"""
    r = client.post("/token", data={
        "grant_type": "client_credentials",
        "client_id": "disabled-bot",
        "client_secret": "disabled-secret",
    })
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "invalid_client"


def test_invalid_scope(client: TestClient):
    """请求 client 没权限的 scope → 400 invalid_scope"""
    r = client.post("/token", data={
        "grant_type": "client_credentials",
        "client_id": "hermes-cli",
        "client_secret": "hermes-secret",
        "scope": "chat.completions admin.god",
    })
    assert r.status_code == 400
    body = r.json()
    assert body["detail"]["error"] == "invalid_scope"
    assert "admin.god" in body["detail"]["error_description"]


def test_unsupported_grant_type(client: TestClient):
    """grant_type 不是 authorization_code / client_credentials → 400"""
    r = client.post("/token", data={
        "grant_type": "password",
        "client_id": "hermes-cli",
        "client_secret": "x",
    })
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "unsupported_grant_type"


# ── client_registry=None — cleanly degrade ───────────────


def test_no_client_registry_returns_503(tmp_path):
    """client_registry=None → /token client_credentials 返 503 unsupported"""
    users_yaml = tmp_path / "users.yaml"
    users_yaml.write_text(f"""
users:
  - email: alice@x.com
    password_hash: {hash_password("password123")}
    name: Alice
    department: sales
""")
    keys_dir = tmp_path / "keys"
    signer = JwtSigner(key_dir=keys_dir)
    registry = UserRegistry(users_path=users_yaml)

    fastapi_app = FastAPI()
    fastapi_app.include_router(
        make_router(
            issuer="http://test:8998",
            signer=signer,
            registry=registry,
            code_store=_CodeStore(),
            client_registry=None,  # 没配
        )
    )
    c = TestClient(fastapi_app)
    r = c.post("/token", data={
        "grant_type": "client_credentials",
        "client_id": "hermes-cli",
        "client_secret": "x",
    })
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "unsupported_grant_type"


# ── authorization_code 仍正常 (regression) ──────────────


def test_authorization_code_still_works(client: TestClient):
    """加了 client_credentials 不能影响老 SSO flow"""
    # 1. 登录拿 code
    r = client.post(
        "/authorize",
        data={
            "client_id": "test-app",
            "redirect_uri": "http://example.com/cb",
            "scope": "openid",
            "state": "xyz",
            "nonce": "n",
            "email": "alice@x.com",
            "password": "password123",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    from urllib.parse import parse_qs, urlparse
    qs = parse_qs(urlparse(r.headers["location"]).query)
    code = qs["code"][0]

    # 2. 换 token
    r = client.post("/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": "http://example.com/cb",
        "client_id": "test-app",
    })
    assert r.status_code == 200
    body = r.json()
    # authorization_code 必须有 id_token (跟 client_credentials 区别)
    assert "id_token" in body
    assert "access_token" in body


def test_authorization_code_missing_code(client: TestClient):
    """authorization_code grant 没传 code → 400 invalid_request"""
    r = client.post("/token", data={
        "grant_type": "authorization_code",
        "redirect_uri": "http://example.com/cb",
        "client_id": "test-app",
    })
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_request"


# ── 5/18 BL-HERMES-AUTH-LONGLIVED: per-client TTL ───────────


def _build_app_with_ttl(tmp_path, ttl_yaml: str) -> tuple[FastAPI, ClientRegistry]:
    """构造一个 app, hermes-cli 在 yaml 里配 ttl_yaml (字符串, 比如 '2592000' / ''/-'badval')."""
    users_yaml = tmp_path / "users.yaml"
    users_yaml.write_text(f"""
users:
  - email: alice@x.com
    password_hash: {hash_password("password123")}
    name: Alice
    department: sales
""")
    extra_ttl = f"\n    service_token_ttl_seconds: {ttl_yaml}" if ttl_yaml else ""
    clients_yaml = tmp_path / "clients.yaml"
    clients_yaml.write_text(f"""
clients:
  - client_id: hermes-cli
    client_secret_hash: {_hash("hermes-secret")}
    allowed_grant_types:
      - client_credentials
    allowed_scopes:
      - chat.completions
    department: infra
    role: service
    enabled: true{extra_ttl}
""", encoding="utf-8")
    keys_dir = tmp_path / "keys"
    signer = JwtSigner(key_dir=keys_dir)
    registry = UserRegistry(users_path=users_yaml)
    client_registry = ClientRegistry(clients_path=clients_yaml)
    fastapi_app = FastAPI()
    fastapi_app.include_router(
        make_router(
            issuer="http://test:8998",
            signer=signer,
            registry=registry,
            code_store=_CodeStore(),
            client_registry=client_registry,
        )
    )
    fastapi_app.state.signer = signer
    return fastapi_app, client_registry


def test_per_client_ttl_30_days(tmp_path):
    """clients.yaml 配 service_token_ttl_seconds: 2592000 → expires_in = 30 天"""
    app, _ = _build_app_with_ttl(tmp_path, "2592000")
    c = TestClient(app)
    r = c.post("/token", data={
        "grant_type": "client_credentials",
        "client_id": "hermes-cli",
        "client_secret": "hermes-secret",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["expires_in"] == 2592000

    # JWT 内 exp - iat 也是 30 天
    from cryptography.hazmat.primitives import serialization
    signer = app.state.signer
    pub_pem = signer._public_key.public_bytes(  # noqa: SLF001
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    payload = pyjwt.decode(
        body["access_token"], pub_pem,
        algorithms=["RS256"], audience="catfish-gateway",
    )
    assert payload["exp"] - payload["iat"] == 2592000


def test_per_client_ttl_caps_at_one_year(tmp_path):
    """clients.yaml 配 999999999 → 被 cap 到 365 天"""
    app, _ = _build_app_with_ttl(tmp_path, "999999999")
    c = TestClient(app)
    r = c.post("/token", data={
        "grant_type": "client_credentials",
        "client_id": "hermes-cli",
        "client_secret": "hermes-secret",
    })
    assert r.status_code == 200
    assert r.json()["expires_in"] == 365 * 24 * 3600


def test_per_client_ttl_floor_60_seconds(tmp_path):
    """配太小 (5 秒) → 被 floor 到 60 秒, 防一个 token 还没用就过期"""
    app, _ = _build_app_with_ttl(tmp_path, "5")
    c = TestClient(app)
    r = c.post("/token", data={
        "grant_type": "client_credentials",
        "client_id": "hermes-cli",
        "client_secret": "hermes-secret",
    })
    assert r.status_code == 200
    assert r.json()["expires_in"] == 60


def test_per_client_ttl_default_when_unset(tmp_path):
    """yaml 没配 → 走 1h 默认"""
    app, _ = _build_app_with_ttl(tmp_path, "")
    c = TestClient(app)
    r = c.post("/token", data={
        "grant_type": "client_credentials",
        "client_id": "hermes-cli",
        "client_secret": "hermes-secret",
    })
    assert r.status_code == 200
    assert r.json()["expires_in"] == 3600


def test_per_client_ttl_bad_value_falls_back_default(tmp_path):
    """配非数字 (字符串 'abc') → 走默认, 不爆"""
    app, _ = _build_app_with_ttl(tmp_path, '"abc"')  # yaml 字符串
    c = TestClient(app)
    r = c.post("/token", data={
        "grant_type": "client_credentials",
        "client_id": "hermes-cli",
        "client_secret": "hermes-secret",
    })
    assert r.status_code == 200
    # IdentityClient.service_token_ttl_seconds=None (parse 失败 fallback) → 默认 1h
    assert r.json()["expires_in"] == 3600


def test_per_client_ttl_zero_uses_default(tmp_path):
    """配 0 → 视为'没配', 走默认 (避免发零 TTL 的废 token)"""
    app, _ = _build_app_with_ttl(tmp_path, "0")
    c = TestClient(app)
    r = c.post("/token", data={
        "grant_type": "client_credentials",
        "client_id": "hermes-cli",
        "client_secret": "hermes-secret",
    })
    assert r.status_code == 200
    assert r.json()["expires_in"] == 3600
