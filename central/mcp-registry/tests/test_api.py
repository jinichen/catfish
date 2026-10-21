"""FastAPI endpoints 单测 (BL-D3 Phase 1)."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_health(client: TestClient):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["manifests_loaded"] == 3
    assert "version" in data


def test_registry_no_dept_only_open(client: TestClient):
    """不传 dept header → 只见 time (全员开放)."""
    r = client.get("/v1/mcp/registry")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    ids = sorted(c["id"] for c in data["connectors"])
    assert ids == ["time"]
    assert data["filtered_by_dept"] is False


def test_registry_engineering_sees_all(client: TestClient):
    """engineering 部门 → 全见 (jira/gitlab 工程类 + time 全员)."""
    r = client.get(
        "/v1/mcp/registry",
        headers={"X-Catfish-User-Dept": "engineering"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 3
    ids = sorted(c["id"] for c in data["connectors"])
    assert ids == ["gitlab", "jira", "time"]
    assert data["user_dept"] == "engineering"
    assert data["filtered_by_dept"] is True


def test_registry_finance_only_open(client: TestClient):
    """finance 部门 → 只见 time, 工程类不见."""
    r = client.get(
        "/v1/mcp/registry",
        headers={"X-Catfish-User-Dept": "finance"},
    )
    assert r.status_code == 200
    data = r.json()
    ids = sorted(c["id"] for c in data["connectors"])
    assert ids == ["time"]
    assert data["filtered_by_dept"] is True


def test_registry_status_filter(client: TestClient):
    """?status_filter=active → 全是 active 状态的."""
    r = client.get(
        "/v1/mcp/registry?status_filter=active",
        headers={"X-Catfish-User-Dept": "engineering"},
    )
    assert r.status_code == 200
    data = r.json()
    for c in data["connectors"]:
        assert c["status"] == "active"


def test_registry_status_filter_no_match(client: TestClient):
    """?status_filter=deprecated → 0 (现在没有 deprecated 状态的连接器)."""
    r = client.get(
        "/v1/mcp/registry?status_filter=deprecated",
        headers={"X-Catfish-User-Dept": "engineering"},
    )
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_registry_response_shape(client: TestClient):
    """单 connector item 字段齐 — Companion Dashboard 卡渲染要这些字段."""
    r = client.get(
        "/v1/mcp/registry",
        headers={"X-Catfish-User-Dept": "engineering"},
    )
    item = next(c for c in r.json()["connectors"] if c["id"] == "jira")
    assert item["name"] == "Jira"
    assert item["version"] == "0.5.2"
    assert item["auth_type"] == "oauth2"
    assert item["status"] == "preview"  # 10/22: OAuth 型员工机还不支持, 市场里「即将开放」
    assert len(item["tools"]) >= 5
    assert item["ui"]["category"] == "项目管理"
    assert item["subscribed"] is False  # Phase 1 永远 False
    assert item["subscriber_count"] == 0


def test_manifest_get(client: TestClient):
    """单连接器详情 — 含 OAuth + mcp_command 内部字段."""
    r = client.get(
        "/v1/mcp/manifest/jira",
        headers={"X-Catfish-User-Dept": "engineering"},
    )
    assert r.status_code == 200
    manifest = r.json()["manifest"]
    assert manifest["id"] == "jira"
    assert manifest["mcp_command"]["package"] == "mcp-server-jira"
    assert manifest["oauth"]["scopes"] == ["read:jira-work", "write:jira-work"]


def test_manifest_404_unknown(client: TestClient):
    r = client.get(
        "/v1/mcp/manifest/nonexistent",
        headers={"X-Catfish-User-Dept": "engineering"},
    )
    assert r.status_code == 404


def test_manifest_403_wrong_dept(client: TestClient):
    """finance 部门访问 jira (engineering only) → 403."""
    r = client.get(
        "/v1/mcp/manifest/jira",
        headers={"X-Catfish-User-Dept": "finance"},
    )
    assert r.status_code == 403
    assert "finance" in r.text.lower() or "engineering" in r.text.lower() or "权限" in r.text or "部门" in r.text


def test_manifest_time_offline_python_module(client: TestClient):
    """time 不要 OAuth, 用随安装包带的 python 模块起 (离线可用, 10/22)。"""
    r = client.get("/v1/mcp/manifest/time")
    assert r.status_code == 200
    manifest = r.json()["manifest"]
    assert manifest["auth_type"] == "none"
    assert manifest["oauth"] is None
    assert manifest["mcp_command"]["type"] == "python"
    assert manifest["mcp_command"]["module"] == "catfish_tool_bridge.connectors.time_server"


def test_manifest_time_no_dept_required(client: TestClient):
    """time allowed_dept 空 → 任何部门 (含没传) 都能访问."""
    r = client.get("/v1/mcp/manifest/time")
    assert r.status_code == 200
    r2 = client.get(
        "/v1/mcp/manifest/time",
        headers={"X-Catfish-User-Dept": "finance"},
    )
    assert r2.status_code == 200


def test_cors_headers(client: TestClient):
    """CORS — Companion 直连或 gateway 转发都要过."""
    r = client.options(
        "/v1/mcp/registry",
        headers={
            "Origin": "http://localhost:1420",
            "Access-Control-Request-Method": "GET",
        },
    )
    # FastAPI CORS middleware 会回 200 + access-control-allow-origin
    assert r.status_code in (200, 204)
