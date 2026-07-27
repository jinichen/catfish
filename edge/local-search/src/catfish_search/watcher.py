"""watchdog 增量索引。

监听 include 目录的文件系统事件，文件变动时增量更新索引。

去抖策略：
    文件保存时编辑器经常在一秒内触发多次事件（write / rename / chmod），
    统一收到事件后推迟 DEBOUNCE_SEC 秒再处理，这段时间内的新事件只更新时间戳。
    等静默够 DEBOUNCE_SEC 再把这个路径刷到索引。
"""
from __future__ import annotations

import logging
import signal
import threading
import time
from pathlib import Path

from .config import SearchConfig, load_config
from .indexer import index_is_empty, index_path, remove_path, run_index, should_index

logger = logging.getLogger("catfish.search.watcher")

# 单次事件后等这么久再落库，避免编辑器连发的多次写入都触发一次索引。
DEBOUNCE_SEC = 2.0

# 主循环唤醒间隔。
TICK_SEC = 0.5


def _import_watchdog():
    """watchdog 是可选依赖，没装就给个友好提示。"""
    try:
        from watchdog.events import FileSystemEventHandler  # noqa: PLC0415
        from watchdog.observers import Observer  # noqa: PLC0415
        return Observer, FileSystemEventHandler
    except ImportError as e:
        raise RuntimeError(
            "watchdog 未安装。请先 pip install 'catfish-local-search[watch]' "
            "或 pip install watchdog"
        ) from e


class _Pending:
    """待处理队列，线程安全。path -> (deadline, op)，op 是 'upsert' 或 'delete'。"""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def mark(self, path: str, op: str) -> None:
        with self._lock:
            self._data[path] = (time.time() + DEBOUNCE_SEC, op)

    def drain_ready(self, now: float, force: bool = False) -> list[tuple[str, str]]:
        ready: list[tuple[str, str]] = []
        with self._lock:
            for p, (deadline, op) in list(self._data.items()):
                if force or now >= deadline:
                    ready.append((p, op))
                    del self._data[p]
        return ready


def _make_handler(pending: _Pending):
    """构造 FileSystemEventHandler 子类实例。"""
    _, base_cls = _import_watchdog()

    class _Handler(base_cls):  # type: ignore[misc, valid-type]
        def on_created(self, event):
            if not event.is_directory:
                pending.mark(event.src_path, "upsert")

        def on_modified(self, event):
            if not event.is_directory:
                pending.mark(event.src_path, "upsert")

        def on_moved(self, event):
            if event.is_directory:
                return
            # 旧路径从索引里删掉，新路径重新索引。
            pending.mark(event.src_path, "delete")
            pending.mark(event.dest_path, "upsert")

        def on_deleted(self, event):
            if not event.is_directory:
                pending.mark(event.src_path, "delete")

    return _Handler()


def _flush(pending: _Pending, cfg: SearchConfig, force: bool = False) -> dict:
    """把静默够久的变更刷到索引库。返回本次处理计数。"""
    now = time.time()
    ready = pending.drain_ready(now, force=force)

    result = {"upsert": 0, "delete": 0, "skip": 0}
    if not ready:
        return result

    for path_str, op in ready:
        p = Path(path_str)
        try:
            if op == "delete":
                if remove_path(p):
                    result["delete"] += 1
                else:
                    result["skip"] += 1
                continue
            # upsert：先过滤，再索引。过滤不过的当跳过。
            if not p.exists() or not should_index(p, cfg):
                result["skip"] += 1
                continue
            if index_path(p):
                result["upsert"] += 1
            else:
                result["skip"] += 1
        except Exception as e:
            logger.debug("flush fail %s: %s", path_str, e)
            result["skip"] += 1

    if result["upsert"] or result["delete"]:
        logger.info(
            "incremental: upsert=%d delete=%d skip=%d",
            result["upsert"], result["delete"], result["skip"],
        )
    return result


def _bootstrap_if_empty(cfg: SearchConfig) -> None:
    """索引库是空的就先跑一次全量。

    BL-SEARCH-NO-BOOTSTRAP (7/27 鸿波实盘): watcher 只吃**变化事件**，
    存量文件永远不会自己进索引。Companion 又只 spawn watch 从不 spawn index，
    结果就是员工配了 ~/Documents 却一条都搜不到（详见 indexer.index_is_empty）。

    只在**空库**时做，不是每次启动都 reconcile —— 后者每次开 Companion 都要
    走一遍全部目录 stat 判重，启动变慢且绝大多数情况白跑。
    库非空说明 bootstrap 早跑过了，增量交给 watcher。
    """
    if not index_is_empty():
        return
    logger.info("索引库是空的，先做一次全量索引（只在首次 / 重建后发生）...")
    stats = run_index(cfg)
    logger.info(
        "全量索引完成: 扫 %d / 索引 %d / 跳过 %d，耗时 %ss",
        stats["scanned"], stats["indexed"], stats["skipped"], stats["duration_sec"],
    )
    for root, n in stats["per_root"].items():
        logger.info("  %6d  %s", n, root)
    for root, reason in stats["unreadable"]:
        logger.warning("  读不了（整棵没进索引）: %s — %s", root, reason)


def run_watch(cfg: SearchConfig | None = None, bootstrap: bool = True) -> int:
    """前台运行 watcher。收到 SIGTERM/SIGINT 时优雅退出。

    bootstrap=True 时，库为空会先跑一次全量索引再进监听循环。
    """
    observer_cls, _ = _import_watchdog()
    cfg = cfg or load_config()
    if not cfg.include:
        logger.error("没有可监听的目录，请先在 ~/.catfish/search-scope.yaml 配置 include。")
        return 1

    if bootstrap:
        _bootstrap_if_empty(cfg)

    pending = _Pending()
    handler = _make_handler(pending)
    observer = observer_cls()

    for root in cfg.include:
        try:
            observer.schedule(handler, str(root), recursive=True)
            logger.info("watching %s", root)
        except OSError as e:
            logger.warning("cannot watch %s: %s", root, e)

    observer.start()

    stop = threading.Event()

    def _signal_exit(*_args):
        logger.info("stopping watcher...")
        stop.set()

    signal.signal(signal.SIGTERM, _signal_exit)
    signal.signal(signal.SIGINT, _signal_exit)

    try:
        while not stop.is_set():
            time.sleep(TICK_SEC)
            _flush(pending, cfg)
    finally:
        observer.stop()
        observer.join(timeout=5)
        # 退出前把剩下的都落库。
        _flush(pending, cfg, force=True)
    return 0
