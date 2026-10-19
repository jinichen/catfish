"""10/19: 「对话太长」的流式错误要带 code=context_length_exceeded。

流式响应的 HTTP 状态已经是 200, 客户端只能看 SSE 里那条 error 认是什么错。
context_preflight 拦下 (413) 时原来只给一句中文, hermes 当普通错误白重试 3 次
(每次同样被拦), 最后把「这个对话太长了」甩给员工 —— 而它本来会压缩历史后重发。
带上 OpenAI 标准 code, hermes 的错误分类就认成上下文溢出, 走压缩恢复。
"""
from __future__ import annotations

import asyncio
import json
import types

from catfish_gateway import app as app_mod
from catfish_gateway import chat_runtime, output_transforms


def test_payload_带_code_时客户端能认出():
    p = app_mod._sse_error_payload("这个对话太长了", "context_length_exceeded")
    assert p == {"error": {"message": "这个对话太长了", "type": "upstream_error", "code": "context_length_exceeded"}}
    assert "code" not in app_mod._sse_error_payload("上游拥堵")["error"], "别的错不乱贴 code"


def test_装不下时_流里那条_error_带_context_length_exceeded(monkeypatch):
    monkeypatch.setattr("catfish_gateway.fallback.estimate_prompt_tokens", lambda *a, **kw: 133_461)
    called, audited = [], []
    monkeypatch.setattr(chat_runtime.litellm, "acompletion", lambda **kw: called.append(kw))
    # 收尾的 audit / 配额链不跑真的 (不碰本机 gateway 的库), 只记下来
    monkeypatch.setattr(output_transforms.DEFAULT_CHAIN, "run", audited.append)
    model = types.SimpleNamespace(
        name="catfish-private-vision", context_window=128_000,
        upstream=types.SimpleNamespace(model="openai/qwen", timeout=60), rate_limits=None,
    )

    async def collect():
        out = []
        async for chunk in chat_runtime._stream_chat_completion(
            {"model": "catfish-private-vision", "messages": [{"role": "user", "content": "你好"}], "stream": True},
            user_sub="t@example.com", user_dept="d", model_name="catfish-private-vision", model=model,
            is_internal=True,
        ):
            out.append(chunk)
        return out

    chunks = asyncio.run(collect())
    errs = [json.loads(c[len("data: "):]) for c in chunks if c.startswith("data: {") and '"error"' in c]
    assert errs, chunks
    err = errs[0]["error"]
    assert err["code"] == "context_length_exceeded"
    assert "对话太长" in err["message"]
    assert not called, "装不下就不该发给上游"
    assert audited and audited[0].status == "error"
