"""把 catfish-search 暴露成 MCP tool。

暴露出去的 tool 名字叫 `local_search`，参数简单、描述清晰，让 Hermes / 其他
MCP 客户端第一眼就能看出"哦这是用来找本地文件的"，跟 `search_files` / `read_file`
之类原生 tool 平级，不需要模型额外"去翻 skill 文档"。

启动方式（stdio，被 Hermes fork 出来的子进程）：
    catfish-search-mcp
    # 等价于：
    python -m catfish_search.mcp_server

协议：MCP 2024-11-05 stdio。每条 JSON-RPC 消息一行。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from typing import Any

logger = logging.getLogger("catfish.search.mcp")


def _try_import_mcp():
    try:
        from mcp.server import Server  # noqa: PLC0415
        from mcp.server.stdio import stdio_server  # noqa: PLC0415
        from mcp.types import TextContent, Tool  # noqa: PLC0415
    except ImportError as e:
        raise RuntimeError(
            "mcp 未安装。请 pip install 'mcp>=1.0' 或 pip install 'catfish-local-search[mcp]'。"
        ) from e
    return Server, stdio_server, TextContent, Tool


def _build_server():
    server_cls, stdio_server, text_content_cls, tool_cls = _try_import_mcp()

    server = server_cls("catfish-local-search")

    @server.list_tools()  # type: ignore[misc]
    async def list_tools() -> list[Any]:
        return [
            tool_cls(
                name="local_search",
                description=(
                    "员工本地 Mac 全文搜索。找文件、合同、文档、笔记、代码、"
                    "Office 文件、PDF 时必选此工具。基于 SQLite FTS5 trigram 索引，"
                    "100ms 内返回，支持 40+ 种文件格式，完全离线不联网。"
                    "典型触发：找文件、找合同、找上次那份、我电脑里有没有、"
                    "本地有没有、上周那份文档。优先用这个工具，"
                    "不要用 search_files / find / grep 全盘扫（那些要 30~60 秒）。"
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索关键词。中文英文都行，多词用空格隔开。",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "返回前 N 条结果，默认 10。",
                            "default": 10,
                            "minimum": 1,
                            "maximum": 50,
                        },
                    },
                    "required": ["query"],
                },
            ),
            tool_cls(
                name="local_search_status",
                description=(
                    "查看本地索引库状态：总文件数、按类型分布、索引库大小。"
                    "典型触发：索引建好了吗、索引里有多少文件、本地搜索能用吗。"
                ),
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    @server.call_tool()  # type: ignore[misc]
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[Any]:
        if name == "local_search":
            return [text_content_cls(type="text", text=_run_query(arguments))]
        if name == "local_search_status":
            return [text_content_cls(type="text", text=_run_status())]
        raise ValueError(f"unknown tool: {name}")

    return server, stdio_server


def _catfish_bin() -> str:
    """找到 catfish-search 可执行文件路径，支持 PATH 覆盖。"""
    return os.environ.get("CATFISH_SEARCH_BIN", "catfish-search")


def _run_query(arguments: dict[str, Any]) -> str:
    query = str(arguments.get("query", "")).strip()
    if not query:
        return json.dumps({"error": "query is required"}, ensure_ascii=False)

    limit = int(arguments.get("limit", 10))
    limit = max(1, min(50, limit))  # clamp 到 [1, 50]

    try:
        r = subprocess.run(  # noqa: S603
            [_catfish_bin(), "query", "--json", "-n", str(limit), query],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return json.dumps(
            {
                "error": "catfish-search not found on PATH",
                "hint": "install via `pip install catfish-local-search` or set CATFISH_SEARCH_BIN",
            },
            ensure_ascii=False,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "catfish-search query timed out after 30s"}, ensure_ascii=False)

    if r.returncode != 0:
        return json.dumps(
            {"error": "catfish-search returned non-zero", "stderr": r.stderr[:500]},
            ensure_ascii=False,
        )
    return r.stdout or "[]"


def _run_status() -> str:
    try:
        r = subprocess.run(  # noqa: S603
            [_catfish_bin(), "status"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except FileNotFoundError:
        return json.dumps({"error": "catfish-search not found on PATH"}, ensure_ascii=False)
    return r.stdout or r.stderr or "(empty)"


async def _async_main() -> None:
    server, stdio_server = _build_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    """同步入口，供 pyproject scripts / `python -m` 使用。"""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "WARNING"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
