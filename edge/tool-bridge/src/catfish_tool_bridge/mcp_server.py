"""catfish-tool-bridge MCP server (stdio transport).

# 为啥要这个

5/19 凌晨 BL-TOOL-BRIDGE-HERMES-INTEGRATION (#28): hermes 切到 API server
模式后 hermes 内部 agent loop 想调 catfish_* tool (catfish_email_search /
catfish_run_skill / 等), 但**hermes 看不到** catfish-tool-bridge — 因为
tool-bridge 当前是 Unix socket JSON-RPC 给 Companion 用的, 不是 MCP server.

这个 module 包 catfish-tool-bridge 的 adapter.list_tools / dispatch_tool 成
**MCP server (stdio transport)**, 让 hermes 通过 `hermes mcp add` 注册接入,
然后 hermes agent loop 就能调所有 catfish_* tool.

# 启动

直接 stdio (hermes 调用时):
    python -m catfish_tool_bridge.mcp_server

注册到 hermes:
    hermes mcp add catfish-tools \\
        --command "$(which python3)" \\
        --args "-m" "catfish_tool_bridge.mcp_server"

# 协议

跟 hermes 一样用 MCP (Anthropic Model Context Protocol) over stdio:
  - server 启动后, stdin 收 JSON-RPC list_tools / call_tool 请求
  - stdout 返 JSON-RPC response
  - tool schema 跟 OpenAI function calling 格式兼容 (adapter.list_tools()
    返的就是 OpenAI 格式, 包成 mcp.types.Tool 即可)

# 安全

- 跟 catfish-tool-bridge unix socket 同等权限 (员工本机, OS 级隔离)
- 不走网络, stdio 进程间通信
- tool 内部 dispatch_tool 自己做 sandbox / audit (跟现 Companion 路径一样)

# 跟现 unix socket server 的关系

并存. unix socket server 给 Companion 走 (Companion 老路径, 通过
spawn_detached 起 server.py). MCP server 给 hermes 走 (hermes 通过 mcp add
启 stdio 进程). 同一份 adapter / catfish_tools 代码, 两个 transport.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict

from . import adapter, bootstrap

logger = logging.getLogger("catfish.tool_bridge.mcp_server")


def _init_catfish_tool_bridge() -> None:
    """bootstrap hermes registry + 注入 adapter. 跟 server.py:init_and_serve 同顺序.

    注意: catfish 自定义 tool (CATFISH_NATIVE_TOOLS) 不需要单独 register —
    adapter.list_tools() 自动包含它们 (catfish_tools.CATFISH_NATIVE_TOOLS), 跟
    server.py 走完全同路径.
    """
    registry_module = bootstrap.bootstrap()
    adapter.install_registry(registry_module)
    logger.info("catfish-tool-bridge MCP server bootstrapped")


async def amain() -> None:
    _init_catfish_tool_bridge()

    # 用 mcp Python SDK (hermes venv 自带). 走 stdio transport.
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import TextContent, Tool
    except ImportError as e:
        sys.stderr.write(
            f"catfish-tool-bridge MCP server: 缺 mcp Python SDK ({e}). "
            "用 hermes venv 跑: ~/.hermes/hermes-agent/venv/bin/python -m "
            "catfish_tool_bridge.mcp_server\n"
        )
        sys.exit(2)

    app: Server = Server("catfish-tool-bridge")

    @app.list_tools()
    async def list_tools() -> list[Tool]:
        """返 catfish-tool-bridge 全部 tool. 跟 adapter.list_tools() 返一样.

        adapter.list_tools() 返 OpenAI function calling 格式
        (`name` / `description` / `parameters` json schema), 我们包成 mcp Tool.
        """
        try:
            schemas = adapter.list_tools()
        except Exception as e:  # noqa: BLE001
            logger.exception("list_tools 失败")
            sys.stderr.write(f"list_tools error: {e}\n")
            return []
        tools: list[Tool] = []
        for s in schemas:
            name = s.get("name")
            if not name:
                continue
            # adapter.list_tools() 返的 schema 用 input_schema (Anthropic/MCP
            # 格式); 老 OpenAI 格式叫 parameters. 双 fallback 容错.
            input_schema = (
                s.get("input_schema")
                or s.get("parameters")
                or {"type": "object"}
            )
            tools.append(
                Tool(
                    name=name,
                    description=s.get("description") or "",
                    inputSchema=input_schema,
                )
            )
        logger.info("MCP list_tools: 返 %d tools", len(tools))
        return tools

    @app.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any] | None) -> list[TextContent]:
        """dispatch 一个 tool. 返 TextContent (JSON 字符串).

        adapter.dispatch_tool 返 `{"ok", "result", "error", "tool", "stderr"}`
        dict. 我们 JSON 序列化给 LLM 看. LLM 解析后就知道成功/失败 + 拿
        result. 跟 Companion 那边 dispatch 同 shape, 行为一致.
        """
        args = arguments or {}
        try:
            result = await adapter.dispatch_tool(name, args)
        except Exception as e:  # noqa: BLE001
            logger.exception("dispatch_tool 失败: %s", name)
            result = {
                "ok": False,
                "result": None,
                "error": f"catfish-tool-bridge MCP server 内部错: {e}",
                "tool": name,
                "stderr": None,
            }
        text = json.dumps(result, ensure_ascii=False)
        return [TextContent(type="text", text=text)]

    # 跑 stdio server
    async with stdio_server() as (read_stream, write_stream):
        init_opts = app.create_initialization_options()
        await app.run(read_stream, write_stream, init_opts)


def main() -> int:
    # 日志走 stderr (stdout 是 MCP 协议数据, 不能污染)
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        asyncio.run(amain())
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"catfish-tool-bridge MCP server crashed: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
