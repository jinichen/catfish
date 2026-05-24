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
    # BL-MEMORY-CATFISH-REMEMBER-BLACKLIST (5/16): catfish_remember 已从 _ALWAYS_ON_TOOLS
    # 移到 _HIDDEN_FROM_LLM (LLM 不再看到), 测试改用 memory 顶替 (hermes 0.13 统一工具).
    always_on_names = ["execute_code", "read_file", "write_file", "clarify", "memory"]
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
    """always-on 集应该至少含核心 5 个.

    BL-MEMORY-CATFISH-REMEMBER-BLACKLIST (5/16): catfish_remember 已黑名单,
    改测 memory (hermes 0.13 统一工具, 顶替 catfish_remember 跨 session 记忆角色).
    """
    must_have = {"execute_code", "read_file", "write_file", "clarify", "memory"}
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
    """30 个 tools (under default 35 cap) — 不被 cap.
    5/22 BL-TOOL-PROFILE 鸿波: _DEFAULT_MAX_TOOLS 50→35 配合 source profile 砍."""
    monkeypatch.delenv("CATFISH_MAX_TOOLS", raising=False)
    body = {"tools": [_tool(f"t_{i}") for i in range(30)]}
    sanitize_tools(body)
    assert len(body["tools"]) == 30


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


# ─── BL-WEB-ALWAYS-ON + BL-MCP-PREFIX-FIX (5/25) ────────────────


def test_web_search_extract_crawl_in_always_on():
    """5/25 BL-WEB-ALWAYS-ON: hermes 0.14 web 三件套必须 always-on, 防 cap 砍 + 排前面."""
    for n in ("web_search", "web_extract", "web_crawl"):
        assert n in _ALWAYS_ON_TOOLS, f"{n} 应该在 ALWAYS_ON_TOOLS"


def test_always_on_promoted_to_front_when_under_cap(monkeypatch):
    """5/25 BL-WEB-ALWAYS-ON: 即使没超 cap, always-on 也要重排到前面.

    动机: hermes 按字母序发 tool, web_search (W) 排倒数. 不超 cap 也要 promote
    到前面, 防模型位置偏置不选 web_search 退化用 catfish_browser_*.
    """
    monkeypatch.delenv("CATFISH_MAX_TOOLS", raising=False)
    # caller 给的顺序: 普通 → always_on (always_on 在尾)
    tools = (
        [_tool("zz_random_1"), _tool("zz_random_2")]
        + [_tool("web_search"), _tool("web_extract")]  # always-on, 字母末段
        + [_tool("zz_random_3")]
    )

    kept, dropped = _cap_tools_by_priority(tools)

    assert dropped == [], "5 个 tool 远低于 cap, 不该砍"
    # 输出顺序: always_on 一组在前, 其他在后 (各自保 caller 给的相对顺序)
    names = [t["function"]["name"] for t in kept]
    assert names[0] == "web_search", "web_search 应该被 promote 到第 1 位"
    assert names[1] == "web_extract"
    assert names[2:] == ["zz_random_1", "zz_random_2", "zz_random_3"]


def test_always_on_preserved_relative_order_among_themselves(monkeypatch):
    """同为 always-on 的 tools, 相对顺序保 caller 给的顺序 (不再排序乱)."""
    monkeypatch.delenv("CATFISH_MAX_TOOLS", raising=False)
    # caller 给: write_file 在前, read_file 在后 (反字母序)
    tools = [
        _tool("random_x"),
        _tool("write_file"),
        _tool("random_y"),
        _tool("read_file"),
    ]
    kept, _ = _cap_tools_by_priority(tools)
    names = [t["function"]["name"] for t in kept]
    # always-on 前置: write_file 仍在 read_file 前 (caller 给的顺序)
    assert names == ["write_file", "read_file", "random_x", "random_y"]


def test_mcp_wrapped_always_on_promoted_too(monkeypatch):
    """5/25 BL-MCP-PREFIX-FIX: mcp_catfish_tools_web_search 也算 always-on."""
    monkeypatch.delenv("CATFISH_MAX_TOOLS", raising=False)
    tools = [
        _tool("zz_random_1"),
        _tool("mcp_catfish_tools_web_search"),   # MCP 包装版
        _tool("zz_random_2"),
        _tool("mcp_catfish_tools_catfish_today_summary"),  # 同样
    ]
    kept, _ = _cap_tools_by_priority(tools)
    names = [t["function"]["name"] for t in kept]
    # 两个 MCP 包装的 always-on 都被 promote 到前面
    assert names[0] == "mcp_catfish_tools_web_search"
    assert names[1] == "mcp_catfish_tools_catfish_today_summary"
    assert names[2:] == ["zz_random_1", "zz_random_2"]


def test_mcp_wrapped_always_on_survives_cap(monkeypatch):
    """5/25 BL-MCP-PREFIX-FIX: 超 cap 时, MCP 包装的 always-on 也不被砍."""
    monkeypatch.setenv("CATFISH_MAX_TOOLS", "11")
    tools = [
        _tool("mcp_catfish_tools_catfish_today_summary"),  # MCP 包装 always-on
        _tool("mcp_catfish_tools_web_search"),             # 同上
    ] + [_tool(f"low_{i}") for i in range(20)]   # 20 low priority

    kept, dropped = _cap_tools_by_priority(tools)
    kept_names = {t["function"]["name"] for t in kept}

    # 2 个 MCP 包装 always-on 保留 (BL-MCP-PREFIX-FIX 前会被当普通工具砍)
    assert "mcp_catfish_tools_catfish_today_summary" in kept_names
    assert "mcp_catfish_tools_web_search" in kept_names
    assert "mcp_catfish_tools_catfish_today_summary" not in dropped
    assert "mcp_catfish_tools_web_search" not in dropped


def test_is_always_on_helper_directly():
    """直接测 _is_always_on helper 的 3 个分支."""
    from catfish_gateway.tools_sanitizer_constants import is_always_on

    # 裸名匹配
    assert is_always_on("web_search") is True
    assert is_always_on("execute_code") is True
    # MCP 包装名 strip 后匹配
    assert is_always_on("mcp_catfish_tools_web_search") is True
    assert is_always_on("mcp_catfish_tools_catfish_today_summary") is True
    # 不在 always-on
    assert is_always_on("random_tool") is False
    assert is_always_on("mcp_catfish_tools_random_tool") is False
    # 边界
    assert is_always_on("") is False
    assert is_always_on(None) is False  # type: ignore[arg-type]
    # 别的 MCP 前缀不识别 (只认 mcp_catfish_tools_)
    assert is_always_on("mcp_someother_server_web_search") is False
