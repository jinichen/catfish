"""BL-RECMODE-MIGRATE-TO-EDGE (5/25): gateway → tool-bridge Unix socket JSON-RPC client.

# 为什么有这个 module

5/25 鸿波 "录屏数据现在还有提交到中央的错误吗" audit 后, 把 RecMode 的
aggregator + selector_repair 整体搬到 edge/tool-bridge/. 中央代码不再直接读
录屏目录或写 skills 目录. 但 Companion 老代码继续打                  # noqa: BOUNDARY
`/api/learn/analyze` 这个 URL (gateway 端的 endpoint), 所以 gateway 端要做
thin proxy: 收 HTTP → 转 Unix socket JSON-RPC → 返结果.

# 当前部署假设 (gateway + tool-bridge co-located on 员工 Mac)

socket 默认 path: 员工 home 下 .catfish 目录里的 tool-bridge.sock (跟  # noqa: BOUNDARY
edge/companion-app catfish_paths::tool_bridge_socket 同源). gateway 跟
tool-bridge 都跑在员工 Mac, 直接走员工 home 下的 socket file.

# SaaS 化时

如果未来 gateway 真独立部署 (不在员工 Mac), 这个 proxy 会 fail-loud (socket file
不存在). 那时 Companion 必须直接调 tool-bridge (走 Tauri 命令 / MCP), 不能再
通过 gateway 中转. 现在的 thin proxy 是过渡, 强制下一次部署模式变化时改 Companion.

# Protocol

newline-delimited JSON, request/response 一一对应:
  request:  {"jsonrpc": "2.0", "id": <int>, "method": <str>, "params": <dict>}
  response: {"jsonrpc": "2.0", "id": <int>, "result": <any>}  或
            {"jsonrpc": "2.0", "id": <int>, "error": {"code": <int>, "message": <str>}}

JSON-RPC 错误码 (跟 server.py 同源):
  -32700 PARSE_ERROR
  -32600 INVALID_REQUEST
  -32601 METHOD_NOT_FOUND
  -32602 INVALID_PARAMS  → 我们转成 HTTP 400 / 404
  -32603 INTERNAL_ERROR  → 我们转成 HTTP 502 / 500
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.gateway.tool_bridge_rpc")

# 16MB readline limit — 跟 tool-bridge server.py 同源 (单条 base64 PNG ~10MB)
_LINE_LIMIT_BYTES = 16 * 1024 * 1024
_DEFAULT_TIMEOUT_S = 120.0  # vision LLM 综合可能慢, 给充足时间

# JSON-RPC 错误码 → HTTP 状态映射 (caller 用)
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INTERNAL_ERROR = -32603


def _socket_path() -> Path:
    """tool-bridge Unix socket 路径. env CATFISH_TOOL_BRIDGE_SOCK 覆盖 (测试用).

    默认是员工 home 下 .catfish/tool-bridge.sock — 跟 edge/companion-app  # noqa: BOUNDARY
    catfish_paths::tool_bridge_socket() 同源.

    BOUNDARY 注: 这是 SaaS 化前过渡期的 thin proxy, 中央 gateway 跟 edge
    tool-bridge co-located 在员工 Mac, 故 gateway 知道 socket 路径合理.
    SaaS 化时 _socket_path() 找不到文件会 fail-loud (ToolBridgeUnreachable),
    强制改 Companion 直连 tool-bridge.
    """
    if env := os.environ.get("CATFISH_TOOL_BRIDGE_SOCK"):
        return Path(env).expanduser()
    return Path.home() / ".catfish" / "tool-bridge.sock"  # noqa: BOUNDARY


class ToolBridgeRPCError(Exception):
    """tool-bridge 返了 error response. 含 JSON-RPC code + message."""

    def __init__(self, code: int, message: str):
        super().__init__(f"tool-bridge JSON-RPC error {code}: {message}")
        self.code = code
        self.message = message


class ToolBridgeUnreachable(Exception):
    """连不上 tool-bridge socket (没起 / 路径错 / 权限). caller 通常返 502."""


async def call(
    method: str,
    params: dict[str, Any],
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    socket_path: Path | None = None,
) -> Any:
    """调 tool-bridge JSON-RPC, 返 result 字段. error 抛 ToolBridgeRPCError.

    每次调一个新连接 (newline-delimited JSON 每行一个请求, 连接复用没必要
    — vision LLM 调用本身 100x 慢于 socket 建连).

    Raises:
        ToolBridgeUnreachable: 连接失败 (socket 不在 / refused)
        ToolBridgeRPCError: tool-bridge 返了 error 响应
        asyncio.TimeoutError: 等响应超时
    """
    sp = socket_path or _socket_path()
    if not sp.exists():
        raise ToolBridgeUnreachable(
            f"tool-bridge socket 不存在 ({sp}). 确认 catfish-tool-bridge 启动了."
        )

    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }
    request_bytes = (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")

    try:
        reader, writer = await asyncio.open_unix_connection(path=str(sp), limit=_LINE_LIMIT_BYTES)
    except (FileNotFoundError, ConnectionRefusedError, PermissionError) as e:
        raise ToolBridgeUnreachable(f"open {sp} 失败: {e}") from e

    try:
        writer.write(request_bytes)
        await writer.drain()

        try:
            line = await asyncio.wait_for(reader.readline(), timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.warning("tool-bridge %s 超时 %.0fs", method, timeout_s)
            raise

        if not line:
            raise ToolBridgeUnreachable("tool-bridge 关闭了连接没回响应")

        try:
            resp = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ToolBridgeRPCError(JSONRPC_INTERNAL_ERROR, f"tool-bridge 返非 JSON: {e}") from e

        if "error" in resp:
            err = resp["error"]
            raise ToolBridgeRPCError(int(err.get("code", JSONRPC_INTERNAL_ERROR)),
                                     str(err.get("message", "unknown")))

        if "result" not in resp:
            raise ToolBridgeRPCError(JSONRPC_INTERNAL_ERROR,
                                     f"tool-bridge 响应缺 result/error 字段: {resp}")

        return resp["result"]
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


__all__ = [
    "call",
    "ToolBridgeRPCError",
    "ToolBridgeUnreachable",
    "JSONRPC_INVALID_PARAMS",
    "JSONRPC_METHOD_NOT_FOUND",
    "JSONRPC_INTERNAL_ERROR",
]
