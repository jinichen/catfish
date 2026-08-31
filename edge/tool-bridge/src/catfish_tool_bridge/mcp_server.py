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

from . import adapter, bootstrap, catfish_tools

logger = logging.getLogger("catfish.tool_bridge.mcp_server")


def _is_catfish_owned(name: str) -> bool:
    """这个 tool 是不是 catfish 自己的 (该通过 MCP 暴露给 hermes).

    BL-MCP-ECHO-HERMES-TOOLS (7/27): 见 list_tools 里的长注释.

    两类算 catfish 的:
      1. CATFISH_NATIVE_TOOLS —— 用 catfish_tools.is_native() 判, 不靠名字前缀猜
         (native 表里除了 catfish_* 还有别的命名, 硬编码前缀会漏)
      2. mcp_client 接进来的 connector tool —— 员工在 catfish 侧装的 MCP
         (飞书 / 高德 等), hermes 自己没有, 该透出去

    其余一律是 hermes registry 里的, 不往回喂.
    """
    if not name:
        return False
    try:
        if catfish_tools.is_native(name):
            return True
    except Exception:  # noqa: BLE001
        # is_native 挂了不该让整个 list_tools 崩 —— 退回名字前缀粗判
        if name.startswith("catfish_"):
            return True
    # catfish 侧 MCP connector: adapter.py:201 说名带 mcp_<connector>_ 前缀
    return name.startswith("mcp_")


def _visible_catfish_schemas(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """MCP 只暴露 Catfish 所有且在当前终端实际可用的工具。"""
    return [
        schema
        for schema in schemas
        if _is_catfish_owned(schema.get("name"))
        and schema.get("available") is not False
    ]


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

    async def list_tools() -> list[Tool]:
        """返 **catfish 自己的** tool. 不含 hermes registry 里的.

        adapter.list_tools() 返 OpenAI function calling 格式
        (`name` / `description` / `parameters` json schema), 我们包成 mcp Tool.

        # BL-MCP-ECHO-HERMES-TOOLS (7/27 鸿波实盘) — 为什么要过滤

        adapter.list_tools() 返三段 (adapter.py:190-229):
          ① catfish_tools.CATFISH_NATIVE_TOOLS      catfish 自己的 ~72 个
          ② mcp_client 的 connector tools           员工装的其它 MCP
          ③ hermes registry 里的**所有** tool        ~79 个

        第 ③ 段对 unix socket 那条路是**必要**的 —— Companion 要通过 tool-bridge
        调 hermes 的工具 (server.py 走同一个 adapter). 但 MCP 这条路的受众是
        **hermes 自己**, 把它自己的工具原路包一层送回去毫无意义:

            mcp__catfish_tools__browser_navigate    hermes 自己就有
            mcp__catfish_tools__terminal            gateway 还专门把 terminal 拉黑过
            mcp__catfish_tools__spotify_playback    同上

        实盘后果 (鸿波 7/28 00:34 gateway 日志):

            registered 151 tool(s)                  ← MCP 注册了 151 个
            tools_count=33
            ALL=[...30 个 hermes core..., 'tool_search', 'tool_describe', 'tool_call']

        hermes 的 progressive tool disclosure (tools/tool_search.py) 规则:
        「可延迟的工具若占到上下文的 threshold_pct (默认 10%) 以上, 就折叠成
        tool_search / tool_describe / tool_call 三个桥接工具; **hermes core
        工具永不延迟**」。151 个的 schema 体积撑过了阈值 → catfish 全部工具被
        折叠, 而 browser_navigate 这类 core 工具照样直出.

        于是 LLM 眼前摆着现成的 browser_navigate, catfish_browser_goto 藏在
        tool_search 后面要主动搜才拿得到 —— 它当然不绕这个弯. 结果就是浏览器
        一直走 hermes 那条会超时的老路, catfish 侧做的降级/fallback 全没机会执行.

        过滤掉第 ③ 段后 schema 体积腰斩, 大概率落回阈值以下不再折叠.
        即便仍折叠, 也不该由 catfish 把 hermes 的工具喂回给 hermes.

        注: 只影响 MCP transport. Companion 走的 unix socket (server.py) 仍拿
        完整列表, 行为不变.
        """
        try:
            schemas = adapter.list_tools()
        except Exception as e:  # noqa: BLE001
            logger.exception("list_tools 失败")
            sys.stderr.write(f"list_tools error: {e}\n")
            return []
        tools: list[Tool] = []
        visible_schemas = _visible_catfish_schemas(schemas)
        echoed_back = sum(
            1 for schema in schemas
            if schema.get("name") and not _is_catfish_owned(schema.get("name"))
        )
        unavailable = sum(
            1 for schema in schemas
            if _is_catfish_owned(schema.get("name"))
            and schema.get("available") is False
        )
        for s in visible_schemas:
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
        logger.info(
            "MCP list_tools: 返 %d 个 catfish 工具 (滤掉 %d 个 hermes registry、"
            "%d 个当前终端不可用工具, "
            "见 BL-MCP-ECHO-HERMES-TOOLS)",
            len(tools), echoed_back, unavailable,
        )
        return tools

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

    # MCP Python SDK 2.0 删除了低层 Server 上的 ``@list_tools`` /
    # ``@call_tool`` 装饰器，改成构造函数 handler。Hermes 0.20.6 首次带入
    # 这个版本；旧 Hermes 仍需保留装饰器路径，方便 Companion 跨版本升级。
    probe = Server("catfish-tool-bridge")
    if hasattr(probe, "list_tools"):
        app: Server = probe
        app.list_tools()(list_tools)
        app.call_tool()(call_tool)
    else:
        from mcp.types import (
            CallToolRequestParams,
            CallToolResult,
            ListToolsResult,
            PaginatedRequestParams,
        )

        async def list_tools_v2(
            _ctx: Any,
            _params: PaginatedRequestParams | None,
        ) -> ListToolsResult:
            return ListToolsResult(tools=await list_tools())

        async def call_tool_v2(
            _ctx: Any,
            params: CallToolRequestParams,
        ) -> CallToolResult:
            return CallToolResult(
                content=await call_tool(params.name, params.arguments),
            )

        app = Server(
            "catfish-tool-bridge",
            on_list_tools=list_tools_v2,
            on_call_tool=call_tool_v2,
        )

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
