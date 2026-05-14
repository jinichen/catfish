"""BL-LEARN-RECMODE gateway endpoints (5/14 v0 骨架) 单测.

用 dependency_overrides 模式 mock get_current_user, 跟 test_audit_events_endpoint 同模式.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _make_client_as(role: str = "employee", tmp_path=None, monkeypatch=None):
    """mock get_current_user 返指定 role 的 User + 隔离 CATFISH_HOME."""
    if tmp_path:
        monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    monkeypatch.setenv("INTERNAL_LLM_KEY", "fake")
    monkeypatch.setenv("INTERNAL_LLM_BASE_QWEN_MAIN", "http://fake")

    from catfish_gateway.app import app, get_current_user as real_dep
    from catfish_gateway.auth.base import User
    fake = User(
        sub=f"{role}@ffcs.cn",
        department="engineering",
        tier="admin" if role in ("admin", "sysadmin") else "employee",
        role=role,
        managed_departments=[],
        auth_method="test",
    )

    async def fake_get_current_user():
        return fake

    app.dependency_overrides[real_dep] = fake_get_current_user
    return TestClient(app)


@pytest.fixture
def client(monkeypatch, tmp_path):
    c = _make_client_as("employee", tmp_path=tmp_path, monkeypatch=monkeypatch)
    yield c
    from catfish_gateway.app import app
    app.dependency_overrides.clear()
    # 清掉残留 RecMode session 防 test 间污染
    from catfish_gateway.recmode import cdp_listener
    cdp_listener._active_sessions.clear()


def test_start_recording_missing_session_id(client):
    r = client.post("/api/learn/start_recording", json={})
    assert r.status_code == 400
    assert "session_id" in r.json()["detail"]


def test_start_then_active_then_stop_roundtrip(client):
    r = client.post("/api/learn/start_recording", json={"session_id": "rec_e2e"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["session_id"] == "rec_e2e"
    assert "started_at" in data
    assert data["viewer"] == "employee@ffcs.cn"

    r = client.get("/api/learn/active")
    assert r.status_code == 200
    assert "rec_e2e" in r.json()["active_session_ids"]

    r = client.post("/api/learn/stop_recording", json={"session_id": "rec_e2e"})
    assert r.status_code == 200
    summary = r.json()
    assert summary["session_id"] == "rec_e2e"
    assert "events_count" in summary
    assert "duration_s" in summary

    r = client.get("/api/learn/active")
    assert "rec_e2e" not in r.json()["active_session_ids"]


def test_duplicate_start_409(client):
    client.post("/api/learn/start_recording", json={"session_id": "rec_dup"})
    r = client.post("/api/learn/start_recording", json={"session_id": "rec_dup"})
    assert r.status_code == 409
    assert "已在录中" in r.json()["detail"]


def test_stop_nonexistent_404(client):
    r = client.post("/api/learn/stop_recording", json={"session_id": "rec_nope"})
    assert r.status_code == 404
    assert "没在录中" in r.json()["detail"]


def test_stop_missing_session_id(client):
    r = client.post("/api/learn/stop_recording", json={})
    assert r.status_code == 400
