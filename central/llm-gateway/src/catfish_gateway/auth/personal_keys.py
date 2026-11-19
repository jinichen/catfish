"""员工个人长期钥匙 `cfk_…` 的校验 (11/19, Claude Code 等直连中央用; 发放在 identity api_keys.py)。

钥匙不是 JWT, 网关自己验不了: 拿去问身份服务 introspect, 换回「这个人现在的 claims」,
造出跟 OIDC 登录同形的 User —— 权限 / 配额 / 审计都按这个员工算。

- 缓存: 有效的 60 秒, 无效的 10 秒 (按钥匙的 sha256 记, 内存里不留原文)。员工吊销 / 管理员锁人
  最多一分钟后生效; 每个请求都去问一次身份服务太重
- **降权**: 不管本人是不是管理员, 拿长期钥匙进来的一律按普通员工 (role=employee),
  auth_method=personal_key。长期钥匙放在别的工具配置里, 比登录态容易外泄 —— 只给它用模型的权限,
  不给管理权限, 也不能再生成新钥匙 (api_keys_routes.py 拒)
- 身份服务连不上: 报 503 (不是 401), 员工能分清「钥匙错了」和「服务不通」
"""
from __future__ import annotations

import hashlib
import logging
import os
import time

import httpx
from fastapi import HTTPException

from .base import User

logger = logging.getLogger("catfish.gateway.auth.personal_keys")

PREFIX = "cfk_"
_OK_TTL = 60.0
_BAD_TTL = 10.0
_cache: dict[str, tuple[float, User | None]] = {}


def is_personal_key(token: str) -> bool:
    return token.startswith(PREFIX)


def _identity_url() -> str:
    return os.environ.get("CATFISH_IDENTITY_URL", "http://127.0.0.1:8998").rstrip("/")


def user_from_claims(claims: dict, sub: str) -> User:
    def _list(k: str) -> list[str]:
        v = claims.get(k)
        return [str(x) for x in v if x] if isinstance(v, list) else []
    return User(
        sub=(claims.get("email") or sub).strip().lower(),
        department=claims.get("department", "") or "",
        tier="employee",
        role="employee",
        managed_departments=[],
        auth_method="personal_key",
        effective_allowed_models=_list("effective_allowed_models"),
        effective_allowed_tools=_list("effective_allowed_tools"),
        effective_allowed_skills=_list("effective_allowed_skills"),
        scopes=[],
    )


async def _introspect(key: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(f"{_identity_url()}/internal/api-keys/introspect", json={"key": key})
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.warning("长期钥匙: 身份服务连不上 (%s)", type(e).__name__)
        raise HTTPException(status_code=503, detail="身份服务暂时连不上, 长期钥匙没法核对, 稍后再试") from e
    if r.status_code != 200:
        logger.warning("长期钥匙: 身份服务 introspect 返回 %s", r.status_code)
        raise HTTPException(status_code=503, detail="身份服务核对长期钥匙失败, 稍后再试")
    return r.json()


async def user_from_key(key: str) -> User | None:
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    now = time.monotonic()
    hit = _cache.get(h)
    if hit and hit[0] > now:
        return hit[1]
    data = await _introspect(key)
    user = None
    if data.get("active") and isinstance(data.get("claims"), dict):
        user = user_from_claims(data["claims"], str(data["claims"].get("email") or ""))
    if len(_cache) > 5000:
        _cache.clear()
    _cache[h] = (now + (_OK_TTL if user else _BAD_TTL), user)
    return user


def _clear_cache_for_testing() -> None:
    _cache.clear()
