"""IP + 域名 / 公网映射同时登录 (11/08): 网关认 identity 全部对外地址签出来的 token。"""
from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from catfish_gateway.auth import CompositeProvider, OIDCProvider, make_auth_provider

IP = "https://192.168.31.199"
DOMAIN = "https://catfish.example.com"
NAT = "https://203.0.113.7:8443"
AUD = "catfish-companion"
KID = "k1"


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def jwks(key):
    return {"keys": [{**jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True),
                      "kid": KID, "alg": "RS256", "use": "sig"}]}


def _token(key, iss: str | None, **extra) -> str:
    now = int(time.time())
    payload = {"sub": "alice@x.com", "aud": AUD, "iat": now, "exp": now + 600, **extra}
    if iss is not None:
        payload["iss"] = iss
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    return jwt.encode(payload, pem, algorithm="RS256", headers={"kid": KID})


@pytest.mark.parametrize("given", [[IP, DOMAIN, NAT], f"{IP},{DOMAIN}/ , {NAT}"])
def test_accepts_every_configured_issuer(key, jwks, given):
    p = OIDCProvider(issuer=given, audience=AUD, jwks_for_testing=jwks)
    assert p.issuers == [IP, DOMAIN, NAT]
    assert p.issuer == IP  # 主地址
    for iss in (IP, DOMAIN, NAT):
        user = p.verify_bearer(f"Bearer {_token(key, iss)}")
        assert user is not None and user.sub == "alice@x.com", iss


@pytest.mark.parametrize("iss", ["https://evil.example", "https://203.0.113.7", "http://192.168.31.199", None])
def test_rejects_issuer_not_in_list_or_missing(key, jwks, iss):
    p = OIDCProvider(issuer=[IP, DOMAIN, NAT], audience=AUD, jwks_for_testing=jwks)
    assert p.verify_bearer(f"Bearer {_token(key, iss)}") is None


def test_single_issuer_unchanged(key, jwks):
    p = OIDCProvider(issuer=IP + "/", audience=AUD, jwks_for_testing=jwks)
    assert p.issuers == [IP] and p.issuer == IP
    assert p.jwks_uri == f"{IP}/.well-known/jwks.json"
    assert p.verify_bearer(f"Bearer {_token(key, IP)}") is not None
    assert p.verify_bearer(f"Bearer {_token(key, DOMAIN)}") is None


def test_empty_issuer_refused():
    with pytest.raises(ValueError):
        OIDCProvider(issuer=" , ", audience=AUD, jwks_for_testing={"keys": []})


def test_factory_merges_issuer_and_issuers(monkeypatch):
    monkeypatch.setenv("CATFISH_ENV", "prod")
    monkeypatch.setenv("CATFISH_OIDC_ISSUER", IP)
    monkeypatch.setenv("CATFISH_OIDC_ISSUERS", f"{DOMAIN},{IP},{NAT}")
    monkeypatch.setenv("CATFISH_OIDC_JWKS_URI", "http://identity:8998/.well-known/jwks.json")
    p = make_auth_provider()
    assert isinstance(p, CompositeProvider)
    oidc = p.providers[0]
    assert isinstance(oidc, OIDCProvider)
    assert oidc.issuers == [IP, DOMAIN, NAT]
    # jwks 仍然走内网直连, 跟地址个数无关
    assert oidc.jwks_uri == "http://identity:8998/.well-known/jwks.json"


def test_factory_comma_separated_issuer_alone(monkeypatch):
    monkeypatch.setenv("CATFISH_ENV", "prod")
    monkeypatch.setenv("CATFISH_OIDC_ISSUER", f"{IP},{DOMAIN}")
    monkeypatch.delenv("CATFISH_OIDC_ISSUERS", raising=False)
    p = make_auth_provider()
    assert p.providers[0].issuers == [IP, DOMAIN]


def test_factory_issuers_alone_is_enough(monkeypatch):
    """relay (DMZ) 的 relay.env 里只写了 CATFISH_OIDC_ISSUERS 也能起 OIDC。"""
    monkeypatch.setenv("CATFISH_ENV", "prod")
    monkeypatch.delenv("CATFISH_OIDC_ISSUER", raising=False)
    monkeypatch.setenv("CATFISH_OIDC_ISSUERS", DOMAIN)
    p = make_auth_provider()
    assert isinstance(p, CompositeProvider)
    assert p.providers[0].issuers == [DOMAIN]
