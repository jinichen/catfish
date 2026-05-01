"""Plan D · JWT 互信 — 五一 sprint Day 4-5 (BL-M4.2, M4.3).

# 设计 (PLAN-D-PROTOCOL.md § 3)

每个 catfish 实例**自己签 JWT**. 中央 catfish-identity 只做 JWKS 公钥分发.

A 给 B 签 (RS256, exp=5min, jti 防重放), B 通过 registry lookup 拿 A 的 JWKS 验签.

# 文件位置

- A 私钥: ~/.catfish/identity/private.pem (PEM 格式 RS256)
- A 公钥: ~/.catfish/identity/public.pem (跟 jwks 同源)
- A JWKS endpoint: 由 catfish-identity 暴露 /.well-known/jwks.json (复用现 JwtSigner)

# 单机 mock 简化

5/5 单机模拟用 env CATFISH_HOME 切 ~/.catfish-alice / ~/.catfish-bob, 各自管自己 key.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import jwt

logger = logging.getLogger("catfish.gateway.a2a_jwt")


# 防重放 jti 缓存. 简化用 in-memory dict, 重启 catfish 清零.
# Phase 2 升级 redis / sqlite (BL Q3 加).
_SEEN_JTI: dict[str, float] = {}  # jti → exp timestamp
_JTI_RETENTION_SEC = 600  # 10 分钟内不重放


def _catfish_home() -> Path:
    """catfish 实例 home 目录. 默认 ~/.catfish, env CATFISH_HOME override."""
    custom = os.environ.get("CATFISH_HOME")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".catfish"


def _private_key_path() -> Path:
    return _catfish_home() / "identity" / "private.pem"


def load_private_key() -> str | None:
    """加载本实例私钥 PEM. 没有返 None (调用方决定 fallback)."""
    path = _private_key_path()
    if not path.exists():
        logger.warning("a2a private key 不存在: %s", path)
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("a2a private key 读取失败: %s", e)
        return None


def sign_a2a_token(
    from_sub: str,
    to_sub: str,
    scope: str = "a2a.ask",
    ttl_seconds: int = 300,
) -> str:
    """A 给 B 签 JWT. ttl 默认 5 分钟."""
    private_key = load_private_key()
    if private_key is None:
        raise RuntimeError(
            f"a2a 签 JWT 失败: private.pem 不存在 ({_private_key_path()}). "
            "需要先初始化 catfish-identity 自签 key."
        )

    now = int(time.time())
    payload = {
        "iss": from_sub,
        "sub": from_sub,
        "aud": to_sub,
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": str(uuid.uuid4()),
        "scope": scope,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def _registry_url() -> str:
    """中央 catfish-identity registry URL. env CATFISH_REGISTRY_URL or 默认 8998."""
    return os.environ.get(
        "CATFISH_REGISTRY_URL",
        "http://127.0.0.1:8998",
    ).rstrip("/")


async def lookup_remote_agent(sub: str) -> dict[str, Any]:
    """通过 catfish-identity registry 查 sub 的 catfish_endpoint + jwks_uri.

    返 {sub, catfish_endpoint, jwks_uri, department, capabilities, last_seen, online}.
    """
    url = f"{_registry_url()}/registry/lookup?sub={sub}"
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(url)
        if resp.status_code == 404:
            raise PermissionError(f"agent not registered: {sub}")
        resp.raise_for_status()
        return resp.json()


_JWKS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_JWKS_CACHE_TTL = 300  # 5 分钟


async def fetch_jwks(jwks_uri: str) -> dict[str, Any]:
    """拉远端 jwks. 5 分钟缓存."""
    now = time.time()
    if jwks_uri in _JWKS_CACHE:
        cached_at, jwks = _JWKS_CACHE[jwks_uri]
        if now - cached_at < _JWKS_CACHE_TTL:
            return jwks

    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(jwks_uri)
        resp.raise_for_status()
        jwks = resp.json()
    _JWKS_CACHE[jwks_uri] = (now, jwks)
    return jwks


async def verify_a2a_token(token: str, expected_aud: str) -> dict[str, Any]:
    """B 端验 A 签的 JWT.

    流程:
    1. 不验签 decode 拿 iss
    2. registry lookup iss → jwks_uri
    3. fetch jwks, 验签 + 验 aud + 验 exp
    4. 防重放: jti 检查

    抛 PermissionError 失败. 返 payload 成功.
    """
    # 1. 拿 iss
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
    except jwt.InvalidTokenError as e:
        raise PermissionError(f"invalid JWT: {e}") from e

    iss = unverified.get("iss")
    if not iss:
        raise PermissionError("JWT 缺 iss")

    # 2. registry lookup
    try:
        entry = await lookup_remote_agent(iss)
    except Exception as e:
        raise PermissionError(f"registry lookup {iss} 失败: {e}") from e

    jwks_uri = entry.get("jwks_uri")
    if not jwks_uri:
        raise PermissionError(f"registry 没 {iss} 的 jwks_uri")

    # 3. fetch + 验签 + 验 aud
    try:
        jwks = await fetch_jwks(jwks_uri)
    except Exception as e:
        raise PermissionError(f"fetch jwks 失败: {e}") from e

    # jwks 是 {"keys": [...]} — 找匹配 kid 的 key
    keys = jwks.get("keys") or []
    if not keys:
        raise PermissionError(f"jwks 空: {jwks_uri}")

    # 简化: 用第一个 key (生产应该按 kid 匹配)
    key_dict = keys[0]
    try:
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key_dict)
    except Exception as e:
        raise PermissionError(f"jwks 解析失败: {e}") from e

    try:
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=expected_aud,
        )
    except jwt.InvalidTokenError as e:
        raise PermissionError(f"JWT 验签 / aud / exp 失败: {e}") from e

    # 4. 防重放
    jti = payload.get("jti", "")
    if not jti:
        raise PermissionError("JWT 缺 jti (防重放)")

    now = time.time()
    # 清过期 jti
    expired = [k for k, v in _SEEN_JTI.items() if v < now]
    for k in expired:
        del _SEEN_JTI[k]

    if jti in _SEEN_JTI:
        raise PermissionError(f"JWT replay detected: jti={jti}")
    _SEEN_JTI[jti] = float(payload.get("exp", now + _JTI_RETENTION_SEC))

    return payload


__all__ = [
    "sign_a2a_token",
    "verify_a2a_token",
    "lookup_remote_agent",
    "fetch_jwks",
    "load_private_key",
]
