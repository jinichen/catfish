"""BL-D3 Phase 1 收尾 (5/9): mcp-registry 反向代理单测.

mock httpx upstream → 验证:
1. headers 注入 X-Catfish-User-Sub / Dept / Role (从 JWT 抽出)
2. Authorization 不透传 (gateway 不让上游看到原 JWT)
3. hop-by-hop headers 过滤
4. 502 上游不可达友好错误
5. 504 上游超时
6. config.mcp_registry.enabled=false → 503

helper functions 单独测 _filter_request_headers / _filter_response_headers.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from catfish_gateway.mcp_registry_proxy import (
    _filter_request_headers,
    _filter_response_headers,
)


# ─── header 过滤纯函数 ─────────────────────────────────────────────


def test_filter_request_headers_drops_authorization():
    """Authorization 不透传给 mcp-registry — gateway 信任上游, 不传原 JWT."""
    headers = {
        "authorization": "Bearer some-jwt",
        "content-type": "application/json",
        "user-agent": "test",
    }
    out = _filter_request_headers(headers)
    assert "authorization" not in {k.lower() for k in out}
    assert "content-type" in out


def test_filter_request_headers_keeps_x_catfish_headers():
    """X-Catfish-* 自定义 header 全透传 (Phase 2+ 可能有 X-Catfish-Trace-Id 等)."""
    headers = {
        "x-catfish-trace-id": "abc123",
        "x-catfish-internal": "true",
    }
    out = _filter_request_headers(headers)
    assert out.get("x-catfish-trace-id") == "abc123"
    assert out.get("x-catfish-internal") == "true"


def test_filter_request_headers_drops_hop_by_hop():
    """connection / keep-alive / transfer-encoding 等 hop-by-hop 不透传."""
    headers = {
        "connection": "keep-alive",
        "keep-alive": "timeout=5",
        "transfer-encoding": "chunked",
        "host": "gateway.local",
        "content-type": "application/json",
    }
    out = _filter_request_headers(headers)
    out_lower = {k.lower() for k in out}
    assert "connection" not in out_lower
    assert "keep-alive" not in out_lower
    assert "transfer-encoding" not in out_lower
    assert "host" not in out_lower  # httpx 自己加
    assert "content-type" in out


def test_filter_request_headers_drops_unknown():
    """白名单外的标准 header 不透传 (除了 X-Catfish-*)."""
    headers = {"cookie": "session=xxx", "referer": "https://x.com"}
    out = _filter_request_headers(headers)
    assert "cookie" not in {k.lower() for k in out}
    assert "referer" not in {k.lower() for k in out}


def test_filter_response_headers_drops_hop_by_hop():
    headers = httpx.Headers([
        ("Content-Type", "application/json"),
        ("Connection", "close"),
        ("Transfer-Encoding", "chunked"),
        ("Content-Length", "100"),  # 也是 hop-by-hop, httpx 自算
    ])
    out = _filter_response_headers(headers)
    out_lower = {k.lower() for k in out}
    assert "content-type" in out_lower
    assert "connection" not in out_lower
    assert "transfer-encoding" not in out_lower
    assert "content-length" not in out_lower


# ─── 集成: FastAPI TestClient + mock httpx upstream ────────────────


@pytest.fixture
def proxy_app(monkeypatch):
    """造一个 minimal FastAPI app 含 mcp_registry_proxy router + mock 状态."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from catfish_gateway.auth import User, get_current_user
    from catfish_gateway.mcp_registry_proxy import router

    app = FastAPI()
    app.include_router(router)

    # mock config — 只关 mcp_registry 字段
    #
    # 7/30: proxy 从 app.state.config (启动快照) 改成走 config.get_config(),
    # 所以这里要 patch 那个函数, 不能只塞 app.state。
    # 仍然把同一个对象挂到 app.state.config 上 —— 下面有用例靠
    # `app.state.config.mcp_registry.enabled = False` 改开关, 挂同一个对象
    # 就能让那种改法继续生效, 不用动那些用例。
    fake_config = SimpleNamespace(
        mcp_registry=SimpleNamespace(
            upstream_url="http://upstream-mcp:8997",
            enabled=True,
            timeout=10,
        )
    )
    app.state.config = fake_config
    monkeypatch.setattr(
        "catfish_gateway.mcp_registry_proxy.get_config", lambda: fake_config
    )

    # mock httpx client (替 lifespan 的 AsyncClient)
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    app.state.mcp_registry_client = mock_client

    # mock 鉴权 — 跳过真 JWT, 注入固定 user
    async def fake_user() -> User:
        return User(
            sub="alice@catfish.dev",
            department="engineering",
            role="employee",
            auth_method="dev",
            managed_departments=[],
        )
    app.dependency_overrides[get_current_user] = fake_user

    return app, mock_client


def test_list_registry_injects_dept_header(proxy_app):
    """GET /v1/mcp/registry 调上游时应注入 X-Catfish-User-Dept."""
    from fastapi.testclient import TestClient

    app, mock_client = proxy_app

    # mock httpx 返 200 + body
    mock_resp = httpx.Response(
        200,
        content=b'{"connectors": [], "total": 0, "user_dept": "engineering"}',
        headers={"content-type": "application/json"},
    )
    mock_client.request.return_value = mock_resp

    client = TestClient(app)
    r = client.get("/v1/mcp/registry")
    assert r.status_code == 200

    # 验证调上游时注入了 dept
    call = mock_client.request.call_args
    assert call.kwargs["url"] == "http://upstream-mcp:8997/v1/mcp/registry"
    headers = call.kwargs["headers"]
    assert headers["X-Catfish-User-Dept"] == "engineering"
    assert headers["X-Catfish-User-Sub"] == "alice@catfish.dev"
    assert headers["X-Catfish-User-Role"] == "employee"


def test_list_registry_strips_authorization(proxy_app):
    """gateway 不把原 JWT 透传给 mcp-registry, 加自己的 X-Catfish-User-* 标识."""
    from fastapi.testclient import TestClient

    app, mock_client = proxy_app
    mock_client.request.return_value = httpx.Response(200, content=b"{}")

    client = TestClient(app)
    client.get(
        "/v1/mcp/registry",
        headers={"Authorization": "Bearer fake-jwt"},
    )

    call = mock_client.request.call_args
    forwarded_headers = call.kwargs["headers"]
    # 上游不应看到原 JWT
    assert "authorization" not in {k.lower() for k in forwarded_headers}
    assert "Authorization" not in forwarded_headers


def test_get_manifest_proxies_path(proxy_app):
    """GET /v1/mcp/manifest/jira → upstream /v1/mcp/manifest/jira."""
    from fastapi.testclient import TestClient

    app, mock_client = proxy_app
    mock_client.request.return_value = httpx.Response(
        200, content=b'{"manifest":{"id":"jira"}}',
        headers={"content-type": "application/json"},
    )

    client = TestClient(app)
    r = client.get("/v1/mcp/manifest/jira")
    assert r.status_code == 200
    call = mock_client.request.call_args
    assert call.kwargs["url"] == "http://upstream-mcp:8997/v1/mcp/manifest/jira"


def test_upstream_404_passthrough(proxy_app):
    """上游 404 透传给 client (例如不存在的 connector)."""
    from fastapi.testclient import TestClient

    app, mock_client = proxy_app
    mock_client.request.return_value = httpx.Response(
        404, content=b'{"detail":"connector not found"}',
        headers={"content-type": "application/json"},
    )

    client = TestClient(app)
    r = client.get("/v1/mcp/manifest/nonexistent")
    assert r.status_code == 404


def test_upstream_403_passthrough(proxy_app):
    """上游 403 (部门不在 allowed_dept) 透传."""
    from fastapi.testclient import TestClient

    app, mock_client = proxy_app
    mock_client.request.return_value = httpx.Response(
        403, content=b'{"detail":"dept not allowed"}',
        headers={"content-type": "application/json"},
    )

    client = TestClient(app)
    r = client.get("/v1/mcp/manifest/jira")
    assert r.status_code == 403


def test_upstream_unreachable_502(proxy_app):
    """上游 connect error → 502 + 友好提示."""
    from fastapi.testclient import TestClient

    app, mock_client = proxy_app
    mock_client.request.side_effect = httpx.ConnectError("Connection refused")

    client = TestClient(app)
    r = client.get("/v1/mcp/registry")
    assert r.status_code == 502
    assert "mcp-registry 不可达" in r.text or "mcp_registry" in r.text


def test_upstream_timeout_504(proxy_app):
    """上游 timeout → 504."""
    from fastapi.testclient import TestClient

    app, mock_client = proxy_app
    mock_client.request.side_effect = httpx.TimeoutException("timeout")

    client = TestClient(app)
    r = client.get("/v1/mcp/registry")
    assert r.status_code == 504


def test_disabled_returns_503(proxy_app):
    """config.mcp_registry.enabled=false → 503."""
    from fastapi.testclient import TestClient

    app, mock_client = proxy_app
    app.state.config.mcp_registry.enabled = False

    client = TestClient(app)
    r = client.get("/v1/mcp/registry")
    assert r.status_code == 503
    assert "未启用" in r.text or "disabled" in r.text.lower() or "enabled" in r.text


def test_query_params_forwarded(proxy_app):
    """?status_filter=active 透传上游."""
    from fastapi.testclient import TestClient

    app, mock_client = proxy_app
    mock_client.request.return_value = httpx.Response(200, content=b"{}")

    client = TestClient(app)
    client.get("/v1/mcp/registry?status_filter=active")
    call = mock_client.request.call_args
    params = call.kwargs["params"]
    # FastAPI 把 query string 转成 QueryParams 类
    assert "status_filter" in str(params) or params.get("status_filter") == "active"


def test_subscribe_post_proxied(proxy_app):
    """POST /v1/mcp/subscribe 也透传 (Phase 2 占位 endpoint, 当前上游会 405)."""
    from fastapi.testclient import TestClient

    app, mock_client = proxy_app
    mock_client.request.return_value = httpx.Response(
        405, content=b'{"detail":"phase 2"}',
        headers={"content-type": "application/json"},
    )

    client = TestClient(app)
    r = client.post("/v1/mcp/subscribe", json={"connector_id": "jira"})
    assert r.status_code == 405  # 上游 phase 1 没实现, 但路径透传成功

    call = mock_client.request.call_args
    assert call.kwargs["method"] == "POST"
    assert call.kwargs["url"] == "http://upstream-mcp:8997/v1/mcp/subscribe"
