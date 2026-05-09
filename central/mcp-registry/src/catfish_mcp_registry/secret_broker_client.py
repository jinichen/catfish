"""secret-broker HTTP 客户端 — mcp-registry 调它存/读 OAuth token (5/9 Phase 2).

设计:
- 单例 httpx.AsyncClient (lifespan 起的)
- 调用时透传 X-Catfish-User-Sub (调用方注入, 防越权)
- 失败 → raise SecretBrokerError, caller 决定是否兜底 / 报 500

env:
    CATFISH_SECRET_BROKER_URL=http://127.0.0.1:8995
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("catfish.mcp_registry.secret_broker_client")


class SecretBrokerError(Exception):
    """secret-broker 调用失败."""


def get_url() -> str:
    return os.environ.get("CATFISH_SECRET_BROKER_URL", "http://127.0.0.1:8995")


async def set_secret(
    client: httpx.AsyncClient,
    *,
    ref: str,
    value: str,
    user_sub: str,
) -> None:
    """写 secret. 失败抛 SecretBrokerError."""
    url = f"{get_url()}/v1/secret"
    try:
        r = await client.post(
            url,
            json={"ref": ref, "value": value},
            headers={"X-Catfish-User-Sub": user_sub},
            timeout=5,
        )
    except httpx.RequestError as e:
        raise SecretBrokerError(f"secret-broker 不可达 ({get_url()}): {e}") from e
    if r.status_code != 200:
        raise SecretBrokerError(f"secret-broker set 失败 {r.status_code}: {r.text}")


async def get_secret(
    client: httpx.AsyncClient,
    *,
    ref: str,
    user_sub: str,
) -> str | None:
    """读 secret. 不存在返 None, 网络失败抛 SecretBrokerError."""
    url = f"{get_url()}/v1/secret/{ref}"
    try:
        r = await client.get(
            url,
            headers={"X-Catfish-User-Sub": user_sub},
            timeout=5,
        )
    except httpx.RequestError as e:
        raise SecretBrokerError(f"secret-broker 不可达 ({get_url()}): {e}") from e
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise SecretBrokerError(f"secret-broker get 失败 {r.status_code}: {r.text}")
    data: dict[str, Any] = r.json()
    return data.get("value")


async def delete_secret(
    client: httpx.AsyncClient,
    *,
    ref: str,
    user_sub: str,
) -> None:
    """删 secret. idempotent — 不存在不报错."""
    url = f"{get_url()}/v1/secret/{ref}"
    try:
        r = await client.delete(
            url,
            headers={"X-Catfish-User-Sub": user_sub},
            timeout=5,
        )
    except httpx.RequestError as e:
        raise SecretBrokerError(f"secret-broker 不可达 ({get_url()}): {e}") from e
    if r.status_code != 200:
        raise SecretBrokerError(f"secret-broker delete 失败 {r.status_code}: {r.text}")
