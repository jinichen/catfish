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
    r = client.post("/api/learn/start_recording", json={"session_id": "rec_e2e", "connect_ws": False})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["session_id"] == "rec_e2e"
    assert "started_at" in data
    assert data["viewer"] == "employee@ffcs.cn"

    r = client.get("/api/learn/active")
    assert r.status_code == 200
    assert "rec_e2e" in r.json()["active_session_ids"]

    r = client.post("/api/learn/stop_recording", json={"session_id": "rec_e2e", "connect_ws": False})
    assert r.status_code == 200
    summary = r.json()
    assert summary["session_id"] == "rec_e2e"
    assert "events_count" in summary
    assert "duration_s" in summary

    r = client.get("/api/learn/active")
    assert "rec_e2e" not in r.json()["active_session_ids"]


def test_duplicate_start_409(client):
    client.post("/api/learn/start_recording", json={"session_id": "rec_dup", "connect_ws": False})
    r = client.post("/api/learn/start_recording", json={"session_id": "rec_dup", "connect_ws": False})
    assert r.status_code == 409
    assert "已在录中" in r.json()["detail"]


def test_stop_nonexistent_404(client):
    r = client.post("/api/learn/stop_recording", json={"session_id": "rec_nope"})
    assert r.status_code == 404
    assert "没在录中" in r.json()["detail"]


def test_stop_missing_session_id(client):
    r = client.post("/api/learn/stop_recording", json={})
    assert r.status_code == 400


def test_analyze_missing_session_id(client):
    r = client.post("/api/learn/analyze", json={})
    assert r.status_code == 400


def test_analyze_session_dir_not_found(client):
    r = client.post("/api/learn/analyze", json={"session_id": "rec_no_such"})
    assert r.status_code == 404
    assert "不存在" in r.json()["detail"]


def test_analyze_e2e_with_mock_llm(client, tmp_path, monkeypatch):
    """端到端 mock aggregator.call_llm → /api/learn/analyze 落 skill 文件."""
    # 准备一个 fake recording session_dir
    rec_dir = tmp_path / "recordings" / "rec_analyze"
    rec_dir.mkdir(parents=True)
    (rec_dir / "events.jsonl").write_text('{"ts": 0, "kind": "click"}\n', encoding="utf-8")
    (rec_dir / "meta.json").write_text('{"session_id": "rec_analyze", "duration_s": 10}', encoding="utf-8")

    fake_response = '{"skill_name": "rec_analyze_test", "namespace": "personal", "description": "测试", "params_schema": [], "steps": [], "execute_code_segment": "", "output_schema": {}, "confidence": 0.7, "questions_for_user": []}'

    async def fake_call_llm(messages, **kw):
        return fake_response

    from catfish_gateway.recmode import aggregator
    monkeypatch.setattr(aggregator, "call_llm", fake_call_llm)
    monkeypatch.setenv("CATFISH_DEV_TOKEN", "fake")  # call_llm 不会用因为 mock 了

    skills_root = tmp_path / "skills"
    r = client.post("/api/learn/analyze", json={
        "session_id": "rec_analyze",
        "skills_root": str(skills_root),
    })
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["skill_name"] == "rec_analyze_test"
    assert out["namespace"] == "personal"
    assert out["confidence"] == 0.7
    assert (skills_root / "personal" / "rec_analyze_test" / "SKILL.md").exists()
