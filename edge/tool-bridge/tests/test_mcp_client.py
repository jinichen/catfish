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


# ─── command_for_manifest (10/22) ─────────────────────────────────────


def test_python_type_uses_current_interpreter():
    """type python → 本进程的 Python -m 模块 (hermes venv, 随安装包, 离线)。"""
    import sys  # noqa: PLC0415
    cmd, env = mcp_client.command_for_manifest(
        {"id": "time", "mcp_command": {"type": "python", "module": "x.y", "args": ["--a"]}})
    assert cmd == [sys.executable, "-m", "x.y", "--a"]
    # hermes bootstrap 会改写本进程 PYTHONPATH —— 子进程必须显式带上 tool-bridge 的 src
    src = str(__import__("pathlib").Path(mcp_client.__file__).resolve().parents[1])
    assert env["PYTHONPATH"].split(__import__("os").pathsep)[0] == src


def test_uvx_falls_back_to_uv_tool_run(monkeypatch):
    """只有 uv 没有 uvx: 原来拼成 `uv <包>` (不是合法命令), 现在是 `uv tool run <包>`。"""
    monkeypatch.setattr(mcp_client.shutil, "which", lambda n: "/bin/uv" if n == "uv" else None)
    cmd, _ = mcp_client.command_for_manifest({"id": "x", "mcp_command": {"type": "uvx", "package": "p"}})
    assert cmd == ["/bin/uv", "tool", "run", "p"]


def test_credential_env_is_skipped():
    """要凭据的 (${SECRET_REF:..}) 员工机还接不了 → 不起, 而不是带着空凭据起。"""
    assert mcp_client.command_for_manifest({"id": "jira", "mcp_command": {
        "type": "python", "module": "m", "env": {"TOKEN": "${SECRET_REF:jira}"}}}) is None


def test_python_type_without_module_skipped():
    assert mcp_client.command_for_manifest({"id": "x", "mcp_command": {"type": "python"}}) is None


# ─── sync_subscriptions: 跑真的随包时间连接器 (10/22) ───────────────────

TIME_MANIFEST = {"id": "time", "mcp_command": {
    "type": "python", "module": "catfish_tool_bridge.connectors.time_server"}}


def _fake_gateway(monkeypatch, subscribed: list[str]):
    def fake(path, _token, timeout=8):
        if path.startswith("/v1/mcp/subscribed"):
            return {"subscriptions": [{"connector_id": c} for c in subscribed]}
        if path == "/v1/mcp/manifest/time":
            return {"manifest": TIME_MANIFEST}
        raise RuntimeError(f"404 {path}")
    monkeypatch.setattr(mcp_client, "_http_get_json", fake)
    monkeypatch.setattr(mcp_client, "_user_token", lambda: "tok")


def test_sync_starts_subscribed_then_stops_unsubscribed(monkeypatch):
    import asyncio  # noqa: PLC0415
    import os  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415
    src = str(Path(__file__).resolve().parents[1] / "src")
    monkeypatch.setenv("PYTHONPATH", src + os.pathsep + os.environ.get("PYTHONPATH", ""))
    mcp_client._FAILED_AT.clear()

    async def run():
        _fake_gateway(monkeypatch, ["time"])
        assert await mcp_client.sync_subscriptions() is True
        names = [t["name"] for t in mcp_client.list_all_tools_as_native_schema()]
        assert names == ["mcp_time_get_current_time", "mcp_time_convert_time"]
        out = await mcp_client.dispatch_mcp_tool("mcp_time_get_current_time", {"timezone": "Asia/Shanghai"})
        assert "+08:00" in out["text"]
        assert await mcp_client.sync_subscriptions() is False, "没变化就不该通知 hermes 刷新"

        _fake_gateway(monkeypatch, [])
        assert await mcp_client.sync_subscriptions() is True
        assert mcp_client.list_all_tools_as_native_schema() == []

    asyncio.run(run())


def test_sync_without_login_keeps_state(monkeypatch):
    import asyncio  # noqa: PLC0415
    monkeypatch.setattr(mcp_client, "_user_token", lambda: None)
    assert asyncio.run(mcp_client.sync_subscriptions()) is False


def test_sync_unknown_connector_backs_off(monkeypatch):
    """manifest 拉不到 → 记失败时间, 10 分钟内不再每轮重试。"""
    import asyncio  # noqa: PLC0415
    mcp_client._FAILED_AT.clear()
    _fake_gateway(monkeypatch, ["gone"])
    assert asyncio.run(mcp_client.sync_subscriptions()) is False
    assert "gone" in mcp_client._FAILED_AT


def test_gateway_url_falls_back_to_hermes_env(monkeypatch, tmp_path):
    """hermes 起的 MCP 子进程 env 里没有网关地址 → 读 hermes .env。"""
    for k in ("CATFISH_MCP_REGISTRY_URL", "CATFISH_GATEWAY_URL"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / ".env").write_text("X=1\nCATFISH_GATEWAY_URL=http://10.0.0.8:8999/\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert mcp_client._gateway_url() == "http://10.0.0.8:8999"
