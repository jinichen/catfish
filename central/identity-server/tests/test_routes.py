"""OIDC 5 个端点 + 完整 authorization code flow 单测.

用 fastapi TestClient 不真起 uvicorn.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from catfish_identity.jwt_signer import JwtSigner
from catfish_identity.routes import _CodeStore, make_router
from catfish_identity.users import UserRegistry, hash_password


@pytest.fixture
def app(tmp_path) -> FastAPI:
    """构造一个独立 app, 私钥 / users 都用 tmp_path 隔离."""
    pwd_hash = hash_password("password123")
    users_yaml = tmp_path / "users.yaml"
    users_yaml.write_text(f"""
users:
  - email: alice@x.com
    password_hash: {pwd_hash}
    name: Alice
    department: sales
    tier: employee
""")
    keys_dir = tmp_path / "keys"
    signer = JwtSigner(key_dir=keys_dir)
    registry = UserRegistry(users_path=users_yaml)
    code_store = _CodeStore()

    fastapi_app = FastAPI()
    fastapi_app.include_router(
        make_router(
            issuer="http://test:8998",
            signer=signer,
            registry=registry,
            code_store=code_store,
        )
    )
    fastapi_app.state.signer = signer  # 测试方便拿
    return fastapi_app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ============================================================
# Discovery + JWKS
# ============================================================


def test_discovery_endpoint(client: TestClient) -> None:
    r = client.get("/.well-known/openid-configuration")
    assert r.status_code == 200
    cfg = r.json()
    assert cfg["issuer"] == "http://test:8998"
    assert cfg["authorization_endpoint"] == "http://test:8998/authorize"
    assert cfg["token_endpoint"] == "http://test:8998/token"
    assert cfg["jwks_uri"] == "http://test:8998/.well-known/jwks.json"
    assert "code" in cfg["response_types_supported"]
    assert "RS256" in cfg["id_token_signing_alg_values_supported"]


def test_jwks_endpoint(client: TestClient) -> None:
    r = client.get("/.well-known/jwks.json")
    assert r.status_code == 200
    jwks = r.json()
    assert len(jwks["keys"]) == 1
    key = jwks["keys"][0]
    assert key["kty"] == "RSA"
    assert key["alg"] == "RS256"
    assert key["use"] == "sig"
    assert "n" in key and "e" in key


# ============================================================
# Authorize GET (登录页)
# ============================================================


def test_authorize_get_returns_login_page(client: TestClient) -> None:
    r = client.get("/authorize", params={
        "client_id": "test-client",
        "redirect_uri": "http://app/cb",
        "response_type": "code",
        "scope": "openid email",
        "state": "abc123",
    })
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "登录" in r.text
    # 隐藏字段透传到 form
    assert 'value="test-client"' in r.text
    assert 'value="abc123"' in r.text
    assert 'value="http://app/cb"' in r.text


def test_authorize_get_rejects_unsupported_response_type(client: TestClient) -> None:
    r = client.get("/authorize", params={
        "client_id": "x",
        "redirect_uri": "http://x/cb",
        "response_type": "token",  # implicit flow not supported
    })
    assert r.status_code == 400


# ============================================================
# Authorize POST (验证 + redirect with code)
# ============================================================


def test_authorize_post_correct_password_redirects(client: TestClient) -> None:
    r = client.post(
        "/authorize",
        data={
            "client_id": "test-client",
            "redirect_uri": "http://app/cb",
            "scope": "openid email",
            "state": "abc123",
            "email": "alice@x.com",
            "password": "password123",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    location = r.headers["location"]
    parsed = urlparse(location)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "http://app/cb"
    qs = parse_qs(parsed.query)
    assert "code" in qs
    assert len(qs["code"][0]) > 20  # 长 random
    assert qs["state"] == ["abc123"]


def test_authorize_post_wrong_password_returns_login_page(client: TestClient) -> None:
    r = client.post(
        "/authorize",
        data={
            "client_id": "x",
            "redirect_uri": "http://x/cb",
            "email": "alice@x.com",
            "password": "WRONG",
        },
        follow_redirects=False,
    )
    assert r.status_code == 401
    assert "email 或密码错误" in r.text


def test_authorize_post_unknown_user_returns_login_page(client: TestClient) -> None:
    r = client.post(
        "/authorize",
        data={
            "client_id": "x",
            "redirect_uri": "http://x/cb",
            "email": "nobody@x.com",
            "password": "anything",
        },
        follow_redirects=False,
    )
    assert r.status_code == 401


# ============================================================
# Token exchange (full flow)
# ============================================================


def _login_get_code(client: TestClient, state: str = "test-state") -> str:
    """跑完 authorize → 拿 code helper."""
    r = client.post(
        "/authorize",
        data={
            "client_id": "test-client",
            "redirect_uri": "http://app/cb",
            "scope": "openid email profile",
            "state": state,
            "nonce": "test-nonce",
            "email": "alice@x.com",
            "password": "password123",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    qs = parse_qs(urlparse(r.headers["location"]).query)
    return qs["code"][0]


def test_token_exchange_returns_id_and_access_token(client: TestClient, app: FastAPI) -> None:
    code = _login_get_code(client)
    r = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://app/cb",
            "client_id": "test-client",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 3600
    assert "id_token" in body
    assert "access_token" in body

    # 验 id_token 结构
    signer: JwtSigner = app.state.signer
    payload = jwt.decode(
        body["id_token"],
        signer._public_key,
        algorithms=["RS256"],
        audience="test-client",
    )
    assert payload["sub"] == "alice@x.com"
    assert payload["iss"] == "http://test:8998"
    assert payload["email"] == "alice@x.com"
    assert payload["nonce"] == "test-nonce"


def test_token_exchange_rejects_used_code(client: TestClient) -> None:
    code = _login_get_code(client)
    # 第一次成功
    r1 = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://app/cb",
            "client_id": "test-client",
        },
    )
    assert r1.status_code == 200
    # 第二次 (replay) 应该失败
    r2 = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://app/cb",
            "client_id": "test-client",
        },
    )
    assert r2.status_code == 400


def test_token_exchange_rejects_wrong_client_id(client: TestClient) -> None:
    code = _login_get_code(client)
    r = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://app/cb",
            "client_id": "DIFFERENT",  # 跟 authorize 时的不一样
        },
    )
    assert r.status_code == 400


def test_token_exchange_rejects_wrong_redirect_uri(client: TestClient) -> None:
    code = _login_get_code(client)
    r = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://evil/cb",  # 跟 authorize 时的不一样
            "client_id": "test-client",
        },
    )
    assert r.status_code == 400


def test_token_exchange_rejects_unsupported_grant(client: TestClient) -> None:
    r = client.post(
        "/token",
        data={
            "grant_type": "password",  # 我们只支持 authorization_code
            "code": "x",
            "redirect_uri": "http://x/cb",
            "client_id": "x",
        },
    )
    assert r.status_code == 400


def test_token_exchange_rejects_invalid_code(client: TestClient) -> None:
    r = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": "fake-code-not-issued",
            "redirect_uri": "http://x/cb",
            "client_id": "x",
        },
    )
    assert r.status_code == 400


# ============================================================
# /userinfo
# ============================================================


def test_userinfo_with_valid_access_token(client: TestClient) -> None:
    code = _login_get_code(client)
    r = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://app/cb",
            "client_id": "test-client",
        },
    )
    access_token = r.json()["access_token"]

    r2 = client.get(
        "/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert r2.status_code == 200
    info = r2.json()
    assert info["sub"] == "alice@x.com"
    assert info["email"] == "alice@x.com"
    assert info["name"] == "Alice"
    assert info["department"] == "sales"


def test_userinfo_without_token(client: TestClient) -> None:
    r = client.get("/userinfo")
    assert r.status_code == 401


def test_userinfo_with_invalid_token(client: TestClient) -> None:
    r = client.get(
        "/userinfo",
        headers={"Authorization": "Bearer not.a.valid.jwt"},
    )
    assert r.status_code == 401
