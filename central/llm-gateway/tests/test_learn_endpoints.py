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
        "draft_only": False,  # 测老路径直接落正式 skills
    })
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["skill_name"] == "rec_analyze_test"
    assert out["namespace"] == "personal"
    assert out["confidence"] == 0.7
    assert (skills_root / "personal" / "rec_analyze_test" / "SKILL.md").exists()


def test_analyze_draft_default(client, tmp_path, monkeypatch):
    """5/14 RecMode C: 默认 draft_only=True, 落 session_dir/skill_draft/"""
    rec_dir = tmp_path / "recordings" / "rec_draft"
    rec_dir.mkdir(parents=True)
    (rec_dir / "events.jsonl").write_text('{"ts": 0, "kind": "click"}\n', encoding="utf-8")
    (rec_dir / "meta.json").write_text('{"session_id": "rec_draft"}', encoding="utf-8")

    fake_response = '{"skill_name": "drafted", "namespace": "personal", "description": "d", "params_schema": [], "steps": [], "execute_code_segment": "", "output_schema": {}, "confidence": 0.6, "questions_for_user": []}'
    from catfish_gateway.recmode import aggregator
    async def fake_call_llm(messages, **kw):
        return fake_response
    monkeypatch.setenv("CATFISH_DEV_TOKEN", "fake")
    monkeypatch.setattr(aggregator, "call_llm", fake_call_llm)

    r = client.post("/api/learn/analyze", json={"session_id": "rec_draft"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["is_draft"] is True
    # draft 在 recordings/rec_draft/skill_draft/personal/drafted/
    assert (rec_dir / "skill_draft" / "personal" / "drafted" / "SKILL.md").exists()


def test_skill_content_endpoint(client, tmp_path):
    """B: /api/learn/skill_content 读 SKILL.md + main.py"""
    sd = tmp_path / "recordings" / "rec_x" / "skill_draft" / "personal" / "test_skill"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text("# test_skill\n\nhello", encoding="utf-8")
    (sd / "main.py").write_text("def main(p): return {}\n", encoding="utf-8")

    r = client.get(f"/api/learn/skill_content?skill_dir={sd}")
    assert r.status_code == 200, r.text
    out = r.json()
    assert "hello" in out["skill_md"]
    assert "def main" in out["main_py"]


def test_skill_content_path_traversal_blocked(client):
    """B: 路径必须在 ~/.catfish/skills 或 recordings 下, 拒绝 /etc /root 等"""
    r = client.get("/api/learn/skill_content?skill_dir=/etc")
    assert r.status_code == 403


def test_record_transcript_endpoint(client, tmp_path):
    """A: /api/learn/record_transcript 写 transcripts.jsonl"""
    rec_dir = tmp_path / "recordings" / "rec_t"
    rec_dir.mkdir(parents=True)
    r = client.post("/api/learn/record_transcript", json={
        "session_id": "rec_t",
        "text": "现在点应用 tab",
        "ts_offset": 1.5,
        "duration": 2.1,
    })
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["lines_count"] == 1
    assert (rec_dir / "transcripts.jsonl").exists()
    content = (rec_dir / "transcripts.jsonl").read_text()
    assert "现在点应用" in content


def test_record_transcript_session_not_found(client):
    r = client.post("/api/learn/record_transcript", json={
        "session_id": "rec_nonexistent",
        "text": "test",
    })
    assert r.status_code == 404


def test_status_endpoint_active(client):
    """F: /api/learn/status/<sid> 返实时 keyframes / events 计数"""
    client.post("/api/learn/start_recording", json={
        "session_id": "rec_status_t", "connect_ws": False,
    })
    r = client.get("/api/learn/status/rec_status_t")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["session_id"] == "rec_status_t"
    assert "elapsed_s" in out
    assert "events_count" in out
    assert "keyframes_count" in out
    client.post("/api/learn/stop_recording", json={"session_id": "rec_status_t"})


def test_status_endpoint_unknown_404(client):
    r = client.get("/api/learn/status/rec_no_such")
    assert r.status_code == 404


def test_save_skill_endpoint(client, tmp_path, monkeypatch):
    """C: /api/learn/save_skill 把 draft 移到正式 skills"""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    rec_dir = tmp_path / "recordings" / "rec_save" / "skill_draft" / "personal" / "save_test"
    rec_dir.mkdir(parents=True)
    (rec_dir / "SKILL.md").write_text("# save_test", encoding="utf-8")
    (rec_dir / "main.py").write_text("def main(p): return {}", encoding="utf-8")
    import json as _json
    (rec_dir / "recmode_meta.json").write_text(_json.dumps({
        "raw_llm_json": {"skill_name": "save_test", "namespace": "personal"},
    }), encoding="utf-8")

    r = client.post("/api/learn/save_skill", json={"draft_dir": str(rec_dir)})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["moved"] is True
    final = tmp_path / "skills" / "personal" / "save_test"
    assert final.exists()
    assert (final / "SKILL.md").exists()


def test_save_skill_rejects_path_outside_recordings(client, tmp_path):
    """C: draft_dir 必须在 recordings/ 下, 拒 /etc /tmp 等"""
    r = client.post("/api/learn/save_skill", json={"draft_dir": "/etc"})
    assert r.status_code == 403
