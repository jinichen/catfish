"""Mock LLM upstream — BL-F10 性能基准用.

# 为什么

catfish gateway 在 N 并发下扛多少? BL-F10 要真测, 但**不能真打 Anthropic / Groq**:
- 100 并发 × 5 min ≈ 几千 chat 调用, 烧 $$$, 撞上游 rate limit
- 测的是 gateway 自己 (auth / RBAC / quota PG 写 / inject / fallback), 不是 LLM 速度

# 怎么用

```bash
# 1. 启 mock upstream (本机或 docker)
uvicorn bench.mock_upstream:app --host 0.0.0.0 --port 9999 --workers 4

# 2. 在 models.bench.yaml 里加一个 model 指向这
#    upstream.api_base: http://mock_upstream:9999/v1

# 3. 跑 locust
locust -f bench/locustfile.py --host http://localhost:8999 ...
```

# 协议

实现 OpenAI 兼容子集:
- `POST /v1/chat/completions` (stream / non-stream)
- `POST /v1/embeddings` (留兜底, 给 embedding model 测)
- `GET /v1/models` (健康检查)

# 延迟模型

每 token 30-80ms 随机, 50 tokens / chat 总 1.5-4s 模拟真 sonnet/groq 平均.
可通过 env 调:
- MOCK_LATENCY_PER_TOKEN_MS_MIN=30
- MOCK_LATENCY_PER_TOKEN_MS_MAX=80
- MOCK_TOKENS_PER_RESPONSE=50
- MOCK_FAIL_RATE=0.0  (0-1, 模拟上游随机 5xx; 测 fallback chain 用)

# 跟 manifesto 关系

跟 catfish 全部代码同样: 不依赖外网 (除了员工同意才出口的 LLM). 这 file 跑在
内部 bench 网络, 不出公司.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import time
import uuid
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="catfish-bench-mock-upstream", version="1.0")

# 配置 (env 覆盖, 默认像 sonnet 平均)
LATENCY_MIN = int(os.environ.get("MOCK_LATENCY_PER_TOKEN_MS_MIN", "30"))
LATENCY_MAX = int(os.environ.get("MOCK_LATENCY_PER_TOKEN_MS_MAX", "80"))
TOKENS_PER_RESPONSE = int(os.environ.get("MOCK_TOKENS_PER_RESPONSE", "50"))
FAIL_RATE = float(os.environ.get("MOCK_FAIL_RATE", "0.0"))

# 50 个常用中英 token 模拟真 streaming chunk
MOCK_TOKENS = [
    "好的", "我", "来", "帮", "你", "处理", "这", "个", "请求", "。",
    "首先", "让", "我", "理解", "一下", "你", "的", "需求", "。",
    "根据", "你", "提到", "的", "情况", ",", "我", "建议", "采用",
    "以下", "方案", ":", "\n\n", "1. ", "第一步", "...\n",
    "2. ", "第二步", "...\n", "3. ", "第三步", "...\n\n",
    "如果", "还有", "其他", "问题", ",", "随时", "告诉", "我", "。",
]


@app.get("/v1/models")
async def list_models():
    """简单健康检查 + 列 mock 提供的 'model'."""
    return {
        "object": "list",
        "data": [
            {"id": "mock-bench", "object": "model", "owned_by": "catfish-bench"},
        ],
    }


@app.post("/v1/embeddings")
async def embeddings(req: Request):
    """Embedding 端点 — 返固定 dim=384 向量 (bge-m3 size), 不模拟延迟."""
    body = await req.json()
    input_texts = body.get("input", [])
    if isinstance(input_texts, str):
        input_texts = [input_texts]
    return {
        "object": "list",
        "data": [
            {
                "object": "embedding",
                "index": i,
                "embedding": [0.001 * (i + 1)] * 384,
            }
            for i in range(len(input_texts))
        ],
        "model": body.get("model", "mock-embed"),
        "usage": {"prompt_tokens": 10, "total_tokens": 10},
    }


@app.post("/v1/chat/completions")
async def chat_completions(req: Request):
    body = await req.json()
    model = body.get("model", "mock-bench")
    is_stream = bool(body.get("stream", False))

    # 模拟上游随机 5xx (测 fallback 链)
    if FAIL_RATE > 0 and random.random() < FAIL_RATE:
        raise HTTPException(status_code=503, detail="mock upstream simulated 503")

    completion_id = f"chatcmpl-mock-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    if not is_stream:
        # non-stream: 累延迟一次返
        total_delay = sum(
            random.uniform(LATENCY_MIN, LATENCY_MAX) / 1000
            for _ in range(TOKENS_PER_RESPONSE)
        )
        await asyncio.sleep(total_delay)
        content = "".join(_sample_tokens())
        return JSONResponse(
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": _estimate_prompt_tokens(body),
                    "completion_tokens": TOKENS_PER_RESPONSE,
                    "total_tokens": _estimate_prompt_tokens(body) + TOKENS_PER_RESPONSE,
                },
            }
        )

    # streaming
    async def gen() -> AsyncIterator[str]:
        # role chunk (跟 OpenAI 真行为对齐)
        first = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(first)}\n\n"

        # content chunks (每 token 一 delay)
        for tok in _sample_tokens():
            await asyncio.sleep(random.uniform(LATENCY_MIN, LATENCY_MAX) / 1000)
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"content": tok}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk)}\n\n"

        # finish chunk
        last = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": _estimate_prompt_tokens(body),
                "completion_tokens": TOKENS_PER_RESPONSE,
                "total_tokens": _estimate_prompt_tokens(body) + TOKENS_PER_RESPONSE,
            },
        }
        yield f"data: {json.dumps(last)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


def _sample_tokens() -> list[str]:
    """从 MOCK_TOKENS 取前 TOKENS_PER_RESPONSE 个 (不随机, 让 prompt_tokens 稳定)."""
    if TOKENS_PER_RESPONSE >= len(MOCK_TOKENS):
        # 重复填充
        return (MOCK_TOKENS * (TOKENS_PER_RESPONSE // len(MOCK_TOKENS) + 1))[:TOKENS_PER_RESPONSE]
    return MOCK_TOKENS[:TOKENS_PER_RESPONSE]


def _estimate_prompt_tokens(body: dict) -> int:
    """粗估 prompt tokens — 中文 / 英文混合按 1 字 ≈ 1 token."""
    total = 0
    for msg in body.get("messages", []):
        c = msg.get("content", "")
        if isinstance(c, str):
            total += len(c)
        elif isinstance(c, list):
            # multimodal 数组, 累 text 部分
            for part in c:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += len(part.get("text", ""))
    return total or 1
