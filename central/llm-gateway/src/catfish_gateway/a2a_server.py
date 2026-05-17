"""Plan D · A2A Server — B 端 /a2a/ask SSE endpoint (五一 sprint Day 4 BL-M4.3).

# 数据流 (PLAN-D-PROTOCOL.md § 2)

A 调 B → POST /a2a/ask + Bearer JWT
  ↓
B verify_a2a_token(token, expected_aud=B 自己 sub)
  ↓
B 解析 question + purpose, 调 check_allow(question, ALLOW.md, from_sub, purpose)
  ↓
allowed: 转 LLM 流式生成回答, SSE chunk 推给 A
denied: SSE 单条 error event (-32001)
  ↓
audit jsonl 写一行 (inbound, status, allow_match, duration_ms)

# 安全边界

- B 自己决定能不能答 (ALLOW.md 是员工自己写的)
- B 答的内容**不写 audit log** (跟现 gateway audit 隐私边界一致, 只记 metadata)
- B 限流: 同一 from_sub 1 分钟内 ≤ 10 次 (防 spam)
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .a2a_allow import check_allow, load_allow_config
from .a2a_audit import write_audit
from .a2a_jwt import verify_a2a_token

logger = logging.getLogger("catfish.gateway.a2a_server")


# ── 限流 (per from_sub, 1 分钟 10 次) ─────────────────────────────


_RATE_LIMITS: dict[str, deque[float]] = defaultdict(deque)
_RATE_WINDOW_SEC = 60.0
_RATE_MAX_REQUESTS = 10


def _rate_limit_check(from_sub: str) -> bool:
    """返 True 通过, False 触发限流."""
    now = time.time()
    bucket = _RATE_LIMITS[from_sub]
    while bucket and bucket[0] < now - _RATE_WINDOW_SEC:
        bucket.popleft()
    if len(bucket) >= _RATE_MAX_REQUESTS:
        return False
    bucket.append(now)
    return True


# ── Request schema ──────────────────────────────────────────────


class A2AAskParams(BaseModel):
    from_sub: str
    from_endpoint: str = ""
    to_sub: str
    question: str
    context_hint: str = ""
    purpose: str = ""
    max_tokens: int = 500


class A2AAskRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: str
    method: str
    params: A2AAskParams


# ── SSE event helpers ──────────────────────────────────────────


def _sse_event(event_name: str, data: dict[str, Any]) -> bytes:
    """格式化一条 SSE event."""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event_name}\ndata: {payload}\n\n".encode()


def _jsonrpc_error(request_id: str, code: int, message: str, details: str = "") -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": code,
            "message": message,
            "details": details,
        },
    }


def _jsonrpc_chunk(request_id: str, chunk_text: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"chunk": chunk_text},
    }


def _jsonrpc_done(request_id: str, audit_id: str, total_chunks: int) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "final": True,
            "audit_id": audit_id,
            "total_chunks": total_chunks,
        },
    }


# ── 主 endpoint ─────────────────────────────────────────────────


def build_a2a_router() -> APIRouter:
    router = APIRouter(prefix="/a2a", tags=["plan-d-a2a"])

    @router.post("/ask")
    async def ask(request: Request) -> StreamingResponse:
        """A → B 的 SSE 流式问答.

        步骤:
        1. 验 JWT (Authorization: Bearer ...)
        2. 解 body
        3. 限流
        4. ALLOW.md 检查
        5. 调 LLM 流式生成
        6. SSE 推给 A
        """
        started_at = time.time()
        audit_id = str(uuid.uuid4())[:12]
        my_sub = os.environ.get("CATFISH_USER_SUB", "")
        if not my_sub:
            # 单机 mock 没设 env, 从 Authorization 反推 (生产应该来自 SSO)
            my_sub = "unknown@local"

        # 解 body 先, audit 失败也能记
        try:
            body = await request.json()
            req = A2AAskRequest(**body)
        except Exception as e:
            return _error_stream("", -32600, f"invalid request: {e}", audit_id)

        request_id = req.id
        params = req.params

        # 验 JWT
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            write_audit({
                "direction": "inbound",
                "from_sub": params.from_sub,
                "question": params.question[:200],
                "status": "denied",
                "error_msg": "missing JWT",
                "audit_id": audit_id,
            })
            return _error_stream(request_id, -32002, "missing JWT", audit_id)
        token = auth[7:]

        try:
            jwt_payload = await verify_a2a_token(token, expected_aud=my_sub)
        except PermissionError as e:
            write_audit({
                "direction": "inbound",
                "from_sub": params.from_sub,
                "question": params.question[:200],
                "status": "denied",
                "error_msg": f"JWT verify failed: {e}",
                "audit_id": audit_id,
            })
            return _error_stream(request_id, -32002, f"JWT verify failed: {e}", audit_id)

        jti = jwt_payload.get("jti", "")
        from_sub_jwt = jwt_payload.get("iss", "")
        if from_sub_jwt != params.from_sub:
            return _error_stream(
                request_id, -32002,
                f"JWT iss ({from_sub_jwt}) != params.from_sub ({params.from_sub})",
                audit_id,
            )

        # 限流
        if not _rate_limit_check(params.from_sub):
            write_audit({
                "direction": "inbound",
                "from_sub": params.from_sub,
                "question": params.question[:200],
                "status": "denied",
                "error_msg": "rate limit exceeded",
                "jti": jti,
                "audit_id": audit_id,
            })
            return _error_stream(request_id, -32003, "rate limit exceeded (10/min)", audit_id)

        # ALLOW.md 检查
        allow_config = load_allow_config()
        # from_attrs 简化: 单机 mock 不查中央获取真实 attrs, 留 Phase 2 加
        # 生产 from_attrs 来自 catfish-identity 的 user 信息 (department / role 等)
        from_attrs: dict[str, str] = {}
        # 单机 mock: env CATFISH_FROM_ATTRS_<sub> 模拟
        env_key = f"CATFISH_FROM_ATTRS_{params.from_sub.replace('@', '_').replace('.', '_')}"
        if env_key in os.environ:
            for pair in os.environ[env_key].split(","):
                if "=" in pair:
                    k, _, v = pair.partition("=")
                    from_attrs[k.strip()] = v.strip()

        allowed, reason = check_allow(
            params.question, allow_config,
            from_sub=params.from_sub,
            from_attrs=from_attrs,
            purpose=params.purpose,
        )
        if not allowed:
            write_audit({
                "direction": "inbound",
                "from_sub": params.from_sub,
                "question": params.question[:200],
                "status": "denied",
                "error_msg": reason,
                "jti": jti,
                "audit_id": audit_id,
                "duration_ms": int((time.time() - started_at) * 1000),
            })
            return _error_stream(
                request_id, -32001,
                "target user denied this question type",
                audit_id, details=reason,
            )

        # LLM 流式生成
        return StreamingResponse(
            _stream_llm_answer(req, audit_id, jti, started_at, allow_match=reason),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Catfish-A2A-Audit-Id": audit_id,
            },
        )

    return router


def _error_stream(
    request_id: str,
    code: int,
    message: str,
    audit_id: str,
    details: str = "",
) -> StreamingResponse:
    """单条 error event 的 SSE 流."""
    err_payload = _jsonrpc_error(request_id, code, message, details)

    async def gen() -> AsyncIterator[bytes]:
        yield _sse_event("message", err_payload)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"X-Catfish-A2A-Audit-Id": audit_id},
    )


async def _stream_llm_answer(
    req: A2AAskRequest,
    audit_id: str,
    jti: str,
    started_at: float,
    allow_match: str,
) -> AsyncIterator[bytes]:
    """转 LLM 流式生成回答, 把每个 chunk 包成 SSE 推给 A.

    单机 mock 简化: 用 catfish gateway 自己的 LLM 路由 (跟普通 chat 一样).
    """
    params = req.params
    chunks_count = 0
    # BL-FED2.4 (5/12) 累积答案前 N 字符做 journal 摘要 (完整 answer 不留)
    answer_buffer = ""
    _ANSWER_BUFFER_MAX = 200

    # 构建 prompt
    system_prompt = (
        f"你是 {os.environ.get('CATFISH_USER_SUB', 'B 鲶鱼')} 的鲶鱼副手. "
        f"另一个员工 {params.from_sub} 的鲶鱼通过 Plan D Federation 协议向你提问. "
        f"目的: {params.purpose or '普通咨询'}. "
        f"上下文: {params.context_hint or '无'}\n"
        "回答要点:\n"
        "1. 简短客观, 不超过 200 字\n"
        "2. 只回答你确实知道的, 不知道就直说\n"
        "3. 不要替员工本人 (B) 拍板任何敏感决策\n"
        "4. 不要泄露任何 ALLOW.md 没明确允许的内容\n"
    )
    user_prompt = params.question

    try:
        # 简化: 直接调 LiteLLM (跟 gateway 内 chat 同样路由).
        # Phase 2 应该走 gateway 内部 LLM client 而不是新建 connection.
        import litellm  # noqa: PLC0415

        api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
        if not api_key:
            # 没 API key, 给 mock 回答 (单机 mock 测试用)
            mock_answer = (
                f"[mock 回答 — 没 DASHSCOPE_API_KEY, 真生产会调 LLM] "
                f"{params.from_sub} 问 '{params.question}'. "
                f"按 ALLOW.md 匹配 ({allow_match}) 我可以答, 但当前没 LLM 配置."
            )
            for chunk in _split_into_chunks(mock_answer):
                chunks_count += 1
                if len(answer_buffer) < _ANSWER_BUFFER_MAX:
                    answer_buffer += chunk
                yield _sse_event("message", _jsonrpc_chunk(req.id, chunk))
            yield _sse_event("done", _jsonrpc_done(req.id, audit_id, chunks_count))
        else:
            # 真 LLM streaming.
            # BL-F14: 不写死模型, 用 pick_internal_model("a2a_aux") 按 tag 选 (private 优先).
            # a2a 走特殊 SSE 链路, 不走 gateway loopback (跟 summarizer/proactive 不同),
            # 直接用 LiteLLM, 但 model + api_base + api_key 都从 catalog 来.
            #
            # BL-INTERNAL-MODEL-FOLLOW-USER (5/17): a2a 是跨员工 federation,
            # 接收方接到 from_sub 的请求, 不绑接收方的 session model. 长期:
            # 让 from_sub (发起方) 透传自己 session 的 model 到 a2a header,
            # 接收方用同款回答 — Day 8 设计. 短期保留 pick_internal_model.
            from .config import load_config  # noqa: PLC0415
            from .internal_models import pick_internal_model  # noqa: PLC0415
            config = load_config()
            chosen_model = pick_internal_model("a2a_aux", config)
            if chosen_model is None:
                # 退化到 mock 答 (catalog 没可用模型)
                mock_answer = (
                    f"[mock 回答 — catalog 没可用 chat 模型, A2A LLM 调用跳过] "
                    f"{params.from_sub} 问 '{params.question}'."
                )
                for chunk in _split_into_chunks(mock_answer):
                    chunks_count += 1
                    if len(answer_buffer) < _ANSWER_BUFFER_MAX:
                        answer_buffer += chunk
                    yield _sse_event("message", _jsonrpc_chunk(req.id, chunk))
                yield _sse_event("done", _jsonrpc_done(req.id, audit_id, chunks_count))
                # BL-FED2.4: 这条早 return 路径也要写 audit + journal hook (跟正常路径同)
                _duration_ms = int((time.time() - started_at) * 1000)
                write_audit({
                    "direction": "inbound",
                    "from_sub": params.from_sub,
                    "question": params.question[:200],
                    "status": "ok",
                    "allow_match": allow_match,
                    "jti": jti,
                    "audit_id": audit_id,
                    "duration_ms": _duration_ms,
                    "total_chunks": chunks_count,
                })
                _try_append_a2a_journal(
                    from_sub=params.from_sub,
                    question=params.question,
                    purpose=getattr(params, "purpose", "") or "",
                    answer_preview=answer_buffer,
                    chunks_count=chunks_count,
                    duration_ms=_duration_ms,
                )
                return

            response = await litellm.acompletion(
                model=chosen_model.upstream.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                api_base=chosen_model.upstream.api_base,
                api_key=chosen_model.upstream.api_key,
                temperature=0.3,
                max_tokens=params.max_tokens,
                stream=True,
                timeout=chosen_model.upstream.timeout,
            )
            async for delta in response:
                content = ""
                try:
                    content = delta.choices[0].delta.content or ""
                except Exception:
                    content = ""
                if content:
                    chunks_count += 1
                    if len(answer_buffer) < _ANSWER_BUFFER_MAX:
                        answer_buffer += content
                    yield _sse_event("message", _jsonrpc_chunk(req.id, content))
            yield _sse_event("done", _jsonrpc_done(req.id, audit_id, chunks_count))

        # audit 成功
        _duration_ms = int((time.time() - started_at) * 1000)
        write_audit({
            "direction": "inbound",
            "from_sub": params.from_sub,
            "question": params.question[:200],
            "status": "ok",
            "allow_match": allow_match,
            "jti": jti,
            "audit_id": audit_id,
            "duration_ms": _duration_ms,
            "total_chunks": chunks_count,
        })

        # BL-FED2.4 (5/12) — A2A 反馈环: B 自己 journal 加一条 [a2a-help] 记录
        # 失败静默, 不影响 a2a 主流程
        _try_append_a2a_journal(
            from_sub=params.from_sub,
            question=params.question,
            purpose=getattr(params, "purpose", "") or "",
            answer_preview=answer_buffer,
            chunks_count=chunks_count,
            duration_ms=_duration_ms,
        )

    except Exception as e:
        logger.warning("a2a LLM 推理失败: %s", e)
        yield _sse_event(
            "message",
            _jsonrpc_error(req.id, -32005, f"LLM error: {e}", ""),
        )
        write_audit({
            "direction": "inbound",
            "from_sub": params.from_sub,
            "question": params.question[:200],
            "status": "error",
            "error_msg": str(e)[:200],
            "jti": jti,
            "audit_id": audit_id,
            "duration_ms": int((time.time() - started_at) * 1000),
        })


def _split_into_chunks(text: str, chunk_size: int = 30) -> list[str]:
    """把 mock 文本分段, 模拟流式 chunk."""
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


def _try_append_a2a_journal(
    *,
    from_sub: str,
    question: str,
    purpose: str,
    answer_preview: str,
    chunks_count: int,
    duration_ms: int,
) -> None:
    """BL-FED2.4 反馈环 — wrapper, 完全 swallow 异常 (a2a 主流程不能因 journal 失败崩).

    journal hook 真实现在 a2a_journal_hook.py.
    """
    try:
        from .a2a_journal_hook import append_a2a_help_entry  # noqa: PLC0415
        append_a2a_help_entry(
            from_sub=from_sub,
            question=question,
            purpose=purpose,
            answer_preview=answer_preview,
            chunks_count=chunks_count,
            duration_ms=duration_ms,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("BL-FED2.4 journal hook 异常 (静默): %s", e)


__all__ = ["build_a2a_router"]
