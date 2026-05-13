"""BL-FIX-MCP-SHORTNAME 测试 — dispatch 短名 → 全名 fallback.

鸿波 Companion 截图: LLM 调 local_search 失败 'unknown tool', 真名是
mcp_catfish_local_search_local_search. SOUL.md 多处用短名教坏 LLM, dispatch
加 fallback 容错.

覆盖:
- _resolve_mcp_short_name: 唯一命中 / 0 / 多候选
- _suggest_mcp_full_names: 给 LLM 的 hint 列表
- E2E dispatch (mock _r) 短名命中改派 + unknown 时 candidates 进 error
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from catfish_tool_bridge import adapter


# ── 单元: _resolve_mcp_short_name ─────────────────────────


def test_resolve_unique_match():
    all_names = [
        "memory_save",
        "mcp_catfish_local_search_local_search",
        "mcp_other_server_other_tool",
    ]
    assert adapter._resolve_mcp_short_name("local_search", all_names) == \
        "mcp_catfish_local_search_local_search"


def test_resolve_no_match():
    all_names = ["memory_save", "mcp_some_thing"]
    assert adapter._resolve_mcp_short_name("nonexistent", all_names) is None


def test_resolve_ambiguous_returns_none():
    """两个 mcp 工具后缀都叫 _foo → 不猜."""
    all_names = [
        "mcp_serverA_foo",
        "mcp_serverB_foo",
    ]
    assert adapter._resolve_mcp_short_name("foo", all_names) is None


def test_resolve_empty_short():
    assert adapter._resolve_mcp_short_name("", []) is None


def test_resolve_already_full_name_returns_none():
    """如果传进来的就是全名 (mcp_xxx), 不走 fallback."""
    all_names = ["mcp_catfish_local_search_local_search"]
    # 全名不是短名, 应该直接走精确匹配 (上层处理), 这里返 None
    assert adapter._resolve_mcp_short_name(
        "mcp_catfish_local_search_local_search", all_names,
    ) is None


def test_resolve_only_matches_mcp_prefix():
    """非 mcp_ 工具就算后缀对也不返 (跟 catfish_native / hermes 工具区分)."""
    all_names = [
        "catfish_local_search",  # 假设有这名 native tool — 不该被当 mcp 候选
        "memory_local_search",   # hermes 老 toolset 的 tool — 同上
    ]
    assert adapter._resolve_mcp_short_name("local_search", all_names) is None


def test_resolve_with_intermediate_underscore_in_short():
    """短名含下划线 (例 'long_query') 也能匹后缀."""
    all_names = ["mcp_search_server_long_query"]
    # short='long_query', suffix='_long_query', match 'mcp_search_server_long_query'
    assert adapter._resolve_mcp_short_name(
        "long_query", all_names,
    ) == "mcp_search_server_long_query"


# ── _suggest_mcp_full_names ──────────────────────────────


def test_suggest_returns_all_candidates_sorted():
    all_names = [
        "memory_save",
        "mcp_z_server_search",
        "mcp_a_server_search",
        "mcp_b_server_search",
        "mcp_other_thing",
    ]
    out = adapter._suggest_mcp_full_names("search", all_names)
    assert out == [
        "mcp_a_server_search",
        "mcp_b_server_search",
        "mcp_z_server_search",
    ]


def test_suggest_empty_short():
    assert adapter._suggest_mcp_full_names("", []) == []


def test_suggest_no_candidates():
    assert adapter._suggest_mcp_full_names("nope", ["memory_save"]) == []


# ── E2E dispatch 集成 ─────────────────────────────────────


def _setup_fake_registry(monkeypatch, tool_names: list[str], dispatch_result=None):
    """mock _r() 返一个假 registry — get_all_tool_names 返指定 list,
    dispatch 调用记录 (caller, name)."""
    fake_registry = MagicMock()
    fake_registry.get_all_tool_names.return_value = tool_names
    if dispatch_result is None:
        dispatch_result = {"echo": "ok"}
    fake_registry.dispatch = MagicMock(return_value=dispatch_result)

    fake_module = MagicMock()
    fake_module.registry = fake_registry
    monkeypatch.setattr(adapter, "_registry_module", fake_module)
    return fake_registry


def test_dispatch_unknown_no_candidates(monkeypatch):
    _setup_fake_registry(monkeypatch, tool_names=["memory_save"])
    r = asyncio.run(adapter.dispatch_tool("totally_made_up", {}))
    assert r["ok"] is False
    assert "unknown tool: totally_made_up" in r["error"]
    # 没候选时 error 不该有"你大概想调"
    assert "你大概想调" not in r["error"]
    assert r["candidates"] == []


def test_dispatch_short_name_routes_to_full_name(monkeypatch):
    fake_reg = _setup_fake_registry(
        monkeypatch,
        tool_names=["mcp_catfish_local_search_local_search"],
    )
    r = asyncio.run(adapter.dispatch_tool("local_search", {"query": "X"}))
    # 不再返 unknown — 改派到全名
    assert r.get("ok") is not False or "unknown" not in str(r.get("error", ""))
    # registry.dispatch 被以全名调用
    fake_reg.dispatch.assert_called_once()
    call_args = fake_reg.dispatch.call_args
    # dispatch(name, args) 第一个 positional arg
    assert call_args[0][0] == "mcp_catfish_local_search_local_search"


def test_dispatch_ambiguous_short_returns_unknown(monkeypatch):
    """两个候选时不猜 — 返 unknown + 给 LLM 看候选 list."""
    _setup_fake_registry(
        monkeypatch,
        tool_names=["mcp_serverA_foo", "mcp_serverB_foo"],
    )
    r = asyncio.run(adapter.dispatch_tool("foo", {}))
    assert r["ok"] is False
    assert "unknown tool: foo" in r["error"]
    # 候选有 2 个
    assert len(r["candidates"]) == 2
    assert "mcp_serverA_foo" in r["candidates"]
    assert "mcp_serverB_foo" in r["candidates"]


def test_dispatch_full_name_passes_through(monkeypatch):
    """LLM 调全名 (SOUL.md 学过的) 直接 dispatch 不走 fallback."""
    fake_reg = _setup_fake_registry(
        monkeypatch,
        tool_names=["mcp_catfish_local_search_local_search"],
    )
    asyncio.run(adapter.dispatch_tool(
        "mcp_catfish_local_search_local_search", {"query": "X"},
    ))
    fake_reg.dispatch.assert_called_once()
    assert fake_reg.dispatch.call_args[0][0] == \
        "mcp_catfish_local_search_local_search"
