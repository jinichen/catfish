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


async def autostart_default_servers() -> None:
    """启动时自动接的 mcp servers (BL-D3 Phase 3.1, 5/9).

    优先级:
    1. **真接 mcp-registry 员工订阅** (生产路径, 推荐):
       env CATFISH_MCP_REGISTRY_URL + CATFISH_USER_JWT 都设 →
       调 gateway /v1/mcp/subscribed 拉员工 active 订阅 → 对每个调
       /v1/mcp/manifest/{id} 拿 mcp_command + auth + 从 secret-broker
       拉 OAuth token (oauth2 时) → spawn subprocess.

    2. **硬编码 fallback** (dev 没配 registry 时):
       CATFISH_MCP_AUTOSTART='time,filesystem' (默认 'time')
       不接 registry, 直接 spawn 已知 connector.

    3. **CATFISH_MCP_AUTOSTART=''** 完全禁用.
    """
    # 路径 1: 真接 mcp-registry
    registry_url = os.environ.get("CATFISH_MCP_REGISTRY_URL", "").strip()
    user_jwt = os.environ.get("CATFISH_USER_JWT", "").strip()
    if registry_url and user_jwt:
        try:
            await _autostart_from_registry(registry_url, user_jwt)
            return
        except Exception as e:
            logger.warning(
                "mcp 真接 registry 失败 (%s), fallback 到硬编码 autostart",
                e,
            )

    # 路径 2: 硬编码 fallback (dev / Companion 没注入 env 时)
    autostart = os.environ.get("CATFISH_MCP_AUTOSTART", "time").strip()
    if not autostart:
        logger.info("mcp autostart 禁用 (CATFISH_MCP_AUTOSTART 为空)")
        return

    connectors = [c.strip() for c in autostart.split(",") if c.strip()]
    for cid in connectors:
        cmd, env = _default_command_for(cid)
        if cmd is None:
            logger.warning(
                "mcp %s 没默认启动命令, 跳过. 配 CATFISH_MCP_REGISTRY_URL "
                "+ CATFISH_USER_JWT 走 registry 路径拉 manifest.",
                cid,
            )
            continue
        ok = await register_connector(cid, cmd, env)
        if ok:
            logger.info("mcp autostart ok: %s (硬编码 fallback)", cid)


async def _autostart_from_registry(registry_url: str, user_jwt: str) -> None:
    """真路径 (BL-D3 Phase 3.1, 5/9): 调 gateway /v1/mcp/subscribed 拿订阅 →
    对每个调 /v1/mcp/manifest/{id} → 解 mcp_command + auth → spawn.

    auth_type=oauth2 时调 secret-broker 拿 token 注入 env. token 没拿到
    跳过该 connector (员工还没完成 OAuth flow).
    """
    import urllib.parse  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    def http_get_json(path: str, timeout: float = 10) -> Any:
        url = registry_url.rstrip("/") + path
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {user_jwt}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # 1. 拉员工 active 订阅
    subs_resp = await asyncio.to_thread(
        http_get_json, "/v1/mcp/subscribed?status_filter=active",
    )
    subs = subs_resp.get("subscriptions", [])
    if not subs:
        logger.info("mcp registry 拉订阅 ok, 但员工 0 个 active 订阅 — 跳 autostart")
        return
    logger.info(
        "mcp 真接 registry: 员工 %d 个 active 订阅 (%s)",
        len(subs),
        ", ".join(s.get("connector_id", "?") for s in subs[:5]),
    )

    # 2. 对每个 subscription 拉 manifest + 启动
    secret_broker_url = os.environ.get(
        "CATFISH_SECRET_BROKER_URL", "http://127.0.0.1:8995",
    )
    user_sub = os.environ.get("CATFISH_USER_SUB", "")

    for sub in subs:
        cid = sub.get("connector_id", "")
        if not cid:
            continue
        try:
            manifest_resp = await asyncio.to_thread(
                http_get_json, f"/v1/mcp/manifest/{cid}",
            )
            manifest = manifest_resp.get("manifest") or {}
            await _spawn_from_manifest(
                manifest,
                user_sub=user_sub,
                secret_broker_url=secret_broker_url,
                token_ref=sub.get("oauth_token_ref"),
            )
        except Exception as e:
            logger.warning(
                "mcp connector %s 启动失败 (跳过, 不阻塞其他): %s", cid, e,
            )


async def _spawn_from_manifest(
    manifest: dict,
    *,
    user_sub: str,
    secret_broker_url: str,
    token_ref: str | None,
) -> None:
    """从 manifest 解 mcp_command + 解 ${SECRET_REF:xxx} / ${ENV_VAR} 占位 + spawn."""
    cid = manifest.get("id", "")
    if not cid:
        raise ValueError("manifest 缺 id")
    mcp_cmd_obj = manifest.get("mcp_command") or {}
    cmd_type = mcp_cmd_obj.get("type", "uvx")
    package = mcp_cmd_obj.get("package", "")
    args = mcp_cmd_obj.get("args") or []
    env_template = mcp_cmd_obj.get("env") or {}

    # 命令解析 — 现在只支持 uvx (5/14 demo 4 connector 都是 uvx).
    # docker / npx 留 5/15+ Phase 3.2.
    if cmd_type == "uvx":
        uvx = shutil.which("uvx") or shutil.which("uv")
        if not uvx:
            logger.warning(
                "mcp %s: uvx 未装, 跳过. 装: curl -LsSf https://astral.sh/uv/install.sh | sh",
                cid,
            )
            return
        command = [uvx, package, *args]
    else:
        logger.warning("mcp %s: 命令类型 %s 暂不支持 (只 uvx). Phase 3.2 加.", cid, cmd_type)
        return

    # 解 env — 替换 ${SECRET_REF:xxx} 跟 ${ENV_VAR}
    resolved_env: dict[str, str] = {}
    skip_due_to_missing_secret = False
    for k, v in env_template.items():
        if not isinstance(v, str):
            resolved_env[k] = str(v)
            continue
        # ${SECRET_REF:jira-oauth} → 从 secret-broker 拉
        if v.startswith("${SECRET_REF:") and v.endswith("}"):
            ref_template = v[len("${SECRET_REF:"):-1]
            # token_ref 优先 (subscription 里写好的真 ref), 否则用 manifest 模板
            actual_ref = token_ref or f"{ref_template}-{user_sub.replace('@', '-at-')}"
            secret_value = await _fetch_secret(
                secret_broker_url, actual_ref, user_sub,
            )
            if secret_value is None:
                logger.warning(
                    "mcp %s: secret '%s' 没拿到 (员工还没完成 OAuth?), 跳过该 connector",
                    cid, actual_ref,
                )
                skip_due_to_missing_secret = True
                break
            resolved_env[k] = secret_value
        # ${ENV_VAR} → 从 process env 拉 (例 ${JIRA_URL})
        elif v.startswith("${") and v.endswith("}"):
            env_name = v[2:-1]
            env_value = os.environ.get(env_name, "")
            if not env_value:
                logger.warning(
                    "mcp %s: env %s 未设, 跳过该 connector",
                    cid, env_name,
                )
                skip_due_to_missing_secret = True
                break
            resolved_env[k] = env_value
        else:
            resolved_env[k] = v

    if skip_due_to_missing_secret:
        return

    ok = await register_connector(cid, command, resolved_env)
    if ok:
        logger.info("mcp registry-driven autostart ok: %s", cid)


async def _fetch_secret(
    broker_url: str, ref: str, user_sub: str,
) -> str | None:
    """同步调 secret-broker GET /v1/secret/{ref} 拿 value. 失败 / 404 返 None."""
    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    def _do_get() -> str | None:
        url = f"{broker_url.rstrip('/')}/v1/secret/{ref}"
        req = urllib.request.Request(
            url, headers={"X-Catfish-User-Sub": user_sub or "tool-bridge"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("value")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            logger.warning("secret-broker get %s 错: %d %s", ref, e.code, e.reason)
            return None
        except Exception as e:
            logger.warning("secret-broker 不可达 %s: %s", broker_url, e)
            return None

    return await asyncio.to_thread(_do_get)


def _default_command_for(cid: str) -> tuple[list[str] | None, dict]:
    """硬编码第一批 connector 启动命令 (Phase 3 fallback). Phase 3.1 后优先走
    _autostart_from_registry, 这里只在 dev / 没 JWT 时兜底."""
    uvx = shutil.which("uvx") or shutil.which("uv")
    if cid == "time":
        if uvx:
            return ([uvx, "mcp-server-time"], {})
        return (None, {})
    if cid == "filesystem":
        if uvx:
            home = os.path.expanduser("~/.catfish/output")
            os.makedirs(home, exist_ok=True)
            return ([uvx, "mcp-server-filesystem", home], {})
        return (None, {})
    return (None, {})
