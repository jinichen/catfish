"""11/06 hermes v0.23: browser_exec (本机跑任意 Python) 不给模型, 不管同一批里有没有 catfish_browser_*。"""
from catfish_gateway.tools_sanitizer import sanitize_tools


def _tool(name):
    return {"type": "function", "function": {"name": name, "description": name, "parameters": {"type": "object", "properties": {}}}}


def _names(body):
    return [t["function"]["name"] for t in body.get("tools") or []]


def test_browser_exec_alone_is_dropped():
    body = {"model": "x", "messages": [], "tools": [_tool("browser_exec"), _tool("execute_code")]}
    sanitize_tools(body)
    assert "browser_exec" not in _names(body)
    assert "execute_code" in _names(body)


def test_browser_exec_mcp_wrapped_is_dropped_too():
    body = {"model": "x", "messages": [], "tools": [_tool("mcp__catfish_tools__browser_exec"), _tool("execute_code")]}
    sanitize_tools(body)
    assert "mcp__catfish_tools__browser_exec" not in _names(body)
