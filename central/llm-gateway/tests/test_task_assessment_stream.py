"""真跑一遍 SSE, 看 task_assessment 里的 skill_guard_fired 到底是什么值。

# 为什么要行为测试, AST 断言不够

test_task_assessment_contract.py 钉的是"这个字段不是写死的常量"——那是**源码
形状**。它挡得住"有人又改回 sg_fired = False", 挡不住"算出来了但算错了"。

而这条链断了 79 天没人发现, 恰恰因为两边的测试都在测自己那一侧的假设:

  · 后端: 没有任何测试跑过真 SSE 去看这个字段的值
  · 前端: promiseCheck.test.ts 的夹具直接写 `skill_guard_fired: true`
          —— 一个后端当时永远产不出的值

所以补一条真的: 驱动 `_stream_chat_completion`, 收完整 SSE, 把 task_assessment
那一行挖出来, 断言它的值随请求变化。

# 上游全 mock

不依赖网络 / DB。`with_fallback` 换成假的, 直接返 (iterator, first_chunk)。
`is_internal=True` 跳过 quota.record_usage (不碰 PG)。
"""
from __future__ import annotations

import importlib
import json

import pytest

app_module = importlib.import_module("catfish_gateway.app")


def _fake_stream(monkeypatch, content: str, *, tool_call: bool = False):
    """让上游吐一条 content chunk + 一条 stop chunk。"""
    delta: dict = {"content": content}
    if tool_call:
        delta["tool_calls"] = [
            {"index": 0, "id": "call_1",
             "function": {"name": "catfish_run_skill", "arguments": "{}"}}
        ]
    first = {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
    tail = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}

    async def _iter():
        yield tail

    async def _fake_with_fallback(config, model, invoke_one, prompt_estimate=0):  # noqa: ARG001
        return ((_iter(), first), model, [])

    monkeypatch.setattr(app_module, "with_fallback", _fake_with_fallback)


def _pick_model():
    cfg = app_module.get_config()
    for m in cfg.models:
        if getattr(m, "mode", "chat") == "chat":
            return m
    pytest.skip("catalog 里没有 chat 模型")


async def _collect(body: dict) -> list[str]:
    model = _pick_model()
    out = []
    gen = app_module._stream_chat_completion(
        body,
        user_sub="tester@example.com",
        user_dept="dev",
        model_name=model.name,
        model=model,
        is_internal=True,      # 跳 quota.record_usage, 不碰 DB
        source_hint="pytest",
        request=None,
    )
    async for chunk in gen:
        out.append(chunk)
    return out


def _task_assessment(sse_lines: list[str]) -> dict:
    for raw in sse_lines:
        for line in raw.splitlines():
            if not line.startswith("data: "):
                continue
            payload = line[len("data: "):].strip()
            if payload == "[DONE]":
                continue
            try:
                d = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict) and d.get("object") == "task_assessment":
                return d
    raise AssertionError(
        "SSE 里没有 task_assessment 事件 —— Companion 的嘴炮检测拿不到判据, "
        f"整条链就是死的。收到的行:\n{''.join(sse_lines)[:800]}"
    )


_PROMISE = "已生成 ~/Desktop/test.md"


@pytest.mark.asyncio
async def test_带工具且零调用_判为可能嘴炮(monkeypatch):
    """★★ 递了工具, 模型一个没调还说"已生成" → skill_guard_fired 必须是 true。

    这是前端出 ⚠ badge 的必要条件之一 (promiseCheck.ts 条件 3)。
    它在 5/26 → 8/13 期间恒为 false, badge 一次都没显示过。
    """
    _fake_stream(monkeypatch, _PROMISE)
    body = {
        "model": "x", "stream": True,
        "messages": [{"role": "user", "content": "帮我生成一份 md"}],
        "tools": [{"type": "function", "function": {
            "name": "catfish_run_skill", "description": "跑 skill",
            "parameters": {"type": "object", "properties": {}}}}],
    }
    ta = _task_assessment(await _collect(body))
    assert ta["skill_guard_fired"] is True, (
        f"递了 tools 却判成 false —— 前端条件 3 过不去, ⚠ badge 出不来。ta={ta}"
    )
    assert ta["cum_has_tool_call"] is False
    assert ta["tool_call_count"] == 0


@pytest.mark.asyncio
async def test_没递工具_不算嘴炮(monkeypatch):
    """★ 没给工具的纯聊天, 模型说什么都不该被判嘴炮 —— 它没有动手的手段。"""
    _fake_stream(monkeypatch, _PROMISE)
    body = {
        "model": "x", "stream": True,
        "messages": [{"role": "user", "content": "你好"}],
    }
    ta = _task_assessment(await _collect(body))
    assert ta["skill_guard_fired"] is False, (
        f"没递 tools 却判成 true —— 普通问答会误报 ⚠。ta={ta}"
    )


@pytest.mark.asyncio
async def test_tool_choice_none_是遵命不是嘴炮(monkeypatch):
    """★ 调用方明确说"这轮别调工具", 模型不调是听话, 不该被判嘴炮。"""
    _fake_stream(monkeypatch, _PROMISE)
    body = {
        "model": "x", "stream": True,
        "messages": [{"role": "user", "content": "帮我生成一份 md"}],
        "tools": [{"type": "function", "function": {
            "name": "catfish_run_skill", "description": "跑 skill",
            "parameters": {"type": "object", "properties": {}}}}],
        "tool_choice": "none",
    }
    ta = _task_assessment(await _collect(body))
    assert ta["skill_guard_fired"] is False, f"tool_choice=none 时误判。ta={ta}"


@pytest.mark.asyncio
async def test_真调了工具_前端会在更前面就放行(monkeypatch):
    """真调了工具 → cum_has_tool_call=true, 前端条件 2 直接放行, 不看条件 3。

    钉这条是为了确认 tool_call_count / cum_has_tool_call 也是真算的 ——
    它们跟 skill_guard_fired 一起构成判据, 任何一个恒定都会让检测失效。
    """
    _fake_stream(monkeypatch, "好的", tool_call=True)
    body = {
        "model": "x", "stream": True,
        "messages": [{"role": "user", "content": "帮我生成一份 md"}],
        "tools": [{"type": "function", "function": {
            "name": "catfish_run_skill", "description": "跑 skill",
            "parameters": {"type": "object", "properties": {}}}}],
    }
    ta = _task_assessment(await _collect(body))
    assert ta["cum_has_tool_call"] is True
    assert ta["tool_call_count"] >= 1
