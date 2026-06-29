"""P3.5.29 Phase 5 (6/17 鸿波) — tool-bridge 真 role resolver client.

HTTP fetch ``GATEWAY_URL/v1/roles`` + in-memory 5 分钟 cache.
跟 Companion ``services/role_config.rs`` (Phase 4) 同 pattern, Python sync 版.

# 用法

```python
from catfish_tool_bridge import role_resolver

model = role_resolver.resolve("chat_default") or "catfish-private-main"
```

# chain (跟 Companion 对齐)

  1. caller 真显式传 ``model=...`` (最高)
  2. role_resolver.resolve(role) — gateway /v1/roles 真值
  3. caller 真 hardcoded fallback (兜底, 给 gateway 挂时用)

# fail-silent

任何错 (httpx 没装 / gateway 挂 / JSON 错) → 返 None. caller 走自己
fallback (hardcoded DEFAULT). 不阻塞 background task.

# 为啥 不用 ``async``?

3 个 caller 调用频率低 (RecMode 录制结束 1 次, dedupe 每条 entry 1 次,
expertise 每邮件 1 次). 5 分钟 cache 首次 ~3s, 后续 0 ms. sync
httpx.Client 简单, 不必 async pollute 调用方.
"""

from __future__ import annotations

import time
from typing import Optional

# ─── module-level cache ────────────────────────────────
# 进程生命周期内 cache. 5 分钟 TTL, 失败 fallback stale cache (如果有).
_CACHE_TTL_SECONDS: float = 300.0
_HTTP_TIMEOUT_SECONDS: float = 3.0

_cache: dict[str, str] = {}
_cache_fetched_at: float = 0.0


def _gateway_url() -> str:
    """复用 browser_locate.GATEWAY_URL — 同一 env var pattern
    (``CATFISH_GATEWAY_URL`` / ``CATFISH_GATEWAY_HOST`` / ``CATFISH_GATEWAY_PORT``).

    lazy import 避免循环 import + 没装 tool-bridge 单独跑时不死.
    """
    try:
        from .browser_locate import GATEWAY_URL  # noqa: PLC0415
        return GATEWAY_URL
    except Exception:  # noqa: BLE001
        import os  # noqa: PLC0415
        return os.environ.get("CATFISH_GATEWAY_URL", "http://127.0.0.1:8999")


def _fetch_roles_from_gateway() -> Optional[dict[str, str]]:
    """GET /v1/roles anonymous, 3 秒 timeout. 失败返 None.

    不传 Authorization — /v1/roles 真 anonymous endpoint (Phase 1 ship).
    """
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

    # 只 keep str → str mapping (防 gateway 真返复杂 nested struct)
    return {k: v for k, v in roles.items() if isinstance(k, str) and isinstance(v, str)}


def resolve(role: str) -> Optional[str]:
    """核心 API: role name → 物理 model name.

    chain:
      1. cache hit 且没过 5 分钟 → 用 cache
      2. cache 过期 / 没 cache → fetch
      3. fetch 成功 → 更新 cache + 返
      4. fetch 失败 但 cache 有 → 返 stale (gateway 短暂挂)
      5. fetch 失败 cache 空 → 返 None (caller 走 hardcoded fallback)

    Args:
        role: 真 ``roles.yaml`` role 名 (snake_case), 例如:
              ``"chat_default"``, ``"rate_fast"``, ``"vision"``, ``"summarize"``,
              ``"embedding"``, ``"advisor_call2"``, ``"public_flash"``.

    Returns:
        物理 model name (例如 ``"catfish-private-main"``), 或 None.
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

    # fetch 失败 stale cache fallback (如果有)
    if _cache:
        return _cache.get(role)
    return None


def _reset_cache_for_tests() -> None:
    """单测专用 — 清 cache 让 mock 生效.

    不 export 真 public API, 但**也不真 _ 双下划线**, 测试方便 import.
    """
    global _cache, _cache_fetched_at  # noqa: PLW0603
    _cache = {}
    _cache_fetched_at = 0.0
