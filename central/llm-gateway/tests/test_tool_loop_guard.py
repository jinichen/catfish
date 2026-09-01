"""Configurable protection for runaway Hermes tool histories."""

import pytest
from fastapi import HTTPException

from catfish_gateway.config import ToolLoopConfig
from catfish_gateway.tool_loop_guard import (
    count_tool_history,
    enforce_tool_loop_budget,
)


def _assistant_calls(count: int) -> dict:
    return {
        "role": "assistant",
        "tool_calls": [{"id": f"call-{i}"} for i in range(count)],
    }


def test_counts_tool_calls_and_results():
    stats = count_tool_history(
        [_assistant_calls(3), {"role": "tool"}, {"role": "function"}]
    )

    assert stats.tool_calls == 3
    assert stats.tool_messages == 2


def test_short_tool_history_is_allowed():
    stats = enforce_tool_loop_budget(
        {"messages": [_assistant_calls(2), {"role": "tool"}]},
        ToolLoopConfig(max_tool_messages=2, max_tool_calls=2),
    )

    assert stats.tool_messages == 1
    assert stats.tool_calls == 2


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (ToolLoopConfig(max_tool_messages=1, max_tool_calls=99), "tool_messages=2>1"),
        (ToolLoopConfig(max_tool_messages=99, max_tool_calls=2), "tool_calls=3>2"),
    ],
)
def test_exceeded_dimension_returns_structured_409(config, expected):
    with pytest.raises(HTTPException) as exc:
        enforce_tool_loop_budget(
            {"messages": [_assistant_calls(3), {"role": "tool"}, {"role": "tool"}]},
            config,
        )

    assert exc.value.status_code == 409
    assert expected in exc.value.detail["exceeded"]
    assert exc.value.detail["error"] == "agent_loop_limit_exceeded"


def test_no_tool_history_is_unaffected():
    stats = enforce_tool_loop_budget(
        {"messages": [{"role": "user", "content": "hello"}]},
        ToolLoopConfig(max_tool_messages=1, max_tool_calls=1),
    )

    assert stats.tool_messages == 0
    assert stats.tool_calls == 0
