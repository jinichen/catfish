"""FastAPI endpoints 单测 (BL-G6 Phase 2)."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_health(client: TestClient):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["backend"] == "memory"


def test_set_then_get(client: TestClient, auth_headers):
    r = client.post(
        "/v1/secret",
        json={"ref": "jira-alice", "value": "token-abc-123"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json() == {"ref": "jira-alice", "exists": True}

    r = client.get("/v1/secret/jira-alice", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"ref": "jira-alice", "value": "token-abc-123"}


def test_get_unknown_404(client: TestClient, auth_headers):
    r = client.get("/v1/secret/nonexistent", headers=auth_headers)
    assert r.status_code == 404


def test_overwrite(client: TestClient, auth_headers):
    client.post("/v1/secret", json={"ref": "k", "value": "v1"}, headers=auth_headers)
    client.post("/v1/secret", json={"ref": "k", "value": "v2"}, headers=auth_headers)
    r = client.get("/v1/secret/k", headers=auth_headers)
    assert r.json()["value"] == "v2"


def test_delete(client: TestClient, auth_headers):
    client.post("/v1/secret", json={"ref": "k", "value": "v"}, headers=auth_headers)
    r = client.delete("/v1/secret/k", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["exists"] is False
    # 删后 get 应 404
    r = client.get("/v1/secret/k", headers=auth_headers)
    assert r.status_code == 404


def test_delete_unknown_idempotent(client: TestClient, auth_headers):
    """删不存在的 ref 不报错 (idempotent)."""
    r = client.delete("/v1/secret/nonexistent", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["exists"] is False


def test_exists_endpoint(client: TestClient, auth_headers):
    r = client.get("/v1/secret/k/exists", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"ref": "k", "exists": False}

    client.post("/v1/secret", json={"ref": "k", "value": "v"}, headers=auth_headers)
    r = client.get("/v1/secret/k/exists", headers=auth_headers)
    assert r.json()["exists"] is True


def test_missing_user_header_401(client: TestClient):
    """没传 X-Catfish-User-Sub → 401 (gateway 应注入)."""
    r = client.post("/v1/secret", json={"ref": "k", "value": "v"})
    assert r.status_code == 401

    r = client.get("/v1/secret/k")
    assert r.status_code == 401


def test_set_validates_required_fields(client: TestClient, auth_headers):
    """ref / value 必填."""
    r = client.post("/v1/secret", json={"ref": ""}, headers=auth_headers)
    assert r.status_code == 422

    r = client.post("/v1/secret", json={"value": "v"}, headers=auth_headers)
    assert r.status_code == 422
