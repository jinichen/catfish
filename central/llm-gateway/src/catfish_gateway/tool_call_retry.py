"""流式工具调用参数是半截 JSON 时, 网关自动重发一次。

# 现象 (10/30 鸿波实盘)

qwen3.8-flash (百炼) 调 delegate_task, 参数只吐出 `{"tasks": ` 流就结束了,
finish_reason=stop。hermes 拿到半截 JSON, 按「撞输出上限被截断」拒绝执行,
Companion 红框。员工点「继续」也一样 —— 当天下午一个会话连撞 25+ 次。

dashscope_tool_schema.py 把最大的诱因 (嵌套的自由对象字段) 去掉之后,
对照实验仍有约 40% 截断, 精简到只剩 goal 一个字段也一样 —— 是上游自己的
毛病, schema 侧治不完。重发一次的成功率约 60%, 两次就到 ~85%。

# 做法

1. 流里一出现 tool_calls, 从这个 chunk 起**先扣住不转发** (之前的思考 / 正文
   照常实时转发)。
2. 流结束后把扣住的参数拼起来逐个 json.loads:
   - 都能解析 → 原样放行, 跟没这层一样。
   - 有半截的 → 用同一个请求再向上游要一次, 最多 MAX_RETRIES 次。重发那一轮
     **只转发 tool_calls 和收尾 chunk**: 它的思考 / 正文第一轮已经转发过一遍,
     再发一遍客户端会拼出重复的话。
   - 重发也没拿到完整参数 → 放行原来扣住的那份, hermes 照旧报截断, 不会更糟。
3. 等重发期间按心跳间隔发 SSE 注释, 防客户端 idle 超时。

只判「参数解析不了」这一条, 不看 finish_reason —— 上游 finish_reason 写成 stop
但参数完整的情况 (日志里有) hermes 能正常执行, 不该动。

例外: finish_reason=length 不重发。那是真撞了 max_tokens (比如一大段代码),
重发还是撞同一个上限, 只会白等两轮、多花两倍的钱。

# 两个配套 (10/30 同批补, 本层对所有模型生效, 影响面大)

- 扣住期间客户端收不到字节: 模型写一大段 execute_code 可能一两分钟, chat_runtime
  在扣住时按心跳间隔发 SSE 注释, 防 hermes / Companion idle 断开。
- 重发也是真花钱: 每轮重发的 usage 累进 usage_out, chat_runtime 加到本请求的
  prompt / completion tokens 上, 配额和审计按实际消耗记。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

logger = logging.getLogger("catfish.gateway.tool_call_retry")

MAX_RETRIES = 2


def chunk_has_tool_calls(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    for choice in data.get("choices") or []:
        delta = (choice or {}).get("delta") or {}
        if delta.get("tool_calls"):
            return True
    return False


def broken_tool_calls(chunks: list[dict]) -> list[str]:
    """拼出每个 tool call 的参数, 返回解析不了的那些 (工具名:参数长度)。"""
    calls: dict[int, dict[str, str]] = {}
    for data in chunks:
        for choice in data.get("choices") or []:
            for tc in ((choice or {}).get("delta") or {}).get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                e = calls.setdefault(tc.get("index", 0), {"name": "", "args": ""})
                fn = tc.get("function") or {}
                e["name"] += fn.get("name") or ""
                e["args"] += fn.get("arguments") or ""
    bad = []
    for e in calls.values():
        try:
            json.loads(e["args"] or "{}")
        except ValueError:
            bad.append(f"{e['name']}:{len(e['args'])}")
    return bad


def _sse(data: Any) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _strip_text(data: dict) -> dict | None:
    """重发轮的 chunk 去掉思考 / 正文; 去完什么都不剩就返回 None (不转发)。"""
    choices = data.get("choices") or []
    if not choices:
        return data  # usage-only chunk
    kept = []
    for choice in choices:
        delta = dict((choice or {}).get("delta") or {})
        delta.pop("content", None)
        delta.pop("reasoning_content", None)
        if delta.get("tool_calls") or choice.get("finish_reason"):
            kept.append({**choice, "delta": delta})
    if not kept:
        return None
    return {**data, "choices": kept}


def finish_reason_of(chunks: list[dict]) -> str | None:
    reason = None
    for data in chunks:
        for choice in data.get("choices") or []:
            reason = (choice or {}).get("finish_reason") or reason
    return reason


def _add_usage(chunks: list[dict], usage_out: dict[str, int]) -> None:
    """一轮流里 usage 只在最后的 chunk 给一次 (include_usage), 取最后一份。"""
    last = None
    for data in chunks:
        if isinstance(data.get("usage"), dict):
            last = data["usage"]
    if last:
        for key in ("prompt_tokens", "completion_tokens"):
            usage_out[key] = usage_out.get(key, 0) + int(last.get(key) or 0)


def _needs_retry(held: list[dict], model_name: str, request_id: str) -> list[str]:
    """返回要重发的半截 tool call; finish_reason=length 是真撞上限, 不重发。"""
    bad = broken_tool_calls(held)
    if bad and finish_reason_of(held) == "length":
        logger.warning(
            "tool_call 参数是半截但 finish_reason=length (真撞 max_tokens), 不重发: "
            "model=%s req=%s broken=%s",
            model_name, request_id, bad,
        )
        return []
    return bad


def _as_dict(chunk: Any) -> Any:
    return chunk.model_dump() if hasattr(chunk, "model_dump") else chunk


async def _retry_once(
    restart: Callable[[], Awaitable[tuple[AsyncIterator[Any], Any]]],
    keepalive: Callable[[AsyncIterator[Any]], AsyncIterator[Any]],
    sink: list[dict],
) -> AsyncIterator[str]:
    """重发一次上游, chunk 收进 sink; 等待期间 yield 心跳行。"""
    iterator, first = await restart()
    if first is not None:
        sink.append(_as_dict(first))
    async for chunk in keepalive(iterator):
        if chunk == "__keepalive__":
            yield ": keepalive\n\n"
        else:
            sink.append(_as_dict(chunk))


async def flush_held(
    held: list[dict],
    restart: Callable[[], Awaitable[tuple[AsyncIterator[Any], Any]]],
    keepalive: Callable[[AsyncIterator[Any]], AsyncIterator[Any]],
    model_name: str,
    request_id: str,
    usage_out: dict[str, int] | None = None,
) -> AsyncIterator[str]:
    """放行扣住的 chunk; 参数是半截的就先重发上游。yield SSE 行。

    usage_out: 重发各轮的 token 用量累加到这里 (原那一轮不算, 调用方自己记过)。
    """
    usage_out = usage_out if usage_out is not None else {}
    bad = _needs_retry(held, model_name, request_id)
    for attempt in range(1, MAX_RETRIES + 1):
        if not bad:
            break
        logger.warning(
            "tool_call 参数是半截 JSON, 重发上游 (%d/%d): model=%s req=%s broken=%s",
            attempt, MAX_RETRIES, model_name, request_id, bad,
        )
        got: list[dict] = []
        try:
            async for line in _retry_once(restart, keepalive, got):
                yield line
        except Exception as e:  # noqa: BLE001
            logger.warning("tool_call 重发失败: model=%s req=%s err=%s", model_name, request_id, e)
            continue
        got = [c for c in got if isinstance(c, dict)]
        _add_usage(got, usage_out)
        tool_chunks = [c for c in got if chunk_has_tool_calls(c)]
        bad = broken_tool_calls(tool_chunks) if tool_chunks else ["(重发没调工具)"]
        if not bad:
            logger.info(
                "tool_call 重发拿到完整参数 (%d/%d): model=%s req=%s",
                attempt, MAX_RETRIES, model_name, request_id,
            )
            for data in got:
                stripped = _strip_text(data)
                if stripped is not None:
                    yield _sse(stripped)
            return
    if bad:
        logger.warning(
            "tool_call 重发 %d 次仍是半截, 放行原响应: model=%s req=%s",
            MAX_RETRIES, model_name, request_id,
        )
    for data in held:
        yield _sse(data)


class ToolCallHold:
    """chat_runtime 流循环里用: 从第一片 tool_calls 起扣住, 流末尾 flush() 验完再放行。

    扣住期间客户端一个字节都收不到 —— 大段 execute_code 能写一两分钟, 所以按
    ping_secs 照常发心跳。重发各轮的 token 累进 self.usage, 调用方加到本请求上。
    """

    def __init__(self, ping_secs: float) -> None:
        self.held: list[dict] | None = None
        self.ping_secs = ping_secs
        self.usage: dict[str, int] = {}
        self._ping_at = 0.0

    def offer(self, data: Any) -> list[str]:
        """收一个 chunk, 返回现在该发给客户端的 SSE 行 (可能为空)。"""
        if self.held is None and not chunk_has_tool_calls(data):
            return [_sse(data)]
        now = time.time()
        if self.held is None:
            self.held, self._ping_at = [], now
        if isinstance(data, dict):
            self.held.append(data)
        if now - self._ping_at >= self.ping_secs:
            self._ping_at = now
            return [": keepalive\n\n"]
        return []

    async def flush(
        self,
        restart: Callable[[], Awaitable[tuple[AsyncIterator[Any], Any]]],
        keepalive: Callable[[AsyncIterator[Any]], AsyncIterator[Any]],
        model_name: str,
        request_id: str,
    ) -> AsyncIterator[str]:
        if not self.held:
            return
        async for line in flush_held(
            self.held, restart, keepalive, model_name, request_id, self.usage,
        ):
            yield line
        if self.usage:
            logger.info(
                "tool_call 重发用量计入本请求: req=%s +prompt=%d +completion=%d",
                request_id,
                self.usage.get("prompt_tokens", 0),
                self.usage.get("completion_tokens", 0),
            )


__all__ = [
    "ToolCallHold",
    "MAX_RETRIES",
    "broken_tool_calls",
    "chunk_has_tool_calls",
    "finish_reason_of",
    "flush_held",
]
