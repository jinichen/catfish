"""BL-D3 Phase 3 + 3.1 (5/9): mcp_client 单测.

跑真 subprocess + JSON-RPC 集成测要 mac + uvx 装好, 留给鸿波 mac 端验. 这里测
**纯函数**: 名前缀解析 / connector 发现 / manifest env 解析逻辑.
"""
from __future__ import annotations

from catfish_tool_bridge import mcp_client


def setup_function(_):
    """每个 test 前清空 _CLIENTS, 不互相污染."""
    mcp_client._CLIENTS.clear()


# ─── _strip_prefix / is_mcp_tool ─────────────────────────────────────


def test_is_mcp_tool_unknown_returns_false():
    assert mcp_client.is_mcp_tool("catfish_remember") is False
    assert mcp_client.is_mcp_tool("execute_code") is False


def test_is_mcp_tool_no_registered_returns_false():
    """前缀对但 connector 没注册 → False (不接受任意 mcp_xxx_yyy)."""
    assert mcp_client.is_mcp_tool("mcp_jira_search") is False


def test_is_mcp_tool_after_register():
    """register 后 is_mcp_tool 识别."""
    fake = mcp_client.ConnectorClient("time", ["uvx", "mcp-server-time"])
    fake.tools = [{"name": "get_current_time", "description": "..."}]
    mcp_client._CLIENTS["time"] = fake

    assert mcp_client.is_mcp_tool("mcp_time_get_current_time") is True
    assert mcp_client.is_mcp_tool("mcp_time_convert_time") is True
    assert mcp_client.is_mcp_tool("mcp_jira_search") is False  # 没注册
    assert mcp_client.is_mcp_tool("mcp_") is False  # 空前缀


def test_strip_prefix_parses_correctly():
    fake = mcp_client.ConnectorClient("time", [])
    mcp_client._CLIENTS["time"] = fake
    assert mcp_client._strip_prefix("mcp_time_get_current_time") == ("time", "get_current_time")
    assert mcp_client._strip_prefix("mcp_time_convert_time") == ("time", "convert_time")


def test_strip_prefix_multi_underscore_connector():
    """connector_id 含下划线时正确切."""
    fake = mcp_client.ConnectorClient("github_enterprise", [])
    mcp_client._CLIENTS["github_enterprise"] = fake
    # mcp_<github_enterprise>_<list_repos> → 切对
    assert mcp_client._strip_prefix("mcp_github_enterprise_list_repos") == (
        "github_enterprise", "list_repos",
    )


# ─── list_all_tools_as_native_schema ────────────────────────────────


def test_list_all_tools_empty():
    assert mcp_client.list_all_tools_as_native_schema() == []


def test_list_all_tools_with_registered():
    """注册后 list 返 catfish 原生 tool schema 形态."""
    fake = mcp_client.ConnectorClient("time", [])
    fake.tools = [
        {
            "name": "get_current_time",
            "description": "Get current time in timezone",
            "inputSchema": {
                "type": "object",
                "properties": {"timezone": {"type": "string"}},
            },
        }
    ]
    mcp_client._CLIENTS["time"] = fake

    out = mcp_client.list_all_tools_as_native_schema()
    assert len(out) == 1
    item = out[0]
    assert item["name"] == "mcp_time_get_current_time"
    assert "MCP time" in item["description"]
    assert item["toolset"] == "mcp_time"
    assert item["available"] is True
    assert item["emoji"] == "🔌"
    assert item["input_schema"]["type"] == "object"


def test_list_all_tools_skips_empty_name():
    """tool 名空 → 跳过, 防注入坏数据."""
    fake = mcp_client.ConnectorClient("time", [])
    fake.tools = [
        {"name": "", "description": "..."},
        {"name": "get_current_time", "description": "ok"},
    ]
    mcp_client._CLIENTS["time"] = fake

    out = mcp_client.list_all_tools_as_native_schema()
    assert len(out) == 1


def test_list_all_tools_default_input_schema():
    """tool 没 inputSchema → 默认 object empty."""
    fake = mcp_client.ConnectorClient("time", [])
    fake.tools = [{"name": "get_current_time", "description": "..."}]
    mcp_client._CLIENTS["time"] = fake

    out = mcp_client.list_all_tools_as_native_schema()
    assert out[0]["input_schema"] == {"type": "object", "properties": {}}


# ─── _default_command_for ────────────────────────────────────────────


def test_default_command_unknown_returns_none():
    cmd, _env = mcp_client._default_command_for("nonexistent")
    assert cmd is None


def test_default_command_time_with_uvx(monkeypatch):
    """time + 有 uvx → 返启动命令."""
    import shutil  # noqa: PLC0415
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/uvx")
    cmd, _env = mcp_client._default_command_for("time")
    assert cmd is not None
    assert "mcp-server-time" in cmd


def test_default_command_time_without_uvx(monkeypatch):
    import shutil  # noqa: PLC0415
    monkeypatch.setattr(shutil, "which", lambda _: None)
    cmd, _env = mcp_client._default_command_for("time")
    assert cmd is None
