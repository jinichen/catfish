"""Unix socket JSON-RPC 2.0 server。

协议:
    newline-delimited JSON。每行一个 JSON-RPC 请求或响应。
    socket 文件 chmod 0600，只员工自己能连。

支持方法:
    tools/list       → 列所有 tool schema
    tools/dispatch   → {name, args} → 执行结果
    health           → 探活 + tool 数

错误响应:
    {"jsonrpc":"2.0","id":<id>,"error":{"code":<int>,"message":<str>}}
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

from . import adapter, bootstrap

logger = logging.getLogger("catfish.tool_bridge.server")

# JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _success(req_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


async def _handle_request(req: Dict[str, Any]) -> Dict[str, Any]:
    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}

    if method == "tools/list":
        return _success(req_id, adapter.list_tools())

    if method == "tools/dispatch":
        name = params.get("name")
        args = params.get("args") or {}
        if not name:
            return _error(req_id, INVALID_PARAMS, "params.name 必填")
        result = await adapter.dispatch_tool(name, args)
        return _success(req_id, result)

    if method == "health":
        return _success(req_id, adapter.health())

    return _error(req_id, METHOD_NOT_FOUND, f"unknown method: {method}")


async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = "unix-client"
    logger.info("client connected: %s", peer)
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            line_text = line.decode("utf-8", errors="replace").strip()
            if not line_text:
                continue
            try:
                req = json.loads(line_text)
            except json.JSONDecodeError as e:
                resp = _error(None, PARSE_ERROR, f"JSON parse: {e}")
            else:
                if not isinstance(req, dict) or req.get("jsonrpc") != "2.0":
                    resp = _error(req.get("id") if isinstance(req, dict) else None,
                                  INVALID_REQUEST, "需要 jsonrpc=2.0")
                else:
                    try:
                        resp = await _handle_request(req)
                    except Exception as e:
                        logger.exception("handler crash")
                        resp = _error(req.get("id"), INTERNAL_ERROR, str(e))

            line_out = json.dumps(resp, ensure_ascii=False) + "\n"
            writer.write(line_out.encode("utf-8"))
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        logger.info("client disconnected: %s", peer)


async def serve_forever(socket_path: Path) -> None:
    # 每次启动先把残留 socket 清掉(防上次 crash 留下的死文件)
    if socket_path.exists():
        try:
            socket_path.unlink()
        except OSError:
            pass

    socket_path.parent.mkdir(parents=True, exist_ok=True)

    server = await asyncio.start_unix_server(_handle_client, str(socket_path))
    os.chmod(socket_path, 0o600)  # 只员工自己能连
    logger.info("listening on %s", socket_path)

    print("─" * 60, flush=True)
    print("🐟 catfish-tool-bridge", flush=True)
    print(f"   socket : {socket_path}", flush=True)
    print(f"   tools  : {len(adapter._r().get_all_tool_names())} 个", flush=True)
    print("─" * 60, flush=True)

    async with server:
        await server.serve_forever()


def init_and_serve(socket_path: Path) -> None:
    """同步入口 —— bootstrap + 起 server"""
    registry_module = bootstrap.bootstrap()
    adapter.install_registry(registry_module)
    asyncio.run(serve_forever(socket_path))
