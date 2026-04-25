"""Smoke tests -- require a running gateway at GATEWAY_URL (default localhost:8000)."""
from __future__ import annotations

import os

import httpx
import pytest


BASE = os.environ.get("GATEWAY_URL", "http://localhost:8999")
TOKEN = os.environ.get("CATFISH_DEV_TOKEN", "dev-token-local")
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def _gateway_up() -> bool:
    try:
        httpx.get(f"{BASE}/health", timeout=2).raise_for_status()
        return True
    except Exception:
        return False


requires_gateway = pytest.mark.skipif(
    not _gateway_up(),
    reason=f"gateway not reachable at {BASE}; start it first",
)


@requires_gateway
def test_health():
    r = httpx.get(f"{BASE}/health", timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "catfish-gateway"


@requires_gateway
def test_models_requires_auth():
    r = httpx.get(f"{BASE}/v1/models", timeout=5)
    assert r.status_code == 401


@requires_gateway
def test_models_rejects_wrong_token():
    r = httpx.get(
        f"{BASE}/v1/models",
        headers={"Authorization": "Bearer totally-wrong"},
        timeout=5,
    )
    assert r.status_code == 401


@requires_gateway
def test_models_list():
    r = httpx.get(f"{BASE}/v1/models", headers=HEADERS, timeout=5)
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "list"
    assert isinstance(data["data"], list)
    assert len(data["data"]) > 0
    names = {m["id"] for m in data["data"]}
    assert "catfish-private-main" in names


@requires_gateway
def test_catalog():
    r = httpx.get(f"{BASE}/v1/catalog", headers=HEADERS, timeout=5)
    assert r.status_code == 200
    data = r.json()
    assert "models" in data
    assert "default" in data
    assert len(data["models"]) > 0
    first = data["models"][0]
    assert "display_name" in first
    assert "tier" in first
    # 登录态应该被标识出来
    assert data.get("authenticated") is True


@requires_gateway
def test_catalog_anonymous_ok():
    """Hermes 首次启动、还没填 token 时也能看到模型列表。"""
    r = httpx.get(f"{BASE}/v1/catalog", timeout=5)  # 不带 Authorization
    assert r.status_code == 200
    data = r.json()
    assert data.get("authenticated") is False
    assert isinstance(data.get("models"), list)
    assert len(data["models"]) > 0
    # 确认敏感字段没漏
    first = data["models"][0]
    assert "api_base" not in first
    assert "api_key_env" not in first
    assert "upstream" not in first


@requires_gateway
def test_catalog_bad_token_is_anonymous_not_401():
    """带错 token 时也走匿名路径，不再返 401。"""
    r = httpx.get(
        f"{BASE}/v1/catalog",
        headers={"Authorization": "Bearer obviously-wrong"},
        timeout=5,
    )
    assert r.status_code == 200
    data = r.json()
    assert data.get("authenticated") is False


@requires_gateway
def test_chat_completion_basic():
    """Actually hits upstream LLM. Skipped if INTERNAL_LLM_KEY not set."""
    if not os.environ.get("INTERNAL_LLM_KEY"):
        pytest.skip("INTERNAL_LLM_KEY not set -- can't reach upstream")

    r = httpx.post(
        f"{BASE}/v1/chat/completions",
        headers={**HEADERS, "Content-Type": "application/json"},
        json={
            "model": "catfish-private-main",
            "messages": [{"role": "user", "content": "说 hello"}],
            "max_tokens": 20,
        },
        timeout=60,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("choices")
    assert data["choices"][0]["message"]["content"]


@requires_gateway
def test_unknown_model_404():
    r = httpx.post(
        f"{BASE}/v1/chat/completions",
        headers={**HEADERS, "Content-Type": "application/json"},
        json={"model": "catfish-does-not-exist", "messages": []},
        timeout=5,
    )
    assert r.status_code == 404
