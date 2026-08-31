"""BL-MCP-ECHO-HERMES-TOOLS (7/27 鸿波实盘): MCP 只暴露 catfish 自己的工具.

# 这条测试守的是什么

catfish 的 MCP server 曾把 hermes registry 里的工具也包一层送回给 hermes:

    mcp__catfish_tools__browser_navigate     hermes 自己就有
    mcp__catfish_tools__terminal             gateway 还专门把 terminal 拉黑过
    mcp__catfish_tools__spotify_playback     同上

一共 151 个。后果不是"多几个工具"那么轻 —— hermes 的 progressive tool
disclosure (tools/tool_search.py) 规定「可延迟工具占到上下文 10% 以上就折叠成
tool_search / tool_describe / tool_call 三个桥接工具，hermes core 工具永不延迟」。

151 个的 schema 撑过了阈值，实测 (7/28 00:34 gateway 日志):

    tools_count=33
    ALL=[...30 个 hermes core..., 'tool_search', 'tool_describe', 'tool_call']

catfish 全部工具被折叠，而 browser_navigate 这类 core 工具照样直出。LLM 眼前
摆着现成的 browser_navigate，catfish_browser_goto 藏在 tool_search 后面要主动
搜才拿得到 —— 它当然不绕这个弯。浏览器于是一直走 hermes 那条会超时的老路，
catfish 侧做的降级 / fallback 全没机会执行。

根因在 adapter.list_tools() 返回三段，其中第 ③ 段是 hermes registry 的全部工具。
那一段对 unix socket 路径 (Companion 调 hermes 工具) 是必要的，对 MCP 路径
(受众就是 hermes 自己) 是有害的 —— 同一份清单喂给两种受众。
"""
from __future__ import annotations

from catfish_tool_bridge import catfish_tools
from catfish_tool_bridge.mcp_server import (
    _is_catfish_owned,
    _visible_catfish_schemas,
)


def test_keeps_all_catfish_native_tools():
    """CATFISH_NATIVE_TOOLS 一个都不能漏 —— 那是 MCP 存在的全部意义."""
    missed = [
        t["name"]
        for t in catfish_tools.CATFISH_NATIVE_TOOLS
        if not _is_catfish_owned(t["name"])
    ]
    assert missed == [], f"这些 catfish 原生工具被误滤了: {missed}"


def test_drops_hermes_registry_tools():
    """hermes 自己的工具不往回喂 —— 这些名字取自鸿波 agent.log 里真实注册的 151 个."""
    for name in (
        "browser_navigate", "browser_cdp", "browser_vision", "browser_back",
        "terminal", "read_terminal", "execute_code", "memory", "patch",
        "read_file", "write_file", "search_files", "session_search",
        "skills_list", "todo", "web_search", "web_extract",
        "spotify_playback", "kanban_create", "feishu_doc_read", "ha_get_state",
    ):
        assert not _is_catfish_owned(name), f"{name} 是 hermes 的，不该经 MCP 送回去"


def test_keeps_catfish_side_mcp_connectors():
    """员工在 catfish 侧装的 MCP connector 要透出去 —— hermes 自己没有这些.

    adapter.py:201 说这类名带 mcp_<connector>_ 前缀。
    """
    assert _is_catfish_owned("mcp_amap_geocode")
    assert _is_catfish_owned("mcp_feishu_send")


def test_browser_tool_namespace_is_disambiguated():
    """catfish_browser_* 留、hermes browser_* 丢 —— 这一对是本 BL 的核心症状.

    两者同时暴露时 LLM 会挑 hermes 那个 (名字短、训练分布里常见)，而那条路
    没有 load 超时降级、要经 agent-browser 外部 CLI、还吃写死的 cdp_url。
    """
    assert _is_catfish_owned("catfish_browser_goto")
    assert _is_catfish_owned("catfish_browser_evaluate")
    assert _is_catfish_owned("catfish_browser_screenshot")
    assert not _is_catfish_owned("browser_navigate")
    assert not _is_catfish_owned("browser_snapshot")


def test_handles_garbage_input():
    """空名 / None 不该炸 —— list_tools 里每个 schema 都会过这一遍."""
    assert not _is_catfish_owned("")
    assert not _is_catfish_owned(None)


def test_filter_shrinks_tool_count_materially():
    """过滤后规模要显著变小，否则压不下 progressive disclosure 的阈值.

    实盘 151 → 期望 ~72(catfish native)+ 少量 connector。
    """
    sample = [t["name"] for t in catfish_tools.CATFISH_NATIVE_TOOLS] + [
        "browser_navigate", "terminal", "execute_code", "memory", "read_file",
        "write_file", "web_search", "spotify_playback", "kanban_list",
    ]
    kept = [n for n in sample if _is_catfish_owned(n)]
    assert len(kept) == len(catfish_tools.CATFISH_NATIVE_TOOLS)
    assert len(kept) < len(sample), "过滤必须真的减少数量"


def test_mcp_hides_runtime_unavailable_catfish_tools():
    schemas = [
        {"name": "catfish_today_summary", "available": True},
        {
            "name": "catfish_list_reminders",
            "available": False,
            "reason_code": "unsupported_platform",
        },
        {"name": "browser_navigate", "available": True},
    ]

    kept = _visible_catfish_schemas(schemas)

    assert [schema["name"] for schema in kept] == ["catfish_today_summary"]
