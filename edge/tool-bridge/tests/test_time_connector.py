"""随包时间连接器 (10/22): 说 MCP JSON-RPC, 工具名/参数跟官方 mcp-server-time 一致。"""
from __future__ import annotations

import json

from catfish_tool_bridge.connectors import time_server as ts


def test_initialize_and_list():
    init = ts.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}, None)
    assert init["result"]["capabilities"] == {"tools": {}}
    tools = ts.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, None)["result"]["tools"]
    assert [t["name"] for t in tools] == ["get_current_time", "convert_time"]
    assert ts.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, None) is None


def _call(name, args, local=None):
    r = ts.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": name, "arguments": args}}, local)
    return r["result"]


def test_convert_time_shanghai_to_london():
    res = _call("convert_time", {"source_timezone": "Asia/Shanghai", "time": "16:00", "target_timezone": "Europe/London"})
    data = json.loads(res["content"][0]["text"])
    assert data["source"]["datetime"].endswith("16:00:00+08:00")
    assert data["time_difference"] in ("-8h", "-7h")  # 伦敦冬/夏令时


def test_bad_timezone_is_tool_error_not_crash():
    res = _call("get_current_time", {"timezone": "Mars/Base"})
    assert res["isError"] is True


def test_local_timezone_flag():
    data = json.loads(_call("get_current_time", {}, local="Asia/Tokyo")["content"][0]["text"])
    assert data["timezone"] == "Asia/Tokyo"
