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

from . import adapter, bootstrap, catfish_tools, config_watcher, skill_watcher

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
        # BL-TODO-BRIDGE-STORE (5/16): session_id 可选, 用于 per-session stateful tool
        # 注入 (hermes todo / 未来其它 per-session store). 客户端不传 → 走 __default__
        # 全局 singleton, 行为兼容老客户端.
        session_id = params.get("session_id")
        if not name:
            return _error(req_id, INVALID_PARAMS, "params.name 必填")
        result = await adapter.dispatch_tool(name, args, session_id=session_id)
        return _success(req_id, result)

    if method == "health":
        return _success(req_id, adapter.health())

    # ── BL-RECMODE-MIGRATE-TO-EDGE (5/25 鸿波拍板"现在必须现在转移") ──
    # 中央 gateway /api/learn/analyze + /repair_selector 现在是 thin proxy,
    # JSON-RPC 转发到这里. 这俩跑在 tool-bridge 进程 (edge), 中央代码
    # (central/) 不再读写 ~/.catfish/recordings/ ~/.catfish/skills/.
    # 详见 recmode/__init__.py + docs/CENTRAL-EDGE-DATA-BOUNDARY.md E.1.
    #
    # 故意不挂 tools/dispatch — 这俩是内部 RecMode pipeline, 不该出现在
    # LLM 看的 tool list 里 (避免 LLM 误调). 走专属 method 名.
    if method == "recmode/analyze":
        return await _handle_recmode_analyze(req_id, params)
    if method == "recmode/repair_selector":
        return await _handle_recmode_repair_selector(req_id, params)
    # 5/26 BL-RECMODE-MIGRATE-TO-EDGE batch 1 (E.2): cdp_listener + cleanup 搬过来.
    # gateway /api/learn/{start_recording, stop_recording, active, status, cleanup}
    # 5 个 endpoint 改 thin proxy 走这条.
    if method == "recmode/start_recording":
        return await _handle_recmode_start_recording(req_id, params)
    if method == "recmode/stop_recording":
        return await _handle_recmode_stop_recording(req_id, params)
    if method == "recmode/active":
        return await _handle_recmode_active(req_id, params)
    if method == "recmode/status":
        return await _handle_recmode_status(req_id, params)
    if method == "recmode/cleanup":
        return await _handle_recmode_cleanup(req_id, params)
    if method == "recmode/list_with_meta":
        return await _handle_recmode_list_with_meta(req_id, params)
    # 5/26 batch 2 (E.1 收尾): RecMode 3 个 app.py 内联 endpoint 搬过来 (batch 1 漏的)
    if method == "recmode/record_transcript":
        return await _handle_recmode_record_transcript(req_id, params)
    if method == "recmode/skill_content":
        return await _handle_recmode_skill_content(req_id, params)
    if method == "recmode/save_skill":
        return await _handle_recmode_save_skill(req_id, params)

    return _error(req_id, METHOD_NOT_FOUND, f"unknown method: {method}")


async def _handle_recmode_analyze(req_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """读 session_dir → vision LLM → 落 SKILL.md + main.py.

    Params: {session_id, skills_root?, draft_only?, auth_token?, catfish_home?}

    catfish_home: 可选 override (env CATFISH_HOME 在 tool-bridge 进程拿不到时
    caller 传过来). 不传按 ~/.catfish/recordings/ 找.
    """
    from pathlib import Path  # noqa: PLC0415
    import os  # noqa: PLC0415
    from .recmode import aggregator  # noqa: PLC0415

    session_id = (params.get("session_id") or "").strip()
    if not session_id:
        return _error(req_id, INVALID_PARAMS, "params.session_id 必填")

    skills_root_str = (params.get("skills_root") or "").strip()
    skills_root = Path(skills_root_str).expanduser() if skills_root_str else None

    catfish_home = (params.get("catfish_home") or os.environ.get("CATFISH_HOME") or "").strip()
    rec_root = (
        Path(catfish_home).expanduser() / "recordings" if catfish_home
        else Path.home() / ".catfish" / "recordings"
    )
    session_dir = rec_root / session_id
    if not session_dir.exists():
        return _error(req_id, INVALID_PARAMS,
                      f"session_dir {session_dir} 不存在. 先调 /start_recording 录一段.")

    auth_token = params.get("auth_token") or os.environ.get("CATFISH_DEV_TOKEN", "")
    draft_only = bool(params.get("draft_only", True))

    try:
        out = await aggregator.aggregate_session(
            session_dir,
            skills_root=skills_root,
            auth_token=auth_token,
            draft_only=draft_only,
        )
        return _success(req_id, out)
    except RuntimeError as e:
        # LLM call / 网络挂 — gateway proxy 会映射 502
        return _error(req_id, INTERNAL_ERROR, f"aggregate_session 失败 (502): {e}")
    except ValueError as e:
        return _error(req_id, INVALID_PARAMS, f"aggregate_session 参数错 (422): {e}")


async def _handle_recmode_repair_selector(req_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """selector 漂移自动修复. Params: {hint, screenshot_b64, context?, auth_token?}."""
    import os  # noqa: PLC0415
    from .recmode import selector_repair  # noqa: PLC0415

    hint = params.get("hint") or {}
    screenshot = (params.get("screenshot_b64") or "").strip()
    context = (params.get("context") or "").strip()
    if not hint:
        return _error(req_id, INVALID_PARAMS, "params.hint 必填")
    if not screenshot:
        return _error(req_id, INVALID_PARAMS, "params.screenshot_b64 必填 (vision 必须看图)")

    auth_token = params.get("auth_token") or os.environ.get("CATFISH_DEV_TOKEN", "")
    try:
        out = await selector_repair.repair_selector(
            hint=hint,
            screenshot_b64=screenshot,
            context=context,
            auth_token=auth_token,
        )
        return _success(req_id, out)
    except RuntimeError as e:
        return _error(req_id, INTERNAL_ERROR, f"repair_selector 失败 (502): {e}")


# ── BL-RECMODE-MIGRATE-TO-EDGE batch 1 (5/26): cdp_listener + cleanup handlers ──
#
# 5 个 JSON-RPC method 对应 gateway /api/learn/{start_recording, stop_recording,
# active, status, cleanup} thin proxy. cdp_listener 文件 5/26 从 central 搬过来,
# 真接 websockets ws://localhost:9222 + Page.captureScreenshot. 详见
# recmode/cdp_listener.py 头部注释.


async def _handle_recmode_start_recording(req_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """开 RecMode session. Params: {session_id, chrome_ws?, connect_ws?}."""
    from .recmode import cdp_listener  # noqa: PLC0415

    session_id = (params.get("session_id") or "").strip()
    if not session_id:
        return _error(req_id, INVALID_PARAMS, "params.session_id 必填")
    chrome_ws = (params.get("chrome_ws") or "ws://localhost:9222").strip()
    connect_ws = bool(params.get("connect_ws", True))

    try:
        info = await cdp_listener.start_recording(
            session_id, chrome_ws=chrome_ws, connect_ws=connect_ws,
        )
        return _success(req_id, info)
    except ValueError as e:
        # 重复 session 等 → gateway 映 409
        return _error(req_id, INVALID_PARAMS, f"start_recording 重复 (409): {e}")
    except RuntimeError as e:
        # ws 连不上 / websockets 没装 → 503
        return _error(req_id, INTERNAL_ERROR, f"start_recording 失败 (503): {e}")


async def _handle_recmode_stop_recording(req_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """停 RecMode session. Params: {session_id}."""
    from .recmode import cdp_listener  # noqa: PLC0415

    session_id = (params.get("session_id") or "").strip()
    if not session_id:
        return _error(req_id, INVALID_PARAMS, "params.session_id 必填")
    try:
        summary = await cdp_listener.stop_recording(session_id)
        return _success(req_id, summary)
    except ValueError as e:
        # session 不存在 → gateway 映 404
        return _error(req_id, INVALID_PARAMS, f"session {session_id} 不存在 (404)")


async def _handle_recmode_active(req_id: Any, _params: Dict[str, Any]) -> Dict[str, Any]:
    """列当前在录的 session id."""
    from .recmode import cdp_listener  # noqa: PLC0415
    return _success(req_id, {"active_session_ids": cdp_listener.list_active()})


async def _handle_recmode_status(req_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """单 session 实时状态. Params: {session_id}. None → INVALID_PARAMS 让 gateway 映 404."""
    from .recmode import cdp_listener  # noqa: PLC0415

    session_id = (params.get("session_id") or "").strip()
    if not session_id:
        return _error(req_id, INVALID_PARAMS, "params.session_id 必填")
    s = cdp_listener.session_status(session_id)
    if s is None:
        return _error(req_id, INVALID_PARAMS, f"session {session_id} 没在录 (404)")
    return _success(req_id, s)


async def _handle_recmode_cleanup(req_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """显式触发录屏清理. Params: {dry_run?, ttl_days?}."""
    from .recmode import cleanup as _recmode_cleanup  # noqa: PLC0415

    dry_run = bool(params.get("dry_run", False))
    ttl_days = int(params.get("ttl_days") or 14)
    stats = _recmode_cleanup.cleanup_old_recordings(
        ttl_seconds=ttl_days * 24 * 3600,
        dry_run=dry_run,
    )
    return _success(req_id, stats)


async def _handle_recmode_list_with_meta(req_id: Any, _params: Dict[str, Any]) -> Dict[str, Any]:
    """5/25 RecordingsCard 用 — 列所有 session inventory (id / started_at / size /
    kept_forever / skill_drafts). Tauri 端 #75 当前直接 fs 读, 这个 method 留作
    未来 catfish-web admin 用 (HTTP 访问 inventory)."""
    from .recmode import cleanup as _recmode_cleanup  # noqa: PLC0415
    return _success(req_id, {"recordings": _recmode_cleanup.list_recordings_with_meta()})


# ── BL-RECMODE-MIGRATE-TO-EDGE batch 2 (5/26 E.1 收尾) ──
#
# 5/25 batch 1 漏搬的 3 个 app.py 内联 endpoint:
# record_transcript / skill_content / save_skill. 都涉及 ~/.catfish/recordings/
# 或 ~/.catfish/skills/ 文件操作, gateway 现在 thin proxy 转 tool-bridge 处理.


async def _handle_recmode_record_transcript(req_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Companion whisper 转写落 ~/.catfish/recordings/<sid>/transcripts.jsonl.

    Params: {session_id, text, ts_offset?, duration?, catfish_home?}
    """
    import json as _json
    import os as _os
    from pathlib import Path as _Path

    session_id = (params.get("session_id") or "").strip()
    text = (params.get("text") or "").strip()
    if not session_id:
        return _error(req_id, INVALID_PARAMS, "params.session_id 必填")
    if not text:
        return _success(req_id, {"ok": True, "transcripts_path": "", "lines_count": 0, "skipped": "empty text"})

    ts_offset = float(params.get("ts_offset") or 0.0)
    duration = float(params.get("duration") or 0.0)
    catfish_home = (params.get("catfish_home") or _os.environ.get("CATFISH_HOME") or "").strip()
    rec_root = (_Path(catfish_home).expanduser() / "recordings") if catfish_home else (_Path.home() / ".catfish" / "recordings")
    sd = rec_root / session_id
    if not sd.exists():
        return _error(req_id, INVALID_PARAMS, f"session_dir {sd} 不存在 (404)")

    path = sd / "transcripts.jsonl"
    record = {"ts": ts_offset, "duration": duration, "text": text}
    with path.open("a", encoding="utf-8") as f:
        f.write(_json.dumps(record, ensure_ascii=False) + "\n")
    lines_count = sum(1 for _ in path.open("r", encoding="utf-8"))
    return _success(req_id, {"ok": True, "transcripts_path": str(path), "lines_count": lines_count})


async def _handle_recmode_skill_content(req_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """读 SKILL.md + main.py + recmode_meta.json. Companion preview UI 用.

    Params: {skill_dir, catfish_home?}
    Path traversal 防御: skill_dir 必须在受信路径下.
    """
    import json as _json
    import os as _os
    from pathlib import Path as _Path

    skill_dir = (params.get("skill_dir") or "").strip()
    if not skill_dir:
        return _error(req_id, INVALID_PARAMS, "params.skill_dir 必填")
    sd = _Path(skill_dir).expanduser().resolve()
    home = _Path.home().resolve()
    allowed_roots = [home / ".catfish" / "skills", home / ".catfish" / "recordings"]
    catfish_home = (params.get("catfish_home") or _os.environ.get("CATFISH_HOME") or "").strip()
    if catfish_home:
        cf = _Path(catfish_home).expanduser().resolve()
        allowed_roots += [cf / "skills", cf / "recordings"]
    if not any(str(sd).startswith(str(r)) for r in allowed_roots):
        return _error(req_id, INVALID_PARAMS, f"skill_dir {sd} 不在受信路径下 (~/.catfish/skills/ 或 recordings/)")
    if not sd.exists():
        return _error(req_id, INVALID_PARAMS, f"skill_dir {sd} 不存在 (404)")

    def _read_or_empty(p: _Path) -> str:
        try:
            return p.read_text(encoding="utf-8") if p.exists() else ""
        except OSError:
            return ""

    meta = None
    meta_path = sd / "recmode_meta.json"
    if meta_path.exists():
        try:
            meta = _json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = None

    return _success(req_id, {
        "skill_dir": str(sd),
        "skill_md": _read_or_empty(sd / "SKILL.md"),
        "main_py": _read_or_empty(sd / "main.py"),
        "recmode_meta": meta,
    })


async def _handle_recmode_save_skill(req_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """RecMode C — draft skill mv → 正式 ~/.catfish/skills/<ns>/<name>/.

    Params: {draft_dir, namespace?, name?, keep_forever?, catfish_home?}
    """
    import json as _json
    import os as _os
    import shutil
    import time as _time
    from pathlib import Path as _Path

    draft_dir_str = (params.get("draft_dir") or "").strip()
    if not draft_dir_str:
        return _error(req_id, INVALID_PARAMS, "params.draft_dir 必填")

    draft_dir = _Path(draft_dir_str).expanduser().resolve()
    home = _Path.home().resolve()
    catfish_home = (params.get("catfish_home") or _os.environ.get("CATFISH_HOME") or "").strip()
    rec_root = (_Path(catfish_home).expanduser().resolve() / "recordings") if catfish_home else (home / ".catfish" / "recordings")
    skills_root = (_Path(catfish_home).expanduser().resolve() / "skills") if catfish_home else (home / ".catfish" / "skills")
    if not str(draft_dir).startswith(str(rec_root)):
        return _error(req_id, INVALID_PARAMS, f"draft_dir 必须在 {rec_root} 下")
    if not draft_dir.exists():
        return _error(req_id, INVALID_PARAMS, f"draft_dir {draft_dir} 不存在 (404)")

    meta_path = draft_dir / "recmode_meta.json"
    namespace = (params.get("namespace") or "").strip()
    name = (params.get("name") or "").strip()
    if (not namespace or not name) and meta_path.exists():
        try:
            meta = _json.loads(meta_path.read_text(encoding="utf-8"))
            raw = meta.get("raw_llm_json") or {}
            namespace = namespace or raw.get("namespace", "personal")
            name = name or raw.get("skill_name", "")
        except Exception:
            pass
    if not namespace or not name:
        return _error(req_id, INVALID_PARAMS, "缺 namespace / name (recmode_meta.json 也没): 请显式传 (422)")

    final_dir = skills_root / namespace / name
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    if final_dir.exists():
        backup = final_dir.with_name(f"{name}.bak.{int(_time.time())}")
        final_dir.rename(backup)
    shutil.copytree(draft_dir, final_dir)

    keep_forever = bool(params.get("keep_forever", False))
    if keep_forever:
        try:
            session_dir = draft_dir.parent.parent.parent
            (session_dir / ".keep_forever").touch()
            new_meta_path = final_dir / "recmode_meta.json"
            if new_meta_path.exists():
                meta2 = _json.loads(new_meta_path.read_text(encoding="utf-8"))
                meta2["_keep_forever"] = True
                new_meta_path.write_text(_json.dumps(meta2, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            logger.warning("save_skill: keep_forever flag 写失败 (不致命)", exc_info=True)

    return _success(req_id, {
        "ok": True,
        "final_dir": str(final_dir),
        "namespace": namespace,
        "name": name,
        "moved": True,
        "keep_forever": keep_forever,
    })


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
    """启动 IPC server.

    Unix: socket_path 是 unix domain socket 文件 (chmod 600 owner-only).
    Windows: BL-WIN8 (5/8) — Windows 没 unix socket, 改走 TCP localhost
             随机端口. socket_path 这时不再是 socket, 而是一个**端口号文件** —
             里面存 ASCII 端口号 (e.g. "54321"), Companion Rust 客户端读这
             个文件拿到端口去 connect("127.0.0.1:<port>"). 语义上跟 Unix
             socket path 一样 — '一个文件代表 RPC 端点'. 只是 Windows 上要
             多一步 'open + read line + parse int'.
    """
    # 每次启动先把残留 socket / port 文件清掉(防上次 crash 留下的死文件)
    if socket_path.exists():
        try:
            socket_path.unlink()
        except OSError:
            pass

    socket_path.parent.mkdir(parents=True, exist_ok=True)

    # readline limit 拉到 16MB —— catfish_screenshot 工具返回的 base64 PNG 单行
    # 可能 5-10 MB, 默认 64KB 会让 readline 抛 LimitOverrunError。
    # 上限 12MB raw + base64 1.33x ≈ 16MB, 跟 catfish_tools._MAX_SCREENSHOT_BYTES
    # 配套, 保险起见多留点。
    is_windows = os.name == "nt"
    if is_windows:
        # BL-WIN8: TCP localhost + 随机端口. 用 0 让 OS 分配避免冲突.
        server = await asyncio.start_server(
            _handle_client,
            host="127.0.0.1",
            port=0,
            limit=16 * 1024 * 1024,
        )
        # 拿到 OS 分配的端口
        sockets = server.sockets or ()
        if not sockets:
            raise RuntimeError("BL-WIN8: TCP server start 后拿不到 sockets")
        actual_port = sockets[0].getsockname()[1]
        # 端口号写到 socket_path (这时它是 port 文件不是 socket)
        socket_path.write_text(str(actual_port), encoding="ascii")
        try:
            # ACL: Windows 上 chmod 不完全支持, 但 0o600 至少把 'Users' 组的
            # default ACL 收紧 (依赖 cygwin/git-bash 路径行为). 失败 silent.
            os.chmod(socket_path, 0o600)
        except OSError:
            pass
        endpoint_desc = f"tcp://127.0.0.1:{actual_port} (port file: {socket_path})"
        logger.info(
            "BL-WIN8 listening on TCP localhost port %d, port file %s",
            actual_port, socket_path,
        )
    else:
        server = await asyncio.start_unix_server(
            _handle_client,
            str(socket_path),
            limit=16 * 1024 * 1024,
        )
        os.chmod(socket_path, 0o600)  # 只员工自己能连
        endpoint_desc = str(socket_path)
        logger.info("listening on %s", socket_path)

    # BL-D3 Phase 3 (5/9): MCP server autostart. 启动时 spawn 配置好的 mcp
    # servers (默认 time, 可 env CATFISH_MCP_AUTOSTART 改). 失败不阻塞 daemon
    # 启动 — mcp 不可用时 catfish 原生 + hermes 工具仍正常用.
    from . import mcp_client  # noqa: PLC0415  延迟 import 避免循环
    try:
        await mcp_client.autostart_default_servers()
    except Exception as e:
        logger.warning("mcp autostart 整体失败 (不阻塞 daemon 启动): %s", e)

    hermes_count = len(adapter._r().get_all_tool_names())
    native_count = len(catfish_tools.CATFISH_NATIVE_TOOLS)
    mcp_tool_count = sum(
        len(c.tools) for c in mcp_client._CLIENTS.values()
    )
    mcp_server_count = len(mcp_client._CLIENTS)
    print("─" * 60, flush=True)
    print("🐟 catfish-tool-bridge", flush=True)
    print(f"   endpoint: {endpoint_desc}", flush=True)
    print(
        f"   tools  : {hermes_count + native_count + mcp_tool_count} 个 "
        f"(hermes {hermes_count} + catfish 原生 {native_count}"
        + (f" + mcp {mcp_tool_count} from {mcp_server_count} servers" if mcp_tool_count else "")
        + ")",
        flush=True,
    )
    print("─" * 60, flush=True)

    # 起 watcher daemons: 监控关键文件变化 → graceful 重启
    # (Companion autostart 会 respawn, 新进程重新 import hermes 拿最新状态)
    skill_watcher.start()    # 监 ~/.hermes/skills/  → 加载新 skill
    config_watcher.start()   # 监 ~/.hermes/config.yaml → 拿新 cdp_url / model 配置

    try:
        async with server:
            await server.serve_forever()
    finally:
        # 优雅关闭 mcp servers (subprocess 资源)
        try:
            await mcp_client.shutdown_all()
        except Exception:
            logger.exception("mcp shutdown_all 失败 (best-effort)")


def init_and_serve(socket_path: Path) -> None:
    """同步入口 —— bootstrap + 起 server"""
    registry_module = bootstrap.bootstrap()
    adapter.install_registry(registry_module)
    asyncio.run(serve_forever(socket_path))
