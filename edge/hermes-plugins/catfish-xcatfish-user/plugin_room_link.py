"""P49 (9/10 鸿波「部门级协同 → 只做横向」): 横向协同的三个 patch + 两个端点。

# 要解决的事

两个员工的小鲶跨机器协作 (A 让 B 的 bot 干活), 用 hermes 0.21 的 hosted room
(RoomLink)。hermes 的机制够用, 但信任模型是「持有 key = 完全信任」, 三处跟
catfish 的红线撞:

  1. `_handle_room_member_invitation` 签 grant 时 permissions 默认四个全给
     (approve / dispatch / status / stop) —— A 能批准 B 机器上的 execute_code。
  2. B 的 bot 跑完, final_response 直接进 run.completed 推给 A —— B 的本地
     数据出端, 没有任何审批点 (审批只在**工具调用**, 不在**回复出站**)。
  3. B 机器上的工具审批推进 run 的 SSE, 而 poll 那条 SSE 的是 A —— 审批方
     反了, 而且 event 里带 B 要执行的 command, 是信息泄露。

# 三个 patch 分别堵哪一处

  P49.1  issue_room_grant        permissions 没显式传 → 只给 dispatch + status
  P49.2  AIAgent.run_conversation  room run 的 final_response 截走, 进待审批表
  P49.3  register_gateway_notify   room run 的审批 cb 换成"只存不推"

判据统一用 `current_room_execution_policy() is not None` —— 那是 hermes 自己
在 api_server_runs.py:813 绑的 contextvar, 814 行 register_gateway_notify 在它
之后, 851 行 reset 在 run_conversation 返回之后。三个 patch 点上它都绑着。
catfish 的 _patch_asyncio_executor_for_contextvars 保证它跨 executor 线程。

# 出站审批为什么不做成"run 挂起等 B 批"

run 完成路径 (api_server_runs.py:894-917) 是 async 函数体, 没有可 patch 的
独立函数; 909 行 `_put_event_if_active(completed_event)` 在 910 行
`_set_run_status("completed")` **之前**, 在后者上拦已经出端。

所以截点提到 run_conversation 返回处: room run 照常 completed 但 output 为空,
真实回复存进 `_pending_outputs`, B 在 Companion 里点了之后**由 Companion 发进
房间** —— 跟 catfish_email_create_draft 同一个形状: 插件只管截和审批, 发送
在客户端。A 通过房间收 B 的回复, 不通过 run output。

# 改道后的审批 cb 为什么不调原 cb

原 cb (`_approval_notify`, api_server_runs.py:735) 做两件事: 更新 run 状态 +
`q.put_nowait(event)` 推 SSE。SSE 的消费者是 A, event 里有 B 机器上要跑的
command。所以只存不推 —— run 从 A 视角一直是 running, B 批了 agent 继续,
最终 completed。A 不需要知道 B 在审批。

B 批准走 P15.2 现成的 `POST /v1/sessions/{sid}/approval` —— /v1/runs 路径的
approval_session_key 就是 run_id (api_server_runs.py:613), 一行不用改。

# 升级风险

三个目标全进 plugin_verify._PATCH_TARGETS。其中 issue_room_grant 的 permissions
子集能力 hermes 本来就有 (hosted_room_peer.py:739-746), 只是 handler 没暴露 ——
这条值得给上游提 PR, 接了 P49.1 就不需要了。

# 副作用 (写明, 不藏)

P49.1 也会影响 tui_gateway/methods_groups.py:311 (hermes 桌面自己的房间邀请),
它同样没显式传 permissions。Companion 不走 TUI, 且方向是限权, 可接受。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger("catfish.xcatfish_user.plugin")

_LOCK = threading.Lock()

#: run_id → 待 B 审批的工具调用 (approval_data 原样, 含 command)
_pending_approvals: dict[str, dict[str, Any]] = {}
#: run_id → 待 B 审批的出站回复
_pending_outputs: dict[str, dict[str, Any]] = {}

#: 横向协同里 A 能拿到的权限。没有 approve —— B 机器上的工具执行只能 B 批;
#: 没有 stop —— A 不能中断 B 正在跑的活。
ROOM_LINK_PERMISSIONS: tuple[str, ...] = ("dispatch", "status")

#: 待审批条目最长保留 (秒)。超过说明 B 一直没处理, 不能无限攒。
PENDING_TTL_SECONDS = 24 * 3600

_ORIG: dict[str, Any] = {}
_PATCHED = False


# ═══════════════════════════════════════════════════════════════
#  判据
# ═══════════════════════════════════════════════════════════════
def _is_room_run() -> bool:
    """当前线程/上下文是不是 RoomLink 发起的 run。

    唯一真源是 hermes 自己绑的 contextvar。不用 session 标题、不用 header,
    那些都是二手信息。
    """
    try:
        from gateway.hosted_room_execution_policy import current_room_execution_policy
    except ImportError:
        return False
    return current_room_execution_policy() is not None


def _gc_pending(now: float | None = None) -> None:
    """清掉超过 TTL 的条目。调用方持锁。"""
    now = time.time() if now is None else now
    cutoff = now - PENDING_TTL_SECONDS
    for table in (_pending_approvals, _pending_outputs):
        stale = [k for k, v in table.items() if float(v.get("_at", 0)) < cutoff]
        for k in stale:
            table.pop(k, None)


# ═══════════════════════════════════════════════════════════════
#  P49.1  issue_room_grant → 只给 dispatch + status
# ═══════════════════════════════════════════════════════════════
def _patch_issue_room_grant() -> None:
    from gateway import hosted_room_peer as _m

    orig = _m.issue_room_grant

    def patched_issue_room_grant(secret, *args, **kwargs):
        # 只在调用方**没显式传** permissions 时注入。刷新路径
        # (_handle_room_member_grant_refresh) 传 claims["permissions"] 原样
        # 继承, 不受影响 —— 所以限了一次就一直限着, 不会被刷新放大回去。
        if "permissions" not in kwargs:
            kwargs["permissions"] = ROOM_LINK_PERMISSIONS
        return orig(secret, *args, **kwargs)

    _ORIG["issue_room_grant"] = orig
    _m.issue_room_grant = patched_issue_room_grant
    logger.info("P49.1 issue_room_grant patched: 默认 permissions → %s", ROOM_LINK_PERMISSIONS)


# ═══════════════════════════════════════════════════════════════
#  P49.2  run_conversation → room run 的 final_response 截走
# ═══════════════════════════════════════════════════════════════
def _patch_run_conversation() -> None:
    from run_agent import AIAgent

    orig = AIAgent.run_conversation

    def patched_run_conversation(self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        if not _is_room_run():
            return result
        if not isinstance(result, dict):
            return result
        final = result.get("final_response")
        if not final:
            return result

        # run_id 从 task_id 来: api_server_runs.py:775
        #   effective_task_id = session_id or run_id
        # 跟 613 行 approval_session_key = run_id 是同一个值 (session_id 为空时)。
        run_key = kwargs.get("task_id") or (args[2] if len(args) > 2 else None) or ""
        run_key = str(run_key)

        with _LOCK:
            _gc_pending()
            _pending_outputs[run_key] = {
                "final_response": final,
                "_at": time.time(),
            }
        logger.info(
            "P49.2 room run %s: final_response (%d 字) 截进待审批表, run output 置空",
            run_key, len(final),
        )
        # 浅拷贝再改, 别动 hermes 内部持有的那个 dict
        result = dict(result)
        result["final_response"] = ""
        return result

    _ORIG["run_conversation"] = orig
    AIAgent.run_conversation = patched_run_conversation
    logger.info("P49.2 AIAgent.run_conversation patched: room run 出站截走")


# ═══════════════════════════════════════════════════════════════
#  P49.3  register_gateway_notify → room run 的审批只存不推
# ═══════════════════════════════════════════════════════════════
def _patch_register_gateway_notify() -> None:
    from tools import approval as _m

    orig = _m.register_gateway_notify

    def patched_register_gateway_notify(session_key: str, cb) -> None:
        if not _is_room_run():
            return orig(session_key, cb)

        def room_cb(approval_data: dict[str, Any]) -> None:
            with _LOCK:
                _gc_pending()
                _pending_approvals[session_key] = {
                    **dict(approval_data or {}),
                    "_at": time.time(),
                }
            logger.info(
                "P49.3 room run %s: 工具审批截进待审批表 (不推 SSE, A 看不到 command)",
                session_key,
            )

        return orig(session_key, room_cb)

    _ORIG["register_gateway_notify"] = orig
    _m.register_gateway_notify = patched_register_gateway_notify
    logger.info("P49.3 register_gateway_notify patched: room run 审批改道")


# ═══════════════════════════════════════════════════════════════
#  端点: B 的 Companion 读待审批 / 处理完清掉
# ═══════════════════════════════════════════════════════════════
ROUTE_LIST = "/api/catfish/room-link/pending"
ROUTE_OUTPUT_ITEM = "/api/catfish/room-link/outputs/{run_id}"


def snapshot_pending() -> dict[str, Any]:
    """给 Companion 看的快照。approval 的 command 已经在 hermes 侧脱敏过
    (_approval_notify 调 _redact_approval_command), 这里原样透。"""
    with _LOCK:
        _gc_pending()
        return {
            "approvals": [
                {"run_id": k, **{kk: vv for kk, vv in v.items() if kk != "_at"}}
                for k, v in _pending_approvals.items()
            ],
            "outputs": [
                {"run_id": k, "final_response": v["final_response"]}
                for k, v in _pending_outputs.items()
            ],
        }


def pop_output(run_id: str) -> dict[str, Any] | None:
    """Companion 发完 (或拒了) 之后清掉。返被清的那条, 没有返 None。"""
    with _LOCK:
        return _pending_outputs.pop(run_id, None)


def pop_approval(run_id: str) -> dict[str, Any] | None:
    """B 批完 (走 P15.2 的 RPC) 之后清掉。"""
    with _LOCK:
        return _pending_approvals.pop(run_id, None)


def register_routes(router: Any) -> bool:
    """在 aiohttp router 上挂端点。时机跟 P44 同一个 fence (Application.__init__,
    router 未 freeze)。鉴权走 plugin_route_auth (fail closed)。"""
    from aiohttp import web as _w  # noqa: PLC0415

    try:  # noqa: SIM105
        from . import plugin_route_auth  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        import plugin_route_auth  # type: ignore[no-redef]  # noqa: PLC0415

    async def _list(request):
        denied = plugin_route_auth.check_auth(request)
        if denied is not None:
            return denied
        return _w.json_response(snapshot_pending())

    async def _delete_output(request):
        denied = plugin_route_auth.check_auth(request)
        if denied is not None:
            return denied
        run_id = request.match_info.get("run_id", "")
        gone = pop_output(run_id)
        if gone is None:
            return _w.json_response({"error": "not found"}, status=404)
        return _w.json_response({"ok": True, "run_id": run_id})

    try:
        router.add_get(ROUTE_LIST, _list)
        router.add_delete(ROUTE_OUTPUT_ITEM, _delete_output)
        logger.info("P49 routes registered ✓: GET %s / DELETE %s", ROUTE_LIST, ROUTE_OUTPUT_ITEM)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("P49 route 注册失败: %s", e)
        return False


# ═══════════════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════════════
def install() -> None:
    """幂等。三个 patch 任一失败整体不装 —— 只装两个会留一个洞
    (比如 P49.1 没装 A 就有 approve 权限, 那 P49.3 的改道形同虚设)。"""
    global _PATCHED
    if _PATCHED:
        return
    _patch_issue_room_grant()
    _patch_run_conversation()
    _patch_register_gateway_notify()
    _PATCHED = True
    logger.info("P49 room-link installed ✓ (3 patches)")


def uninstall() -> None:
    """单测用: 还原三个目标。"""
    global _PATCHED
    if not _PATCHED:
        return
    from gateway import hosted_room_peer as _hrp
    from run_agent import AIAgent
    from tools import approval as _ap

    _hrp.issue_room_grant = _ORIG.pop("issue_room_grant")
    AIAgent.run_conversation = _ORIG.pop("run_conversation")
    _ap.register_gateway_notify = _ORIG.pop("register_gateway_notify")
    with _LOCK:
        _pending_approvals.clear()
        _pending_outputs.clear()
    _PATCHED = False
