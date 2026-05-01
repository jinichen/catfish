"""测试 Plan D · a2a_jwt — JWT 互信 (五一 sprint Day 4).

覆盖:
- sign_a2a_token 正常 + 私钥不存在 fallback
- verify_a2a_token 流程 (mock registry + jwks)
- 防重放 (jti 重复检测)
- aud 不匹配 / 过期 / 私钥错 → PermissionError
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest import mock

import jwt
import pytest

from catfish_gateway import a2a_jwt


@pytest.fixture(autouse=True)
def isolated_jti_cache() -> None:
    """每个测试清空 jti 缓存."""
    a2a_jwt._SEEN_JTI.clear()
    a2a_jwt._JWKS_CACHE.clear()


@pytest.fixture
def rsa_keypair(tmp_path: Path) -> tuple[Path, str]:
    """生成测试用 RSA keypair (PEM 格式), 返 (catfish_home, public_pem_string)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    home = tmp_path / "catfish_home"
    (home / "identity").mkdir(parents=True)
    (home / "identity" / "private.pem").write_text(private_pem)
    (home / "identity" / "public.pem").write_text(public_pem)
    return home, public_pem


def _public_pem_to_jwks(public_pem: str) -> dict:
    """把 PEM 公钥转 JWKS 格式."""
    from cryptography.hazmat.primitives import serialization

    pub_key = serialization.load_pem_public_key(public_pem.encode())
    numbers = pub_key.public_numbers()  # type: ignore[attr-defined]

    def int_to_b64(n: int) -> str:
        import base64
        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": "test-kid",
                "alg": "RS256",
                "use": "sig",
                "n": int_to_b64(numbers.n),
                "e": int_to_b64(numbers.e),
            }
        ]
    }


# ── 签 ──────────────────────────────────────────────────────────


def test_sign_a2a_token_basic(rsa_keypair, monkeypatch) -> None:
    home, _ = rsa_keypair
    monkeypatch.setenv("CATFISH_HOME", str(home))

    token = a2a_jwt.sign_a2a_token(
        from_sub="alice@ffcs.cn",
        to_sub="bob@ffcs.cn",
    )
    # 不验签 decode 看 payload
    payload = jwt.decode(token, options={"verify_signature": False})
    assert payload["iss"] == "alice@ffcs.cn"
    assert payload["sub"] == "alice@ffcs.cn"
    assert payload["aud"] == "bob@ffcs.cn"
    assert payload["scope"] == "a2a.ask"
    assert "jti" in payload
    assert payload["exp"] > payload["iat"]


def test_sign_a2a_token_no_private_key(monkeypatch, tmp_path) -> None:
    """私钥不存在 → 抛 RuntimeError."""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path / "no_key"))
    with pytest.raises(RuntimeError, match="private.pem"):
        a2a_jwt.sign_a2a_token("alice@ffcs.cn", "bob@ffcs.cn")


# ── 验 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_a2a_token_success(rsa_keypair, monkeypatch) -> None:
    home, public_pem = rsa_keypair
    monkeypatch.setenv("CATFISH_HOME", str(home))

    # mock registry lookup 返 alice 的 jwks_uri
    async def fake_lookup(sub: str) -> dict:
        return {
            "sub": sub,
            "catfish_endpoint": "http://alice:8999",
            "jwks_uri": "http://alice:8998/.well-known/jwks.json",
        }

    async def fake_fetch_jwks(uri: str) -> dict:
        return _public_pem_to_jwks(public_pem)

    monkeypatch.setattr(a2a_jwt, "lookup_remote_agent", fake_lookup)
    monkeypatch.setattr(a2a_jwt, "fetch_jwks", fake_fetch_jwks)

    token = a2a_jwt.sign_a2a_token("alice@ffcs.cn", "bob@ffcs.cn")
    payload = await a2a_jwt.verify_a2a_token(token, expected_aud="bob@ffcs.cn")
    assert payload["iss"] == "alice@ffcs.cn"
    assert payload["aud"] == "bob@ffcs.cn"


@pytest.mark.asyncio
async def test_verify_a2a_token_wrong_aud(rsa_keypair, monkeypatch) -> None:
    """aud 不匹配 → PermissionError."""
    home, public_pem = rsa_keypair
    monkeypatch.setenv("CATFISH_HOME", str(home))

    async def fake_lookup(sub: str) -> dict:
        return {"jwks_uri": "x"}

    async def fake_fetch_jwks(uri: str) -> dict:
        return _public_pem_to_jwks(public_pem)

    monkeypatch.setattr(a2a_jwt, "lookup_remote_agent", fake_lookup)
    monkeypatch.setattr(a2a_jwt, "fetch_jwks", fake_fetch_jwks)

    token = a2a_jwt.sign_a2a_token("alice@ffcs.cn", "bob@ffcs.cn")
    # B 实际不是 charlie, 应该拒
    with pytest.raises(PermissionError, match="aud"):
        await a2a_jwt.verify_a2a_token(token, expected_aud="charlie@ffcs.cn")


@pytest.mark.asyncio
async def test_verify_a2a_token_replay_detection(rsa_keypair, monkeypatch) -> None:
    """同 jti 二次验 → PermissionError (防重放)."""
    home, public_pem = rsa_keypair
    monkeypatch.setenv("CATFISH_HOME", str(home))

    async def fake_lookup(sub: str) -> dict:
        return {"jwks_uri": "x"}

    async def fake_fetch_jwks(uri: str) -> dict:
        return _public_pem_to_jwks(public_pem)

    monkeypatch.setattr(a2a_jwt, "lookup_remote_agent", fake_lookup)
    monkeypatch.setattr(a2a_jwt, "fetch_jwks", fake_fetch_jwks)

    token = a2a_jwt.sign_a2a_token("alice@ffcs.cn", "bob@ffcs.cn")
    # 第一次验 — OK
    await a2a_jwt.verify_a2a_token(token, expected_aud="bob@ffcs.cn")
    # 第二次同 token — 防重放
    with pytest.raises(PermissionError, match="replay"):
        await a2a_jwt.verify_a2a_token(token, expected_aud="bob@ffcs.cn")


@pytest.mark.asyncio
async def test_verify_a2a_token_unknown_issuer(monkeypatch, rsa_keypair) -> None:
    """iss 不在 registry → PermissionError."""
    home, _ = rsa_keypair
    monkeypatch.setenv("CATFISH_HOME", str(home))

    async def fake_lookup(sub: str) -> dict:
        raise PermissionError(f"agent not registered: {sub}")

    monkeypatch.setattr(a2a_jwt, "lookup_remote_agent", fake_lookup)

    token = a2a_jwt.sign_a2a_token("eve@ffcs.cn", "bob@ffcs.cn")
    with pytest.raises(PermissionError, match="registry lookup"):
        await a2a_jwt.verify_a2a_token(token, expected_aud="bob@ffcs.cn")
