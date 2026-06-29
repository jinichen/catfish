"""P3.5.29 Phase 5 (6/17 鸿波) — role_resolver 真单测.

mock httpx, 测:
- gateway 返 valid roles → resolve 真 hit cache + 返新值
- gateway 返 503 → fail-silent 返 None
- gateway timeout → fail-silent 返 None
- gateway 返 garbage JSON → 返 None
- 5 分钟 cache TTL 内 0 次 HTTP 调用
- cache stale (gateway 后挂) → 用 stale cache, 不返 None
- chat_default 没在 yaml → resolve 返 None
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def reset_cache():
    """每 test 前 清 cache, 防 cross-test 污染."""
    from catfish_tool_bridge import role_resolver
    role_resolver._reset_cache_for_tests()
    yield
    role_resolver._reset_cache_for_tests()


def _mk_resp(status: int, json_data: Any) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data
    return resp


def _mk_client(resp_or_exception):
    """mock httpx.Client 真 context manager, get(url) 返 resp 或 raise."""
    client_cm = MagicMock()
    client_instance = MagicMock()
    if isinstance(resp_or_exception, Exception):
        client_instance.get.side_effect = resp_or_exception
    else:
        client_instance.get.return_value = resp_or_exception
    client_cm.__enter__.return_value = client_instance
    client_cm.__exit__.return_value = False
    return client_cm, client_instance


# ─── 基本 happy path ──────────────────────────────────────


def test_resolve_returns_model_from_gateway():
    """gateway 返 valid roles → resolve 真值."""
    from catfish_tool_bridge import role_resolver

    cm, _ = _mk_client(_mk_resp(200, {
        "roles": {
            "chat_default": "catfish-private-main",
            "rate_fast": "catfish-public-qwen-flash",
        }
    }))
    with patch("httpx.Client", return_value=cm):
        assert role_resolver.resolve("chat_default") == "catfish-private-main"
        assert role_resolver.resolve("rate_fast") == "catfish-public-qwen-flash"


def test_resolve_unknown_role_returns_none():
    """gateway 真返 roles, 但 caller 真问 unknown role → None.

    (catfish-gateway roles.yaml 没 fictional_role, resolve 真返 None.)
    """
    from catfish_tool_bridge import role_resolver

    cm, _ = _mk_client(_mk_resp(200, {
        "roles": {"chat_default": "catfish-private-main"}
    }))
    with patch("httpx.Client", return_value=cm):
        assert role_resolver.resolve("fictional_role") is None


# ─── fail-silent paths ───────────────────────────────────


def test_resolve_503_returns_none():
    """gateway 返 503 → fail-silent 返 None."""
    from catfish_tool_bridge import role_resolver

    cm, _ = _mk_client(_mk_resp(503, {"detail": "roles 没加载"}))
    with patch("httpx.Client", return_value=cm):
        assert role_resolver.resolve("chat_default") is None


def test_resolve_connection_error_returns_none():
    """httpx.ConnectError → fail-silent 返 None (gateway 没起)."""
    import httpx

    from catfish_tool_bridge import role_resolver

    cm, _ = _mk_client(httpx.ConnectError("connection refused"))
    with patch("httpx.Client", return_value=cm):
        assert role_resolver.resolve("chat_default") is None


def test_resolve_garbage_json_returns_none():
    """gateway 返 200 但 JSON 结构错 → 返 None."""
    from catfish_tool_bridge import role_resolver

    cm, _ = _mk_client(_mk_resp(200, {"unexpected": "structure"}))
    with patch("httpx.Client", return_value=cm):
        assert role_resolver.resolve("chat_default") is None


def test_resolve_non_dict_roles_returns_none():
    """gateway 返 ``{"roles": "not_a_dict"}`` → 返 None (防 type confusion)."""
    from catfish_tool_bridge import role_resolver

    cm, _ = _mk_client(_mk_resp(200, {"roles": "not_a_dict"}))
    with patch("httpx.Client", return_value=cm):
        assert role_resolver.resolve("chat_default") is None


# ─── cache 行为 ─────────────────────────────────────────


def test_cache_hit_no_second_http_call():
    """5 分钟 TTL 内 只 1 次 HTTP call."""
    from catfish_tool_bridge import role_resolver

    cm, client_instance = _mk_client(_mk_resp(200, {
        "roles": {"chat_default": "catfish-private-main"}
    }))
    with patch("httpx.Client", return_value=cm):
        role_resolver.resolve("chat_default")
        role_resolver.resolve("chat_default")
        role_resolver.resolve("rate_fast")
        # 3 次 resolve, 真1 次 HTTP**
        assert client_instance.get.call_count == 1


def test_stale_cache_fallback_when_gateway_dies():
    """首次 fetch 成功 → cache. 然后 gateway 挂, TTL 过, 返 stale.

    保护 background task gateway 短暂挂时不死.
    """
    import httpx

    from catfish_tool_bridge import role_resolver

    # 首次 — gateway 返 valid
    cm_ok, _ = _mk_client(_mk_resp(200, {
        "roles": {"chat_default": "catfish-private-main"}
    }))
    with patch("httpx.Client", return_value=cm_ok):
        assert role_resolver.resolve("chat_default") == "catfish-private-main"

    # 手动老化 cache 强制过 TTL
    role_resolver._cache_fetched_at = 0.0

    # gateway 挂 — fetch 真返 None, 但 cache 还有
    cm_dead, _ = _mk_client(httpx.ConnectError("gateway 挂"))
    with patch("httpx.Client", return_value=cm_dead):
        # stale fallback — 返**老 cache 值**
        assert role_resolver.resolve("chat_default") == "catfish-private-main"


def test_resolve_returns_none_when_no_cache_and_gateway_dead():
    """首次 gateway 就挂 + cache 空 → 返 None (caller 走 hardcoded fallback)."""
    import httpx

    from catfish_tool_bridge import role_resolver

    cm, _ = _mk_client(httpx.ConnectError("gateway 挂"))
    with patch("httpx.Client", return_value=cm):
        assert role_resolver.resolve("chat_default") is None


# ─── 端到端 fallback chain caller 用法 ───────────


def test_caller_chain_pattern():
    """caller 标准 pattern: ``resolve(role) or "hardcoded_default"``.

    catfish_tools.py / install_and_ops.py 真用法 真 verify.
    """
    from catfish_tool_bridge import role_resolver

    # gateway 真 valid
    cm, _ = _mk_client(_mk_resp(200, {
        "roles": {"chat_default": "customer-x-main"}
    }))
    with patch("httpx.Client", return_value=cm):
        model = role_resolver.resolve("chat_default") or "catfish-private-main"
        assert model == "customer-x-main"

    # gateway 挂 + cache 空
    role_resolver._reset_cache_for_tests()
    cm_dead, _ = _mk_client(_mk_resp(503, {}))
    with patch("httpx.Client", return_value=cm_dead):
        model = role_resolver.resolve("chat_default") or "catfish-private-main"
        assert model == "catfish-private-main"
