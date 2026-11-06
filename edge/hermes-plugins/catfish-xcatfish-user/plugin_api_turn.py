"""P65 (11/06, hermes v0.23): Companion 经 api_server 发的对话, 员工身份跟着「会话」走。

# 为什么

v0.22 及以前: 一个 HTTP 请求 → `APIServerAdapter._create_agent` 当场建 agent 跑。P6 在那里把请求头 X-Catfish-User
(中间件放进 CV_CF_USER) 写到 `agent._catfish_outgoing_user`, P10 再按它给发往鲶鱼网关的请求带上员工身份。

v0.23 起「一个网关管所有会话」: 请求只做一件事 —— 写一条排队记录 (`gateway.session_api_turn.admit_api_steps`),
真正跑这一轮的是网关按会话排队的后台任务 (`_drain` → `GatewayRunner._handle_message`), 根本不经过 `_create_agent`。
实测 (docs/HERMES-023-UPGRADE-ASSESSMENT.md): 主对话发给网关的请求**不带** X-Catfish-User。

# 怎么做

1. 包 `admit_api_steps`: 请求还在自己的上下文里 (中间件设好的 CV_CF_USER / CV_CF_SOURCE 读得到),
   把「会话 → 员工 / 来源」登记进 session_registry —— 请求带的会话号, 和 hermes 实际排进去的会话号 (续接压缩后的根会话) 都登记。
   请求没带会话号时先替 hermes 生成一个 (它自己也是 uuid4().hex, 一样的格式), 好登记。
2. `outgoing_user(agent)` / `outgoing_source(agent)`: P2 / P10 取员工时, api_server (或不知平台) 的 agent 先按会话号查登记
   (查不到再顺着 parent_session_id 往上找几层, 压缩续接出来的子会话), 再走 resolver 原来那条链。
   **不拿后台任务里的 CV 兜底**: 排队任务可能是别的请求建的, CV 不一定是这个会话的人 (RoomLink 远端请求也走这条路)。

v0.22 没有 `gateway.session_api_turn`, 第 1 步跳过 (不算失败); 第 2 步在 v0.22 上也成立 (登记表由 P2 / P6 填)。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger("catfish.xcatfish_user.plugin")

_PARENT_HOPS = 5


def _sib(name):
    """兄弟模块 —— 跟 plugin_session._sib 同一套 (相对 import 优先, 命中 sys.modules 里同一个模块对象, 见那边的说明)。"""
    from importlib import import_module  # noqa: PLC0415
    if __package__:
        try:
            return import_module(f".{name}", package=__package__)
        except (ImportError, SystemError, ValueError, TypeError):
            pass
    from plugin import _import_sibling  # noqa: PLC0415
    return _import_sibling(name)


def _registry():
    return _sib("session_registry")


def _ctx():
    return _sib("plugin_ctx")


def _session_chain(agent) -> list[str]:
    """agent 的会话号, 再加上顺着 parent_session_id 往上几层 (压缩续接出来的子会话 → 原会话)。"""
    sid = str(getattr(agent, "session_id", "") or "").strip()
    if not sid:
        return []
    chain = [sid]
    db = getattr(agent, "_session_db", None)
    get = getattr(db, "get_session", None)
    if not callable(get):
        return chain
    cur = sid
    for _ in range(_PARENT_HOPS):
        try:
            row = get(cur) or {}
        except Exception:  # noqa: BLE001  查不到就到此为止, 不能拖垮发请求
            break
        parent = str((row.get("parent_session_id") if isinstance(row, dict) else "") or "").strip()
        if not parent or parent in chain:
            break
        chain.append(parent)
        cur = parent
    return chain


def _is_api_agent(agent) -> bool:
    platform = str(getattr(agent, "platform", "") or "").lower().strip()
    return platform in ("", "api_server")


def outgoing_user(agent) -> str:
    """这一轮主对话是哪位员工的 (P2 / P10 用)。"""
    attr = str(getattr(agent, "_catfish_outgoing_user", "") or "").strip()
    if attr:
        return attr
    if _is_api_agent(agent):
        reg = _registry()
        for sid in _session_chain(agent):
            user = (reg.lookup(sid) or "").strip()
            if user:
                try:
                    agent._catfish_outgoing_user = user   # 别的补丁 (审批留痕 / 记忆闸) 也认这个属性
                except Exception:  # noqa: BLE001
                    pass
                return user
    return (_sib("resolver").resolve_for_agent(agent) or "").strip()


def outgoing_source(agent) -> str:
    """这一轮的来源标记 (X-Catfish-Source): 登记表里有就用 (Companion 的 catfish_source), 没有返回空。"""
    if not _is_api_agent(agent):
        return ""
    reg = _registry()
    for sid in _session_chain(agent):
        src = (reg.lookup_source(sid) or "").strip()
        if src:
            return src
    return ""


def _register(session_id: str) -> None:
    if not session_id:
        return
    try:
        user = (_ctx().CV_CF_USER.get() or "").strip()
        source = (_ctx().CV_CF_SOURCE.get() or "").strip()
    except Exception:  # noqa: BLE001
        return
    reg = _registry()
    if user:
        reg.register(session_id, user)
    if source:
        reg.register_source(session_id, source)


def _patch_p65_api_turn_identity() -> None:
    """包 gateway.session_api_turn.admit_api_steps (v0.23+)。v0.22 没有这个模块 → 跳过, 不算失败。"""
    try:
        from gateway import session_api_turn  # noqa: PLC0415
    except ImportError:
        logger.info("P65: hermes 没有 gateway.session_api_turn (v0.22 及以前), 对话仍走 _create_agent, 跳过")
        return
    _orig = session_api_turn.admit_api_steps
    if getattr(_orig, "_catfish_p65", False):
        return

    def admit_api_steps(adapter, **kwargs: Any):
        if not kwargs.get("session_id"):
            kwargs["session_id"] = uuid.uuid4().hex   # 跟 hermes 自己生成的同格式, 只是提前生成好登记
        _register(str(kwargs["session_id"]))
        result = yield from _orig(adapter, **kwargs)
        try:
            ref = result[1]
            actual = str(getattr(ref, "session_id", "") or "")
            if actual and actual != kwargs["session_id"]:
                _register(actual)   # 压缩续接: hermes 排进的是根会话
        except Exception:  # noqa: BLE001
            pass
        return result

    admit_api_steps._catfish_p65 = True  # type: ignore[attr-defined]
    session_api_turn.admit_api_steps = admit_api_steps
    logger.info("P65 ✓ api 对话排队时登记会话 → 员工 (v0.23 不再经过 _create_agent)")
