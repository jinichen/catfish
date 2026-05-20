"""catfish-todo-sync hermes plugin — module-load time monkey-patch.

# 加载方式 (v0.1.4 关键修复)

hermes plugin loader 有 "one-provider limit" (memory.provider 只能 active 一个),
catfish-memory 占了 active 位置, 我们 catfish-todo-sync 不会被 load_memory_provider
调到, 因此 register(ctx) 不会被调用.

但 hermes `discover_memory_providers()` 启动时 import 每个 bundled plugin
的 __init__.py 做 availability check — 这是我们唯一被 import 的机会.

**修法**: monkey-patch 不放 register(ctx) 里, 放 module 顶层. import time 立即
应用. 不依赖 active load, 只依赖 discover (一定会 import).

register(ctx) 保留作为协议兼容, 但实际工作已在 import 时完成.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("catfish.todo_sync.plugin")

# ── 关键: import time 立即 monkey-patch ──────────────────────────
# hermes discover_memory_providers() 启动时 import 我们 __init__.py 做
# availability check, 这一次 import 触发下面 _apply_patch().
# 之后所有 TodoStore.write 调用都走 patched 版本.
try:
    from .catfish_todo_sync import _apply_patch as _bootstrap_patch
except ImportError:
    from catfish_todo_sync import _apply_patch as _bootstrap_patch  # type: ignore[no-redef]

try:
    _patched_at_import = _bootstrap_patch()
    if _patched_at_import:
        logger.info("catfish-todo-sync: monkey-patch 应用成功 (import-time) ✓")
    else:
        logger.debug("catfish-todo-sync: import-time patch skipped (already patched or no todo tool)")
except Exception as _e:
    logger.warning("catfish-todo-sync: import-time patch failed: %s", _e)


def register(ctx) -> None:
    """hermes plugin loader 调用入口 (BL-CATFISH-TODO-SYNC, 5/20).

    ctx 参数跟 catfish-memory 同 pattern, 提供 register_memory_provider().
    我们不真用 ctx 来注册 active memory provider, 只借这个 hook 时机
    应用 monkey-patch.
    """
    # 1. 应用 monkey-patch TodoStore.write (核心)
    try:
        from .catfish_todo_sync import _apply_patch
    except ImportError:
        from catfish_todo_sync import _apply_patch  # type: ignore[no-redef]

    patched = _apply_patch()
    if patched:
        logger.info("catfish-todo-sync: monkey-patch 应用成功 ✓")
    else:
        logger.warning("catfish-todo-sync: monkey-patch 未应用 (hermes 没 todo_tool 或已 patch)")

    # 2. 注册 dummy provider 满足 hermes plugin loader 协议
    # is_available() 返 False, MemoryManager 不会 active 它,
    # 但 hermes 不会报"loaded but no provider instance found" 错
    try:
        from .catfish_todo_sync import CatfishTodoSyncDummyProvider
    except ImportError:
        from catfish_todo_sync import CatfishTodoSyncDummyProvider  # type: ignore[no-redef]

    provider = CatfishTodoSyncDummyProvider()
    try:
        ctx.register_memory_provider(provider)
        logger.info("catfish-todo-sync: dummy provider 注册 (不抢 active)")
    except Exception as e:
        # 注册失败也不影响 monkey-patch 已生效, 只是 hermes 可能 log warn
        logger.debug("catfish-todo-sync: dummy provider 注册失败 (忽略): %s", e)


# Double-import 兼容: pytest / IDE 用绝对 import 拿到内部符号
try:
    from .catfish_todo_sync import _apply_patch, _sync_to_journal  # noqa: F401
except ImportError:
    from catfish_todo_sync import _apply_patch, _sync_to_journal  # type: ignore[no-redef]


__all__ = ["register", "_apply_patch", "_sync_to_journal"]
