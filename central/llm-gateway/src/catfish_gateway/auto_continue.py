"""
auto_continue.py — BL-A1.1 (5/8 ship): LLM 输出被 max_tokens 截断时自动续写.

# 为啥要这模块

5/7 鸿波报: "鲶鱼写 30 页《资质管理办法》docx, output token 撞 4-8K 上限, chat
里写到一半停了, 需要员工说'继续' 才能接." 这是 Copilot 行为, 不是 Agent.

真 Agent 应该自己检测 finish_reason="length" 自动续写, 直到任务完成. 员工只输入
一次, 不需要催.

# 设计

把现有 `with_fallback(config, model, _call)` 包一层 `call_with_auto_continue`,
逻辑:

    while True:
        response = await with_fallback(...)
        accumulated.append(response.message.content)
        if finish_reason != "length":  # stop / tool_calls / content_filter / null
            break
        if continuation_count >= MAX:   # 防死循环
            break
        body.messages += assistant(content) + user("继续从...接着写")
        continuation_count += 1
    final_response.message.content = "".join(accumulated)

# 不该自动续的情况

1. **流式响应** (stream=True) — finish_reason 在 chunk 流末尾, 处理复杂; 5/8 后续做
2. **tool_calls** finish_reason — 必须让 client 处理 tool 拿结果, 不可续
3. **internal 调用** (summarizer/proactive/a2a) — 这些有 max_tokens 短设计, 续无意义
4. **content_filter** finish_reason — provider 拦截, 续也没用

# 测试

单测 8 个 (tests/test_auto_continue.py):
    - finish_reason=stop 不续
    - finish_reason=length 续 1 次
    - finish_reason=length 连续 5 次撞上限放弃
    - finish_reason=tool_calls 不续 (即使 length 状态相似)
    - enable=False 不续
    - 累加 content 正确
    - body 不被原地修改 (deep copy)
    - last 200 chars hint 拼对
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any

logger = logging.getLogger("catfish.gateway.auto_continue")

# 防死循环的硬上限. 5 次 × 8K token = 40K 字, 30 页 docx 也够了.
DEFAULT_MAX_CONTINUATIONS = 5

# 不能自动续的 finish_reason (除 stop 外, 其他 'terminal' 状态也不续)
NON_CONTINUABLE_FINISH_REASONS = {
    "stop",            # LLM 自然结束
    "tool_calls",      # LLM 要调 tool, 必须 client 处理
    "function_call",   # 老版 OpenAI tool_calls 名
    "content_filter",  # provider 拦截
    None,              # 异常 / unknown
}

# 给 LLM 的续写 hint (放 user message), 强提示不要重复 / 不要客套
_CONTINUATION_HINT_TEMPLATE = (
    "（你的上一条回复因输出长度限制被截断了, 最后说到 \"...{last_chunk}\". "
    "请从被截断处**接着写**, 直到完整完成员工的任务. "
    "**不要**重复已写过的内容, **不要**说 \"抱歉被截断\" 之类客套, **不要**重新开头. "
    "直接接续输出剩余内容.）"
)
# 续写 hint 引用上一段末尾多少字符做锚点
_LAST_CHUNK_LEN = 200


def _extract_finish_reason(response: Any) -> str | None:
    """从 litellm/openai response 里取 finish_reason. 各种 shape 兼容."""
    try:
        choices = getattr(response, "choices", None)
        if choices is None and isinstance(response, dict):
            choices = response.get("choices")
        if not choices:
            return None
        first = choices[0]
        fr = getattr(first, "finish_reason", None)
        if fr is None and isinstance(first, dict):
            fr = first.get("finish_reason")
        return fr
    except Exception:
        logger.warning("提取 finish_reason 失败, 当作 None", exc_info=True)
        return None


def _extract_message_content(response: Any) -> str:
    """从 response 里取 assistant message.content (字符串). 没有返 ''."""
    try:
        choices = getattr(response, "choices", None)
        if choices is None and isinstance(response, dict):
            choices = response.get("choices")
        if not choices:
            return ""
        first = choices[0]
        msg = getattr(first, "message", None)
        if msg is None and isinstance(first, dict):
            msg = first.get("message")
        if msg is None:
            return ""
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content")
        return content or ""
    except Exception:
        logger.warning("提取 message content 失败, 返 ''", exc_info=True)
        return ""


def _set_message_content(response: Any, content: str) -> None:
    """把累加完的 content 塞回 response.choices[0].message.content."""
    try:
        choices = getattr(response, "choices", None)
        if choices is None and isinstance(response, dict):
            choices = response.get("choices")
        if not choices:
            return
        first = choices[0]
        msg = getattr(first, "message", None)
        if msg is None and isinstance(first, dict):
            msg = first.get("message")
        if msg is None:
            return
        if isinstance(msg, dict):
            msg["content"] = content
        else:
            try:
                msg.content = content
            except Exception:
                # pydantic v2 frozen 之类不让改, 退路: 改 dict 形式
                logger.warning("无法 setattr message.content, response shape 未知")
    except Exception:
        logger.warning("回填 message content 失败", exc_info=True)


def _build_continuation_messages(
    original_messages: list[dict],
    assistant_content: str,
) -> list[dict]:
    """构造续写用的新 messages = 原 + assistant(被截内容) + user(续写 hint).

    取 last_chunk = assistant_content 末尾 200 字 (按 char, 不按 byte) 给 LLM 当锚点.
    UTF-8 安全 (Python str 默认 char 索引).
    """
    last_chunk = assistant_content[-_LAST_CHUNK_LEN:] if len(assistant_content) > _LAST_CHUNK_LEN else assistant_content
    hint = _CONTINUATION_HINT_TEMPLATE.format(last_chunk=last_chunk)

    new_messages = list(original_messages)
    new_messages.append({"role": "assistant", "content": assistant_content})
    new_messages.append({"role": "user", "content": hint})
    return new_messages


async def call_with_auto_continue(
    body: dict,
    *,
    invoker: Callable[[dict], Awaitable[Any]],
    max_continuations: int = DEFAULT_MAX_CONTINUATIONS,
    enable: bool = True,
) -> tuple[Any, int]:
    """对 LLM 调用自动续写.

    Args:
        body: chat_completions body, 含 messages / model / tools / 等.
              **会被 deep-copy**, 不修改 caller 引用.
        invoker: 用 body 调 LLM 的异步函数, 返 litellm response.
                 caller 自己处理 fallback chain (传入的就是 with_fallback wrapper).
        max_continuations: 上限, 防死循环. 默认 5.
        enable: False 关闭 auto-continue, 等价于 invoker 调一次直接返.
                给 internal call (summarizer / proactive) 用.

    Returns:
        (final_response, continuation_count)
        - final_response: 最后一次 response, 但 message.content 已是累加完整版
        - continuation_count: 触发了几次续写 (0 = 没续)

    Raises:
        invoker 抛的异常透传 (with_fallback 已处理 fallback chain, 这里不再 catch).
    """
    if not enable:
        # 直接调一次, 不进 loop, 等价于 baseline 行为
        response = await invoker(body)
        return response, 0

    # deep copy 防破坏 caller 的 body (caller 可能基于 body 做 audit / log)
    work_body = deepcopy(body)
    accumulated_content_parts: list[str] = []
    continuation_count = 0
    final_response: Any = None

    while True:
        response = await invoker(work_body)
        final_response = response

        finish_reason = _extract_finish_reason(response)
        content = _extract_message_content(response)
        accumulated_content_parts.append(content)

        if finish_reason in NON_CONTINUABLE_FINISH_REASONS:
            # stop / tool_calls / content_filter / None — 不续
            break

        if finish_reason != "length":
            # 未知 finish_reason (可能 provider 自定义), 保守不续
            logger.info(
                "auto-continue 遇到未知 finish_reason=%r, 不续",
                finish_reason,
            )
            break

        if continuation_count >= max_continuations:
            logger.warning(
                "auto-continue 已 %d 次仍 length, 放弃 (员工任务可能太复杂或 max_tokens 设太小). "
                "返回当前累加结果, 标记最终 finish_reason=length",
                continuation_count,
            )
            break

        # 续写: 把 assistant content 加进 messages, 加 hint
        work_body["messages"] = _build_continuation_messages(
            work_body.get("messages", []),
            content,
        )
        continuation_count += 1
        logger.info(
            "auto-continue triggered: count=%d total_chars_so_far=%d msgs_len=%d",
            continuation_count,
            sum(len(p) for p in accumulated_content_parts),
            len(work_body["messages"]),
        )

    # 如果触发过续写, 把累加 content 塞回 final_response
    if continuation_count > 0:
        full_content = "".join(accumulated_content_parts)
        _set_message_content(final_response, full_content)
        logger.info(
            "auto-continue done: %d continuations, total %d chars",
            continuation_count, len(full_content),
        )

    return final_response, continuation_count
