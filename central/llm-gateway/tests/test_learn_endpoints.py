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
    # 5/26 BL-RECMODE-MIGRATE-TO-EDGE batch 1: cdp_listener 搬 edge, central stub.
    # 不再需要清理 central _active_sessions (那是 stub 永远抛 RuntimeError).
    # 真 session 状态在 tool-bridge 进程, 跨 test 隔离由测试 mock 提供.


def test_start_recording_missing_session_id(client):
    r = client.post("/api/learn/start_recording", json={})
    assert r.status_code == 400
    assert "session_id" in r.json()["detail"]


# 5/26 BL-RECMODE-MIGRATE-TO-EDGE batch 1: start/stop/active/status/cleanup 改 thin
# proxy 后, 原 e2e roundtrip 测试 (真起 listener + active 列出 + stop) 搬到
# edge/tool-bridge/tests/recmode/test_cdp_listener.py — 那是真 cdp_listener 的家.
# 这里只测 gateway 端 proxy 行为 (body 校验 + JSON-RPC code → HTTP code 映射).


def test_start_recording_proxy_success(client, monkeypatch):
    """tool-bridge 返成功 → gateway 透传 + 注 viewer."""
    from catfish_gateway import tool_bridge_rpc as _tb_rpc
    expected = {"session_id": "rec_x", "started_at": 1234567890, "output_dir": "/tmp/rec_x"}

    async def fake_call(method, params, **kw):
        assert method == "recmode/start_recording"
        assert params["session_id"] == "rec_x"
        return dict(expected)

    monkeypatch.setattr(_tb_rpc, "call", fake_call)
    r = client.post("/api/learn/start_recording", json={"session_id": "rec_x", "connect_ws": False})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["session_id"] == "rec_x"
    assert out["viewer"] == "employee@ffcs.cn"


def test_start_recording_duplicate_409(client, monkeypatch):
    """tool-bridge 报"重复" → gateway 映 409."""
    from catfish_gateway import tool_bridge_rpc as _tb_rpc

    async def fake_call(method, params, **kw):
        raise _tb_rpc.ToolBridgeRPCError(_tb_rpc.JSONRPC_INVALID_PARAMS,
                                          "start_recording 重复 (409): session 已在录中")

    monkeypatch.setattr(_tb_rpc, "call", fake_call)
    r = client.post("/api/learn/start_recording", json={"session_id": "rec_dup", "connect_ws": False})
    assert r.status_code == 409


def test_stop_recording_not_found_404(client, monkeypatch):
    """tool-bridge 报"不存在" → gateway 映 404."""
    from catfish_gateway import tool_bridge_rpc as _tb_rpc

    async def fake_call(method, params, **kw):
        raise _tb_rpc.ToolBridgeRPCError(_tb_rpc.JSONRPC_INVALID_PARAMS,
                                          "session rec_nope 不存在 (404)")

    monkeypatch.setattr(_tb_rpc, "call", fake_call)
    r = client.post("/api/learn/stop_recording", json={"session_id": "rec_nope"})
    assert r.status_code == 404


def test_stop_missing_session_id(client):
    r = client.post("/api/learn/stop_recording", json={})
    assert r.status_code == 400


def test_analyze_missing_session_id(client):
    r = client.post("/api/learn/analyze", json={})
    assert r.status_code == 400


# ─── BL-RECMODE-MIGRATE-TO-EDGE (5/25) ────────────────────
#
# /api/learn/analyze + /repair_selector 现在是 thin proxy → edge tool-bridge.
# 老 e2e 测试 mock 的是 aggregator.call_llm — 那个 module 现在是 stub
# (RuntimeError on import). 改 mock 层到 tool_bridge_rpc.call, 测的就是
# proxy 行为 (deserialize body → JSON-RPC call → 错误码映射), 真 aggregator
# 落盘逻辑的 e2e 测试现在归 edge/tool-bridge/tests/.
#
# 测的边界:
#   - 老 e2e ("session 真存在 → 落 SKILL.md") → 移 edge/tool-bridge/tests/
#   - 这里只测 proxy 层 (body 校验 + JSON-RPC error code → HTTP 码 映射 + viewer 注入)


def test_analyze_session_dir_not_found_via_proxy(client, monkeypatch):
    """tool-bridge 报 INVALID_PARAMS 含 'session_dir...不存在' → gateway 应映 404."""
    from catfish_gateway import tool_bridge_rpc as _tb_rpc

    async def fake_call(method, params, **kw):
        from catfish_gateway.tool_bridge_rpc import (
            ToolBridgeRPCError,
            JSONRPC_INVALID_PARAMS,
        )
        raise ToolBridgeRPCError(JSONRPC_INVALID_PARAMS, "session_dir /tmp/recordings/rec_no 不存在.")

    monkeypatch.setattr(_tb_rpc, "call", fake_call)
    r = client.post("/api/learn/analyze", json={"session_id": "rec_no"})
    assert r.status_code == 404
    assert "不存在" in r.json()["detail"]


def test_analyze_proxy_success(client, monkeypatch):
    """tool-bridge 返成功结果 → gateway 透传 + 注入 viewer."""
    from catfish_gateway import tool_bridge_rpc as _tb_rpc

    expected = {
        "skill_name": "rec_analyze_test",
        "namespace": "personal",
        "skill_dir": "/tmp/skills/personal/rec_analyze_test",
        "confidence": 0.7,
        "questions_for_user": [],
        "steps_count": 3,
    }

    captured = {}
    async def fake_call(method, params, **kw):
        captured["method"] = method
        captured["params"] = params
        return dict(expected)

    monkeypatch.setattr(_tb_rpc, "call", fake_call)
    r = client.post("/api/learn/analyze", json={
        "session_id": "rec_analyze",
        "skills_root": "/tmp/skills",
        "draft_only": False,
    })
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["skill_name"] == "rec_analyze_test"
    assert out["confidence"] == 0.7
    assert "viewer" in out  # gateway 注入身份
    # 校验 proxy 透传参数正确
    assert captured["method"] == "recmode/analyze"
    assert captured["params"]["session_id"] == "rec_analyze"
    assert captured["params"]["skills_root"] == "/tmp/skills"
    assert captured["params"]["draft_only"] is False


def test_analyze_tool_bridge_unreachable_502(client, monkeypatch):
    """tool-bridge 没起 / socket 缺 → gateway 应返 502."""
    from catfish_gateway import tool_bridge_rpc as _tb_rpc

    async def fake_call(method, params, **kw):
        raise _tb_rpc.ToolBridgeUnreachable("socket 不存在")

    monkeypatch.setattr(_tb_rpc, "call", fake_call)
    r = client.post("/api/learn/analyze", json={"session_id": "any"})
    assert r.status_code == 502
    assert "tool-bridge 不可达" in r.json()["detail"]


def test_analyze_tool_bridge_internal_error_502(client, monkeypatch):
    """tool-bridge INTERNAL_ERROR (e.g. vision LLM 挂) → gateway 应返 502."""
    from catfish_gateway import tool_bridge_rpc as _tb_rpc

    async def fake_call(method, params, **kw):
        raise _tb_rpc.ToolBridgeRPCError(_tb_rpc.JSONRPC_INTERNAL_ERROR,
                                          "LLM call timeout")

    monkeypatch.setattr(_tb_rpc, "call", fake_call)
    r = client.post("/api/learn/analyze", json={"session_id": "any"})
    assert r.status_code == 502
    assert "LLM call timeout" in r.json()["detail"]


def test_analyze_value_error_422(client, monkeypatch):
    """tool-bridge 报 INTERNAL_ERROR 含 '参数错' (ValueError 转过来的) → 422."""
    from catfish_gateway import tool_bridge_rpc as _tb_rpc

    async def fake_call(method, params, **kw):
        raise _tb_rpc.ToolBridgeRPCError(_tb_rpc.JSONRPC_INTERNAL_ERROR,
                                          "aggregate_session 参数错 (422): bad skill_name")

    monkeypatch.setattr(_tb_rpc, "call", fake_call)
    r = client.post("/api/learn/analyze", json={"session_id": "any"})
    assert r.status_code == 422


# ─── BL-RECMODE-MIGRATE-TO-EDGE batch 2 (5/26): A/B/C 3 个 endpoint thin proxy 化 ──
#
# 老 e2e (真写 transcripts.jsonl / 真 mkdir skill_dir) 搬 edge/tool-bridge/tests/.
# gateway 端只测 thin proxy: body 校验 + JSON-RPC code → HTTP code 映射 + viewer 注入.


def test_skill_content_endpoint(client, monkeypatch):
    """B: /api/learn/skill_content thin proxy → tool-bridge 返 {skill_md, main_py}."""
    from catfish_gateway import tool_bridge_rpc as _tb_rpc
    expected = {"skill_md": "# test_skill\n\nhello", "main_py": "def main(p): return {}\n"}

    async def fake_call(method, params, **kw):
        assert method == "recmode/skill_content"
        assert "skill_dir" in params
        return dict(expected)

    monkeypatch.setattr(_tb_rpc, "call", fake_call)
    r = client.get("/api/learn/skill_content?skill_dir=/tmp/any")
    assert r.status_code == 200, r.text
    out = r.json()
    assert "hello" in out["skill_md"]
    assert "def main" in out["main_py"]
    assert out["viewer"] == "employee@ffcs.cn"


def test_skill_content_path_traversal_blocked(client, monkeypatch):
    """B: 路径校验在 tool-bridge 端 (返 '受信') → gateway 映 403."""
    from catfish_gateway import tool_bridge_rpc as _tb_rpc

    async def fake_call(method, params, **kw):
        raise _tb_rpc.ToolBridgeRPCError(_tb_rpc.JSONRPC_INVALID_PARAMS,
                                          "skill_dir /etc 不在受信路径下")

    monkeypatch.setattr(_tb_rpc, "call", fake_call)
    r = client.get("/api/learn/skill_content?skill_dir=/etc")
    assert r.status_code == 403


def test_record_transcript_endpoint(client, monkeypatch):
    """A: /api/learn/record_transcript thin proxy → tool-bridge 返 {lines_count}."""
    from catfish_gateway import tool_bridge_rpc as _tb_rpc

    captured = {}
    async def fake_call(method, params, **kw):
        captured["method"] = method
        captured["params"] = params
        return {"lines_count": 1}

    monkeypatch.setattr(_tb_rpc, "call", fake_call)
    r = client.post("/api/learn/record_transcript", json={
        "session_id": "rec_t",
        "text": "现在点应用 tab",
        "ts_offset": 1.5,
        "duration": 2.1,
    })
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["lines_count"] == 1
    assert out["viewer"] == "employee@ffcs.cn"
    assert captured["method"] == "recmode/record_transcript"
    assert captured["params"]["session_id"] == "rec_t"
    assert captured["params"]["text"] == "现在点应用 tab"


def test_record_transcript_session_not_found(client, monkeypatch):
    """A: tool-bridge 报 session '不存在' → gateway 映 404."""
    from catfish_gateway import tool_bridge_rpc as _tb_rpc

    async def fake_call(method, params, **kw):
        raise _tb_rpc.ToolBridgeRPCError(_tb_rpc.JSONRPC_INVALID_PARAMS,
                                          "session rec_nonexistent 不存在")

    monkeypatch.setattr(_tb_rpc, "call", fake_call)
    r = client.post("/api/learn/record_transcript", json={
        "session_id": "rec_nonexistent",
        "text": "test",
    })
    assert r.status_code == 404


def test_status_endpoint_proxy_success(client, monkeypatch):
    """F: /api/learn/status/<sid> proxy → tool-bridge 返成功"""
    from catfish_gateway import tool_bridge_rpc as _tb_rpc
    expected = {
        "session_id": "rec_status_t",
        "elapsed_s": 5.2,
        "events_count": 12,
        "keyframes_count": 3,
        "ws_connected": True,
    }

    async def fake_call(method, params, **kw):
        assert method == "recmode/status"
        assert params["session_id"] == "rec_status_t"
        return dict(expected)

    monkeypatch.setattr(_tb_rpc, "call", fake_call)
    r = client.get("/api/learn/status/rec_status_t")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["session_id"] == "rec_status_t"
    assert out["keyframes_count"] == 3


def test_status_endpoint_unknown_404(client, monkeypatch):
    """tool-bridge 报"没在录" → gateway 映 404."""
    from catfish_gateway import tool_bridge_rpc as _tb_rpc

    async def fake_call(method, params, **kw):
        raise _tb_rpc.ToolBridgeRPCError(_tb_rpc.JSONRPC_INVALID_PARAMS,
                                          "session rec_no_such 没在录 (404)")

    monkeypatch.setattr(_tb_rpc, "call", fake_call)
    r = client.get("/api/learn/status/rec_no_such")
    assert r.status_code == 404


def test_save_skill_endpoint(client, monkeypatch):
    """C: /api/learn/save_skill thin proxy → tool-bridge 返 {moved, final_dir}."""
    from catfish_gateway import tool_bridge_rpc as _tb_rpc

    captured = {}
    async def fake_call(method, params, **kw):
        captured["method"] = method
        captured["params"] = params
        return {"moved": True, "final_dir": "/tmp/skills/personal/save_test"}

    monkeypatch.setattr(_tb_rpc, "call", fake_call)
    r = client.post("/api/learn/save_skill", json={
        "draft_dir": "/tmp/recordings/rec_save/skill_draft/personal/save_test",
        "namespace": "personal",
        "name": "save_test",
    })
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["moved"] is True
    assert out["viewer"] == "employee@ffcs.cn"
    assert captured["method"] == "recmode/save_skill"
    assert captured["params"]["namespace"] == "personal"


def test_save_skill_rejects_path_outside_recordings(client, monkeypatch):
    """C: draft_dir 校验在 tool-bridge 端 (返 '必须在') → gateway 映 403."""
    from catfish_gateway import tool_bridge_rpc as _tb_rpc

    async def fake_call(method, params, **kw):
        raise _tb_rpc.ToolBridgeRPCError(_tb_rpc.JSONRPC_INVALID_PARAMS,
                                          "draft_dir 必须在 recordings/ 下")

    monkeypatch.setattr(_tb_rpc, "call", fake_call)
    r = client.post("/api/learn/save_skill", json={"draft_dir": "/etc"})
    assert r.status_code == 403
