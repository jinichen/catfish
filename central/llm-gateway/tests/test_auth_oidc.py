"""OIDCProvider + CompositeProvider 单测.

不依赖跨项目 import (catfish-identity 是独立项目). 自己生成 RSA + 签 token + 验.

覆盖:
  - 验 OIDC token (合法 / 过期 / 错 iss / 错 aud / 错签 / 缺 sub / access_token 拒)
  - jwks 注入 (jwks_for_testing) 跳过 HTTP 拉
  - CompositeProvider 优先级 / fallback / 全 fail
  - make_auth_provider env=prod 各种配置
"""
from __future__ import annotations

import base64
import hashlib
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from catfish_gateway.auth import (
    AuthProvider,
    CompositeProvider,
    DevTokenProvider,
    OIDCProvider,
    User,
    make_auth_provider,
)


# ============================================================
# fixtures: RSA + jwks + 签 token (不依赖 catfish-identity)
# ============================================================


def _kid_from_pubkey(public_key: rsa.RSAPublicKey) -> str:
    pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(pem).hexdigest()[:16]


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _make_jwks(public_key: rsa.RSAPublicKey, kid: str) -> dict:
    n = public_key.public_numbers().n
    e = public_key.public_numbers().e
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": kid,
                "n": _b64url(n.to_bytes((n.bit_length() + 7) // 8, "big")),
                "e": _b64url(e.to_bytes((e.bit_length() + 7) // 8, "big")),
            }
        ]
    }


def _sign(
    private_key: rsa.RSAPrivateKey,
    kid: str,
    payload: dict,
) -> str:
    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(payload, priv_pem, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def keypair():
    """RSA 密钥对, scope=function 每个测试新生成 (隔离)."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def kid(keypair):
    return _kid_from_pubkey(keypair.public_key())


@pytest.fixture
def jwks(keypair, kid):
    return _make_jwks(keypair.public_key(), kid)


@pytest.fixture
def issuer():
    return "http://test-idp.local"


@pytest.fixture
def audience():
    return "catfish-companion"


@pytest.fixture
def provider(issuer, audience, jwks):
    """OIDCProvider, jwks 注入 (不真拉 HTTP)."""
    return OIDCProvider(
        issuer=issuer,
        audience=audience,
        jwks_for_testing=jwks,
    )


def _valid_payload(issuer: str, audience: str, **overrides) -> dict:
    now = int(time.time())
    base = {
        "iss": issuer,
        "sub": "alice@example.com",
        "aud": audience,
        "iat": now,
        "exp": now + 3600,
        "email": "alice@example.com",
        "department": "sales",
        "tier": "employee",
    }
    base.update(overrides)
    return base


# ============================================================
# OIDCProvider · 合法 token
# ============================================================


def test_valid_token_returns_user(provider, keypair, kid, issuer, audience) -> None:
    token = _sign(keypair, kid, _valid_payload(issuer, audience))
    user = provider.verify_bearer(f"Bearer {token}")
    assert user is not None
    assert user.sub == "alice@example.com"
    assert user.department == "sales"
    assert user.tier == "employee"
    assert user.auth_method == f"oidc:{issuer}"


# ============================================================
# OIDCProvider · 拒绝
# ============================================================


def test_reject_no_authorization(provider) -> None:
    assert provider.verify_bearer(None) is None
    assert provider.verify_bearer("") is None


def test_reject_no_bearer_prefix(provider) -> None:
    assert provider.verify_bearer("Token x") is None
    assert provider.verify_bearer("just-a-token") is None


def test_reject_empty_token(provider) -> None:
    assert provider.verify_bearer("Bearer ") is None
    assert provider.verify_bearer("Bearer    ") is None


def test_reject_garbage_token(provider) -> None:
    assert provider.verify_bearer("Bearer not.a.jwt") is None
    assert provider.verify_bearer("Bearer xxx") is None


def test_reject_expired_token(provider, keypair, kid, issuer, audience) -> None:
    payload = _valid_payload(issuer, audience, exp=int(time.time()) - 60)
    token = _sign(keypair, kid, payload)
    assert provider.verify_bearer(f"Bearer {token}") is None


def test_reject_wrong_issuer(provider, keypair, kid, audience) -> None:
    """token iss 跟 provider issuer 不一样 → 拒"""
    payload = _valid_payload("http://evil.com", audience)
    token = _sign(keypair, kid, payload)
    assert provider.verify_bearer(f"Bearer {token}") is None


def test_reject_wrong_audience(provider, keypair, kid, issuer) -> None:
    """token aud 跟 provider audience 不一样 → 拒"""
    payload = _valid_payload(issuer, "different-app")
    token = _sign(keypair, kid, payload)
    assert provider.verify_bearer(f"Bearer {token}") is None


def test_reject_missing_sub(provider, keypair, kid, issuer, audience) -> None:
    payload = _valid_payload(issuer, audience)
    payload.pop("sub")
    token = _sign(keypair, kid, payload)
    assert provider.verify_bearer(f"Bearer {token}") is None


def test_reject_empty_sub(provider, keypair, kid, issuer, audience) -> None:
    payload = _valid_payload(issuer, audience, sub="   ")
    token = _sign(keypair, kid, payload)
    assert provider.verify_bearer(f"Bearer {token}") is None


def test_reject_access_token_used_as_id_token(
    provider, keypair, kid, issuer, audience
) -> None:
    """catfish-identity 给 access_token 加 token_use='access', 不该当 id_token 用."""
    payload = _valid_payload(issuer, audience, token_use="access")
    token = _sign(keypair, kid, payload)
    assert provider.verify_bearer(f"Bearer {token}") is None


def test_reject_unknown_kid(provider, keypair, issuer, audience) -> None:
    """token header 的 kid 不在 jwks 里 → 拒"""
    payload = _valid_payload(issuer, audience)
    token = _sign(keypair, "bogus-kid-not-in-jwks", payload)
    assert provider.verify_bearer(f"Bearer {token}") is None


def test_reject_token_signed_by_other_key(
    provider, kid, issuer, audience
) -> None:
    """不同私钥签的 token, 即使 kid 假装一样, 验签也失败"""
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    payload = _valid_payload(issuer, audience)
    token = _sign(other_key, kid, payload)  # 假装是同 kid
    assert provider.verify_bearer(f"Bearer {token}") is None


# ============================================================
# OIDCProvider · metadata
# ============================================================


def test_provider_name(provider, issuer) -> None:
    assert provider.name == f"oidc:{issuer}"


def test_provider_is_strict(provider) -> None:
    assert provider.is_strict is True


def test_jwks_uri_default(issuer, audience, jwks) -> None:
    p = OIDCProvider(issuer=issuer, audience=audience, jwks_for_testing=jwks)
    assert p.jwks_uri == f"{issuer}/.well-known/jwks.json"


def test_jwks_uri_explicit(issuer, audience, jwks) -> None:
    p = OIDCProvider(
        issuer=issuer,
        audience=audience,
        jwks_uri="https://idp.test/keys",
        jwks_for_testing=jwks,
    )
    assert p.jwks_uri == "https://idp.test/keys"


# ============================================================
# CompositeProvider
# ============================================================


class _FixedUserProvider(AuthProvider):
    """测试用 — 永远返指定 User."""

    def __init__(self, user: User | None, name_: str = "fixed") -> None:
        self._user = user
        self._n = name_

    def verify_bearer(self, authorization):
        return self._user

    @property
    def name(self) -> str:
        return self._n


def test_composite_first_match_wins() -> None:
    u1 = User(sub="first@x", auth_method="first")
    u2 = User(sub="second@x", auth_method="second")
    composite = CompositeProvider([
        _FixedUserProvider(u1, "p1"),
        _FixedUserProvider(u2, "p2"),
    ])
    result = composite.verify_bearer("Bearer xxx")
    assert result is u1  # 第一个 provider 的 user


def test_composite_falls_through_to_next() -> None:
    u_late = User(sub="late@x", auth_method="late")
    composite = CompositeProvider([
        _FixedUserProvider(None, "p1"),     # 失败
        _FixedUserProvider(None, "p2"),     # 失败
        _FixedUserProvider(u_late, "p3"),   # 成功
    ])
    result = composite.verify_bearer("Bearer xxx")
    assert result is u_late


def test_composite_all_fail_returns_none() -> None:
    composite = CompositeProvider([
        _FixedUserProvider(None, "p1"),
        _FixedUserProvider(None, "p2"),
    ])
    assert composite.verify_bearer("Bearer xxx") is None


def test_composite_empty_raises() -> None:
    with pytest.raises(ValueError, match="至少需要"):
        CompositeProvider([])


def test_composite_name_lists_all() -> None:
    composite = CompositeProvider([
        _FixedUserProvider(None, "alpha"),
        _FixedUserProvider(None, "beta"),
    ])
    assert composite.name == "composite[alpha, beta]"


def test_composite_is_strict_high_watermark() -> None:
    """任一 strict → composite strict"""
    composite = CompositeProvider([
        DevTokenProvider(),  # is_strict=False
        _FixedUserProvider(None, "strict_one"),  # 默认 is_strict=True (AuthProvider 默认)
    ])
    assert composite.is_strict is True


def test_composite_all_lax_returns_lax() -> None:
    composite = CompositeProvider([DevTokenProvider(), DevTokenProvider()])
    assert composite.is_strict is False


def test_composite_with_real_oidc_and_dev_token(
    keypair, kid, jwks, issuer, audience, monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """真实组合: OIDC + dev_token. OIDC 通过用 OIDC, OIDC 失败 fallback dev_token."""
    monkeypatch.setenv("CATFISH_DEV_TOKEN", "dev-secret")
    # BL-security 5/9: dev_token env 兜底需要 yaml.default 段
    yaml_path = tmp_path / "dev_users.yaml"
    yaml_path.write_text("""
default:
  email: dev-user@catfish.dev
  name: Dev User
  department: engineering
  role: admin
""", encoding="utf-8")
    monkeypatch.setenv("CATFISH_DEV_USERS_PATH", str(yaml_path))
    composite = CompositeProvider([
        OIDCProvider(issuer=issuer, audience=audience, jwks_for_testing=jwks),
        DevTokenProvider(),
    ])

    # OIDC token 通过
    oidc_token = _sign(keypair, kid, _valid_payload(issuer, audience))
    user_oidc = composite.verify_bearer(f"Bearer {oidc_token}")
    assert user_oidc is not None
    assert user_oidc.auth_method == f"oidc:{issuer}"

    # OIDC 不识别但 dev_token 识别 (顺序很关键)
    user_dev = composite.verify_bearer("Bearer dev-secret")
    assert user_dev is not None
    assert user_dev.auth_method == "dev_token"

    # 都不识别 (随便假 token)
    assert composite.verify_bearer("Bearer garbage") is None


# ============================================================
# make_auth_provider 工厂
# ============================================================


def test_make_provider_dev_env_returns_dev_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CATFISH_ENV", "dev")
    p = make_auth_provider()
    assert isinstance(p, DevTokenProvider)


def test_make_provider_prod_with_oidc_returns_composite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CATFISH_ENV", "prod")
    monkeypatch.setenv("CATFISH_OIDC_ISSUER", "https://sso.test.com")
    monkeypatch.setenv("CATFISH_OIDC_AUDIENCE", "catfish-companion")
    p = make_auth_provider()
    assert isinstance(p, CompositeProvider)
    assert len(p.providers) == 2
    assert isinstance(p.providers[0], OIDCProvider)
    assert isinstance(p.providers[1], DevTokenProvider)
    assert p.providers[0].issuer == "https://sso.test.com"


def test_make_provider_prod_without_oidc_returns_dev_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env=prod 但 CATFISH_OIDC_ISSUER 没设 → fallback dev_token + warning"""
    monkeypatch.setenv("CATFISH_ENV", "prod")
    monkeypatch.delenv("CATFISH_OIDC_ISSUER", raising=False)
    p = make_auth_provider()
    assert isinstance(p, DevTokenProvider)


def test_make_provider_prod_with_explicit_jwks_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """支持 CATFISH_OIDC_JWKS_URI 覆盖默认 issuer/.well-known/jwks.json"""
    monkeypatch.setenv("CATFISH_ENV", "prod")
    monkeypatch.setenv("CATFISH_OIDC_ISSUER", "https://sso.test.com")
    monkeypatch.setenv("CATFISH_OIDC_JWKS_URI", "https://keys.alt.com/jwks.json")
    p = make_auth_provider()
    assert isinstance(p, CompositeProvider)
    oidc = p.providers[0]
    assert isinstance(oidc, OIDCProvider)
    assert oidc.jwks_uri == "https://keys.alt.com/jwks.json"
