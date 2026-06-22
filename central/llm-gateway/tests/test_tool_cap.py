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


# ── P3.5.70 (6/22 鸿波 catch "工作台拒调 execute_code") ──
# 锁住 terminal/read_terminal 不暴露给 LLM 的架构. catfish 安全设计:
# LLM 直调 shell 唯一通道 = execute_code (沙箱+审批). terminal 给 skill
# 内部 dispatch 用, 不给 LLM tool list. 老 5/17 BL-HERMES-014-UPGRADE
# 加 terminal 到 ALWAYS_ON 是失误, 本测锁回归.

def test_terminal_not_in_always_on():
    """P3.5.70: terminal/read_terminal 不在 ALWAYS_ON_TOOLS — 它们 hidden."""
    from catfish_gateway.tools_sanitizer_constants import ALWAYS_ON_TOOLS
    assert "terminal" not in ALWAYS_ON_TOOLS, "P3.5.70 红线: terminal 不该在 ALWAYS_ON"
    assert "read_terminal" not in ALWAYS_ON_TOOLS


def test_terminal_in_hidden_from_llm():
    """P3.5.70: terminal/read_terminal 在 HIDDEN_FROM_LLM, sanitize 必砍."""
    from catfish_gateway.tools_sanitizer_constants import HIDDEN_FROM_LLM
    assert "terminal" in HIDDEN_FROM_LLM
    assert "read_terminal" in HIDDEN_FROM_LLM


def test_is_hidden_from_llm_helper():
    """P3.5.70: is_hidden_from_llm() 同时认裸名 + MCP 前缀 (跟 is_always_on 对称)."""
    from catfish_gateway.tools_sanitizer_constants import is_hidden_from_llm

    # 裸名匹配
    assert is_hidden_from_llm("terminal") is True
    assert is_hidden_from_llm("read_terminal") is True
    assert is_hidden_from_llm("catfish_remember") is True
    # MCP 包装名 strip 后匹配 (老 bug: 没这层就 mcp_catfish_tools_terminal 漏砍)
    assert is_hidden_from_llm("mcp_catfish_tools_terminal") is True
    assert is_hidden_from_llm("mcp_catfish_tools_read_terminal") is True
    assert is_hidden_from_llm("mcp_catfish_tools_catfish_remember") is True
    # 不在 hidden
    assert is_hidden_from_llm("execute_code") is False
    assert is_hidden_from_llm("mcp_catfish_tools_execute_code") is False
    # 边界
    assert is_hidden_from_llm("") is False
    assert is_hidden_from_llm(None) is False  # type: ignore[arg-type]


def test_sanitize_drops_terminal(monkeypatch):
    """P3.5.70: sanitize_tools 真砍 terminal 跟 mcp 前缀版本, LLM tool list 不含."""
    monkeypatch.delenv("CATFISH_EXPOSE_TERMINAL", raising=False)
    monkeypatch.delenv("CATFISH_EXPOSE_REMEMBER", raising=False)
    body = {
        "tools": [
            _tool("terminal"),
            _tool("mcp_catfish_tools_terminal"),
            _tool("read_terminal"),
            _tool("mcp_catfish_tools_read_terminal"),
            _tool("execute_code"),  # 控制组: 不该砍
            _tool("web_search"),    # 控制组: 不该砍
        ],
    }
    result = sanitize_tools(body)
    names = [t["function"]["name"] for t in result["tools"]]
    # terminal / read_terminal 4 个变种全砍
    assert "terminal" not in names
    assert "read_terminal" not in names
    assert "mcp_catfish_tools_terminal" not in names
    assert "mcp_catfish_tools_read_terminal" not in names
    # 控制组保留
    assert "execute_code" in names
    assert "web_search" in names


def test_sanitize_keeps_terminal_when_env_override(monkeypatch):
    """P3.5.70: CATFISH_EXPOSE_TERMINAL=1 时 terminal 恢复 expose (dept 信任高场景)."""
    monkeypatch.setenv("CATFISH_EXPOSE_TERMINAL", "1")
    body = {
        "tools": [
            _tool("terminal"),
            _tool("mcp_catfish_tools_terminal"),
        ],
    }
    result = sanitize_tools(body)
    names = [t["function"]["name"] for t in result["tools"]]
    # env override 后两个 variants 都保留
    assert "terminal" in names
    assert "mcp_catfish_tools_terminal" in names


# ── P3.5.72 (6/22 鸿波 catch "工作台 LLM 看到 execute_code 还是拒绝") ──
# 锁 execute_code description 被 catfish 平台合同覆盖.

def test_sanitize_rewrites_execute_code_description():
    """P3.5.72: execute_code 的 description 被改写成 catfish 合同."""
    body = {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "execute_code",
                    "description": "hermes 上游 description (Run a Python script... use normal tool calls instead)",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ],
    }
    result = sanitize_tools(body)
    descs = [t["function"]["description"] for t in result["tools"]]
    # description 被改写 — 必含 catfish 平台合同关键字 (P3.5.72.1: 改简洁版,
    # 砍审批/安全字眼避免 LLM 联想保守)
    assert any("Execute a Python script" in d for d in descs), \
        f"P3.5.72: execute_code description 没被改写, 实际: {descs}"
    assert any("Don't ask the user to run it themselves" in d for d in descs)
    # hermes 上游原 description 被覆盖
    assert not any("use normal tool calls instead" in d for d in descs)


def test_sanitize_rewrites_execute_code_mcp_prefix():
    """P3.5.72: mcp_catfish_tools_execute_code 同名前缀版本也被改写."""
    body = {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "mcp_catfish_tools_execute_code",
                    "description": "hermes 原",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ],
    }
    result = sanitize_tools(body)
    descs = [t["function"]["description"] for t in result["tools"]]
    assert any("Execute a Python script" in d for d in descs)


def test_sanitize_other_tool_descriptions_untouched():
    """P3.5.72: 改 execute_code 只改 execute_code, 别的 tool description 不动."""
    body = {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "execute_code",
                    "description": "original",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search the web for query",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ],
    }
    result = sanitize_tools(body)
    by_name = {t["function"]["name"]: t["function"]["description"] for t in result["tools"]}
    assert "Execute a Python script" in by_name["execute_code"]
    assert by_name["web_search"] == "Search the web for query"  # 不动
