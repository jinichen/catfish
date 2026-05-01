"""Plan D · gateway 启动时自动 register 到中央 catfish-identity.

# 用途 (五一 sprint Day 5 B 方案)

每个 catfish 实例 (Alice / Bob / ...) 启动时:
1. 加载 ~/.catfish/identity/public.pem
2. POST CATFISH_REGISTRY_URL/registry/register, 带:
   - sub (= CATFISH_USER_SUB env)
   - catfish_endpoint (本机 gateway URL)
   - public_pem (上面加载的)
   - department / capabilities
3. 中央 registry 把 public_pem store 到 yaml, 暴露在
   `/registry/agents/<sub>/jwks.json` 让别人验签时 fetch

# 单机 mock vs 生产

- 单机 mock: 共享一个 registry (8998), alice/bob 各自的 public_pem 由各自 catfish_home 提供
- 生产: 每员工独立 catfish, register 到公司中央 catfish-identity

# 触发时机

gateway lifespan 启动期跑一次. 失败不阻塞启动 (a2a 功能不可用, 但聊天等其他功能正常).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx

logger = logging.getLogger("catfish.gateway.a2a_self_register")


def _catfish_home() -> Path:
    return Path(os.environ.get("CATFISH_HOME") or (Path.home() / ".catfish")).expanduser()


def _public_pem_path() -> Path:
    return _catfish_home() / "identity" / "public.pem"


async def self_register() -> bool:
    """启动时调用, register 到中央 registry.

    返回 True 成功, False 失败 (a2a 功能将不可用, 但其他功能正常).
    """
    sub = os.environ.get("CATFISH_USER_SUB", "").strip()
    if not sub:
        logger.info(
            "self_register: CATFISH_USER_SUB 未设, 跳过 (Plan D A2A 不可用). "
            "员工本机生产应通过 SSO 设置."
        )
        return False

    catfish_endpoint = os.environ.get("CATFISH_GATEWAY_URL", "").strip()
    if not catfish_endpoint:
        # 默认从端口推断
        port = os.environ.get("CATFISH_GATEWAY_PORT", "8999")
        catfish_endpoint = f"http://127.0.0.1:{port}"

    registry_url = os.environ.get("CATFISH_REGISTRY_URL", "http://127.0.0.1:8998").rstrip("/")

    # 加载 public.pem
    pub_path = _public_pem_path()
    if not pub_path.exists():
        logger.warning(
            "self_register: public.pem 不存在 (%s), Plan D A2A 不可用. "
            "跑 scripts/plan_d_mock_init.sh 生成 RSA keypair.",
            pub_path,
        )
        return False
    try:
        public_pem = pub_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("self_register: 读 public.pem 失败: %s", e)
        return False

    # 选 department / capabilities (env 或默认)
    department = os.environ.get("CATFISH_DEPARTMENT", "")
    capabilities = ["a2a.ask"]

    # 显式算 jwks_uri 不依赖 registry 自动算 — 兼容老版 registry (jwks_uri 必填).
    jwks_uri = f"{registry_url}/registry/agents/{sub}/jwks.json"

    body = {
        "sub": sub,
        "catfish_endpoint": catfish_endpoint,
        "jwks_uri": jwks_uri,
        "public_pem": public_pem,
        "department": department,
        "capabilities": capabilities,
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{registry_url}/registry/register", json=body)
            if resp.status_code != 200:
                # 把 response body 一起记到 log, 方便诊断 422 / 5xx 等
                logger.warning(
                    "self_register: HTTP %d %s\nrequest body keys: %s\nresponse body: %s",
                    resp.status_code,
                    resp.reason_phrase,
                    list(body.keys()),
                    resp.text[:800],
                )
                return False
            data = resp.json()
        logger.info(
            "self_register: ✅ %s registered to %s (total %d agents online)",
            sub, registry_url, data.get("total_agents", "?"),
        )
        return True
    except Exception as e:
        logger.warning(
            "self_register: 失败 %s → %r. Plan D A2A 不可用, 其他功能正常.",
            registry_url, e,
        )
        return False


__all__ = ["self_register"]
