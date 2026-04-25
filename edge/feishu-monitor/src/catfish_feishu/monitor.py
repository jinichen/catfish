"""主循环：连 CDP → 接事件 → 过滤 → 分发。

这是 catfish-feishu daemon 的心脏。
"""
from __future__ import annotations

import asyncio
import logging
import signal
import time
from typing import Any

from .cdp_client import cdp_http_base_from_ws, watch_all_feishu_tabs
from .config import FeishuConfig, load_config, resolve_cdp_url
from .handlers import dispatch
from .relevance import judge

logger = logging.getLogger("catfish.feishu.monitor")


class DebouncedQueue:
    """简单去抖：同一会话内 N 毫秒连发的消息合并处理。

    第一条消息进来后开启一个倒计时；倒计时内再来消息就延期；
    倒计时结束时把这个会话里所有 pending 消息刷出去。
    """

    def __init__(self, debounce_ms: int) -> None:
        self.debounce_ms = debounce_ms
        self._pending: dict[str, list[dict[str, Any]]] = {}
        self._flush_tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def put(
        self,
        msg: dict[str, Any],
        flush_cb,
    ) -> None:
        conv = msg.get("conversation") or "__no_conv__"
        async with self._lock:
            self._pending.setdefault(conv, []).append(msg)
            existing = self._flush_tasks.get(conv)
            if existing and not existing.done():
                existing.cancel()
            self._flush_tasks[conv] = asyncio.create_task(
                self._delayed_flush(conv, flush_cb)
            )

    async def _delayed_flush(self, conv: str, flush_cb) -> None:
        try:
            await asyncio.sleep(self.debounce_ms / 1000.0)
            async with self._lock:
                batch = self._pending.pop(conv, [])
                self._flush_tasks.pop(conv, None)
            if batch:
                await flush_cb(batch)
        except asyncio.CancelledError:
            pass


class Monitor:
    def __init__(self, cfg: FeishuConfig) -> None:
        self.cfg = cfg
        self.queue = DebouncedQueue(cfg.runtime.debounce_ms)
        self._running = False
        self._stats = {"events": 0, "relevant": 0, "drafts": 0, "notifications": 0}

    def on_event(self, payload: dict[str, Any]) -> asyncio.Task:
        """CDP 线程回调：把事件丢进去抖队列。这里不能 await（回调是同步的）。"""
        etype = payload.get("type")
        if etype == "observer_ready":
            logger.info(
                "observer 在 '%s' 就绪，baseline=%d 条历史",
                payload.get("conversation"),
                payload.get("baseline"),
            )
            return asyncio.create_task(asyncio.sleep(0))  # no-op
        if etype == "new_message":
            return asyncio.create_task(self._on_message(payload))
        logger.debug("未知事件类型：%s", etype)
        return asyncio.create_task(asyncio.sleep(0))

    async def _on_message(self, payload: dict[str, Any]) -> None:
        self._stats["events"] += 1
        await self.queue.put(payload, self._process_batch)

    async def _process_batch(self, batch: list[dict[str, Any]]) -> None:
        for msg in batch:
            verdict = judge(
                message_text=msg.get("text", ""),
                sender=msg.get("sender", ""),
                is_direct_message=bool(msg.get("is_dm", False)),
                cfg=self.cfg.relevance,
            )
            if verdict.level.value == "none":
                continue
            self._stats["relevant"] += 1

            # dispatch 是同步的（目前），里面有些 IO（httpx、subprocess）
            # 未来可以改 async。现在放在 executor 避免阻塞主 loop
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                dispatch,
                msg,
                verdict,
                self.cfg.handlers,
                self.cfg.draft,
            )
            if result.get("notified"):
                self._stats["notifications"] += 1
            if result.get("draft_path"):
                self._stats["drafts"] += 1

            logger.info(
                "dispatched: level=%s sender=%s matched=%s",
                result.get("level"),
                msg.get("sender"),
                verdict.matched,
            )

    async def run(self) -> None:
        ws_url = resolve_cdp_url(self.cfg)
        if not ws_url:
            raise RuntimeError(
                "找不到 CDP URL。先跑 catfish-browser-attach.sh 让 Hermes config.yaml 有 browser.cdp_url。"
            )
        http_base = cdp_http_base_from_ws(ws_url)
        logger.info("CDP HTTP base: %s", http_base)

        self._running = True
        stop = asyncio.Event()

        def _signal_stop(*_args) -> None:
            logger.info("收到停止信号，退出...")
            stop.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_stop)
            except NotImplementedError:
                # Windows 不支持
                signal.signal(sig, lambda *_: _signal_stop())

        watcher = watch_all_feishu_tabs(
            http_base,
            self.cfg.runtime.feishu_domains,
            self.on_event,
        )

        last_stat_log = time.time()
        async for _ in watcher:
            if stop.is_set():
                break
            now = time.time()
            if now - last_stat_log > 60:
                logger.info("stats: %s", self._stats)
                last_stat_log = now

        self._running = False
        logger.info("monitor 退出，final stats: %s", self._stats)


def run_forever(cfg: FeishuConfig | None = None) -> None:
    """阻塞运行 monitor。给 CLI / daemon 用。"""
    cfg = cfg or load_config()
    logging.basicConfig(
        level=cfg.runtime.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    mon = Monitor(cfg)
    try:
        asyncio.run(mon.run())
    except KeyboardInterrupt:
        pass
