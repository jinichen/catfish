"""Plan D · A2A Client — A 端调 B 的 helper (五一 sprint Day 4 BL-M4.2).

# 用法

```python
from catfish_gateway.a2a_client import ask_remote_agent

async def example():
    async for chunk in ask_remote_agent(
        from_sub="alice@ffcs.cn",
        to_sub="bob@ffcs.cn",
        question="项目 X 上周进展?",
        purpose="周报",
    ):
        print(chunk, end="", flush=True)
```

# 流程

1. registry lookup (拿 B 的 catfish_endpoint + jwks_uri)
2. 检查 B 在线 (last_seen 在 2 分钟内)
3. sign_a2a_token → 给 B 签 JWT
4. POST B 的 /a2a/ask + Authorization: Bearer <jwt>
5. 接 SSE 流, yield 每个 chunk
6. 写 audit jsonl (outbound)

# 错误处理

抛 PermissionError / ConnectionError / RuntimeError, 调用方决定 UI 展示.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, AsyncIterator

import httpx

from .a2a_audit import write_audit
from .a2a_jwt import lookup_remote_agent, sign_a2a_token

logger = logging.getLogger("catfish.gateway.a2a_client")


async def ask_remote_agent(
    from_sub: str,
    to_sub: str,
    question: str,
    purpose: str = "",
    context_hint: str = "",
    max_tokens: int = 500,
    from_endpoint: str = "",
) -> AsyncIterator[str]:
    """A 端调 B, 流式 yield B 的回答 chunk.

    抛错:
      - PermissionError: B 拒绝 / JWT 验签失败 / B 没注册
      - ConnectionError: B 不可达
      - RuntimeError: 协议错误 / LLM 错误
    """
    request_id = str(uuid.uuid4())[:12]
    started_at = time.time()
    chunks_count = 0
    audit_id_remote = ""

    # 1. lookup
    try:
        entry = await lookup_remote_agent(to_sub)
    except PermissionError:
        write_audit({
            "direction": "outbound", "to_sub": to_sub, "question": question[:200],
            "status": "denied", "error_code": -32004,
            "error_msg": "agent not registered",
            "request_id": request_id,
        })
        raise
    except Exception as e:
        write_audit({
            "direction": "outbound", "to_sub": to_sub, "question": question[:200],
            "status": "error", "error_msg": f"registry lookup: {e}",
            "request_id": request_id,
        })
        raise ConnectionError(f"registry lookup 失败: {e}") from e

    if not entry.get("online"):
        write_audit({
            "direction": "outbound", "to_sub": to_sub, "question": question[:200],
            "status": "denied", "error_code": -32004,
            "error_msg": "target offline (last_seen > 2 min)",
            "request_id": request_id,
        })
        raise ConnectionError(f"{to_sub} 当前离线")

    catfish_endpoint = entry["catfish_endpoint"].rstrip("/")
    target_url = f"{catfish_endpoint}/a2a/ask"

    # 2. sign JWT
    try:
        token = sign_a2a_token(from_sub=from_sub, to_sub=to_sub)
    except Exception as e:
        write_audit({
            "direction": "outbound", "to_sub": to_sub, "question": question[:200],
            "status": "error", "error_msg": f"sign JWT 失败: {e}",
            "request_id": request_id,
        })
        raise RuntimeError(f"sign JWT 失败: {e}") from e

    # 3. 发 SSE 请求
    body = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "ask",
        "params": {
            "from_sub": from_sub,
            "from_endpoint": from_endpoint,
            "to_sub": to_sub,
            "question": question,
            "context_hint": context_hint,
            "purpose": purpose,
            "max_tokens": max_tokens,
        },
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    error_collected: tuple[int, str] | None = None  # (code, message)

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("POST", target_url, json=body, headers=headers) as resp:
                # 拿 audit_id (B 端写在 header)
                audit_id_remote = resp.headers.get("x-catfish-a2a-audit-id", "")
                if resp.status_code != 200:
                    body_text = ""
                    try:
                        body_text = (await resp.aread()).decode("utf-8", errors="ignore")[:500]
                    except Exception:
                        pass
                    raise RuntimeError(
                        f"HTTP {resp.status_code} from {target_url}: {body_text}"
                    )

                # 解 SSE 流
                event_name = ""
                data_buf: list[str] = []
                async for line in resp.aiter_lines():
                    if not line:
                        # 一条 event 结束
                        if data_buf:
                            data_str = "\n".join(data_buf)
                            data_buf.clear()
                            try:
                                payload = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue
                            chunk_or_done = await _process_sse_event(event_name, payload)
                            if chunk_or_done == "_done":
                                break
                            elif isinstance(chunk_or_done, tuple):
                                # error
                                error_collected = chunk_or_done
                                break
                            elif chunk_or_done:
                                chunks_count += 1
                                yield chunk_or_done
                        event_name = ""
                        continue
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        data_buf.append(line[5:].strip())
    except httpx.HTTPError as e:
        write_audit({
            "direction": "outbound", "to_sub": to_sub, "question": question[:200],
            "status": "error", "error_msg": f"HTTP: {e}",
            "request_id": request_id, "audit_id_remote": audit_id_remote,
        })
        raise ConnectionError(f"调 B 失败: {e}") from e

    duration_ms = int((time.time() - started_at) * 1000)

    if error_collected:
        code, message = error_collected
        write_audit({
            "direction": "outbound", "to_sub": to_sub, "question": question[:200],
            "status": "denied" if code == -32001 else "error",
            "error_code": code, "error_msg": message,
            "request_id": request_id, "audit_id_remote": audit_id_remote,
            "duration_ms": duration_ms,
        })
        # 把 error 透传给上层
        if code == -32001:
            raise PermissionError(f"B 拒绝: {message}")
        else:
            raise RuntimeError(f"A2A 错误 [{code}]: {message}")

    # 成功完成
    write_audit({
        "direction": "outbound", "to_sub": to_sub, "question": question[:200],
        "status": "ok",
        "request_id": request_id, "audit_id_remote": audit_id_remote,
        "duration_ms": duration_ms, "chunks": chunks_count,
    })


async def _process_sse_event(
    event_name: str,
    payload: dict[str, Any],
) -> str | tuple[int, str] | None:
    """处理一条 SSE event, 返:
      - str: chunk 内容 (yield 给上层)
      - tuple[int, str]: (error_code, message), 上层抛错
      - "_done": 流结束
      - None: 跳过
    """
    if event_name == "done":
        return "_done"
    if "error" in payload:
        err = payload["error"]
        return (err.get("code", 0), err.get("message", "unknown error"))
    if "result" in payload:
        result = payload["result"]
        if result.get("final"):
            return "_done"
        chunk = result.get("chunk", "")
        if chunk:
            return chunk
    return None


__all__ = ["ask_remote_agent"]
