"""mcp_client.py — BL-D3 Phase 3 (5/9 ship): tool-bridge 接 MCP server (stdio JSON-RPC).

# 为啥要这模块

5/9 BL-D3 Phase 1+2 ship 完: registry / 订阅 / OAuth / Companion Card 都通了.
但**Agent 真用 MCP server 拿数据**这一步缺位 — LLM 看不到订阅的 mcp tools.

Phase 3 真招: tool-bridge 启动时根据本员工订阅 spawn mcp server subprocess,
建立 stdio JSON-RPC 连接, 拉 tools list 注册到 catfish tools, LLM 调对应
mcp_<connector>_<tool> 时透传 tools/call 请求.

# 设计

- subprocess-per-connector (不是 docker pod, 5/14 demo 不需要那么重)
- 进程 lifetime = tool-bridge daemon
- 异步 stdio JSON-RPC 2.0 (mcp 协议)
- tools 名加 mcp_<connector>_ 前缀防跟 catfish 原生工具撞
- 调用失败不阻塞其他工具

# 跟 mcp-registry 关系

mcp-registry 是中央**目录** (员工订阅 / OAuth / 部门权限).
mcp_client 是边缘**运行时** (真启进程 + 调 tool).

5/9 demo: tool-bridge 启动时:
  1. 调 gateway /v1/mcp/subscribed 拿当前员工 active 订阅
  2. 对每个订阅 spawn mcp server (从 manifest mcp_command 读启动参数)
  3. 拉 tools list 注册到 catfish_tools
  4. LLM 调 mcp_time_get_current_time → 走本模块 → mcp server stdio → 返结果

# 第一批 demo 只接 time

time 不需要 OAuth, uvx 一行启, 验证 stack 通就行. filesystem / jira / gitlab
留 5/15+ Phase 3.1.

# 协议

MCP 协议 (Anthropic 开放标准, JSON-RPC 2.0 over stdio):

  client → server: {"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}
  server → client: {"jsonrpc":"2.0","id":1,"result":{"capabilities":{...}}}
  client → server: {"jsonrpc":"2.0","id":2,"method":"tools/list"}
  server → client: {"jsonrpc":"2.0","id":2,"result":{"tools":[...]}}
  client → server: {"jsonrpc":"2.0","id":3,"method":"tools/call",
                    "params":{"name":"get_current_time","arguments":{...}}}
  server → client: {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"..."}]}}
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.mcp_client")

# 单例 connectors map: connector_id → ConnectorClient
_CLIENTS: dict[str, "ConnectorClient"] = {}

# 名前缀, 防跟 catfish 原生工具撞名
_TOOL_PREFIX = "mcp_"


def _strip_prefix(prefixed_name: str) -> tuple[str, str] | None:
    """'mcp_time_get_current_time' → ('time', 'get_current_time'). 失败返 None."""
    if not prefixed_name.startswith(_TOOL_PREFIX):
        return None
    rest = prefixed_name[len(_TOOL_PREFIX):]
    # 第一段是 connector_id (匹配已注册的)
    for cid in _CLIENTS:
        if rest.startswith(cid + "_"):
            return (cid, rest[len(cid) + 1:])
    return None


class ConnectorClient:
    """单个 mcp server 进程 + JSON-RPC 通信 (stdio).

    Lifetime = tool-bridge daemon. 进程死了 best-effort 重启 (Phase 3.1+).
    """

    def __init__(self, connector_id: str, command: list[str], env: dict[str, str] | None = None):
        self.connector_id = connector_id
        self.command = command
        self.env = env or {}
        self.proc: asyncio.subprocess.Process | None = None
        self._req_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self.tools: list[dict] = []  # [{name, description, inputSchema}]
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """spawn 子进程 + initialize + tools/list. 失败抛 RuntimeError."""
        full_env = {**os.environ, **self.env}
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=full_env,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                f"mcp server '{self.connector_id}' 启动失败: 命令不存在 {self.command[0]}. "
                f"装 uvx (curl -LsSf https://astral.sh/uv/install.sh | sh) 或检查 PATH."
            ) from e

        # 启读 loop (后台)
        self._reader_task = asyncio.create_task(self._read_loop(), name=f"mcp-reader-{self.connector_id}")

        # initialize
        try:
            init_resp = await self._call(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "catfish-tool-bridge", "version": "0.1.0"},
                },
                timeout=10,
            )
            await self._send_notification("notifications/initialized", {})
            logger.info(
                "mcp server initialized: %s (server=%s)",
                self.connector_id,
                init_resp.get("serverInfo", {}).get("name", "?"),
            )

            # tools/list
            list_resp = await self._call("tools/list", {}, timeout=10)
            self.tools = list_resp.get("tools", [])
            logger.info(
                "mcp server tools loaded: %s tools=%d (%s)",
                self.connector_id,
                len(self.tools),
                ", ".join(t["name"] for t in self.tools[:5]),
            )
        except Exception:
            await self.stop()
            raise

    async def _read_loop(self) -> None:
        """从 stdout 读 JSON-RPC 响应, 派发到等待的 future."""
        if self.proc is None or self.proc.stdout is None:
            return
        while True:
            try:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue
                try:
                    msg = json.loads(line_str)
                except json.JSONDecodeError:
                    logger.warning("mcp %s 收到非 JSON: %s", self.connector_id, line_str[:200])
                    continue
                req_id = msg.get("id")
                if req_id is not None and req_id in self._pending:
                    fut = self._pending.pop(req_id)
                    if not fut.done():
                        if "error" in msg:
                            fut.set_exception(RuntimeError(f"mcp error: {msg['error']}"))
                        else:
                            fut.set_result(msg.get("result", {}))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("mcp %s read loop error", self.connector_id)
                break
        # 进程退出, 唤醒所有 pending
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError(f"mcp {self.connector_id} 进程退出"))
        self._pending.clear()

    async def _call(self, method: str, params: dict, timeout: float = 30) -> dict:
        """JSON-RPC request, 等响应."""
        if self.proc is None or self.proc.stdin is None:
            raise RuntimeError(f"mcp {self.connector_id} 未启动")
        async with self._lock:
            self._req_id += 1
            req_id = self._req_id
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        try:
            self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
            await self.proc.stdin.drain()
        except Exception as e:
            self._pending.pop(req_id, None)
            raise RuntimeError(f"mcp {self.connector_id} 写 stdin 失败: {e}") from e
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise RuntimeError(f"mcp {self.connector_id}.{method} 超时 ({timeout}s)") from None

    async def _send_notification(self, method: str, params: dict) -> None:
        """JSON-RPC notification (无 id, 不等响应)."""
        if self.proc is None or self.proc.stdin is None:
            return
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """调一个 tool. 返 mcp result (含 content list).

        catfish 调用方拿到后看 result["content"][0]["text"] 提取人话.
        """
        return await self._call(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
            timeout=60,
        )

    async def stop(self) -> None:
        """优雅关闭."""
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        if self.proc:
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), timeout=3)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self.proc.kill()
                except ProcessLookupError:
                    pass
            self.proc = None


# ── 公共 API ─────────────────────────────────────────────────────────


async def register_connector(connector_id: str, command: list[str], env: dict | None = None) -> bool:
    """spawn + initialize + tools/list. 成功 → 注册到 _CLIENTS, 失败 → False."""
    if connector_id in _CLIENTS:
        logger.warning("mcp %s 已注册, 跳过", connector_id)
        return True
    client = ConnectorClient(connector_id, command, env)
    try:
        await client.start()
    except Exception as e:
        logger.warning("mcp %s 注册失败: %s", connector_id, e)
        return False
    _CLIENTS[connector_id] = client
    return True


def list_all_tools_as_native_schema() -> list[dict]:
    """把所有已注册 mcp server 的 tools 转成 catfish 原生 tool schema 形态.

    返回的 dict 跟 NATIVE_TOOL_DEFS 同形态, 直接拼到 catfish_tools 列表.
    名字加 mcp_<connector>_ 前缀防撞.
    """
    out = []
    for cid, client in _CLIENTS.items():
        for tool in client.tools:
            tname = tool.get("name", "")
            if not tname:
                continue
            prefixed = f"{_TOOL_PREFIX}{cid}_{tname}"
            out.append(
                {
                    "name": prefixed,
                    "description": (
                        f"[MCP {cid}] {tool.get('description', '')}".strip()
                    ),
                    "input_schema": tool.get("inputSchema") or {"type": "object", "properties": {}},
                    "emoji": "🔌",
                    "toolset": f"mcp_{cid}",
                    "available": True,
                }
            )
    return out


def is_mcp_tool(name: str) -> bool:
    return _strip_prefix(name) is not None


async def dispatch_mcp_tool(prefixed_name: str, args: dict) -> dict:
    """LLM 调 mcp_<connector>_<tool> 时走这里. 透传到对应 mcp server."""
    parsed = _strip_prefix(prefixed_name)
    if parsed is None:
        return {"type": "error", "error": f"非 mcp tool 名: {prefixed_name}"}
    connector_id, tool_name = parsed
    client = _CLIENTS.get(connector_id)
    if client is None:
        return {
            "type": "error",
            "error": f"mcp connector '{connector_id}' 未注册. 检查 tool-bridge 启动日志.",
        }
    try:
        result = await client.call_tool(tool_name, args)
        # mcp result.content 是 list[{type, text/data}], 提取 text
        content = result.get("content") or []
        texts = [
            item.get("text", "") for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return {
            "type": "ok",
            "connector": connector_id,
            "tool": tool_name,
            "text": "\n".join(texts) if texts else "",
            "raw": result,
        }
    except Exception as e:
        logger.exception("mcp %s.%s call failed", connector_id, tool_name)
        return {
            "type": "error",
            "error": f"mcp {connector_id}.{tool_name} 调用失败: {e}",
        }


async def shutdown_all() -> None:
    """tool-bridge 退出时调."""
    for cid, client in list(_CLIENTS.items()):
        try:
            await client.stop()
        except Exception as e:
            logger.warning("mcp %s 关闭出错: %s", cid, e)
    _CLIENTS.clear()


# ── 按员工订阅同步 (10/22 重做) ──────────────────────────────────────
#
# 原来 (5/9) 只在 Companion 起的 tool-bridge 守护进程里拉一次订阅, 但:
#   · 小鲶用的工具来自 hermes 自己起的 `catfish_tool_bridge.mcp_server`, 那个进程从不拉订阅
#   · 守护进程拿不到员工令牌 (env 里没有 CATFISH_USER_JWT), 一直走硬编码 fallback, 报
#     「mcp time 没默认启动命令」
#   · 启动命令是 `uvx <包>` 现场联网下载, 内网装不了; filesystem 的包在 PyPI 上根本不存在
#   · OAuth 型连接器要找的 secret-broker 6/13 已经删了
# 现在: 两种进程都调 sync_subscriptions(), mcp_server 里每隔几分钟再同步一次 (订阅/退订不用
# 重启); 令牌和网关地址直接读 Companion 落盘的那份; 连接器优先用 `type: python` —— 包随
# 安装包进 hermes venv, 离线可用。

_SYNC_RETRY_SECONDS = 600  # 起不来的连接器隔这么久再试, 不每轮都拉进程
_FAILED_AT: dict[str, float] = {}


def _hermes_env_value(key: str) -> str | None:
    try:
        from .hermes_paths import hermes_home  # noqa: PLC0415
        text = (hermes_home() / ".env").read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None
    for line in text.splitlines():
        if line.startswith(f"{key}="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            return value or None
    return None


def _gateway_url() -> str:
    """hermes 起的 MCP 子进程 env 只有 PATH/HOME 几项 —— 读不到时去 hermes 的 .env 找。"""
    for value in (
        os.environ.get("CATFISH_MCP_REGISTRY_URL"),
        os.environ.get("CATFISH_GATEWAY_URL"),
        _hermes_env_value("CATFISH_GATEWAY_URL"),
    ):
        if value and value.strip():
            return value.strip().rstrip("/")
    return "http://127.0.0.1:8999"


def _user_token() -> str | None:
    """Companion 登录后落盘的 id_token (它会静默续期, 每次现读拿到的是新的); 没有再看 env。"""
    path = os.path.join(os.path.expanduser("~"), ".catfish", "oauth", "id_token")
    try:
        with open(path, encoding="utf-8") as f:
            token = f.read().strip()
        if token:
            return token
    except OSError:
        pass
    return os.environ.get("CATFISH_USER_JWT", "").strip() or None


def _http_get_json(path: str, token: str, timeout: float = 8) -> Any:
    import urllib.request  # noqa: PLC0415

    req = urllib.request.Request(
        _gateway_url() + path,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def command_for_manifest(manifest: dict) -> tuple[list[str], dict[str, str]] | None:
    """manifest.mcp_command → (启动命令, env)。起不了的返 None 并说明原因。

    type python: `python -m <module>`, 用跑本进程的这个 Python (hermes venv) —— 包跟着安装包走,
                 离线可用。新连接器优先用这种。
    type uvx:    `uvx <package>` 现场从 PyPI 拉, 只适合能上网的机器。
    """
    import sys  # noqa: PLC0415

    cid = manifest.get("id", "?")
    spec = manifest.get("mcp_command") or {}
    kind = spec.get("type", "uvx")
    args = [str(a) for a in (spec.get("args") or [])]
    env: dict[str, str] = {}
    for key, value in (spec.get("env") or {}).items():
        text = str(value)
        if text.startswith("${"):
            logger.warning("mcp %s: env %s 要凭据 (%s), 本机还不支持 OAuth 型连接器, 跳过", cid, key, text)
            return None
        env[key] = text
    if kind == "python":
        module = spec.get("module")
        if not module:
            logger.warning("mcp %s: type=python 但没写 module, 跳过", cid)
            return None
        # 子进程要能 import 随包的模块: 本进程里 hermes 的 bootstrap 会把 PYTHONPATH 改写成
        # hermes 自己的路径 (10/22 实测, 连接器起来就因 import 不到退出), 这里把 tool-bridge 的
        # src 显式放最前。
        src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        inherited = env.get("PYTHONPATH") or os.environ.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join(p for p in (src, inherited) if p)
        return [sys.executable, "-m", module, *args], env
    if kind == "uvx":
        package = spec.get("package")
        uvx = shutil.which("uvx")
        if uvx and package:
            return [uvx, package, *args], env
        uv = shutil.which("uv")
        if uv and package:
            return [uv, "tool", "run", package, *args], env
        logger.warning("mcp %s: 需要 uvx 现场下载 %s, 本机没有 uvx, 跳过", cid, package)
        return None
    logger.warning("mcp %s: 不认识的启动方式 %s, 跳过", cid, kind)
    return None


async def sync_subscriptions() -> bool:
    """让本进程跑着的连接器 = 员工当前 active 订阅。返回工具列表有没有变。

    没登录 / 网关不通 → 不动现有的, 返 False (下一轮再试)。
    """
    import time  # noqa: PLC0415

    token = _user_token()
    if not token:
        logger.info("mcp 订阅同步: 没有员工登录令牌, 跳过")
        return False
    try:
        resp = await asyncio.to_thread(_http_get_json, "/v1/mcp/subscribed?status_filter=active", token)
    except Exception as e:  # noqa: BLE001
        logger.warning("mcp 订阅同步: 拉订阅失败 (%s), 保持现状", e)
        return False
    wanted = {s.get("connector_id") for s in resp.get("subscriptions", []) if s.get("connector_id")}

    changed = False
    for cid in [c for c in _CLIENTS if c not in wanted]:
        client = _CLIENTS.pop(cid)
        try:
            await client.stop()
        except Exception as e:  # noqa: BLE001
            logger.warning("mcp %s 关闭出错: %s", cid, e)
        logger.info("mcp %s: 已退订, 停掉", cid)
        changed = True

    now = time.monotonic()
    for cid in sorted(wanted - set(_CLIENTS)):
        if now - _FAILED_AT.get(cid, -_SYNC_RETRY_SECONDS) < _SYNC_RETRY_SECONDS:
            continue
        try:
            manifest = (await asyncio.to_thread(_http_get_json, f"/v1/mcp/manifest/{cid}", token)).get("manifest") or {}
        except Exception as e:  # noqa: BLE001
            logger.warning("mcp %s: 拉 manifest 失败 (%s)", cid, e)
            _FAILED_AT[cid] = now
            continue
        spec = command_for_manifest(manifest)
        if spec is None or not await register_connector(cid, spec[0], spec[1]):
            _FAILED_AT[cid] = now
            continue
        _FAILED_AT.pop(cid, None)
        logger.info("mcp %s: 按订阅启动 ok", cid)
        changed = True
    return changed


async def autostart_default_servers() -> None:
    """启动时按员工订阅接 MCP 连接器。`CATFISH_MCP_AUTOSTART=''` 关掉 (调试用)。"""
    if os.environ.get("CATFISH_MCP_AUTOSTART", "on").strip() == "":
        logger.info("mcp autostart 禁用 (CATFISH_MCP_AUTOSTART 为空)")
        return
    await sync_subscriptions()
