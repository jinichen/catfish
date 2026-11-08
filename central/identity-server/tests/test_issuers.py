"""IP + 域名 / 公网映射同时登录 (11/08): 一个 identity, 按请求来源挑 issuer。"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from catfish_identity.issuers import (
    configured_issuers,
    pick_issuer,
    request_origin,
)
from catfish_identity.jwt_signer import JwtSigner
from catfish_identity.refresh_tokens import RefreshTokenStore
from catfish_identity.routes import _CodeStore, make_router
from catfish_identity.users import UserRegistry, hash_password

IP = "https://192.168.31.199"
DOMAIN = "https://catfish.example.com"
NAT = "https://203.0.113.7:8443"
ISSUERS = [IP, DOMAIN, NAT]


# ── 纯函数 ──────────────────────────────────────────────────────────


def test_configured_issuers_list_and_legacy(monkeypatch):
    monkeypatch.delenv("CATFISH_IDENTITY_ISSUERS", raising=False)
    monkeypatch.delenv("CATFISH_IDENTITY_ISSUER", raising=False)
    assert configured_issuers("http://0.0.0.0:8998") == ["http://0.0.0.0:8998"]
    monkeypatch.setenv("CATFISH_IDENTITY_ISSUER", IP + "/")
    assert configured_issuers("x") == [IP]
    monkeypatch.setenv("CATFISH_IDENTITY_ISSUERS", f"{IP}, {DOMAIN} ,{NAT},{DOMAIN}")
    assert configured_issuers("x") == ISSUERS
    # 单值那个永远排第一 (它就是主地址), 即便列表里顺序不同
    monkeypatch.setenv("CATFISH_IDENTITY_ISSUERS", f"{DOMAIN},{IP}")
    assert configured_issuers("x") == [IP, DOMAIN]
    monkeypatch.delenv("CATFISH_IDENTITY_ISSUER")
    assert configured_issuers("x") == [DOMAIN, IP]


@pytest.mark.parametrize(
    ("headers", "want"),
    [
        ({"host": "192.168.31.199", "x-forwarded-proto": "https"}, IP),
        ({"host": "192.168.31.199:443", "x-forwarded-proto": "https"}, IP),
        ({"host": "catfish.example.com", "x-forwarded-proto": "https"}, DOMAIN),
        ({"host": "CATFISH.Example.com", "x-forwarded-proto": "https"}, DOMAIN),
        # 公网 203.0.113.7:8443 → 内网 443: nginx 的 $host 去掉了端口, X-Forwarded-Host 带着
        ({"host": "203.0.113.7", "x-forwarded-host": "203.0.113.7:8443",
          "x-forwarded-proto": "https"}, NAT),
        ({"host": "203.0.113.7:8443", "x-forwarded-proto": "https"}, NAT),
        # 端口在路上丢了 (前面一层只转 $host), 但这个 host 只有一个 issuer → 仍然认得
        ({"host": "203.0.113.7", "x-forwarded-proto": "https"}, NAT),
        # 不认识的来源 → 主地址 (跟以前一样), 不会把请求里的东西当 issuer
        ({"host": "evil.example", "x-forwarded-proto": "https"}, IP),
        ({"host": "catfish.example.com:9999", "x-forwarded-proto": "http"}, DOMAIN),
        ({}, IP),
    ],
)
def test_pick_issuer(headers, want):
    origin = request_origin(headers, "http", "identity:8998")
    assert pick_issuer(ISSUERS, origin) == want


def test_same_host_two_ports_needs_exact_port():
    a, b = "https://203.0.113.7", "https://203.0.113.7:8443"
    issuers = [IP, a, b]
    assert pick_issuer(issuers, request_origin({"host": "203.0.113.7:8443", "x-forwarded-proto": "https"}, "http", "")) == b
    assert pick_issuer(issuers, request_origin({"host": "203.0.113.7", "x-forwarded-proto": "https"}, "http", "")) == a
    # 端口对不上任何一个, host 又不唯一 → 主地址
    assert pick_issuer(issuers, request_origin({"host": "203.0.113.7:9000", "x-forwarded-proto": "https"}, "http", "")) == IP


def test_http_mode_direct_8998():
    issuers = ["http://10.1.2.3:8998"]
    assert pick_issuer(issuers, request_origin({"host": "10.1.2.3:8998"}, "http", "")) == issuers[0]


# ── 端到端: discovery / token / refresh 都按来源走 ────────────────────


@pytest.fixture
def client(tmp_path) -> TestClient:
    users_yaml = tmp_path / "users.yaml"
    users_yaml.write_text(f"""
users:
  - email: alice@x.com
    password_hash: {hash_password("password123")}
    name: Alice
    department: sales
    tier: employee
""")
    signer = JwtSigner(key_dir=tmp_path / "keys")
    app = FastAPI()
    app.include_router(make_router(
        issuer=IP,
        issuers=ISSUERS,
        signer=signer,
        registry=UserRegistry(users_path=users_yaml),
        code_store=_CodeStore(db_path=tmp_path / "codes.db"),
        refresh_token_store=RefreshTokenStore(db_path=tmp_path / "rt.db"),
    ))
    app.state.signer = signer
    return TestClient(app)


def _hdr(origin: str) -> dict[str, str]:
    u = urlparse(origin)
    return {"host": u.hostname or "", "x-forwarded-host": u.netloc, "x-forwarded-proto": u.scheme}


@pytest.mark.parametrize("origin", ISSUERS)
def test_discovery_follows_request_origin(client, origin):
    cfg = client.get("/.well-known/openid-configuration", headers=_hdr(origin)).json()
    assert cfg["issuer"] == origin
    assert cfg["authorization_endpoint"] == f"{origin}/authorize"
    assert cfg["token_endpoint"] == f"{origin}/token"
    assert cfg["jwks_uri"] == f"{origin}/.well-known/jwks.json"


def _login(client, origin: str) -> dict:
    h = _hdr(origin)
    r = client.post("/authorize", headers=h, follow_redirects=False, data={
        "client_id": "catfish-companion", "redirect_uri": f"{origin}/auth/callback",
        "scope": "openid email profile", "state": "s", "email": "alice@x.com",
        "password": "password123",
    })
    assert r.status_code == 302
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    r = client.post("/token", headers=h, data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": f"{origin}/auth/callback", "client_id": "catfish-companion",
    })
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("origin", ISSUERS)
def test_tokens_carry_the_origin_issuer_and_refresh_keeps_it(client, origin):
    toks = _login(client, origin)
    for k in ("id_token", "access_token"):
        assert jwt.decode(toks[k], options={"verify_signature": False})["iss"] == origin
    r = client.post("/token", headers=_hdr(origin), data={
        "grant_type": "refresh_token", "refresh_token": toks["refresh_token"],
        "client_id": "catfish-companion",
    })
    assert r.status_code == 200, r.text
    assert jwt.decode(r.json()["id_token"], options={"verify_signature": False})["iss"] == origin


def test_all_issuers_share_one_signing_key(client):
    kids = {jwt.get_unverified_header(_login(client, o)["id_token"])["kid"] for o in ISSUERS}
    assert len(kids) == 1
    jwks = [client.get("/.well-known/jwks.json", headers=_hdr(o)).json() for o in ISSUERS]
    assert jwks[0] == jwks[1] == jwks[2]


def test_single_issuer_ignores_host(tmp_path):
    """没配多个地址时跟以前完全一样: 不看请求来源。"""
    app = FastAPI()
    app.include_router(make_router(
        issuer="https://10.0.0.1", signer=JwtSigner(key_dir=tmp_path / "k"),
        registry=UserRegistry(users_path=tmp_path / "none.yaml"),
        code_store=_CodeStore(db_path=tmp_path / "c.db"),
    ))
    cfg = TestClient(app).get("/.well-known/openid-configuration", headers=_hdr(DOMAIN)).json()
    assert cfg["issuer"] == "https://10.0.0.1"


def test_create_app_reads_env_list_and_allows_every_origin(tmp_path, monkeypatch):
    from catfish_identity.app import create_app
    from catfish_identity.clients import ClientRegistry

    monkeypatch.setenv("CATFISH_IDENTITY_ISSUER", IP)
    monkeypatch.setenv("CATFISH_IDENTITY_ISSUERS", ",".join(ISSUERS))
    monkeypatch.setenv("CATFISH_IDENTITY_CORS_ORIGINS", IP)  # 故意漏了域名
    app = create_app(
        signer=JwtSigner(key_dir=tmp_path / "k"),
        registry=UserRegistry(users_path=tmp_path / "none.yaml"),
        code_store=_CodeStore(db_path=tmp_path / "c.db"),
        client_registry=ClientRegistry(clients_path=tmp_path / "none-clients.yaml"),
        refresh_token_store=RefreshTokenStore(db_path=tmp_path / "rt.db"),
    )
    c = TestClient(app)
    hz = c.get("/healthz").json()
    assert hz["issuer"] == IP and hz["issuers"] == ISSUERS
    for origin in ISSUERS:
        r = c.get("/.well-known/openid-configuration", headers={**_hdr(origin), "origin": origin})
        assert r.json()["issuer"] == origin
        assert r.headers.get("access-control-allow-origin") == origin
