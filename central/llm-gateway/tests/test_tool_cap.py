"""BL-TOOL-CAP (5/15 鸿波撞 Qwen 122B 83 tools 空 400) — tool 数量 cap 单测.

跑法: cd central/llm-gateway && PYTHONPATH=src python -m pytest tests/test_tool_cap.py -q

覆盖:
  - 不超 cap 时不动
  - 超 cap 时保留 always-on + 按顺序填剩余
  - always-on 占多 slot 时, other 几乎全丢
  - env CATFISH_MAX_TOOLS 调阈值
  - cap 不会砍 < 10 (always-on 保护)
  - sanitize_tools 集成 cap (端到端)
  - 历史 messages 里被 cap 砍掉的 tool_calls 一起 scrub
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from catfish_gateway.tools_sanitizer import (
    _ALWAYS_ON_TOOLS,
    _DEFAULT_MAX_TOOLS,
    _cap_tools_by_priority,
    sanitize_tools,
)


def _tool(name: str) -> dict:
    """造一个合法 tool dict"""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"desc for {name}",
            "parameters": {"type": "object", "properties": {}},
        },
    }


# ─── _cap_tools_by_priority ─────────────────────────────────


def test_no_cap_when_under_limit():
    tools = [_tool(f"t_{i}") for i in range(10)]
    kept, dropped = _cap_tools_by_priority(tools)
    assert kept == tools
    assert dropped == []


def test_cap_keeps_always_on_first(monkeypatch):
    """超 cap 时, always-on 必须保留"""
    monkeypatch.setenv("CATFISH_MAX_TOOLS", "15")
    # 5 个 always-on + 20 个 other = 25 total, cap=15
    always_on_names = ["execute_code", "read_file", "write_file", "clarify", "catfish_remember"]
    other_names = [f"random_tool_{i}" for i in range(20)]
    # 顺序故意打乱 — always-on 不在最前
    tools = (
        [_tool(other_names[0])] +
        [_tool(always_on_names[0])] +
        [_tool(other_names[1])] +
        [_tool(always_on_names[1])] +
        [_tool(n) for n in other_names[2:]] +
        [_tool(n) for n in always_on_names[2:]]
    )

    kept, dropped = _cap_tools_by_priority(tools)
    assert len(kept) == 15

    kept_names = [t["function"]["name"] for t in kept]
    # 5 个 always-on 全在
    for n in always_on_names:
        assert n in kept_names
    # other 只剩 15 - 5 = 10 个
    other_kept = [n for n in kept_names if n.startswith("random_tool_")]
    assert len(other_kept) == 10


def test_cap_drops_low_priority_first(monkeypatch):
    """cap 时 always-on 不在 dropped 里"""
    monkeypatch.setenv("CATFISH_MAX_TOOLS", "12")
    tools = [_tool("execute_code"), _tool("read_file")]  # 2 always-on
    tools += [_tool(f"misc_{i}") for i in range(20)]   # 20 other
    # total 22, cap 12 → keep 2 always-on + 10 misc, drop 10 misc

    kept, dropped = _cap_tools_by_priority(tools)
    assert len(kept) == 12
    assert "execute_code" not in dropped
    assert "read_file" not in dropped
    assert len(dropped) == 10
    # dropped 全是 misc
    for d in dropped:
        assert d.startswith("misc_")


def test_cap_env_override(monkeypatch):
    """env CATFISH_MAX_TOOLS 真生效 (min 10 强制)"""
    monkeypatch.setenv("CATFISH_MAX_TOOLS", "15")
    tools = [_tool(f"t_{i}") for i in range(30)]
    kept, dropped = _cap_tools_by_priority(tools)
    assert len(kept) == 15
    assert len(dropped) == 15


def test_cap_env_invalid_falls_back(monkeypatch):
    """env 不是数字 → 默认 50"""
    monkeypatch.setenv("CATFISH_MAX_TOOLS", "notanumber")
    tools = [_tool(f"t_{i}") for i in range(60)]
    kept, dropped = _cap_tools_by_priority(tools)
    assert len(kept) == _DEFAULT_MAX_TOOLS
    assert len(dropped) == 60 - _DEFAULT_MAX_TOOLS


def test_cap_minimum_10(monkeypatch):
    """env CATFISH_MAX_TOOLS=5 实际不允许 < 10 (always-on 都装不下)"""
    monkeypatch.setenv("CATFISH_MAX_TOOLS", "3")
    tools = [_tool(f"t_{i}") for i in range(20)]
    kept, _ = _cap_tools_by_priority(tools)
    assert len(kept) == 10  # 强制最低 10


def test_always_on_set_basic_sanity():
    """always-on 集应该至少含核心 5 个"""
    must_have = {"execute_code", "read_file", "write_file", "clarify", "catfish_remember"}
    assert must_have.issubset(_ALWAYS_ON_TOOLS)


# ─── sanitize_tools 集成 (端到端) ────────────────────────


def test_sanitize_tools_applies_cap(monkeypatch):
    """sanitize_tools 端到端: 进 83 个 → 出 50 个 (默认 cap)"""
    monkeypatch.delenv("CATFISH_MAX_TOOLS", raising=False)
    body = {
        "tools": [_tool(f"t_{i}") for i in range(83)]
    }
    sanitize_tools(body)
    assert len(body["tools"]) == _DEFAULT_MAX_TOOLS


def test_sanitize_tools_no_cap_when_under_limit(monkeypatch):
    """40 个 tools (under 50) — 不被 cap"""
    monkeypatch.delenv("CATFISH_MAX_TOOLS", raising=False)
    body = {"tools": [_tool(f"t_{i}") for i in range(40)]}
    sanitize_tools(body)
    assert len(body["tools"]) == 40


def test_sanitize_tools_scrubs_history_for_capped(monkeypatch):
    """cap 砍掉的 tool, 历史 messages 里的 tool_calls 一起 scrub —
    防 Qwen Go gRPC 校验 "tool_call 的 name 必须在 tools 列表里" 撞 400.
    用 cap=12 (>= min 10), low_pri_15 在 dropped 范围内."""
    monkeypatch.setenv("CATFISH_MAX_TOOLS", "12")
    body = {
        "tools": (
            [_tool("execute_code"), _tool("read_file")]  # 2 always-on
            + [_tool(f"low_pri_{i}") for i in range(20)]  # 20 low-priority
        ),
        "messages": [
            {"role": "user", "content": "hi"},
            # assistant 调了 low_pri_15 (远超 cap, 会被砍)
            {
                "role": "assistant",
                "content": "用工具",
                "tool_calls": [
                    {"id": "call_1", "type": "function",
                     "function": {"name": "low_pri_15", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "result"},
        ],
    }
    sanitize_tools(body)

    # tools 数 = 12 (cap)
    assert len(body["tools"]) == 12
    kept_names = {t["function"]["name"] for t in body["tools"]}
    # always-on 保留
    assert "execute_code" in kept_names
    assert "read_file" in kept_names
    # low_pri_15 应该被砍 (顺序: low_pri_0..9 在前 10 个保留, 10..19 被砍)
    assert "low_pri_15" not in kept_names

    # low_pri_15 的 tool_call 应该被 scrub (砍掉的 tool 不能在 history)
    asst_msg = next(m for m in body["messages"] if m.get("role") == "assistant")
    if "tool_calls" in asst_msg:
        for tc in asst_msg.get("tool_calls", []):
            assert tc["function"]["name"] != "low_pri_15"
