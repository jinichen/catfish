"""Anthropic 格式的对话接口 `/v1/messages` (11/19)。

鸿波「目前是不是只支持 openai 兼容的 API, 不支持 anthropic 兼容?」—— 网关对外原来只有
OpenAI 格式的 /v1/chat/completions。Claude Code 这类只认 Anthropic 格式的工具连不上中央。

做法: **不另起一条流水线**。请求用 LiteLLM 自带的转换器翻成 OpenAI 格式, 交给
app.handle_chat —— 跟 /v1/chat/completions 走同一条 (认模型 / 权限 / 配额 / 审计 /
fallback / 工具循环预算), 回来的结果再翻回 Anthropic 格式。所以:
  - 模型名用中央的模型名 (或 catfish-auto); Claude Code 里设 ANTHROPIC_MODEL /
    ANTHROPIC_SMALL_FAST_MODEL。Anthropic 自己的模型名 (claude-…) 中央不认识, 报 404 并写明怎么设
  - 钥匙跟 OpenAI 那边同一把: Authorization: Bearer … 或 Anthropic 习惯的 x-api-key
  - 流式: OpenAI 的 SSE 一块块翻成 message_start / content_block_* / message_delta / message_stop;
    30 秒没动静时网关发的保活注释翻成 Anthropic 的 ping
  - 模型的思考过程 (reasoning_content) 不转出去: Anthropic 的 thinking 块要带签名, 我们给不出,
    客户端下一轮原样带回来反而会被拒。回答正文和工具调用照常

只翻格式, 不改语义。翻不了的 (比如思考过程) 宁可不发, 不发半截。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, AsyncIterator

from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .auth import User, get_current_user

logger = logging.getLogger("catfish.gateway")

_STOP_REASON = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use", "function_call": "tool_use"}
_ERROR_TYPE = {400: "invalid_request_error", 401: "authentication_error", 403: "permission_error",
               404: "not_found_error", 413: "request_too_large", 429: "rate_limit_error", 529: "overloaded_error"}
# 翻过来的参数里, 上游不一定认的 (Anthropic 的 thinking 被翻成 reasoning_effort, 国内模型多半报 400)
_DROP_AFTER_TRANSLATE = ("reasoning_effort", "thinking", "metadata")


async def anthropic_user(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> User:
    """Anthropic 客户端习惯用 x-api-key 放钥匙; 没有 Authorization 时把它当 Bearer 用, 校验逻辑不变。"""
    if not authorization and x_api_key:
        authorization = f"Bearer {x_api_key.strip()}"
    return await get_current_user(authorization=authorization)


def error_body(status: int, message: str) -> dict:
    kind = _ERROR_TYPE.get(status, "api_error" if status >= 500 else "invalid_request_error")
    return {"type": "error", "error": {"type": kind, "message": message}}


def to_openai_request(body: dict) -> tuple[dict, dict[str, str]]:
    """Anthropic 请求 → OpenAI 请求 (LiteLLM 转换器), 外加 {截短后的工具名: 原名}。"""
    from litellm.llms.anthropic.experimental_pass_through.adapters.transformation import (  # noqa: PLC0415
        LiteLLMAnthropicMessagesAdapter,
    )
    if not body.get("model"):
        raise HTTPException(status_code=400, detail="model parameter required")
    if not isinstance(body.get("messages"), list):
        raise HTTPException(status_code=400, detail="messages must be a list")
    req, tool_map = LiteLLMAnthropicMessagesAdapter().translate_anthropic_to_openai(body)  # type: ignore[arg-type]
    out = json.loads(json.dumps(req, default=lambda o: getattr(o, "model_dump", lambda: str(o))()))
    for k in _DROP_AFTER_TRANSLATE:
        out.pop(k, None)
    out["stream"] = bool(body.get("stream"))
    if out["stream"]:
        out["stream_options"] = {"include_usage": True}  # 要 usage 才能在 message_delta 里报用量
    return out, dict(tool_map or {})


def _tool_input(arguments: Any) -> dict:
    if isinstance(arguments, dict):
        return arguments
    try:
        v = json.loads(arguments or "{}")
        return v if isinstance(v, dict) else {"value": v}
    except (TypeError, ValueError):
        return {"_raw": arguments}  # 模型给了不合法的 JSON: 原样交出去, 不吞


def to_anthropic_response(resp: dict, model: str, tool_map: dict[str, str]) -> dict:
    """OpenAI 非流式结果 → Anthropic message。"""
    choice = (resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content: list[dict] = []
    if msg.get("content"):
        content.append({"type": "text", "text": msg["content"]})
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        name = fn.get("name") or ""
        content.append({"type": "tool_use", "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:24]}",
                        "name": tool_map.get(name, name), "input": _tool_input(fn.get("arguments"))})
    usage = resp.get("usage") or {}
    return {
        "id": f"msg_{(resp.get('id') or uuid.uuid4().hex)[-24:]}",
        "type": "message", "role": "assistant", "model": model, "content": content,
        "stop_reason": _STOP_REASON.get(choice.get("finish_reason") or "stop", "end_turn"),
        "stop_sequence": None,
        "usage": {"input_tokens": int(usage.get("prompt_tokens") or 0),
                  "output_tokens": int(usage.get("completion_tokens") or 0)},
    }


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class StreamTranslator:
    """OpenAI 流式块 → Anthropic SSE 事件。一次请求一个实例。"""

    def __init__(self, model: str, tool_map: dict[str, str]):
        self.model, self.tool_map = model, tool_map
        self.block = -1          # 当前打开的块序号
        self.open: str | None = None  # "text" / "tool:<openai index>"
        self.stop_reason = "end_turn"
        self.input_tokens = self.output_tokens = 0
        self.finished = False

    def start(self) -> str:
        return _sse("message_start", {"type": "message_start", "message": {
            "id": f"msg_{uuid.uuid4().hex[:24]}", "type": "message", "role": "assistant", "model": self.model,
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}}})

    def _close(self) -> list[str]:
        if self.open is None:
            return []
        self.open = None
        return [_sse("content_block_stop", {"type": "content_block_stop", "index": self.block})]

    def _open(self, kind: str, block: dict) -> list[str]:
        out = self._close()
        self.block += 1
        self.open = kind
        out.append(_sse("content_block_start", {"type": "content_block_start", "index": self.block, "content_block": block}))
        return out

    def chunk(self, data: dict) -> list[str]:
        if "error" in data and not data.get("choices"):
            err = data["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            self.finished = True
            return self._close() + [_sse("error", error_body(500, msg or "upstream error"))]
        out: list[str] = []
        usage = data.get("usage") or {}
        self.input_tokens = int(usage.get("prompt_tokens") or self.input_tokens)
        self.output_tokens = int(usage.get("completion_tokens") or self.output_tokens)
        for choice in data.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("content"):
                if self.open != "text":
                    out += self._open("text", {"type": "text", "text": ""})
                out.append(_sse("content_block_delta", {"type": "content_block_delta", "index": self.block,
                                                        "delta": {"type": "text_delta", "text": delta["content"]}}))
            for tc in delta.get("tool_calls") or []:
                key = f"tool:{tc.get('index', 0)}"
                fn = tc.get("function") or {}
                if self.open != key:
                    name = fn.get("name") or ""
                    out += self._open(key, {"type": "tool_use", "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:24]}",
                                            "name": self.tool_map.get(name, name), "input": {}})
                if fn.get("arguments"):
                    out.append(_sse("content_block_delta", {"type": "content_block_delta", "index": self.block,
                                                            "delta": {"type": "input_json_delta", "partial_json": fn["arguments"]}}))
            if choice.get("finish_reason"):
                self.stop_reason = _STOP_REASON.get(choice["finish_reason"], "end_turn")
        return out

    def end(self) -> list[str]:
        if self.finished:
            return []
        self.finished = True
        return self._close() + [
            _sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": self.stop_reason, "stop_sequence": None},
                                   "usage": {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}}),
            _sse("message_stop", {"type": "message_stop"}),
        ]


async def translate_stream(lines: AsyncIterator[Any], tr: StreamTranslator) -> AsyncIterator[str]:
    """网关吐出的 OpenAI SSE 文本 (可能一次几条) → Anthropic SSE。"""
    yield tr.start()
    buf = ""
    async for piece in lines:
        buf += piece.decode() if isinstance(piece, bytes) else str(piece)
        while "\n\n" in buf:
            frame, buf = buf.split("\n\n", 1)
            frame = frame.strip()
            if not frame:
                continue
            if frame.startswith(":"):  # 网关的保活注释
                yield _sse("ping", {"type": "ping"})
                continue
            payload = "\n".join(ln[5:].strip() for ln in frame.splitlines() if ln.startswith("data:"))
            if not payload:
                continue
            if payload == "[DONE]":
                for ev in tr.end():
                    yield ev
                continue
            try:
                data = json.loads(payload)
            except ValueError:
                logger.warning("anthropic stream: 跳过解析不了的一块 (长度 %d)", len(payload))  # 不打内容: 中央日志不留员工正文
                continue
            for ev in tr.chunk(data):
                yield ev
    for ev in tr.end():  # 上游没发 [DONE] 也要收尾, 客户端才知道结束了
        yield ev


async def _anthropic_reply(request: Request, user: User, body: dict):
    from . import app as app_mod  # noqa: PLC0415 — app 在导入期注册本模块, 这里用时再取
    model = body.get("model") or ""
    try:
        oa_body, tool_map = to_openai_request(body)
        result = await app_mod.handle_chat(request, user, oa_body)
    except HTTPException as e:
        detail = str(e.detail)
        if e.status_code == 404 and "model not found" in detail:
            detail += " —— 用中央的模型名 (或 catfish-auto)。Claude Code: 设 ANTHROPIC_MODEL 和 ANTHROPIC_SMALL_FAST_MODEL"
        return JSONResponse(error_body(e.status_code, detail), status_code=e.status_code)
    if isinstance(result, StreamingResponse):
        tr = StreamTranslator(model, tool_map)
        return StreamingResponse(translate_stream(result.body_iterator, tr), media_type="text/event-stream")
    if isinstance(result, JSONResponse):
        data = json.loads(bytes(result.body).decode())
        if result.status_code >= 400:
            msg = (data.get("error") or {}).get("message") if isinstance(data.get("error"), dict) else data.get("detail")
            return JSONResponse(error_body(result.status_code, str(msg or data)), status_code=result.status_code)
    else:
        data = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    return JSONResponse(to_anthropic_response(data, model, tool_map))


def register_anthropic_routes(app) -> None:
    @app.post("/v1/messages")
    async def anthropic_messages(request: Request, user: User = Depends(anthropic_user)):
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse(error_body(400, "请求体不是 JSON"), status_code=400)
        return await _anthropic_reply(request, user, body)

    @app.post("/v1/messages/count_tokens")
    async def anthropic_count_tokens(request: Request, user: User = Depends(anthropic_user)):
        """Claude Code 会先问一句「这段有多少 token」。按网关同一把估算器算, 不调上游、不扣配额。"""
        from . import app as app_mod  # noqa: PLC0415 — 同 _anthropic_reply
        from .fallback import estimate_prompt_tokens_raw  # noqa: PLC0415
        try:
            body = await request.json()
            oa_body, _ = to_openai_request({**body, "max_tokens": body.get("max_tokens") or 1})
        except HTTPException as e:
            return JSONResponse(error_body(e.status_code, str(e.detail)), status_code=e.status_code)
        except ValueError:
            return JSONResponse(error_body(400, "请求体不是 JSON"), status_code=400)
        m = app_mod.get_config().get_model(app_mod._resolve_auto_sentinel(body.get("model") or ""))
        n = estimate_prompt_tokens_raw(oa_body.get("messages") or [], model=m.upstream.model if m else "gpt-4o",
                                       tools=oa_body.get("tools"))
        return {"input_tokens": int(n)}
