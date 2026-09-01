"""Bound accumulated client-side tool loops before another LLM call."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from .config import ToolLoopConfig


@dataclass(frozen=True)
class ToolLoopStats:
    """Counts extracted from the submitted OpenAI message history."""

    tool_messages: int
    tool_calls: int


def count_tool_history(messages: Any) -> ToolLoopStats:
    """Count tool results and assistant tool calls without inspecting content."""
    if not isinstance(messages, list):
        return ToolLoopStats(tool_messages=0, tool_calls=0)

    tool_messages = 0
    tool_calls = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role in {"tool", "function"}:
            tool_messages += 1

        calls = message.get("tool_calls")
        if isinstance(calls, list):
            tool_calls += len(calls)
        elif calls:
            # Malformed but truthy history still represents one attempted call.
            tool_calls += 1
        if message.get("function_call"):
            tool_calls += 1

    return ToolLoopStats(tool_messages=tool_messages, tool_calls=tool_calls)


def enforce_tool_loop_budget(body: dict[str, Any], config: ToolLoopConfig) -> ToolLoopStats:
    """Stop a runaway agent history with a structured, client-visible 409.

    The caller remains responsible for deciding whether to retry. This guard
    never changes messages, picks a fallback model, or silently truncates work.
    """
    stats = count_tool_history(body.get("messages"))
    exceeded: list[str] = []
    if config.max_tool_messages is not None and stats.tool_messages > config.max_tool_messages:
        exceeded.append(
            f"tool_messages={stats.tool_messages}>{config.max_tool_messages}"
        )
    if config.max_tool_calls is not None and stats.tool_calls > config.max_tool_calls:
        exceeded.append(f"tool_calls={stats.tool_calls}>{config.max_tool_calls}")
    if exceeded:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "agent_loop_limit_exceeded",
                "message": (
                    "工具调用历史超过当前网关配置的安全上限，已停止继续调用模型。"
                    "请拆小任务或清理失败的工具调用后重试。"
                ),
                "limits": {
                    "max_tool_messages": config.max_tool_messages,
                    "max_tool_calls": config.max_tool_calls,
                },
                "observed": {
                    "tool_messages": stats.tool_messages,
                    "tool_calls": stats.tool_calls,
                },
                "exceeded": exceeded,
            },
        )
    return stats


__all__ = ["ToolLoopStats", "count_tool_history", "enforce_tool_loop_budget"]
