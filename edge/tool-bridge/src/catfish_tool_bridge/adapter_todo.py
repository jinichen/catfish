"""TodoStore 持久化 helpers — 抽自 adapter.py (5/21 拆分).

存 hermes 内置 todo tool 的 TodoStore (in-memory) 到磁盘 (.catfish/todo_state/<session>.json),
让 chat 重启 / 切 session 后 TODO 不丢. 跟 catfish-todo-sync plugin (5/20 ship)
互补: catfish-todo-sync 是 monkey-patch write → 同步 journal, 这里是
in-memory ↔ disk 持久化.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("catfish.adapter")

# ============================================================

_TODO_STORE_DEFAULT_KEY = "__default__"
_TODO_STORE_MAX_SESSIONS = 50
_todo_store_cache: Dict[str, Any] = {}
_todo_store_init_failed = False

# 5/21 移: 从 adapter.py 移过来 — _get_memory_store 用
_memory_store_cache: Any = None
_memory_store_init_failed = False


def _todo_persist_dir() -> Path:
    """BL-TODO-STORE-PERSIST: 持久化目录 ~/.catfish/todo_store/. 不存在自动建."""
    d = Path.home() / ".catfish" / "todo_store"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _todo_persist_path(session_id: str) -> Path:
    """每 session 一个 json 文件. session_id 含特殊字符 safe — 沙箱级危险不存在,
    catfish session_id 由我们自家 sessions.rs 生成, 是 timestamp+hash 格式 safe."""
    return _todo_persist_dir() / f"{session_id}.json"


def _load_todo_store_from_disk(session_id: str, store: Any) -> None:
    """从盘读 items 灌进新建的 TodoStore. 失败 silent (空 store 是合理 fallback)."""
    path = _todo_persist_path(session_id)
    if not path.exists():
        return
    try:
        import json  # noqa: PLC0415
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # TodoStore._items 直接 list, 简单赋值
        if isinstance(data, list):
            store._items = data
            logger.info(
                "BL-TODO-STORE-PERSIST: load session=%r %d items from disk",
                session_id, len(data),
            )
    except Exception as e:
        logger.warning(
            "BL-TODO-STORE-PERSIST: load session=%r 失败 (盘文件可能 corrupt): %s",
            session_id, e,
        )


def _persist_todo_store(session_id: str, store: Any) -> None:
    """dispatch 后写盘. 失败 silent (in-memory 仍 OK, 下次重启丢但不阻塞当前调用)."""
    try:
        import json  # noqa: PLC0415
        items = getattr(store, "_items", None)
        if items is None:
            return
        path = _todo_persist_path(session_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(
            "BL-TODO-STORE-PERSIST: persist session=%r 失败: %s",
            session_id, e,
        )


def _get_todo_store(session_id: Optional[str]):
    """Per-session lazy TodoStore. session_id 缺失 → __default__ key 全局共享.

    BL-TODO-STORE-PERSIST (5/16): 新建时先看盘, 有 ~/.catfish/todo_store/<sid>.json
    就反加载. dispatch 后由 _dispatch_via_hermes_registry 调 _persist_todo_store 写盘.

    简化 LRU: 超 50 session 时随机 evict 一个 oldest key (dict insertion order).
    Evict 时同步删盘文件 (避免无限增长).
    """
    global _todo_store_init_failed
    if _todo_store_init_failed:
        return None

    key = session_id or _TODO_STORE_DEFAULT_KEY
    if key in _todo_store_cache:
        return _todo_store_cache[key]

    # LRU evict
    if len(_todo_store_cache) >= _TODO_STORE_MAX_SESSIONS:
        oldest_key = next(iter(_todo_store_cache))
        _todo_store_cache.pop(oldest_key, None)
        # 同步删盘 (防累积)
        try:
            _todo_persist_path(oldest_key).unlink(missing_ok=True)
        except Exception:
            pass
        logger.info(
            "BL-TODO-BRIDGE-STORE: cache 满, evict session=%r (含盘文件)", oldest_key,
        )

    try:
        from tools.todo_tool import TodoStore  # noqa: PLC0415
        store = TodoStore()
        _load_todo_store_from_disk(key, store)  # 反加载
        _todo_store_cache[key] = store
        logger.info(
            "BL-TODO-BRIDGE-STORE: 新建 TodoStore session=%r (cache 大小=%d, 已加载 %d items)",
            key, len(_todo_store_cache), len(getattr(store, "_items", [])),
        )
        return store
    except Exception as e:
        logger.exception(
            "BL-TODO-BRIDGE-STORE: TodoStore 初始化失败, todo 工具将持续返 disabled: %s",
            e,
        )
        _todo_store_init_failed = True
        return None


def _read_hermes_memory_config() -> dict:
    """读 ~/.hermes/config.yaml 的 memory 段. 缺失 / 解析失败返空 dict.

    返回的 dict 用 MemoryStore 默认值兜底 (memory_char_limit=2200, user_char_limit=1375).
    """
    cfg_path = Path.home() / ".hermes" / "config.yaml"
    if not cfg_path.exists():
        return {}
    try:
        import yaml  # noqa: PLC0415  # 延迟 import, 避免影响 tool-bridge 启动速度
        with open(cfg_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("memory") or {}
    except Exception as e:
        logger.warning(
            "BL-MEMORY-BRIDGE-STORE: 读 ~/.hermes/config.yaml memory 段失败, "
            "用 hermes 默认 char_limit: %s",
            e,
        )
        return {}


def _get_memory_store():
    """Lazy + cache hermes MemoryStore singleton.

    第一次调用时构造 + load_from_disk(), 后续返同一实例.
    任何步骤失败 → 标 init_failed, 一直返 None (不反复重试免刷 log).
    """
    global _memory_store_cache, _memory_store_init_failed
    if _memory_store_cache is not None:
        return _memory_store_cache
    if _memory_store_init_failed:
        return None

    try:
        # hermes-agent 已经在 sys.path 里 (bootstrap.bootstrap() 启动时塞的)
        from tools.memory_tool import MemoryStore  # noqa: PLC0415

        cfg = _read_hermes_memory_config()
        store = MemoryStore(
            memory_char_limit=int(cfg.get("memory_char_limit", 2200)),
            user_char_limit=int(cfg.get("user_char_limit", 1375)),
        )
        store.load_from_disk()
        _memory_store_cache = store
        logger.info(
            "BL-MEMORY-BRIDGE-STORE: hermes MemoryStore 初始化成功 "
            "(mem_limit=%d user_limit=%d, mem_entries=%d user_entries=%d)",
            store.memory_char_limit,
            store.user_char_limit,
            len(store.memory_entries),
            len(store.user_entries),
        )
        return store
    except Exception as e:
        logger.exception(
            "BL-MEMORY-BRIDGE-STORE: MemoryStore 初始化失败, "
            "memory tool 将持续返 disabled. 错: %s",
            e,
        )
        _memory_store_init_failed = True
        return None



