"""Chat completion execution runtime extracted from the FastAPI assembly module.

The public compatibility exports remain in ``catfish_gateway.app``. This module
contains streaming and non-streaming execution paths only; route assembly and
process lifecycle stay in ``app.py``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import litellm
from fastapi import HTTPException, Request

from . import quota as _quota_module
from .config import Config
from .errors import (
    friendly_upstream_error as _friendly_upstream_error,
    is_quota_or_rate_limit as _is_quota_or_rate_limit,
    localize_reset_hint as _localize_reset_hint,
)
from .fallback import with_fallback
from .llm_params import _extract_nested_usage, _raise_upstream_error

logger = logging.getLogger("catfish.gateway")

#: keepalive 心跳间隔。定义在这里而不是 app.py —— app.py 里那份**没有任何人用**,
#: 只有本模块用。它从 app.py 反向 import 回去, 保持 `app._KEEPALIVE_INTERVAL_SECS`
#: 这个名字对外还在 (万一有人 monkeypatch 它)。
#:
#: ⚠ 必须是模块级常量, 不能改成惰性取值 —— `_stream_with_keepalive` 拿它当
#:   **默认参数**, 默认参数在 import 时就求值了。
_KEEPALIVE_INTERVAL_SECS = 30


def _app_mod() -> Any:
    """惰性拿 app 模块。**不要**改回模块级 `from . import app`。

    # 8/18 实撞

    app.py 第 410 行 `from .chat_runtime import ...`, 本模块又 `from . import app`
    —— 一个环。平时不炸是因为 uvicorn 走 `catfish_gateway.app:app`, 那时
    `catfish_gateway.app` 已经在 sys.modules 里 (虽然只初始化了一半, 但要读的
    几个属性正好都在第 410 行之前定义好了) —— 靠**行的先后顺序**成立, 很脆。

    `python -m catfish_gateway.app` 就不成立了: runpy 把 app.py 当 `__main__` 跑,
    `catfish_gateway.app` **不在** sys.modules, 于是这行触发 app.py 的第二次完整
    执行 (现象是路由被挂载两遍), 第二遍走到第 410 行时本模块才执行到第 18 行,
    要的名字一个都还不存在:

        ImportError: cannot import name '_invoke_chat_completion' from
        partially initialized module 'catfish_gateway.chat_runtime'
        (most likely due to a circular import)

    改成惰性: 函数体里才 import, 那时两个模块都已经初始化完, 环就不存在了。
    """
    from . import app as _app  # noqa: PLC0415  故意放在函数里, 见 docstring
    return _app


def _pick_cache_read_from_streaming_usage(usage: dict, current: int) -> int:
    return _app_mod()._pick_cache_read_from_streaming_usage(usage, current)


def get_config() -> Config:
    """Resolve config through app.py so existing monkeypatches keep working."""
    return _app_mod().get_config()


def _build_litellm_params(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Resolve request parameter construction through the compatibility module."""
    return _app_mod()._build_litellm_params(*args, **kwargs)


def _check_context_usage(*args: Any, **kwargs: Any) -> Any:
    """Resolve the context metric through app.py for legacy monkeypatch paths."""
    return _app_mod()._check_context_usage(*args, **kwargs)


def _extract_content_text(response_dict: dict) -> str:
    return _app_mod()._extract_content_text(response_dict)


def _looks_like_upstream_error_as_content(response_dict: dict) -> bool:
    return _app_mod()._looks_like_upstream_error_as_content(response_dict)


def _write_metadata(**kwargs: Any) -> Any:
    """Call metrics lazily so tests and deployments can patch that module."""
    from .metrics import log_request_metadata
    return log_request_metadata(**kwargs)


async def _stream_with_keepalive(iterator, interval_secs: float = _KEEPALIVE_INTERVAL_SECS):
    """Wrap async iterator: chunk 间隔 > interval_secs 时 yield keepalive marker.

    Yields:
        - 原 chunk 对象 (上游来的 ChatCompletionChunk)
        - 字符串 "__keepalive__" (上游慢, 该发心跳了; caller 自己翻译成 SSE comment)

    用 asyncio.wait_for 给每次 __anext__ 加超时, 不影响最终拿到的总数据.
    """
    while True:
        try:
            chunk = await asyncio.wait_for(
                iterator.__anext__(), timeout=interval_secs
            )
            yield chunk
        except TimeoutError:
            yield "__keepalive__"
        except StopAsyncIteration:
            return

async def _stream_chat_completion(
    body: dict,
    *,
    user_sub: str,
    user_dept: str,  # 五一 sprint 5/2 RBAC: quota.record_usage 需要部门
    model_name: str,
    model,
    security_concern: str | None = None,
    is_internal: bool = False,  # BL-F17 (5/5): internal 调用跳 record_usage
    source_hint: str = "unknown",  # BL-RBAC-DAY4-HARDENING (5/17): X-Catfish-Source audit
    # BL-ABORT-PROPAGATE (7/23 达华 POC): 传 request 进来 · 循环里定期 check
    # request.is_disconnected() · 客户 abort 时主动 aclose upstream iterator ·
    # 省 token / 释 vLLM slot. 老行为 · client abort 后 gateway 仍继续烧到本轮
    # finish · 只在下次 SSE write 时 broken pipe 才感知. None = 兼容老 caller.
    request: Request | None = None,
    # BL-LEAN-SESSION teaching_mode 参数 已 DELETED (5/13 鸿波"全部清干净"):
    # 之前给 BL-FIX23 retry 的 _lean gate 用. retry 删了它就 dead arg.
    # _lean 控制 SOUL inject pipeline 那部分仍在 chat_completions 里 (1651), 不影响.
) -> AsyncIterator[str]:
    """SSE async generator for streaming chat completions, with fallback chain.

    Fallback 时机:
        在 "acompletion + 首 chunk" 阶段失败 → 切下一个模型重试
        已开始流之后挂掉 → 没法切, 直接转 SSE error 返回 (中途换模型会乱掉客户端解析)

    BL-FIX23 L5 (5/9): plan-only retry — finish_reason=stop + 没 tool_call +
    plan-only content + 上一条 user 是反馈 → 内部起新 acompletion 强制重发,
    最多 2 次. 客户端无感, 看着像鲶鱼自己续写.
    """
    start = time.time()
    ttft_ms: float | None = None  # 首 token / 首 chunk 延迟, fallback 后会被覆盖成实际值
    prompt_tokens = 0
    completion_tokens = 0
    # BL-CACHE-AUDIT (5/17): streaming Anthropic prompt cache 字段, 末尾 chunk
    # 的 usage 里抓. 不存在或非 Anthropic 时永远 0.
    _stream_cache_creation = 0
    _stream_cache_read = 0
    status_str = "ok"
    err = ""
    used_model = model
    config: Config = get_config()
    attempts_log: list[str] = []

    # BL-ABORT-PROPAGATE (7/23 达华 POC): pre-declare · 若 fallback 阶段 raise ·
    # finally 里 iterator 未 assign 会 UnboundLocalError. None 兜底.
    iterator: Any = None

    # BL-HERMES013-4 (5/12): in-flight tracking — 流开始 mark, 结束 chain
    # 跑 InflightCleanupTransform unlink. gateway 真崩 (SIGKILL) 文件留下,
    # 启动时 reap_interrupted 写一条 'interrupted_resumed' audit 替补.
    import uuid as _uuid  # noqa: PLC0415

    from . import inflight_streams  # noqa: PLC0415
    request_id = _uuid.uuid4().hex
    inflight_streams.mark_started(
        request_id,
        user=user_sub,
        model=model_name,
        message_count=len(body.get("messages") or []),
        extra={"is_internal": is_internal},
    )

    try:
        # 用 fallback 链找一个能拿到首 chunk 的模型
        async def _start_stream(candidate_model):
            params = _build_litellm_params(body, candidate_model)
            try:
                response = await litellm.acompletion(**params)
            except Exception as _e:
                # 8/10: 上游返"什么都没说的 400"时把请求**形状**存一份 (不含正文)。
                # 内网 Qwen3-VL 的错误是
                #   error: code = 400 reason =  message =  metadata = map[] cause = <nil>
                # 两轮八个探针全没复现, 继续猜变量的成本已经高过抓真身。
                # 有话说的错误 (余额不足 / 超上下文) 不会触发, 见 request_shape_dump。
                from .request_shape_dump import dump_on_opaque_error  # noqa: PLC0415

                dump_on_opaque_error(params, candidate_model.name, _e)
                raise
            iterator = response.__aiter__()
            # 拉首 chunk —— 这是 429 / 503 最容易抛错的地方
            try:
                first = await iterator.__anext__()
            except StopAsyncIteration:
                first = None
            return iterator, first

        # BL-FALLBACK-PROMPT-CAP (5/14): 算 prompt 估算传给 with_fallback, 大 prompt
        # 失败时跳过公网 candidate (公网更慢更贵, 不该兜底).
        # BL-TOKEN-COUNTER-LITELLM (5/15): 传 model 名让 estimate 用真 tokenizer.
        # BL-TOOLS-IN-ESTIMATE (5/15 22:00): 加 tools schema 估算, 上游也算 tools.
        from .fallback import estimate_prompt_tokens  # noqa: PLC0415
        prompt_estimate = estimate_prompt_tokens(
            body.get("messages") or [],
            model=model.upstream.model,
            tools=body.get("tools"),
        )
        # 6/7 BL-GROQ-TPM-PREFLIGHT: 检查上游 rate_limits.tpm, 超就 raise 413 +
        # 友好中文 + 替代 model. 拦下来不让员工看 stack trace.
        from .rate_limit_preflight import preflight_check_rate_limits  # noqa: PLC0415
        preflight_check_rate_limits(model, prompt_estimate, config=config)

        # 8/10: 压完还是装不下, 就别发了 —— 发出去只会换回一个 reason/message
        # 全空的 400, 员工看到「未知错误」, 谁都查不出原因 (今天为这个 400 做了
        # 两轮八个探针才靠 shape dump 找到)。dyn ≤ 0 已经说明装不下, 见
        # context_preflight。**位置必须在压缩之后** —— 压缩能省 89%, 压之前拦
        # 会把本来救得回来的对话也拒掉。
        from .context_preflight import check_context_fits  # noqa: PLC0415

        _too_long = check_context_fits(prompt_estimate, model)
        if _too_long:
            raise HTTPException(status_code=413, detail=_too_long)
        # ── 8/15: 等首 chunk 期间每 30s 出一次声 ──────────────────────
        #
        # 下面那句 await 会一直阻塞到上游吐出第一个 chunk。这段时间里**什么日志
        # 都没有**, 而它可能很长: 8/15 现场一条 deepseek 流卡在这里, 日志上表现
        # 为"这条请求没有收尾行", 要靠人比对前后才能看出来。
        #
        # 下面 2580 行那条 TTFT 告警帮不上忙 —— 它在首 chunk **到了之后**才执行,
        # 永远不来就永远不打。
        #
        # 心跳 (_stream_with_keepalive) 也帮不上 —— 它包的是 iterator, 而 iterator
        # 正是这句 await 的产物。中途卡住有保护, 开头卡住没有。
        #
        # 这里只加日志, 不动超时: 真正的超时是 model.upstream.timeout 传给
        # litellm 的那个, 改它是另一件事 (会影响所有慢模型)。
        async def _warn_while_waiting() -> None:
            waited = 0.0
            while True:
                await asyncio.sleep(_KEEPALIVE_INTERVAL_SECS)
                waited += _KEEPALIVE_INTERVAL_SECS
                logger.warning(
                    "等上游首 chunk 已 %.0fs: model=%s request_id=%s user=%s "
                    "(上游 timeout=%ss). 这段时间客户端收不到任何字节 —— "
                    "SSE 心跳要等首 chunk 之后才开始。",
                    waited, model.name, request_id, user_sub,
                    getattr(model.upstream, "timeout", "?"),
                )

        _first_chunk_watch = asyncio.create_task(_warn_while_waiting())
        try:
            # attempts_out=attempts_log: 让 with_fallback 原地填, 这样**抛异常时**
            # 下面 except 里也拿得到试过谁 (返回值那条路在 raise 时走不到)。
            (iterator, first_chunk), used_model, attempts_log = await with_fallback(
                config, model, _start_stream, prompt_estimate=prompt_estimate,
                attempts_out=attempts_log,
            )
        finally:
            _first_chunk_watch.cancel()
        # 首 chunk 拿到 = 上游开始往外吐数据. 这就是 TTFT (time-to-first-token).
        # 注: 如果走了 fallback, 这里记的是"最终成功那个模型的 TTFT", 不算前面失败模型的等待.
        ttft_ms = (time.time() - start) * 1000
        if ttft_ms > 30_000:
            logger.warning(
                "TTFT 异常: model=%s ttft=%.0fms (>30s, 上游可能拥堵, 看是否需要切 flash)",
                used_model.name, ttft_ms,
            )

        # 重新对齐 model_name 到实际用的 (给 metrics + 客户端 [DONE] 之前的元信息)
        if used_model is not model:
            logger.info(
                "stream served by fallback: requested=%s used=%s",
                model.name, used_model.name,
            )

        # ── 干净转发 stream (5/13 鸿波"乱七八糟"反馈, 删掉 BL-FIX23 retry loop) ──
        # 单轮 acompletion 跑完 → 转发所有 chunk → [DONE] 收尾. 不再判 plan-only,
        # 不再 retry 灌 hint, 不再物理强迫 tool_choice. LLM stop 就 stop, 客户端
        # 自己跟它说"继续". gateway 干净转发, 不猜 LLM 心思.
        chunk_stats = {"total": 0, "content": 0, "reasoning": 0, "tool_calls": 0, "empty": 0}
        last_finish_reason: str | None = None
        cumulative_content = ""
        cumulative_has_tool_call = False

        # 写出首 chunk (上面 with_fallback 拉到的那一个)
        if first_chunk is not None:
            data = first_chunk.model_dump() if hasattr(first_chunk, "model_dump") else first_chunk
            if isinstance(data, dict):
                usage = data.get("usage") or {}
                prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                completion_tokens = usage.get("completion_tokens", completion_tokens)
                # BL-CACHE-AUDIT (5/17 + 6/2 BL-CACHE-AUDIT-PROVIDER-FIELDS):
                # Anthropic 顶层字段 + LiteLLM 统一字段 (deepseek/dashscope/gemini/openai).
                _stream_cache_creation = (
                    usage.get("cache_creation_input_tokens", _stream_cache_creation) or _stream_cache_creation
                )
                _stream_cache_read = _pick_cache_read_from_streaming_usage(
                    usage, _stream_cache_read,
                )
                choices = data.get("choices") or []
                if choices:
                    choice0 = choices[0]
                    delta = choice0.get("delta") or {}
                    chunk_stats["total"] += 1
                    has_any = False
                    if delta.get("content"):
                        chunk_stats["content"] += 1
                        cumulative_content += delta["content"]
                        has_any = True
                    if delta.get("reasoning_content"):
                        chunk_stats["reasoning"] += 1
                        has_any = True
                    if delta.get("tool_calls"):
                        chunk_stats["tool_calls"] += 1
                        cumulative_has_tool_call = True
                        has_any = True
                    if not has_any:
                        chunk_stats["empty"] += 1
                    if choice0.get("finish_reason"):
                        last_finish_reason = choice0["finish_reason"]
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

        # 后续 chunks. _stream_with_keepalive 包装: chunk 间隔 > 30s 时插 SSE
        # comment 防客户端 / 中间代理 timeout 断开.
        async for chunk in _stream_with_keepalive(iterator):
            # BL-ABORT-PROPAGATE (7/23 达华 POC): 每 chunk 前检 client 是否断连.
            # 断了就 return · 触发 finally · aclose upstream · 停 token 消耗.
            # 老行为 · client 断了 gateway 继续烧到本轮 finish · 浪费 token / vLLM slot.
            if request is not None and await request.is_disconnected():
                logger.info(
                    "[abort] client disconnected mid-stream · req=%s user=%s "
                    "model=%s chunks_forwarded=%d",
                    request_id, user_sub, used_model.name, chunk_stats["total"],
                )
                status_str = "aborted"
                inflight_streams.mark_aborted(request_id, reason="client_disconnect")
                return  # generator return · finally 会跑 (aclose + output_transforms)
            if chunk == "__keepalive__":
                yield ": keepalive\n\n"
                continue
            data = chunk.model_dump() if hasattr(chunk, "model_dump") else chunk
            if isinstance(data, dict):
                usage = data.get("usage") or {}
                prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                completion_tokens = usage.get("completion_tokens", completion_tokens)
                # BL-CACHE-AUDIT (5/17 + 6/2 cache_read 兼容统一字段): final chunk usage
                _stream_cache_creation = (
                    usage.get("cache_creation_input_tokens", _stream_cache_creation) or _stream_cache_creation
                )
                _stream_cache_read = _pick_cache_read_from_streaming_usage(
                    usage, _stream_cache_read,
                )
                choices = data.get("choices") or []
                if choices:
                    choice0 = choices[0]
                    delta = choice0.get("delta") or {}
                    chunk_stats["total"] += 1
                    has_any = False
                    if delta.get("content"):
                        chunk_stats["content"] += 1
                        cumulative_content += delta["content"]
                        has_any = True
                    if delta.get("reasoning_content"):
                        chunk_stats["reasoning"] += 1
                        has_any = True
                    if delta.get("tool_calls"):
                        chunk_stats["tool_calls"] += 1
                        cumulative_has_tool_call = True
                        has_any = True
                    if not has_any:
                        chunk_stats["empty"] += 1
                    if choice0.get("finish_reason"):
                        last_finish_reason = choice0["finish_reason"]
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

        # 流末尾 stat 日志 (debug 用 — 看 chunk 形态 + finish_reason)
        if chunk_stats["total"] > 0:
            logger.info(
                "chunk stats: model=%s total=%d content=%d reasoning=%d "
                "tool_calls=%d empty=%d finish_reason=%s (cum_content=%d cum_tc=%s)",
                used_model.name,
                chunk_stats["total"],
                chunk_stats["content"],
                chunk_stats["reasoning"],
                chunk_stats["tool_calls"],
                chunk_stats["empty"],
                last_finish_reason,
                len(cumulative_content),
                cumulative_has_tool_call,
            )
            if last_finish_reason == "length":
                logger.warning(
                    "finish_reason=length: model=%s max_tokens 截了 (L4a 兜底 4096 "
                    "还撞 → 调大 / 真做 BL-A1.2 streaming auto-continue).",
                    used_model.name,
                )

        # ── 8/13 删: BL-TASK-ASSESS-1-GATEWAY (5/15) 的 task_assessment 事件 ──
        #
        # 它原本在 [DONE] 前多发一条 `object="task_assessment"` 的 SSE, 给
        # Companion 做 promise-vs-reality 检测 (模型说"已生成 x.docx"但没真调
        # 工具 → UI 打 ⚠)。删掉的理由有两层, 第二层才是决定性的:
        #
        # 1. **它从来没到过客户端。** 5/19 切 hermes 后 Companion 连的是
        #    hermes:8642, 这条事件产生在 hermes → gateway 这一段, hermes 不转发
        #    (hermes 侧全仓 0 处提及 task_assessment)。所以 ⚠ badge 一次都没显示过。
        #
        #    ⚠ 同一天我还先修过一个更里层的 bug: 判据字段 `skill_guard_fired`
        #      被写死成 False (5/26 砍 skills_loader 时留下的), 于是就算走老的
        #      直连路径也永不触发。修完才发现主路径压根收不到这条事件 ——
        #      **先修后查, 顺序反了。**
        #
        # 2. **判据在错的层, 转发也救不回来。** hermes 一次用户回合 = agent loop
        #    里多次 gateway 请求, 而 gateway 只看得见其中一轮。成功任务的最后
        #    一轮恰恰没有 tool_call (它是总结轮), 文字还常写"已生成 xxx.md" ——
        #    因为前面几轮真的生成了。照搬转发会在**几乎每个成功的文件生成任务**
        #    上误报。
        #
        # 结论: 这个判断只有 hermes 做得对 —— 只有它知道"这一整回合总共调了
        # 几次工具"。要重做就在 hermes 侧做, 不要再从 gateway 这层发信号。
        #
        # 前端同批删干净: promiseCheck.ts / PromiseCheckBadge.tsx / _promise_check
        # / TaskAssessment / onNudge 链路。恢复看本提交之前。
        yield "data: [DONE]\n\n"
    except asyncio.CancelledError:
        # BL-ABORT-PROPAGATE (7/23 达华 POC): fastapi/starlette 感知客户端 TCP 断连时 ·
        # 会 cancel 当前 generator task · raise CancelledError 到这里. 军规 · 不吞 ·
        # re-raise 让上层框架清理. finally 会跑 aclose upstream + output_transforms audit.
        # 注 · disconnect check 已在循环里覆盖大多数情况 · 这里是 check 与断连的窗口期兜底.
        logger.info(
            "[abort] CancelledError · req=%s user=%s model=%s",
            request_id, user_sub,
            used_model.name if used_model is not None else model_name,
        )
        status_str = "aborted"
        inflight_streams.mark_aborted(request_id, reason="cancelled")
        raise
    except Exception as e:  # noqa: BLE001
        status_str = "error"
        err = str(e)
        # 8/15: 上游报的恢复时间是 UTC, 而这行日志打的是本地时间。两个时区并排
        # 放着 (日期还可能差一天), 排查的人会以为早就该恢复了。换算一份出来。
        _reset = _localize_reset_hint(err)
        # 8/15: 原来这里是 `if attempts_log else "single"`, 而 attempts_log 在失败
        # 路径上恒为空 (见上面 attempts_out) —— 于是**每一次失败**都打 "single",
        # 不管实际试过几个。现在有真数据了; 还空就说明连主模型都没跑起来。
        _attempts = " -> ".join(attempts_log) if attempts_log else "无记录(主模型都没跑起来)"
        if _is_quota_or_rate_limit(err):
            # 配额 / 限流**不打 traceback**。
            #
            # 那四十行栈全是 litellm / openai 内部调用链, 对这类错零价值 —— 它不是
            # 我们的 bug, 是上游的账务状态。代价却很实在: 每个请求刷一屏, 把真正
            # 要看的三样 (哪个模型 / 上游原话 / 什么时候恢复) 挤出屏幕。
            #
            # 最要命的一条: 上游那句 UTC 恢复时间原样躺在栈的**最后一行**, 而我们
            # 换算成本地的那份在四十行之上 —— 人的眼睛落在栈底, 看到的永远是没
            # 换算的那个。8/15 就这么把 "08-14 23:54 UTC" 读成了 "早该恢复了"
            # (实际是本地 08-15 07:54)。信息在不在日志里, 和人能不能看见, 是两回事。
            logger.error(
                "streaming chat completion failed (attempts=%s) · model=%s · 上游原话: %s%s",
                _attempts,
                model.name,
                err.replace("\n", " ")[:300],
                f" · {_reset}" if _reset else "",
            )
        else:
            logger.exception(
                "streaming chat completion failed (attempts=%s)%s",
                _attempts,
                f" · {_reset}" if _reset else "",
            )
        # 给客户端一个 friendly 错误 —— 把内部 trace 简化成人话
        friendly = _friendly_upstream_error(err)
        # BL-FIX-TIMEOUT-OUTPUTS (5/13 鸿波"做不出文档"): 上游 LLM 卡 / timeout
        # 时, 副手实际可能已经写过文件 (execute_code 早跑了), 列最近 ~/.catfish/output/  # noqa: BOUNDARY
        # 文件给员工看, 别让他以为"做不出来"实际"已经做了".
        err_low = err.lower()
        is_timeout_or_overload = (
            "timeout" in err_low or "timed out" in err_low
            or "overloaded" in err_low or "503" in err
            or "broken pipe" in err_low or "connection error" in err_low
        )
        if is_timeout_or_overload:
            # 5/26 BL-RECENT-OUTPUTS-DECOUPLE: gateway 不再读员工本机 ~/.catfish/output/.  # noqa: BOUNDARY
            # 老 BL-FIX-TIMEOUT-OUTPUTS 在 timeout 错误段追加"过去 24h 已写文件"
            # 列表, 现砍 — Companion 端在 timeout 时自己显示 toast 列本地 outputs
            # (BL-X 后续做; recent_outputs.py 改 stub).
            friendly += (
                "\n💡 上游模型可能拥堵, 试试:\n"
                "  - **切大模型**: 输入 `/model catfish-public-gemini-pro` (2M 上下文, 公网快)\n"
                "  - **新建会话** (Cmd+N): 减小 prompt 让上游推理更快\n"
                "  - 上游 catfish-private-main 是内网 122B Qwen, 长 prompt + 高负载下推理 5+ 分钟"
            )
        # 8/9: error 必须是**对象**, 不能是裸字符串。
        #
        # 老写法 `{'error': friendly}` 里 friendly 是 str。OpenAI 官方客户端
        # (hermes 走的就是它) 的判据是 openai/_streaming.py:88-99:
        #
        #     if is_mapping(data) and data.get("error"):
        #         error = data.get("error")
        #         if is_mapping(error):            ← 字符串不是 mapping
        #             message = error.get("message")
        #         if not message or not isinstance(message, str):
        #             message = "An error occurred during streaming"   ← 落这里
        #         raise APIError(message=message, ...)
        #
        # 也就是说: **我们精心写的 friendly 文案被整个丢掉**, 客户端只拿到一句
        # 无信息量的 "An error occurred during streaming"。
        #
        # 8/9 实测代价 (P44 进度探针刚上线就照出来的第一个问题): hermes 拿着这句
        # 空话重试 3 次 (每次退避 2s / 6s), 白烧 ~22K token 的 prompt 三遍, 最后
        # 返 200 但正文是 "API call failed after 3 retries..."。员工看到的是
        # "等了很久然后没结果", 而真实原因 (friendly 里写着的) 一路都没传出去。
        #
        # 新形状跟 OpenAI 一致: {"error": {"message": ..., "type": ...}}。
        # Companion 那边 chat.ts 老代码只认字符串, 已同步改成两种都认 ——
        # 新旧网关 / 新旧 Companion 交叉组合都不会瞎。
        yield "data: {}\n\n".format(
            json.dumps(_app_mod()._sse_error_payload(friendly), ensure_ascii=False)
        )
    finally:
        # BL-ABORT-PROPAGATE (7/23 达华 POC): 显式关 upstream iterator · 停 token.
        # 正常完成 · iterator 已 exhausted · aclose 是 no-op. Abort 时 (client_disconnect
        # 走 return / CancelledError raise / Exception raise) 才实际发 close 到 litellm ·
        # 传到 vLLM/DashScope · 上游停生成. 军规 · 无 iterator/aclose 时 silent skip (预热
        # 阶段 iterator=None · 或某些 litellm 版本没 aclose 都 OK · 不该阻塞 audit).
        if iterator is not None:
            _aclose = getattr(iterator, "aclose", None)
            if _aclose is not None:
                try:
                    await _aclose()
                except Exception as _e:  # noqa: BLE001
                    logger.debug("upstream iterator aclose 失败 (可忽略): %s", _e)

        # BL-HERMES013-5 (5/12): 散点 audit/quota/context inline 调用 重构成
        # output_transforms ABC plugin chain (借鉴 Hermes 0.13 transform_llm_output).
        # 默认 chain: ContextUsageTransform → AuditTransform → QuotaTransform.
        # 后续加新 hook (in-flight 持久化 / 客户定制脱敏) 只改 build_default_chain.
        from . import output_transforms  # noqa: PLC0415
        actual_model_name = used_model.name if used_model is not None else model_name
        ctx = output_transforms.OutputCtx(
            user=user_sub,
            department=user_dept,
            model=actual_model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=(time.time() - start) * 1000,
            ttft_ms=ttft_ms,
            status=status_str,
            error=err,
            security_concern=security_concern,
            is_internal=is_internal,
            used_model=used_model,
            request_id=request_id,
            # BL-CACHE-AUDIT (5/17): streaming 路径同抓 cache tokens (final chunk
            # usage 里). 抓不到 (非 Anthropic / fallback model) 默认 0.
            cache_creation_tokens=_stream_cache_creation,
            cache_read_tokens=_stream_cache_read,
            # BL-RBAC-DAY4-HARDENING (5/17): X-Catfish-Source audit
            source=source_hint,
        )
        output_transforms.DEFAULT_CHAIN.run(ctx)

async def _invoke_chat_completion(
    body: dict,
    *,
    user_sub: str,
    user_dept: str = "",  # 五一 sprint 5/2 RBAC: quota.record_usage 用
    model_name: str,
    model,
    security_concern: str | None = None,
    is_internal: bool = False,  # BL-F17 (5/5): internal 调用跳 record_usage
    source_hint: str = "unknown",  # BL-RBAC-DAY4-HARDENING (5/17): X-Catfish-Source audit
) -> dict[str, Any]:
    """Non-streaming chat completion path, with fallback chain support.

    BL-A1.1 (5/8): 加 auto-continue on finish_reason=length. LLM 输出被
    max_tokens 截时自动续写, 直到 stop / tool_calls / 5 次上限. 让员工不需要
    手动说"继续", 真 Agent 行为.
    """
    from . import auto_continue  # noqa: PLC0415  lazy import 防循环

    start = time.time()
    config: Config = get_config()

    # 给 with_fallback 用的 inner caller — 一次 LLM 调用 (含 fallback chain)
    used_model_holder: list = [model]  # 用 list 当 mutable 容器, 让闭包能写

    async def _invoker_with_fallback(call_body: dict):
        async def _call(candidate_model):
            params = _build_litellm_params(call_body, candidate_model)
            return await litellm.acompletion(**params)
        # BL-FALLBACK-PROMPT-CAP (5/14): 大 prompt 失败时跳过公网
        # BL-TOKEN-COUNTER-LITELLM (5/15): 真 tokenizer 估算
        # BL-TOOLS-IN-ESTIMATE (5/15 22:00): tools schema 也算
        from .fallback import estimate_prompt_tokens  # noqa: PLC0415
        pe = estimate_prompt_tokens(
            call_body.get("messages") or [],
            model=model.upstream.model,
            tools=call_body.get("tools"),
        )
        # 6/7 BL-GROQ-TPM-PREFLIGHT: 拦超 TPM 请求 (友好 413 替 stack trace)
        from .rate_limit_preflight import preflight_check_rate_limits  # noqa: PLC0415
        preflight_check_rate_limits(model, pe, config=config)
        resp, used, _attempts = await with_fallback(
            config, model, _call, prompt_estimate=pe,
        )
        used_model_holder[0] = used  # 续写跨次都记最新 used_model
        return resp

    try:
        response, continuation_count = await auto_continue.call_with_auto_continue(
            body,
            invoker=_invoker_with_fallback,
            # internal 调用 (summarizer / proactive / a2a 辅助) 关 auto-continue:
            # 它们 max_tokens 是有意设短的 (600/120/80), 续写没意义 + 浪费 quota.
            enable=not is_internal,
        )
    except Exception as e:
        _raise_upstream_error(
            e,
            user_sub=user_sub,
            model_name=model_name,
            latency_ms=(time.time() - start) * 1000,
            log_context="chat completion failed",
        )
        raise  # unreachable; satisfies type checker

    used_model = used_model_holder[0]

    if continuation_count > 0:
        logger.info(
            "auto-continue: user=%s model=%s 续写 %d 次完成 (latency_ms=%.0f)",
            user_sub, used_model.name, continuation_count,
            (time.time() - start) * 1000,
        )

    if used_model is not model:
        logger.info(
            "non-stream served by fallback: requested=%s used=%s",
            model.name, used_model.name,
        )

    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
    completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
    # BL-CACHE-AUDIT (5/17): Anthropic prompt cache hit metrics.
    # Anthropic 在 usage 里返 cache_creation_input_tokens (首次写 cache 的 tokens)
    # + cache_read_input_tokens (命中 cache 复用的 tokens).
    #
    # 6/2 BL-CACHE-AUDIT-PROVIDER-FIELDS (鸿波 6/2 晚抓的真问题): 5/17 只抓 Anthropic
    # 字段, 但 catfish 真用的 deepseek/dashscope/gemini/openai 走 LiteLLM 统一字段
    # `usage.prompt_tokens_details.cached_tokens` (OpenAI 标准). 抓不到导致鸿波重启
    # 后 cache_read=0 误以为 cache 没生效. 加 cached_tokens fallback.
    #
    # LiteLLM 1.86 真实测 (llms/dashscope/cost_calculator.py:28-30 真证):
    #   - DashScope qwen-max:    usage.prompt_tokens_details.cached_tokens
    #   - DeepSeek API:           usage.prompt_tokens_details.cached_tokens
    #   - Gemini (cached_content):usage.prompt_tokens_details.cached_tokens
    #   - OpenAI gpt-4o:          usage.prompt_tokens_details.cached_tokens
    #   - Anthropic Claude:      usage.cache_read_input_tokens (顶层, 旧字段)
    cache_creation = (
        getattr(usage, "cache_creation_input_tokens", 0)
        or _extract_nested_usage(usage, "cache_creation_input_tokens")
        or 0
    ) if usage else 0
    cache_read = (
        getattr(usage, "cache_read_input_tokens", 0)
        or _extract_nested_usage(usage, "cache_read_input_tokens")
        or _extract_nested_usage(usage, "cached_tokens")  # 6/2 LiteLLM 统一字段
        or 0
    ) if usage else 0
    if cache_creation or cache_read:
        # 命中率 = cache_read / (cache_read + non-cached prompt_tokens)
        # 但 prompt_tokens 是总数 (含 cache_read), Anthropic 文档讲法不一, 都 log
        logger.info(
            "BL-CACHE-AUDIT: model=%s prompt=%d cache_create=%d cache_read=%d "
            "(cache_read/prompt=%.0f%%) user=%s",
            used_model.name, prompt_tokens, cache_creation, cache_read,
            (cache_read / prompt_tokens * 100) if prompt_tokens else 0,
            user_sub,
        )
    _check_context_usage(used_model, prompt_tokens, user_sub)
    _write_metadata(
        user=user_sub,
        model=used_model.name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=(time.time() - start) * 1000,
        status="ok",
        security_concern=security_concern,
        cache_creation_tokens=cache_creation,
        cache_read_tokens=cache_read,
        # BL-RBAC-DAY4-HARDENING (5/17): X-Catfish-Source audit
        source=source_hint,
    )
    # 五一 sprint 5/2 收尾: 同步写 quota_events. ok 才记 (error 时 tokens=0).
    # BL-F17 (5/5): internal 调用跳 record_usage (audit log 仍写, 只 quota 跳).
    if (prompt_tokens > 0 or completion_tokens > 0) and not is_internal:
        _quota_module.record_usage(
            user_email=user_sub,
            department=user_dept,
            model=used_model.name,
            tokens_in=prompt_tokens,
            tokens_out=completion_tokens,
        )

    # BL-ADVISOR-UPSTREAM-ERROR-AS-CONTENT (6/1 鸿波): 上游某些 LiteLLM-based 私有 LLM
    # 服务 (e.g. catfish-private-main) streaming fail 重试用尽后, 把 error 当作
    # completion content 返 200 OK, gateway 透传 → 客户端 (advisor / chat) 看 200
    # 走 JSON.parse 失败, 误判 "LLM 返非 JSON", 真因 (上游挂) 完全隐藏.
    #
    # 这里 detect 已知错误关键词 → 转 502 让客户端正确知道上游问题, 不污染 quota.
    # 真 LLM 输出含这些词 (e.g. 用户问"What's API call failed?" LLM 复读) 概率极低,
    # 但避免误杀: 只在 content 是**纯错误文本** (不超 500 字) 时识别.
    result = response.model_dump() if hasattr(response, "model_dump") else response
    if _looks_like_upstream_error_as_content(result):
        upstream_msg = _extract_content_text(result)[:300]
        logger.warning(
            "BL-ADVISOR-UPSTREAM-ERROR-AS-CONTENT: 上游 LLM 返 200 但 content 是错误文本, "
            "转 502 给客户端. model=%s user=%s content=%r",
            used_model.name, user_sub, upstream_msg[:200],
        )
        raise HTTPException(
            status_code=502,
            detail={
                "error": "upstream_error_as_content",
                "error_type": "UpstreamErrorAsContent",
                "message": upstream_msg,
                "friendly": "上游 LLM 服务暂时不可用 (重试用尽), 稍后再试.",
                "model": used_model.name,
            },
        )
    return result

__all__ = ["_stream_with_keepalive", "_stream_chat_completion", "_invoke_chat_completion"]
