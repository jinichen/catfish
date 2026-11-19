"""11/19 Anthropic 格式 /v1/messages: 请求翻成 OpenAI 走同一条流水线, 结果翻回来。

鸿波「目前是不是只支持 openai 兼容的 API, 不支持 anthropic 兼容?」→「两个都做」。
"""
from __future__ import annotations

import asyncio
import json

from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient

from catfish_gateway import anthropic_routes as ar
from catfish_gateway import app as app_mod
from catfish_gateway.auth import User

CLAUDE_CODE_LIKE = {
    "model": "catfish-public-qwen-flash",
    "max_tokens": 512,
    "system": [{"type": "text", "text": "你是写代码的助手"}],
    "messages": [
        {"role": "user", "content": [{"type": "text", "text": "看下 a.py"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"path": "a.py"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "print(1)"}]},
    ],
    "tools": [{"name": "Read", "description": "读文件", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}}],
    "thinking": {"type": "enabled", "budget_tokens": 1024},
    "metadata": {"user_id": "u1"},
    "stop_sequences": ["###"],
    "stream": True,
}


def test_请求翻成_OpenAI_格式():
    oa, tool_map = ar.to_openai_request(CLAUDE_CODE_LIKE)
    assert oa["model"] == "catfish-public-qwen-flash" and oa["max_tokens"] == 512
    roles = [m["role"] for m in oa["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]
    assert oa["messages"][2]["tool_calls"][0]["function"]["name"] == "Read"
    assert oa["messages"][3]["tool_call_id"] == "toolu_1"
    assert oa["tools"][0]["function"]["name"] == "Read"
    assert oa["stream"] is True and oa["stream_options"] == {"include_usage": True}
    for k in ("reasoning_effort", "thinking", "metadata"):
        assert k not in oa, f"{k} 上游不一定认, 不该带过去"
    assert tool_map == {}
    json.dumps(oa)  # 纯 JSON, 能交给流水线


def test_缺模型或消息_报_400():
    for bad in ({"messages": []}, {"model": "x", "messages": "hi"}):
        try:
            ar.to_openai_request(bad)
        except HTTPException as e:
            assert e.status_code == 400
        else:
            raise AssertionError(bad)


def test_非流式结果翻回_Anthropic():
    resp = {"id": "chatcmpl-abc", "choices": [{"finish_reason": "tool_calls", "message": {
        "content": "我先读一下", "tool_calls": [{"id": "call_9", "function": {"name": "Read", "arguments": "{\"path\": \"b.py\"}"}}]}}],
        "usage": {"prompt_tokens": 30, "completion_tokens": 7}}
    out = ar.to_anthropic_response(resp, "m", {})
    assert out["type"] == "message" and out["role"] == "assistant" and out["model"] == "m"
    assert out["content"][0] == {"type": "text", "text": "我先读一下"}
    assert out["content"][1] == {"type": "tool_use", "id": "call_9", "name": "Read", "input": {"path": "b.py"}}
    assert out["stop_reason"] == "tool_use"
    assert out["usage"] == {"input_tokens": 30, "output_tokens": 7}
    # 截短过的工具名还原; 停止原因映射
    long = {"choices": [{"finish_reason": "length", "message": {"tool_calls": [{"id": "c", "function": {"name": "short", "arguments": "{}"}}]}}]}
    o2 = ar.to_anthropic_response(long, "m", {"short": "a_very_long_original_name"})
    assert o2["content"][0]["name"] == "a_very_long_original_name" and o2["stop_reason"] == "max_tokens"


def _sse_frames(chunks):
    async def gen():
        for c in chunks:
            yield c
    tr = ar.StreamTranslator("m", {})

    async def run():
        return [e async for e in ar.translate_stream(gen(), tr)]
    out = asyncio.run(run())
    events = []
    for e in out:
        ev, data = e.strip().split("\n", 1)
        events.append((ev[len("event: "):], json.loads(data[len("data: "):])))
    return events


def _d(obj):
    return f"data: {json.dumps(obj)}\n\n"


def test_流式_文字加工具调用_事件顺序对():
    chunks = [
        _d({"choices": [{"delta": {"role": "assistant", "content": "好"}}]}),
        _d({"choices": [{"delta": {"content": "的"}}]}) + ": keepalive\n\n",  # 一次来两条
        _d({"choices": [{"delta": {"reasoning_content": "想一想"}}]}),         # 思考过程不转
        _d({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "Read", "arguments": ""}}]}}]}),
        _d({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{\"path\":"}}]}}]}),
        _d({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": " \"a.py\"}"}}]}}]}),
        _d({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        _d({"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 5}}),
        "data: [DONE]\n\n",
    ]
    ev = _sse_frames(chunks)
    names = [e for e, _ in ev]
    assert names == ["message_start", "content_block_start", "content_block_delta", "content_block_delta", "ping",
                     "content_block_stop", "content_block_start", "content_block_delta", "content_block_delta",
                     "content_block_stop", "message_delta", "message_stop"]
    assert ev[1][1]["content_block"] == {"type": "text", "text": ""}
    assert "".join(d["delta"]["text"] for e, d in ev if e == "content_block_delta" and d["delta"]["type"] == "text_delta") == "好的"
    tool_start = ev[6][1]
    assert tool_start["index"] == 1 and tool_start["content_block"]["type"] == "tool_use"
    assert tool_start["content_block"]["name"] == "Read" and tool_start["content_block"]["id"] == "call_1"
    args = "".join(d["delta"]["partial_json"] for e, d in ev if e == "content_block_delta" and d["delta"]["type"] == "input_json_delta")
    assert json.loads(args) == {"path": "a.py"}
    md = ev[-2][1]
    assert md["delta"]["stop_reason"] == "tool_use"
    assert md["usage"] == {"input_tokens": 12, "output_tokens": 5}
    assert not any("想一想" in json.dumps(d, ensure_ascii=False) for _, d in ev)


def test_流式_中途出错发_error_事件_没有_DONE_也收尾():
    ev = _sse_frames([_d({"choices": [{"delta": {"content": "半"}}]}), _d({"error": {"message": "上游拥堵", "type": "upstream_error"}})])
    assert [e for e, _ in ev] == ["message_start", "content_block_start", "content_block_delta", "content_block_stop", "error"]
    assert ev[-1][1]["error"]["message"] == "上游拥堵"
    ev2 = _sse_frames([_d({"choices": [{"delta": {"content": "x"}, "finish_reason": "stop"}]})])
    assert [e for e, _ in ev2][-2:] == ["message_delta", "message_stop"]
    assert ev2[-2][1]["delta"]["stop_reason"] == "end_turn"


# ── 端点: 钥匙、错误格式、跟 /v1/chat/completions 同一条流水线 ──────────

def _client(monkeypatch, handle):
    seen = {}

    async def fake_handle(request, user, body):
        seen["body"], seen["user"] = body, user
        return await handle(body)
    monkeypatch.setattr(app_mod, "handle_chat", fake_handle)
    app_mod.app.dependency_overrides[ar.anthropic_user] = lambda: User(sub="dev@example.com", role="employee")
    return TestClient(app_mod.app), seen


def teardown_function(_):
    app_mod.app.dependency_overrides.pop(ar.anthropic_user, None)


def test_端点_非流式(monkeypatch):
    async def handle(body):
        return {"id": "x", "choices": [{"finish_reason": "stop", "message": {"content": "你好"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 1}}
    c, seen = _client(monkeypatch, handle)
    r = c.post("/v1/messages", json={"model": "catfish-auto", "max_tokens": 50, "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    assert r.json()["content"] == [{"type": "text", "text": "你好"}]
    assert seen["body"]["messages"] == [{"role": "user", "content": "hi"}] and seen["body"]["stream"] is False


def test_端点_流式(monkeypatch):
    async def handle(body):
        async def it():
            yield _d({"choices": [{"delta": {"content": "嗨"}, "finish_reason": "stop"}]})
            yield "data: [DONE]\n\n"
        return StreamingResponse(it(), media_type="text/event-stream")
    c, _ = _client(monkeypatch, handle)
    r = c.post("/v1/messages", json={"model": "m", "max_tokens": 50, "stream": True, "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert "event: message_start" in r.text and "event: message_stop" in r.text and "嗨" in r.text


def test_端点_错误是_Anthropic_格式_模型名写明怎么设(monkeypatch):
    async def handle(body):
        raise HTTPException(status_code=404, detail=f"model not found: {body['model']}")
    c, _ = _client(monkeypatch, handle)
    r = c.post("/v1/messages", json={"model": "claude-sonnet-4", "max_tokens": 5, "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 404
    err = r.json()
    assert err["type"] == "error" and err["error"]["type"] == "not_found_error"
    assert "ANTHROPIC_MODEL" in err["error"]["message"]

    async def synthetic(body):
        return JSONResponse({"error": {"message": "配额用完了"}}, status_code=429)
    c2, _ = _client(monkeypatch, synthetic)
    r2 = c2.post("/v1/messages", json={"model": "m", "max_tokens": 5, "messages": [{"role": "user", "content": "hi"}]})
    assert r2.status_code == 429 and r2.json()["error"] == {"type": "rate_limit_error", "message": "配额用完了"}


def test_钥匙_x_api_key_当_Bearer_用(monkeypatch):
    got = {}

    async def fake_get_current_user(authorization=None):
        got["auth"] = authorization
        return User(sub="dev@example.com", role="employee")
    monkeypatch.setattr(ar, "get_current_user", fake_get_current_user)
    asyncio.run(ar.anthropic_user(authorization=None, x_api_key=" sk-abc "))
    assert got["auth"] == "Bearer sk-abc"
    asyncio.run(ar.anthropic_user(authorization="Bearer jwt1", x_api_key="sk-abc"))
    assert got["auth"] == "Bearer jwt1", "两个都带时以 Authorization 为准"


def test_count_tokens_不调上游(monkeypatch):
    app_mod.app.dependency_overrides[ar.anthropic_user] = lambda: User(sub="dev@example.com", role="employee")
    monkeypatch.setattr("catfish_gateway.fallback.estimate_prompt_tokens_raw", lambda msgs, model=None, tools=None: 42)
    r = TestClient(app_mod.app).post("/v1/messages/count_tokens", json={"model": "nope", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200 and r.json() == {"input_tokens": 42}
