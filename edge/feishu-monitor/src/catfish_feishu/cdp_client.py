"""CDP 客户端：连 Chrome，找到飞书 tab，注入 observer JS，接收新消息事件。

设计点：
    1. 通过 HTTP /json 端点发现所有 tab，过滤出飞书域名的
    2. 对每个飞书 tab 开一个 CDP WebSocket，注入 MutationObserver JS
    3. JS 里用 Runtime.addBinding 注册一个 __catfishEmit 函数（跨 JS→Python 通道）
    4. 新消息到达 → JS 调 __catfishEmit → Python 收到事件
    5. 多 tab 并行监听（员工可能同时开多个飞书群页面）

注意：这个实现的容错性优先于优雅性 —— 飞书 DOM 可能变，单 tab 挂掉不能影响别的。
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

import httpx
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

logger = logging.getLogger("catfish.feishu.cdp")


OBSERVER_JS_PATH = Path(__file__).parent / "scripts" / "feishu_dom_observer.js"


def _observer_js() -> str:
    """读取注入 JS 的内容。打成 package 后仍有效（用 package_data）。"""
    if not OBSERVER_JS_PATH.exists():
        raise FileNotFoundError(f"找不到 observer JS: {OBSERVER_JS_PATH}")
    return OBSERVER_JS_PATH.read_text(encoding="utf-8")


async def list_targets(http_base: str) -> list[dict[str, Any]]:
    """从 CDP HTTP /json 拿所有 tab。http_base 类似 http://localhost:9222。"""
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(f"{http_base}/json")
        r.raise_for_status()
        return r.json()


def pick_feishu_targets(
    targets: list[dict[str, Any]],
    domains: list[str],
) -> list[dict[str, Any]]:
    """从所有 tab 里挑出飞书域名的 page 类型 target。"""
    out = []
    for t in targets:
        if t.get("type") != "page":
            continue
        url = t.get("url") or ""
        if any(d in url for d in domains):
            out.append(t)
    return out


def cdp_http_base_from_ws(ws_url: str) -> str:
    """从 CDP WebSocket URL 反推 HTTP 端点。

    ws://localhost:9222/devtools/browser/xxx  →  http://localhost:9222
    """
    # 不用 urlparse，避免 ws:// 被当成不支持的 scheme
    without_scheme = ws_url.split("://", 1)[-1]
    host = without_scheme.split("/", 1)[0]
    return f"http://{host}"


class CdpTabSession:
    """订阅单个飞书 tab 的 CDP 事件。"""

    def __init__(
        self,
        target: dict[str, Any],
        on_event: Callable[[dict[str, Any]], Any],
    ) -> None:
        self.target = target
        self.target_id = target.get("id") or target.get("targetId")
        self.ws_url: str = target["webSocketDebuggerUrl"]
        self.on_event = on_event
        self._msg_id = 0
        self._ws: Optional[websockets.WebSocketClientProtocol] = None

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def _send(self, method: str, params: Optional[dict[str, Any]] = None) -> int:
        mid = self._next_id()
        await self._ws.send(json.dumps({  # type: ignore[union-attr]
            "id": mid,
            "method": method,
            "params": params or {},
        }))
        return mid

    async def run(self) -> None:
        """连上 CDP WebSocket，注入 observer，循环读消息分发。"""
        logger.info("开始监听 feishu tab: %s", self.target.get("url"))
        try:
            async with websockets.connect(self.ws_url, max_size=10 * 1024 * 1024) as ws:
                self._ws = ws
                await self._setup()
                await self._loop()
        except (ConnectionClosed, WebSocketException, OSError) as e:
            logger.warning("tab 监听断开（正常情况下员工切换页面会触发）：%s", e)

    async def _setup(self) -> None:
        """开启 Runtime + Page domain，注册 binding，注入 observer JS。"""
        await self._send("Runtime.enable")
        await self._send("Page.enable")
        # addBinding：让页面里的 JS 可以调 window.__catfishEmit(jsonStr)
        await self._send("Runtime.addBinding", {"name": "__catfishEmit"})
        # 注入监听器 JS
        await self._send("Runtime.evaluate", {
            "expression": _observer_js(),
            "awaitPromise": False,
            "returnByValue": False,
        })

    async def _loop(self) -> None:
        assert self._ws is not None
        async for raw in self._ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await self._handle(msg)

    async def _handle(self, msg: dict[str, Any]) -> None:
        method = msg.get("method")
        if method == "Runtime.bindingCalled":
            params = msg.get("params") or {}
            if params.get("name") != "__catfishEmit":
                return
            payload_str = params.get("payload") or "{}"
            try:
                payload = json.loads(payload_str)
            except json.JSONDecodeError:
                logger.debug("binding payload 解析失败")
                return
            try:
                res = self.on_event(payload)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:  # noqa: BLE001
                logger.exception("on_event 处理失败: %s", e)


async def watch_all_feishu_tabs(
    http_base: str,
    domains: list[str],
    on_event: Callable[[dict[str, Any]], Any],
    rescan_seconds: int = 15,
) -> AsyncIterator[None]:
    """常驻循环：周期性重扫 tab 列表，对新飞书 tab 建立监听。

    yield 让调用方也能做其他 await，避免 hog event loop。
    """
    active: dict[str, asyncio.Task] = {}

    while True:
        try:
            targets = await list_targets(http_base)
        except (httpx.HTTPError, OSError) as e:
            logger.warning("列 target 失败（Chrome 关了？）: %s", e)
            await asyncio.sleep(rescan_seconds)
            yield
            continue

        feishu = pick_feishu_targets(targets, domains)
        feishu_ids = {t.get("id") or t.get("targetId") for t in feishu}

        # 清理已关闭的 tab 的任务
        for tid in list(active):
            if tid not in feishu_ids or active[tid].done():
                task = active.pop(tid)
                if not task.done():
                    task.cancel()

        # 对新 tab 启动监听
        for t in feishu:
            tid = t.get("id") or t.get("targetId")
            if tid in active and not active[tid].done():
                continue
            session = CdpTabSession(t, on_event)
            active[tid] = asyncio.create_task(session.run())
            logger.info("新监听：%s", t.get("url"))

        if not feishu:
            logger.debug("当前没有飞书 tab 在 Catfish Chrome 里打开")

        await asyncio.sleep(rescan_seconds)
        yield
