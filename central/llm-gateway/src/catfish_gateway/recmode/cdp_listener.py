"""BL-LEARN-RECMODE / CDP listener (5/14 v1 真接 ws — 任务 #63).

连 Catfish Chrome ws://localhost:9222 监听 events 落 JSONL + keyframe 截图.
**不要 Chrome 扩展** — Catfish Chrome 已开 CDP (BL-CHROME), 后端直接订阅.

Events 捕获策略 (设计文档 §3.2):
- Page.frameNavigated  → URL 切换 = keyframe 触发
- DOM.documentUpdated  → 大 DOM 变化 = keyframe 触发 (节流 500ms)
- Page.javascriptDialogOpening → alert/confirm 弹窗
- Network.responseReceived → API 调用 (用户操作引发的, 推断业务逻辑)
- 长停顿 ≥ 3s → keyframe 触发 (用户在看数据)

输出: ~/.catfish/recordings/<session_id>/
  events.jsonl   (每行一个 event, ts + kind + content)
  screenshots/   (PNG 每张 keyframe)
  meta.json      (session 元数据 + 截图 keyframe 计数)

跑法:
    from catfish_gateway.recmode.cdp_listener import CDPRecordingSession
    sess = await CDPRecordingSession.start(session_id="rec_abc", chrome_ws="ws://localhost:9222")
    # ... 用户操作 Catfish Chrome ...
    summary = await sess.stop()
    # summary = {events_count, keyframes, duration_s, output_dir}

依赖:
    pip install websockets  (~1MB, asyncio CDP 客户端)

5/14 v0 → v1 升级 (任务 #63): 真接 websockets ws + Page.captureScreenshot
+ event loop. 真用了, 不只是占位. Network 监听 + DOM diff summary 算法
留 V2 (#68).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# websockets 包 — 5/14 装上 (pip install websockets, ~1MB).
# import 时不挂, 真用 (start) 时才连 ws — 测试环境也能 import 这个模块.
try:
    import websockets  # type: ignore
    _HAS_WS = True
except ImportError:
    websockets = None  # type: ignore
    _HAS_WS = False

logger = logging.getLogger("catfish.recmode.cdp")


# ─── 常量 ──────────────────────────────────────────────────

_DEFAULT_CHROME_WS = "ws://localhost:9222"
_LONG_PAUSE_THRESHOLD_S = 3.0  # 用户停 ≥ 3s = keyframe (在看数据)
_DOM_THROTTLE_MS = 500  # DOM mutation 节流 (避免每次 click 都 dump 截图)
_MAX_RECORDING_DURATION_S = 30 * 60  # 30 min 安全上限, 防忘点停录


# ─── data class ────────────────────────────────────────────


@dataclass
class CDPEvent:
    """一条 CDP event, 写入 events.jsonl."""
    ts: float
    kind: str  # page_navigated / click / input / dom_changed / long_pause / network_response
    content: dict
    screenshot_id: str | None = None  # 关联 keyframe (如有)


@dataclass
class RecordingState:
    """一次 RecMode 会话的运行时状态."""
    session_id: str
    output_dir: Path
    started_at: float
    events: list[CDPEvent] = field(default_factory=list)
    keyframe_count: int = 0
    last_event_ts: float = 0.0
    last_keyframe_ts: float = 0.0
    chrome_ws: str = _DEFAULT_CHROME_WS
    _ws: Any = None  # websockets connection
    _msg_id_counter: int = 0
    _stop_requested: bool = False
    # 待响应的 send (id → asyncio.Future), 让 _cdp_send 能 await response
    _pending_responses: dict[int, asyncio.Future] = field(default_factory=dict)
    # 后台 tasks (event loop / detector / watchdog), stop 时取消
    _bg_tasks: list[asyncio.Task] = field(default_factory=list)


# ─── CDP 协议低层 ──────────────────────────────────────────


async def _cdp_send(
    state: RecordingState,
    method: str,
    params: dict | None = None,
    timeout: float = 10.0,
) -> dict:
    """发 CDP request, 等响应 (按 id 匹配, 超时抛 TimeoutError).

    依赖 _event_loop 持续 recv ws messages, 把 id 命中的 future 完成.
    test 路径下没起 _event_loop, 这函数会卡 → caller 用 mock.
    """
    state._msg_id_counter += 1
    msg_id = state._msg_id_counter
    msg = {"id": msg_id, "method": method}
    if params:
        msg["params"] = params
    if state._ws is None:
        raise RuntimeError("_cdp_send: ws 未连接")

    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    state._pending_responses[msg_id] = fut
    try:
        await state._ws.send(json.dumps(msg))
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("CDP send timeout: method=%s id=%d", method, msg_id)
        raise
    finally:
        state._pending_responses.pop(msg_id, None)


async def _capture_screenshot(state: RecordingState) -> str:
    """真触发 Page.captureScreenshot, 落 PNG 到 screenshots/, 返 keyframe_id.

    没 ws 连接时退化为 noop (只 ++ counter, 不真截图) — 给 test / 长停顿 detector
    在没真 ws 时也能运行不挂.
    """
    state.keyframe_count += 1
    kf_id = f"kf_{state.keyframe_count:04d}"
    state.last_keyframe_ts = time.time()

    if state._ws is None:
        logger.debug("CDP keyframe %s noop (ws 未连)", kf_id)
        return kf_id

    try:
        resp = await _cdp_send(state, "Page.captureScreenshot", {"format": "png"})
        data_b64 = resp.get("result", {}).get("data", "")
        if data_b64:
            png_bytes = base64.b64decode(data_b64)
            out_path = state.output_dir / "screenshots" / f"{kf_id}.png"
            out_path.write_bytes(png_bytes)
            logger.debug("CDP keyframe %s 落档 %d bytes", kf_id, len(png_bytes))
        else:
            logger.warning("CDP keyframe %s 拿到空 data, 跳过写盘", kf_id)
    except Exception:  # noqa: BLE001
        logger.warning("CDP keyframe %s 截图失败 (不致命)", kf_id, exc_info=True)
    return kf_id


# ─── event handler (按 CDP event method 分发) ─────────────


async def _on_page_navigated(state: RecordingState, params: dict) -> None:
    """Page.frameNavigated → URL 切换, 必触发 keyframe."""
    frame = params.get("frame", {})
    url = frame.get("url", "")
    # 只关心 main frame
    if frame.get("parentId"):
        return
    kf_id = await _capture_screenshot(state)
    state.events.append(CDPEvent(
        ts=time.time() - state.started_at,
        kind="page_navigated",
        content={"url": url, "title": ""},  # title 5/26 真做时从 Runtime.evaluate 拿
        screenshot_id=kf_id,
    ))


async def _on_dom_updated(state: RecordingState, params: dict) -> None:
    """DOM.documentUpdated → 大 DOM 变化. 节流 500ms 避免每次都触发."""
    now = time.time()
    if (now - state.last_event_ts) * 1000 < _DOM_THROTTLE_MS:
        return  # 节流
    state.last_event_ts = now
    # 这里只记 event, keyframe 看 DOM 变化大小再决定 (5/26 加 mutation summary)
    state.events.append(CDPEvent(
        ts=now - state.started_at,
        kind="dom_changed",
        content={"summary": "DOM updated"},
    ))


async def _on_dialog_opening(state: RecordingState, params: dict) -> None:
    """JS dialog (alert/confirm) — 一定 keyframe + 记下文本."""
    kf_id = await _capture_screenshot(state)
    state.events.append(CDPEvent(
        ts=time.time() - state.started_at,
        kind="js_dialog",
        content={
            "type": params.get("type", "alert"),
            "message": params.get("message", "")[:200],
        },
        screenshot_id=kf_id,
    ))


async def _on_network_response(state: RecordingState, params: dict) -> None:
    """Network.responseReceived — 推断业务逻辑 (e.g. POST /api/qual/list 表示拉资质)."""
    resp = params.get("response", {})
    url = resp.get("url", "")
    # 只记 API 调用, 不记静态资源
    if any(url.endswith(ext) for ext in (".js", ".css", ".png", ".jpg", ".woff", ".ico")):
        return
    state.events.append(CDPEvent(
        ts=time.time() - state.started_at,
        kind="network_response",
        content={
            "url": url,
            "status": resp.get("status"),
            "method": params.get("type", "?"),
        },
    ))


# 长停顿 detector (后台 task 跑)
async def _long_pause_detector(state: RecordingState) -> None:
    """每秒检查上次 event 距今多久, ≥ 3s 触发 keyframe."""
    while not state._stop_requested:
        await asyncio.sleep(1.0)
        now = time.time()
        gap = now - state.last_event_ts
        if gap >= _LONG_PAUSE_THRESHOLD_S and (now - state.last_keyframe_ts) >= _LONG_PAUSE_THRESHOLD_S:
            kf_id = await _capture_screenshot(state)
            state.events.append(CDPEvent(
                ts=now - state.started_at,
                kind="long_pause",
                content={"duration_s": round(gap, 1)},
                screenshot_id=kf_id,
            ))
            state.last_event_ts = now  # 重置, 防一直触发


# 安全上限 detector (防忘点停录)
async def _max_duration_watchdog(state: RecordingState) -> None:
    """录屏超 30 min 自动停, 防忘点停录浪费磁盘."""
    await asyncio.sleep(_MAX_RECORDING_DURATION_S)
    if not state._stop_requested:
        logger.warning(
            "RecMode session %s 超 %ds 自动停录 (防忘记关)",
            state.session_id, _MAX_RECORDING_DURATION_S,
        )
        state._stop_requested = True


# ─── event loop (recv ws + dispatch) ──────────────────────


_CDP_EVENT_HANDLERS = {
    "Page.frameNavigated": _on_page_navigated,
    "DOM.documentUpdated": _on_dom_updated,
    "Page.javascriptDialogOpening": _on_dialog_opening,
    "Network.responseReceived": _on_network_response,
}


async def _event_loop(state: RecordingState) -> None:
    """持续 recv ws messages — id 响应 → 完成 future; method event → dispatch.

    退出条件: state._stop_requested 或 ws closed.
    """
    if state._ws is None:
        return
    try:
        async for raw in state._ws:
            if state._stop_requested:
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.debug("CDP recv 坏 json: %r", raw[:80])
                continue
            # 响应 (含 id 字段)
            if "id" in msg:
                fut = state._pending_responses.get(msg["id"])
                if fut and not fut.done():
                    fut.set_result(msg)
                continue
            # event (含 method 字段)
            method = msg.get("method")
            params = msg.get("params") or {}
            handler = _CDP_EVENT_HANDLERS.get(method)
            if handler:
                try:
                    await handler(state, params)
                except Exception:  # noqa: BLE001
                    logger.warning("CDP handler %s 失败 (不致命)", method, exc_info=True)
    except Exception:  # noqa: BLE001
        # ws 断了 / closed
        if not state._stop_requested:
            logger.warning("CDP event_loop 异常退出 (ws closed?)", exc_info=True)


# ─── 主入口 ────────────────────────────────────────────────


class CDPRecordingSession:
    """RecMode 一次录屏 session 的封装. 跟 Companion UI 状态机对应."""

    def __init__(self, state: RecordingState):
        self.state = state

    @classmethod
    async def start(
        cls,
        session_id: str,
        chrome_ws: str = _DEFAULT_CHROME_WS,
        output_root: Path | None = None,
        connect_ws: bool = True,
    ) -> "CDPRecordingSession":
        """开 RecMode session — 连 CDP ws + 起后台 detector tasks.

        Args:
            session_id: 唯一 ID (caller 给)
            chrome_ws: Catfish Chrome 的 CDP endpoint
            output_root: 输出根目录 (默认 ~/.catfish/recordings/)
            connect_ws: 是否真连 ws (test 路径传 False 跑骨架)
        """
        if output_root is None:
            catfish_home = os.environ.get("CATFISH_HOME", "").strip()
            output_root = Path(catfish_home).expanduser() / "recordings" if catfish_home else Path.home() / ".catfish" / "recordings"

        output_dir = output_root / session_id
        (output_dir / "screenshots").mkdir(parents=True, exist_ok=True)

        state = RecordingState(
            session_id=session_id,
            output_dir=output_dir,
            started_at=time.time(),
            chrome_ws=chrome_ws,
        )

        # 真连 ws (除非 test 跳过)
        if connect_ws:
            if not _HAS_WS:
                raise RuntimeError(
                    "websockets 包未装. pip install websockets. "
                    "或 test 路径传 connect_ws=False."
                )
            try:
                state._ws = await websockets.connect(chrome_ws, max_size=20 * 1024 * 1024)  # 20MB 截图
            except Exception as e:
                raise RuntimeError(
                    f"连 Catfish Chrome CDP 失败 ({chrome_ws}): {e}. "
                    f"看 Catfish Chrome 是不是用 --remote-debugging-port=9222 启动了."
                ) from e
            # 起 event loop (recv ws + dispatch handlers)
            state._bg_tasks.append(asyncio.create_task(_event_loop(state)))
            # 启用 CDP domain
            try:
                await _cdp_send(state, "Page.enable", timeout=5.0)
                await _cdp_send(state, "DOM.enable", timeout=5.0)
                await _cdp_send(state, "Network.enable", timeout=5.0)
                await _cdp_send(state, "Runtime.enable", timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("CDP enable domains 超时 (继续, 部分 events 可能漏)")
            # 起 detector + watchdog
            state._bg_tasks.append(asyncio.create_task(_long_pause_detector(state)))
            state._bg_tasks.append(asyncio.create_task(_max_duration_watchdog(state)))
            # 立刻拍一张初始截图 (用户在哪页开始的)
            await _capture_screenshot(state)

        logger.info(
            "RecMode session 开始: id=%s output=%s ws=%s",
            session_id, output_dir, "真连" if connect_ws and state._ws else "占位",
        )
        return cls(state)

    async def stop(self) -> dict:
        """停录 — 关 ws + 取消后台 tasks + flush events.jsonl + 写 meta.json."""
        self.state._stop_requested = True
        # cancel 后台 tasks
        for t in self.state._bg_tasks:
            if not t.done():
                t.cancel()
        # 等 cancel 干净 (gather suppress CancelledError)
        if self.state._bg_tasks:
            await asyncio.gather(*self.state._bg_tasks, return_exceptions=True)
        # 关 ws
        if self.state._ws is not None:
            try:
                await self.state._ws.close()
            except Exception:  # noqa: BLE001
                pass

        # flush events
        events_path = self.state.output_dir / "events.jsonl"
        with events_path.open("w", encoding="utf-8") as f:
            for e in self.state.events:
                f.write(json.dumps({
                    "ts": round(e.ts, 3),
                    "kind": e.kind,
                    **e.content,
                    "screenshot_id": e.screenshot_id,
                }, ensure_ascii=False) + "\n")

        # 写 meta
        duration = time.time() - self.state.started_at
        meta = {
            "session_id": self.state.session_id,
            "started_at": self.state.started_at,
            "stopped_at": time.time(),
            "duration_s": round(duration, 1),
            "events_count": len(self.state.events),
            "keyframes_count": self.state.keyframe_count,
            "chrome_ws": self.state.chrome_ws,
        }
        (self.state.output_dir / "meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        logger.info(
            "RecMode session 停止: id=%s duration=%.1fs events=%d keyframes=%d",
            self.state.session_id, duration, len(self.state.events), self.state.keyframe_count,
        )
        return {
            **meta,
            "output_dir": str(self.state.output_dir),
            "events_path": str(events_path),
        }


# ─── 给 gateway endpoint 用的 module-level helper ──────────


_active_sessions: dict[str, CDPRecordingSession] = {}


async def start_recording(
    session_id: str,
    chrome_ws: str = _DEFAULT_CHROME_WS,
    connect_ws: bool = True,
) -> dict:
    """gateway endpoint /api/learn/start 调用. 重复 session_id → 错误.

    connect_ws=False 走测试 / dev 路径 (不真连 Catfish Chrome).
    """
    if session_id in _active_sessions:
        raise ValueError(f"session {session_id} 已在录中, 重复 start")
    sess = await CDPRecordingSession.start(session_id, chrome_ws=chrome_ws, connect_ws=connect_ws)
    _active_sessions[session_id] = sess
    return {
        "session_id": session_id,
        "started_at": sess.state.started_at,
        "output_dir": str(sess.state.output_dir),
        "ws_connected": sess.state._ws is not None,
    }


async def stop_recording(session_id: str) -> dict:
    """gateway endpoint /api/learn/stop 调用. 不存在 → 错误."""
    sess = _active_sessions.pop(session_id, None)
    if sess is None:
        raise ValueError(f"session {session_id} 没在录中, 无法 stop")
    return await sess.stop()


def list_active() -> list[str]:
    """给监控用 — 看现在有几个 RecMode session 在跑."""
    return list(_active_sessions.keys())


__all__ = [
    "CDPRecordingSession",
    "CDPEvent",
    "RecordingState",
    "start_recording",
    "stop_recording",
    "list_active",
]
