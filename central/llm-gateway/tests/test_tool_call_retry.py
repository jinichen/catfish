"""半截 tool_call 参数的重发兜底。见 tool_call_retry.py。"""

from __future__ import annotations

import asyncio
import json
import types

from catfish_gateway import chat_runtime, output_transforms
from catfish_gateway.tool_call_retry import broken_tool_calls, flush_held


def _tc(args: str, name: str | None = "delegate_task", finish=None, content=None) -> dict:
    fn = {"arguments": args}
    if name:
        fn["name"] = name
    delta = {"tool_calls": [{"index": 0, "id": "c1" if name else None, "function": fn}]}
    if content:
        delta["content"] = content
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def _finish(reason: str) -> dict:
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


async def _passthrough(it):
    async for c in it:
        yield c


def _restart_with(*runs):
    calls = {"n": 0}

    async def restart():
        chunks = list(runs[calls["n"]])
        calls["n"] += 1

        async def gen():
            for c in chunks[1:]:
                yield c

        return gen(), chunks[0]

    return restart, calls


def _collect(held, restart, usage_out=None):
    async def run():
        gen = flush_held(held, restart, _passthrough, "m", "r", usage_out)
        return [line async for line in gen]

    return asyncio.run(run())


def _payloads(lines):
    return [json.loads(x[6:]) for x in lines if x.startswith("data: {")]


def test_broken_detection():
    assert broken_tool_calls([_tc('{"tasks": ')]) == ["delegate_task:10"]
    assert broken_tool_calls([_tc('{"tasks": '), _tc("[]}", name=None)]) == []


def test_complete_args_pass_through_without_retry():
    held = [_tc('{"tasks": []}'), _finish("tool_calls")]
    restart, calls = _restart_with()
    assert _payloads(_collect(held, restart)) == held
    assert calls["n"] == 0


def test_broken_args_retried_and_text_stripped():
    held = [_tc('{"tasks": '), _finish("stop")]
    good = [
        {
            "choices": [
                {"index": 0, "delta": {"reasoning_content": "想一想"}, "finish_reason": None}
            ]
        },
        _tc('{"tasks": [{"goal": "x"}]}', content="又说一遍"),
        _finish("tool_calls"),
        {"choices": [], "usage": {"prompt_tokens": 1}},
    ]
    restart, calls = _restart_with(good)
    usage: dict = {}
    out = _payloads(_collect(held, restart, usage))
    assert calls["n"] == 1
    assert usage == {"prompt_tokens": 1, "completion_tokens": 0}
    assert len(out) == 3  # 思考 chunk 丢掉, tool chunk + finish + usage
    assert "content" not in out[0]["choices"][0]["delta"]
    assert out[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"].endswith("]}")
    assert out[1]["choices"][0]["finish_reason"] == "tool_calls"


def test_gives_up_after_max_retries_and_releases_original():
    held = [_tc('{"tasks": '), _finish("stop")]
    bad = [_tc('{"tasks": '), _finish("stop")]
    restart, calls = _restart_with(bad, bad)
    assert _payloads(_collect(held, restart)) == held
    assert calls["n"] == 2


def test_length_cutoff_not_retried():
    held = [_tc('{"code": "print('), _finish("length")]
    restart, calls = _restart_with()
    assert _payloads(_collect(held, restart)) == held
    assert calls["n"] == 0


def test_failed_retries_still_count_usage():
    held = [_tc('{"tasks": '), _finish("stop")]
    bad = [
        _tc('{"tasks": '),
        _finish("stop"),
        {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 5}},
    ]
    restart, _ = _restart_with(bad, bad)
    usage: dict = {}
    _collect(held, restart, usage)
    assert usage == {"prompt_tokens": 200, "completion_tokens": 10}


# ── 接进 _stream_chat_completion 之后的整条链 ──────────────────────────


def _run_stream(monkeypatch, runs, keepalive_secs=30):
    monkeypatch.setattr("catfish_gateway.fallback.estimate_prompt_tokens", lambda *a, **kw: 100)
    monkeypatch.setattr("catfish_gateway.fallback.estimate_prompt_tokens_raw", lambda *a, **kw: 100)
    monkeypatch.setattr(chat_runtime, "_KEEPALIVE_INTERVAL_SECS", keepalive_secs)
    audited: list = []
    monkeypatch.setattr(output_transforms.DEFAULT_CHAIN, "run", audited.append)
    queue = list(runs)

    async def fake_acompletion(**kw):
        chunks = queue.pop(0)

        async def gen():
            for c in chunks:
                yield c

        return gen()

    monkeypatch.setattr(chat_runtime.litellm, "acompletion", fake_acompletion)
    model = types.SimpleNamespace(
        name="m",
        context_window=1_000_000,
        max_output_tokens=65536,
        upstream=types.SimpleNamespace(
            model="openai/qwen3.8-flash",
            timeout=60,
            api_key="x",
            api_base=None,
            provider=None,
            param_overrides=None,
        ),
        rate_limits=None,
    )

    async def collect():
        return [
            c
            async for c in chat_runtime._stream_chat_completion(
                {"model": "m", "messages": [{"role": "user", "content": "hi"}], "stream": True},
                user_sub="t@example.com",
                user_dept="d",
                model_name="m",
                model=model,
                is_internal=True,
            )
        ]

    return asyncio.run(collect()), audited, queue


def test_stream_retries_broken_tool_call_and_bills_retry(monkeypatch):
    usage = {"choices": [], "usage": {"prompt_tokens": 1000, "completion_tokens": 10}}
    first = [
        {"choices": [{"index": 0, "delta": {"content": "派下去"}, "finish_reason": None}]},
        _tc('{"tasks": '),
        _finish("stop"),
        usage,
    ]
    second = [_tc('{"tasks": [{"goal": "x"}]}'), _finish("tool_calls"), usage]
    out, audited, queue = _run_stream(monkeypatch, [first, second])
    assert not queue, "应该重发了一次"
    payloads = _payloads(out)
    args = "".join(
        tc["function"].get("arguments") or ""
        for p in payloads
        for ch in p.get("choices") or []
        for tc in (ch.get("delta") or {}).get("tool_calls") or []
    )
    assert args == '{"tasks": [{"goal": "x"}]}', "半截那份不能漏给客户端"
    assert sum("派下去" in json.dumps(p, ensure_ascii=False) for p in payloads) == 1
    assert out[-1] == "data: [DONE]\n\n"
    rec = audited[0]
    assert (rec.prompt_tokens, rec.completion_tokens) == (2000, 20)


def test_stream_pings_while_holding_tool_calls(monkeypatch):
    run = [_tc('{"tasks": '), _tc("[]}", name=None), _tc("", name=None), _finish("tool_calls")]
    out, _, _ = _run_stream(monkeypatch, [run], keepalive_secs=0)
    assert out.count(": keepalive\n\n") >= 2
