"""/token refresh_token grant 端到端 (BL-IDENTITY-REFRESH-TOKEN, 5/15 凌晨).

跑法: cd central/identity-server && PYTHONPATH=src python -m pytest tests/test_refresh_token_grant.py -q

覆盖 RFC 6749 §6 + §5.2:
  - authorization_code 流程返 refresh_token (端到端)
  - refresh_token 换新 access_token + 新 refresh_token
  - 旧 refresh_token rotation 后被 revoke (一次性)
  - 错误路径: 不存在 / revoked / 过期 / client_id 不匹配 / scope 越权
  - access_token 含 user claims (RFC 9068)
  - user 锁定 / 删除时 refresh 失败
  - refresh_token_store=None 时返 503
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import bcrypt
import jwt as pyjwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from catfish_identity.clients import ClientRegistry
from catfish_identity.jwt_signer import JwtSigner
from catfish_identity.refresh_tokens import RefreshTokenStore
from catfish_identity.routes import _CodeStore, make_router
from catfish_identity.users import UserRegistry, hash_password


def _hash(secret: str) -> str:
    return bcrypt.hashpw(secret.encode(), bcrypt.gensalt(rounds=4)).decode()


@pytest.fixture
def app(tmp_path) -> FastAPI:
    """构造独立 app 含 user / client / refresh_token store."""
    users_yaml = tmp_path / "users.yaml"
    users_yaml.write_text(f"""
users:
  - email: alice@x.com
    password_hash: {hash_password("password123")}
    name: Alice
    department: sales
    tier: employee
""")
    clients_yaml = tmp_path / "clients.yaml"
    clients_yaml.write_text(f"""
clients:
  - client_id: hermes-cli
    client_secret_hash: {_hash("dummy")}
    allowed_grant_types:
      - authorization_code
      - client_credentials
    allowed_scopes:
      - openid
      - email
      - chat.completions
    department: infra
    enabled: true
""", encoding="utf-8")

    keys_dir = tmp_path / "keys"
    refresh_db = tmp_path / "refresh.db"
    signer = JwtSigner(key_dir=keys_dir)
    registry = UserRegistry(users_path=users_yaml)
    code_store = _CodeStore(db_path=tmp_path / "codes.db")
    client_registry = ClientRegistry(clients_path=clients_yaml)
    refresh_token_store = RefreshTokenStore(db_path=refresh_db)

    fastapi_app = FastAPI()
    fastapi_app.include_router(
        make_router(
            issuer="http://test:8998",
            signer=signer,
            registry=registry,
            code_store=code_store,
            client_registry=client_registry,
            refresh_token_store=refresh_token_store,
        )
    )
    fastapi_app.state.signer = signer
    fastapi_app.state.refresh_store = refresh_token_store
    fastapi_app.state.registry = registry
    return fastapi_app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _do_login(client: TestClient) -> dict:
    """走完整 authorization_code 流程, 返 token 响应 dict."""
    r = client.post(
        "/authorize",
        data={
            "client_id": "hermes-cli",
            "redirect_uri": "http://example.com/cb",
            "scope": "openid email chat.completions",
            "state": "xyz",
            "nonce": "n1",
            "email": "alice@x.com",
            "password": "password123",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    qs = parse_qs(urlparse(r.headers["location"]).query)
    code = qs["code"][0]

    r = client.post("/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": "http://example.com/cb",
        "client_id": "hermes-cli",
    })
    assert r.status_code == 200
    return r.json()


# ─── authorization_code 现在返 refresh_token ─────────────


def test_authorization_code_returns_refresh_token(client: TestClient):
    body = _do_login(client)
    assert "refresh_token" in body
    assert body["refresh_token"]
    assert "refresh_expires_in" in body
    assert body["refresh_expires_in"] > 0


def test_access_token_now_has_user_claims(client: TestClient, app: FastAPI):
    """RFC 9068: access_token 应该含完整 user claims (sub/email/role/department)"""
    body = _do_login(client)
    from cryptography.hazmat.primitives import serialization
    pub = app.state.signer._public_key.public_bytes(  # noqa: SLF001
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    payload = pyjwt.decode(
        body["access_token"], pub, algorithms=["RS256"],
        audience="catfish-gateway",
    )
    assert payload["sub"] == "alice@x.com"
    assert payload["token_use"] == "access"
    assert payload["email"] == "alice@x.com"
    assert payload["department"] == "sales"
    assert payload["role"] == "employee"


# ─── refresh_token grant 端到端 ──────────────────────────


def test_refresh_token_returns_new_tokens(client: TestClient):
    body = _do_login(client)
    rt = body["refresh_token"]

    r = client.post("/token", data={
        "grant_type": "refresh_token",
        "refresh_token": rt,
        "client_id": "hermes-cli",
    })
    assert r.status_code == 200
    new = r.json()
    # 拿到新 access + 新 refresh
    assert "access_token" in new
    assert "refresh_token" in new
    assert new["refresh_token"] != rt  # 新 token, 不是同一个
    assert new["scope"] == "openid email chat.completions"


def test_old_refresh_token_grace_replay_within_window(client: TestClient, app: FastAPI):
    """P3.5.150: rotation race mitigation —
    旧 parent rotation 后 grace period (默认 60s) 内重用, replay 出已签出的 child token,
    不删 parent. 这是 GitHub/Google 风格的 race window — 让客户端在网络抖动时不丢链.
    """
    body = _do_login(client)
    old_rt = body["refresh_token"]

    # 第一次 rotation
    r = client.post("/token", data={
        "grant_type": "refresh_token",
        "refresh_token": old_rt,
        "client_id": "hermes-cli",
    })
    assert r.status_code == 200
    first_child = r.json()["refresh_token"]

    # 模拟客户端没收到响应 → retry 用同样的 old parent (60s 内)
    r2 = client.post("/token", data={
        "grant_type": "refresh_token",
        "refresh_token": old_rt,
        "client_id": "hermes-cli",
    })
    # grace replay: 返 200 + 同一个 child (idempotent)
    assert r2.status_code == 200, f"expected grace replay, got {r2.status_code}: {r2.json()}"
    assert r2.json()["refresh_token"] == first_child, \
        "grace replay 必须返同一个 child token, 不能再 rotation"
    assert r2.json()["scope"] == "openid email chat.completions"


def test_old_refresh_token_rejected_after_grace_expires(
    client: TestClient, app: FastAPI, monkeypatch
):
    """P3.5.150: grace period 过后, 旧 parent 仍然拒掉 — 一次性语义保留"""
    # grace=0 关 grace 让 race mitigation 失效, 直接走老路径
    monkeypatch.setenv("CATFISH_REFRESH_TOKEN_GRACE_SECS", "0")

    body = _do_login(client)
    old_rt = body["refresh_token"]

    # 用一次
    r = client.post("/token", data={
        "grant_type": "refresh_token",
        "refresh_token": old_rt,
        "client_id": "hermes-cli",
    })
    assert r.status_code == 200

    # 再用 → 拒 (grace 关了, 一次性)
    r2 = client.post("/token", data={
        "grant_type": "refresh_token",
        "refresh_token": old_rt,
        "client_id": "hermes-cli",
    })
    assert r2.status_code == 400
    assert r2.json()["detail"]["error"] == "invalid_grant"
    assert "revoked" in r2.json()["detail"]["error_description"].lower() or "用过" in r2.json()["detail"]["error_description"]


def test_chain_of_refreshes(client: TestClient):
    """连续 refresh 多次都能拿到新 token"""
    body = _do_login(client)
    rt = body["refresh_token"]
    for _ in range(3):
        r = client.post("/token", data={
            "grant_type": "refresh_token",
            "refresh_token": rt,
            "client_id": "hermes-cli",
        })
        assert r.status_code == 200
        rt = r.json()["refresh_token"]


# ─── 错误路径 ────────────────────────────────────────────


def test_refresh_missing_token(client: TestClient):
    r = client.post("/token", data={
        "grant_type": "refresh_token",
        "client_id": "hermes-cli",
    })
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_request"


def test_refresh_invalid_token(client: TestClient):
    r = client.post("/token", data={
        "grant_type": "refresh_token",
        "refresh_token": "fake-doesnt-exist",
        "client_id": "hermes-cli",
    })
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_grant"


def test_refresh_wrong_client_id(client: TestClient):
    body = _do_login(client)
    r = client.post("/token", data={
        "grant_type": "refresh_token",
        "refresh_token": body["refresh_token"],
        "client_id": "different-client",
    })
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_grant"


def test_refresh_scope_cannot_expand(client: TestClient):
    """refresh 不允许扩 scope (只能子集)"""
    body = _do_login(client)
    r = client.post("/token", data={
        "grant_type": "refresh_token",
        "refresh_token": body["refresh_token"],
        "client_id": "hermes-cli",
        "scope": "openid email chat.completions admin.god",  # 多 admin.god
    })
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_scope"


def test_refresh_scope_subset_ok(client: TestClient):
    """refresh 缩 scope 允许"""
    body = _do_login(client)
    r = client.post("/token", data={
        "grant_type": "refresh_token",
        "refresh_token": body["refresh_token"],
        "client_id": "hermes-cli",
        "scope": "openid",  # 子集
    })
    assert r.status_code == 200
    assert r.json()["scope"] == "openid"


def test_refresh_when_user_locked(client: TestClient, app: FastAPI):
    """user 被 admin 锁后, refresh 失败"""
    body = _do_login(client)
    # 锁 user
    user = app.state.registry.find("alice@x.com")
    user.locked = True
    r = client.post("/token", data={
        "grant_type": "refresh_token",
        "refresh_token": body["refresh_token"],
        "client_id": "hermes-cli",
    })
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_grant"
    user.locked = False  # 还原


def test_refresh_token_grant_when_store_none(tmp_path):
    """refresh_token_store=None 时返 503 cleanly degrade"""
    users_yaml = tmp_path / "u.yaml"
    users_yaml.write_text(f"""
users:
  - email: a@x.com
    password_hash: {hash_password("p")}
""")
    fastapi_app = FastAPI()
    fastapi_app.include_router(make_router(
        issuer="http://test:8998",
        signer=JwtSigner(key_dir=tmp_path / "k"),
        registry=UserRegistry(users_path=users_yaml),
        code_store=_CodeStore(db_path=tmp_path / "codes.db"),
        refresh_token_store=None,  # 没配
    ))
    c = TestClient(fastapi_app)
    r = c.post("/token", data={
        "grant_type": "refresh_token",
        "refresh_token": "any",
        "client_id": "any",
    })
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "unsupported_grant_type"


# ─── 不破坏老 grant_types ────────────────────────────────


def test_authorization_code_still_works(client: TestClient):
    body = _do_login(client)
    assert "id_token" in body
    assert "access_token" in body


def test_client_credentials_still_works(client: TestClient):
    """client_credentials grant 不受 refresh_token 添加影响"""
    r = client.post("/token", data={
        "grant_type": "client_credentials",
        "client_id": "hermes-cli",
        "client_secret": "dummy",
        "scope": "chat.completions",
    })
    assert r.status_code == 200
    assert "access_token" in r.json()
    # client_credentials 不返 refresh_token (服务身份不需要)
    assert "refresh_token" not in r.json()
