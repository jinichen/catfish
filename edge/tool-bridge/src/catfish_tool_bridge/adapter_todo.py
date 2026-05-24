"""TodoStore 持久化 helpers — 抽自 adapter.py (5/21 拆分).

存 hermes 内置 todo tool 的 TodoStore (in-memory) 到磁盘 (.catfish/todo_state/<session>.json),
让 chat 重启 / 切 session 后 TODO 不丢.

5/23 BL-TODO-SYNC-INLINE (鸿波): 把老的 catfish-todo-sync hermes-plugin 同步逻辑搬进来.
背景: plugin 设计依赖 hermes plugin loader import-time monkey-patch TodoStore.write,
但 catfish 实际路径是 tool-bridge 直接 `from tools.todo_tool import TodoStore` —
bypass plugin loader, monkey-patch 永远不跑. 5/23 验证: deepseek 在 chat 调 todo_write
落了 10 项 TODO 进 TodoStore + ~/.catfish/todo_store/<sid>.json, 但 employee_journal.md
完全没收到. 老 plugin 整套死代码.

现在: 把 _sync_to_journal subprocess catfish-journal 调用直接放在 _persist_todo_store
末尾 — tool-bridge 这条路径上 100% 跑得到, 不靠 hermes plugin loader.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    """dispatch 后写盘 + 同步到 employee_journal.md.

    1. 写 ~/.catfish/todo_store/<sid>.json (per-session in-memory dump)
    2. 5/23 BL-TODO-SYNC-INLINE (鸿波): 同步 pending/in_progress/completed/cancelled
       到 ~/.catfish/employee_journal.md (跨 session 真待办). 这一步替代老的
       catfish-todo-sync plugin (那个 plugin 的 monkey-patch 在 tool-bridge 路径
       永远没被触发, 整套 ~280 行死代码).

    全程失败静默 — TodoStore in-memory 不受影响, LLM 后续 todo_read 仍 OK.
    """
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
        return  # json 都写不进, journal sync 也别试了

    # 5/23 BL-TODO-SYNC-INLINE: 同步到 employee_journal.md (老 plugin 干的事)
    try:
        _sync_to_journal(items)
    except Exception as e:
        # 双重防御: _sync_to_journal 自己已 try/except, 这层是兜底
        logger.debug("BL-TODO-SYNC-INLINE outer guard caught: %s", e)


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


# ============================================================
# 5/23 BL-TODO-SYNC-INLINE — TodoStore → employee_journal.md 同步
# 抄自老的 edge/hermes-plugins/catfish-todo-sync/ (那 plugin 设计依赖 hermes
# plugin loader monkey-patch, 在 tool-bridge 直接 import TodoStore 这条路径
# 永远不跑). 5/23 鸿波拍: 搬进 tool-bridge 真路径, 删 plugin.
# ============================================================


def _find_catfish_journal_bin() -> Optional[str]:
    """找 catfish-journal CLI. 优先 hermes venv (install.sh 装到这), 兜底 PATH."""
    home = Path.home()
    venv_bin = home / ".hermes/hermes-agent/venv/bin/catfish-journal"
    if venv_bin.exists() and venv_bin.is_file():
        return str(venv_bin)
    return shutil.which("catfish-journal")


def _sync_to_journal_batch(bin_path: str, todos: List[Dict[str, Any]]) -> bool:
    """批量 sync: 一次 `catfish-journal sync --stdin` 处理所有 todos.

    返 True = batch 成功 (或空 todos), False = batch 不可用 (老 CLI 没 sync /
    timeout / 错). False 时 caller fallback 单调路径.

    单次 subprocess 比 N 次单调省 N-1 次 Python 启动开销 (~50-200ms 每次).
    """
    if not todos:
        return True
    ops = []
    for t in todos:
        content = str(t.get("content", "")).strip()
        status = str(t.get("status", "pending")).strip().lower()
        if not content:
            continue
        ops.append({"content": content, "status": status})
    if not ops:
        return True
    stdin_data = "\n".join(
        json.dumps(op, ensure_ascii=False) for op in ops
    ).encode("utf-8")
    try:
        result = subprocess.run(
            [bin_path, "sync", "--stdin"],
            input=stdin_data,
            capture_output=True,
            timeout=5,  # batch 比单调久, 5s 给 N todo + journal IO
            check=False,
        )
        # 老 CLI 没 sync 子命令 → exit 2 + stderr 含 "invalid choice"
        if result.returncode == 2 and b"invalid choice" in result.stderr:
            logger.debug("BL-TODO-SYNC-INLINE: 老 CLI 无 sync 子命令, fallback 单调")
            return False
        if result.returncode != 0:
            logger.debug(
                "BL-TODO-SYNC-INLINE: sync exit=%d stderr=%s, fallback 单调",
                result.returncode, result.stderr[:200],
            )
            return False
        logger.debug(
            "BL-TODO-SYNC-INLINE: batch ok %d ops, stats=%s",
            len(ops), result.stdout.decode("utf-8", errors="replace")[:200],
        )
        return True
    except subprocess.TimeoutExpired:
        logger.debug("BL-TODO-SYNC-INLINE: sync timeout, fallback 单调")
        return False
    except Exception as e:
        logger.debug("BL-TODO-SYNC-INLINE: sync 异常 (%s), fallback 单调", e)
        return False


def _find_todo_line_in_journal(
    bin_path: str, content: str,
) -> Optional[tuple[int, str]]:
    """跑 catfish-journal list 找该 content 对应的 (line, hint).

    返 None 表 journal 里没该 TODO. 用于 fallback 单调 done/delete 路径定位行号.
    """
    try:
        result = subprocess.run(
            [bin_path, "list", "--format=json"],
            capture_output=True, timeout=3, check=False,
        )
        if result.returncode != 0:
            return None
        todos = json.loads(result.stdout.decode("utf-8") or "[]")
        target = content.strip()
        for t in todos:
            if t.get("text", "").strip() == target:
                return (int(t.get("line", 0)), target[:30])
        return None
    except (json.JSONDecodeError, subprocess.TimeoutExpired, Exception) as e:
        logger.debug("BL-TODO-SYNC-INLINE: _find_todo_line_in_journal failed: %s", e)
        return None


def _sync_to_journal(todos: List[Dict[str, Any]]) -> None:
    """同步 todos 到 ~/.catfish/employee_journal.md.

    Status lifecycle:
    - pending / in_progress → catfish-journal add (CLI 幂等, 重复不加)
    - completed → catfish-journal done (- [ ] → - [x]); 找不到 line 时
      add --done 补历史 [x] (LLM 直接标完成没经 add 的场景)
    - cancelled → catfish-journal delete (整行删)

    默认 batch 路径 (1 次 subprocess), fallback 老 CLI 走 N 次单调.
    每次调用都 timeout, 出错静默 — 不阻塞 tool-bridge dispatch 主流程.
    """
    bin_path = _find_catfish_journal_bin()
    if not bin_path:
        logger.debug("BL-TODO-SYNC-INLINE: catfish-journal CLI 没找到, skip")
        return

    # 优先 batch
    if _sync_to_journal_batch(bin_path, todos):
        return

    # fallback: 单调 N 次 (catfish-journal 老版本无 sync 子命令)
    for t in todos:
        try:
            content = str(t.get("content", "")).strip()
            status = str(t.get("status", "pending")).strip().lower()
            if not content:
                continue

            if status in ("pending", "in_progress"):
                subprocess.run(
                    [bin_path, "add", content],
                    capture_output=True, timeout=3, check=False,
                )
                logger.debug("BL-TODO-SYNC-INLINE wrote pending: %s", content[:60])

            elif status == "completed":
                located = _find_todo_line_in_journal(bin_path, content)
                if located:
                    line, hint = located
                    subprocess.run(
                        [bin_path, "done", "--line", str(line), "--hint", hint],
                        capture_output=True, timeout=3, check=False,
                    )
                    logger.debug(
                        "BL-TODO-SYNC-INLINE marked done: %s (line=%d)",
                        content[:60], line,
                    )
                else:
                    # 直接标完成没经 add → 补 [x] 历史 (CLI 幂等)
                    subprocess.run(
                        [bin_path, "add", content, "--done"],
                        capture_output=True, timeout=3, check=False,
                    )
                    logger.debug(
                        "BL-TODO-SYNC-INLINE: completed 直接标, 补 [x]: %s",
                        content[:60],
                    )

            elif status == "cancelled":
                located = _find_todo_line_in_journal(bin_path, content)
                if located:
                    line, hint = located
                    subprocess.run(
                        [bin_path, "delete", "--line", str(line), "--hint", hint],
                        capture_output=True, timeout=3, check=False,
                    )
                    logger.debug(
                        "BL-TODO-SYNC-INLINE deleted: %s (line=%d)",
                        content[:60], line,
                    )
                else:
                    logger.debug(
                        "BL-TODO-SYNC-INLINE: cancelled 在 journal 找不到, skip",
                    )
            # 未知 status: 静默跳过
        except subprocess.TimeoutExpired:
            logger.debug(
                "BL-TODO-SYNC-INLINE: subprocess timeout, skip: %s",
                content[:30] if content else "?",
            )
        except Exception as e:
            logger.debug("BL-TODO-SYNC-INLINE error (ignored): %s", e)


# ============================================================


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



