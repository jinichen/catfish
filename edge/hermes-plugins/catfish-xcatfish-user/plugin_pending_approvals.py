"""P63 (11/01): 列出「现在等员工批准的步骤」—— 给手机端用 (docs/MOBILE-CLIENT-DESIGN.md §5)。

    GET /api/catfish/approvals/pending  →  {"approvals": [{run_id, request_id, execution_generation,
                                                            command, description, session_id, created_at}]}

# 为什么要这个

手机上能批准, 原来只限**手机上发起的对话** —— 那条流是 Companion 手机通道自己开的, 审批事件
在流里。员工在电脑上发起的对话 / 早安里的任务对话也会停下来等批准, 那些事件在电脑的界面流里,
人不在电脑前就一直等到超时。手机通道定时问一下这里, 把还在等的推给已连上的手机。

# 数据从哪来

hermes API server 自己就记着每个 run 的状态 (`adapter._run_statuses`): 等批准时 status 是
`waiting_for_approval`, `approval` 字段就是流里发出去的那个 `approval.request` 事件
(api_server_openai_routes._register_stream_approval / api_server_runs 两条路都这么记)。
这里只读那张表, 不打补丁、不改 hermes 的任何行为。adapter 走 P7 stash (同 P26 / P30)。

回执仍走 hermes 原生的 POST /v1/runs/{run_id}/approval, 本模块不碰。
命令文本已由 hermes 脱敏 (`_redact_approval_command`), 这里原样转。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("catfish.xcatfish_user.pending_approvals")

ROUTE = "/api/catfish/approvals/pending"
_FIELDS = ("request_id", "execution_generation", "command", "description", "session_id")


def snapshot(adapter: Any) -> list[dict[str, Any]]:
    """adapter._run_statuses 里还在等批准的那些, 按开始时间排。"""
    statuses = getattr(adapter, "_run_statuses", None)
    if not isinstance(statuses, dict):
        return []
    out = []
    for run_id, st in list(statuses.items()):
        if not isinstance(st, dict) or st.get("status") != "waiting_for_approval":
            continue
        ev = st.get("approval") if isinstance(st.get("approval"), dict) else {}
        item = {"run_id": str(run_id), "created_at": st.get("created_at")}
        for k in _FIELDS:
            v = ev.get(k, st.get(k))
            if v is not None:
                item[k] = v
        out.append(item)
    out.sort(key=lambda x: x.get("created_at") or 0)
    return out


API_SESSION_PREFIX = "agent:main:api_server:dm:"


def queued_snapshot(queues: dict | None = None) -> list[dict[str, Any]]:
    """11/24 鸿波「为什么没有弹出授权卡片？」: 不是从窗口那条流发起的一轮 (卡住的对话解开后排队消息接着跑、
    Companion 中途重启) 不在 _run_statuses 里, 只在 hermes 的批准队列 (tools.approval._gateway_queues)。
    这里把队列里对话窗口的那些也列出来 —— 没有 run_id, 带上 session_key, 回执走 P15.2 POST
    /v1/sessions/{session_key}/approval (带 request_id, 只批这一条)。只读。"""
    if queues is None:
        try:
            from tools import approval as _ap  # noqa: PLC0415
        except ImportError:
            return []
        with _ap._lock:
            keys = list(_ap._gateway_queues)
        rows = {k: _ap.list_gateway_approvals(k) for k in keys}
    else:
        rows = queues
    out = []
    for key, entries in rows.items():
        if not isinstance(key, str) or not key.startswith(API_SESSION_PREFIX):
            continue
        for d in entries or []:
            if not isinstance(d, dict) or not d.get("request_id"):
                continue
            out.append({
                "session_key": key,
                "session_id": key[len(API_SESSION_PREFIX):],
                "request_id": d["request_id"],
                "command": d.get("command", ""),
                "description": d.get("description", ""),
            })
    return out


def merged(adapter: Any, queues: dict | None = None) -> list[dict[str, Any]]:
    """流里的 (有 run_id) 在前; 队列里同一个 request_id 的不再重复列。"""
    runs = snapshot(adapter)
    seen = {r.get("request_id") for r in runs}
    return runs + [q for q in queued_snapshot(queues) if q["request_id"] not in seen]


def register_routes(router: Any) -> bool:
    """跟 P44 / P49 同一个 fence (Application.__init__, router 未 freeze)。鉴权 fail closed。"""
    from aiohttp import web as _w  # noqa: PLC0415

    try:  # noqa: SIM105
        from . import plugin_route_auth  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        import plugin_route_auth  # type: ignore[no-redef]  # noqa: PLC0415

    async def _list(request):
        denied = plugin_route_auth.check_auth(request)
        if denied is not None:
            return denied
        adapter = request.app.get("_catfish_apiserver_adapter")
        return _w.json_response({"approvals": merged(adapter)})

    try:
        router.add_get(ROUTE, _list)
        logger.info("P63 route registered ✓: GET %s", ROUTE)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("P63 route 注册失败: %s", e)
        return False
