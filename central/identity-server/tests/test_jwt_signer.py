"""jwt_signer 单测.

覆盖: 密钥生成 / 持久化 / 加载 / 签 / JWKS 导出 / 跨实例验签.
"""
from __future__ import annotations

import jwt
import pytest

from catfish_identity.jwt_signer import JwtSigner


def test_generates_keypair_on_first_run(tmp_path) -> None:
    signer = JwtSigner(key_dir=tmp_path)
    assert (tmp_path / "private.pem").exists()
    assert (tmp_path / "public.pem").exists()
    assert len(signer.kid) == 16  # sha256 前 16 hex


def test_reuses_existing_key(tmp_path) -> None:
    s1 = JwtSigner(key_dir=tmp_path)
    s2 = JwtSigner(key_dir=tmp_path)
    assert s1.kid == s2.kid  # 同一密钥 = 同一 kid


def test_sign_and_verify_roundtrip(tmp_path) -> None:
    signer = JwtSigner(key_dir=tmp_path)
    token = signer.sign_id_token(
        issuer="http://localhost:8998",
        subject="alice@x.com",
        audience="catfish-companion",
        claims={"email": "alice@x.com", "name": "Alice"},
        ttl_seconds=600,
    )
    # 用 jwks 验签
    jwks = signer.jwks()
    assert len(jwks["keys"]) == 1
    key_dict = jwks["keys"][0]
    assert key_dict["kty"] == "RSA"
    assert key_dict["alg"] == "RS256"
    assert key_dict["kid"] == signer.kid

    # PyJWK 从 jwks 构造验签 key
    public_jwk = jwt.PyJWK(key_dict)
    payload = jwt.decode(
        token, public_jwk.key, algorithms=["RS256"], audience="catfish-companion"
    )
    assert payload["sub"] == "alice@x.com"
    assert payload["iss"] == "http://localhost:8998"
    assert payload["email"] == "alice@x.com"


def test_kid_in_jwt_header(tmp_path) -> None:
    """gateway 验签时要从 header 拿 kid 找对应公钥, 必须签的时候带上."""
    signer = JwtSigner(key_dir=tmp_path)
    token = signer.sign_id_token(
        issuer="http://x", subject="x", audience="x", claims={}, ttl_seconds=60
    )
    headers = jwt.get_unverified_header(token)
    assert headers["kid"] == signer.kid
    assert headers["alg"] == "RS256"


def test_corrupted_private_key_raises(tmp_path) -> None:
    """私钥文件被乱写 → 加载时报错, 不静默 fallback"""
    bad = tmp_path / "private.pem"
    bad.write_text("not a real key")
    with pytest.raises((ValueError, Exception)):
        JwtSigner(key_dir=tmp_path)
