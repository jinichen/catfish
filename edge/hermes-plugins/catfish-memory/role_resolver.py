"""P3.5.29 Phase 7 (6/17 鸿波) — hermes-memory plugin 真 role resolver client.

真**HTTP fetch** ``GATEWAY_URL/v1/roles`` + in-memory 5 分钟 cache.
跟 tool-bridge ``catfish_tool_bridge/role_resolver.py`` 同 pattern, 同**Python sync**.

# 真**用法**

```python
from . import role_resolver

# _get_summarize_model 真**优先级 chain**:
#   picker_state.json > role_resolver("summarize") > yaml > env
summarize_model = role_resolver.resolve("summarize") or ""
```

# 真**fail-silent**

httpx 没装 / gateway 挂 / JSON 错 → 返 None. caller 真**走自己**
fallback (yaml/env). 真**不阻塞** hermes plugin sync_turn.
"""

from __future__ import annotations

import os
import time
from typing import Optional

# ─── 真**module-level cache** ────────────────────────────────
_CACHE_TTL_SECONDS: float = 300.0
_HTTP_TIMEOUT_SECONDS: float = 3.0

_cache: dict[str, str] = {}
_cache_fetched_at: float = 0.0


def _gateway_url() -> str:
    """真**env var pattern** 跟 tool-bridge / Companion 真**对齐**."""
    return os.environ.get("CATFISH_GATEWAY_URL", "http://127.0.0.1:8999")


def _fetch_roles_from_gateway() -> Optional[dict[str, str]]:
    """真**GET /v1/roles** anonymous, 3 秒 timeout. 失败返 None."""
    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        return None

    url = f"{_gateway_url()}/v1/roles"
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            resp = client.get(url)
    except Exception:  # noqa: BLE001
        return None

    if resp.status_code != 200:
        return None

    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return None

    roles = data.get("roles") if isinstance(data, dict) else None
    if not isinstance(roles, dict):
        return None

    return {k: v for k, v in roles.items() if isinstance(k, str) and isinstance(v, str)}


def resolve(role: str) -> Optional[str]:
    """真**核心 API**: role name → 物理 model name. 失败返 None.

    Args:
        role: 真 ``roles.yaml`` 真**role 名** (e.g. ``"summarize"``)

    Returns:
        真**物理 model name** 或 None.
    """
    global _cache, _cache_fetched_at  # noqa: PLW0603
    now = time.monotonic()

    if _cache and (now - _cache_fetched_at) < _CACHE_TTL_SECONDS:
        return _cache.get(role)

    fresh = _fetch_roles_from_gateway()
    if fresh is not None:
        _cache = fresh
        _cache_fetched_at = now
        return fresh.get(role)

    # 真**fetch 失败** 真**stale cache fallback**
    if _cache:
        return _cache.get(role)
    return None


def _reset_cache_for_tests() -> None:
    """真**单测专用** — 清 cache."""
    global _cache, _cache_fetched_at  # noqa: PLW0603
    _cache = {}
    _cache_fetched_at = 0.0
