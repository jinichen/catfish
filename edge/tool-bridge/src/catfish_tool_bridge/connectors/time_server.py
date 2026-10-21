"""「时间 / 时区」MCP 连接器 —— 随安装包发, 离线可用 (10/22)。

原来中央 manifest 让员工机 `uvx mcp-server-time` 现场从 PyPI 下: 内网装不了; 而且它的各个
版本要么钉 `mcp<2`、要么要 `mcp>=2.3`, 跟 hermes 钉的 mcp 2.0.0 都不兼容 (2026.7.10 那版
import 就崩)。这里直接说 MCP 的 JSON-RPC (stdio, 一行一条), 不依赖 mcp SDK 版本, 工具名和
参数跟官方 mcp-server-time 一致。

启动: `python -m catfish_tool_bridge.connectors.time_server [--local-timezone Asia/Shanghai]`
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

PROTOCOL_VERSION = "2025-06-18"

TOOLS = [
    {
        "name": "get_current_time",
        "description": "取某个时区的当前时间 (IANA 时区名, 如 Asia/Shanghai、America/New_York)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "timezone": {"type": "string", "description": "IANA 时区名; 不填用本机时区"},
            },
        },
    },
    {
        "name": "convert_time",
        "description": "把某时区的一个时刻 (HH:MM, 24 小时制, 今天) 换算到另一个时区",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_timezone": {"type": "string", "description": "源时区 IANA 名"},
                "time": {"type": "string", "description": "HH:MM"},
                "target_timezone": {"type": "string", "description": "目标时区 IANA 名"},
            },
            "required": ["source_timezone", "time", "target_timezone"],
        },
    },
]


def _zone(name: str | None, local: str | None) -> Any:
    if name:
        return ZoneInfo(name)
    if local:
        return ZoneInfo(local)
    return datetime.now().astimezone().tzinfo


def _describe(dt: datetime) -> dict[str, Any]:
    return {
        "timezone": getattr(dt.tzinfo, "key", None) or dt.tzname(),
        "datetime": dt.isoformat(timespec="seconds"),
        "weekday": dt.strftime("%A"),
        "is_dst": bool(dt.dst()),
    }


def get_current_time(args: dict[str, Any], local: str | None) -> dict[str, Any]:
    return _describe(datetime.now(_zone(args.get("timezone"), local)))


def convert_time(args: dict[str, Any], local: str | None) -> dict[str, Any]:
    source = _zone(args["source_timezone"], local)
    target = _zone(args["target_timezone"], local)
    hour, minute = (int(x) for x in str(args["time"]).split(":", 1))
    src = datetime.now(source).replace(hour=hour, minute=minute, second=0, microsecond=0)
    dst = src.astimezone(target)
    diff = (dst.utcoffset() or timedelta()) - (src.utcoffset() or timedelta())
    hours = diff.total_seconds() / 3600
    return {
        "source": _describe(src),
        "target": _describe(dst),
        "time_difference": f"{hours:+g}h",
    }


HANDLERS = {"get_current_time": get_current_time, "convert_time": convert_time}


def handle(msg: dict[str, Any], local: str | None) -> dict[str, Any] | None:
    """一条请求 → 一条响应; 通知 (没 id) 返 None。"""
    method = msg.get("method")
    if "id" not in msg:
        return None
    reply: dict[str, Any] = {"jsonrpc": "2.0", "id": msg["id"]}
    if method == "initialize":
        reply["result"] = {
            "protocolVersion": (msg.get("params") or {}).get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "catfish-time", "version": "1.0.0"},
        }
    elif method == "tools/list":
        reply["result"] = {"tools": TOOLS}
    elif method == "tools/call":
        params = msg.get("params") or {}
        fn = HANDLERS.get(params.get("name"))
        if fn is None:
            reply["error"] = {"code": -32602, "message": f"未知工具: {params.get('name')}"}
        else:
            try:
                data = fn(params.get("arguments") or {}, local)
                reply["result"] = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]}
            except Exception as e:  # noqa: BLE001 —— 参数错 / 时区名错, 告诉模型而不是崩
                reply["result"] = {"content": [{"type": "text", "text": f"出错: {e}"}], "isError": True}
    elif method == "ping":
        reply["result"] = {}
    else:
        reply["error"] = {"code": -32601, "message": f"不支持的方法: {method}"}
    return reply


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    local = None
    if "--local-timezone" in argv:
        i = argv.index("--local-timezone")
        local = argv[i + 1] if i + 1 < len(argv) else None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        reply = handle(msg, local)
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
