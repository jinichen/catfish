"""catfish-memory — hermes MemoryProvider plugin 聚合 catfish 边缘数据源.

详见 README.md + docs/MEMORY-OWNERSHIP-ARCHITECTURE.md.

# 加载方式

hermes 启动时 `plugins/memory/__init__.py` 扫 `$HERMES_HOME/plugins/catfish-memory/`,
看到本 __init__.py 的 `register(ctx)` 函数, 调它实例化 provider 并通过
`ctx.register_memory_provider(provider)` 注册.

跟 holographic plugin (~/.hermes/hermes-agent/plugins/memory/holographic/__init__.py)
同 pattern.

# 激活 (config.yaml)

    memory:
      provider: catfish-memory

注意 hermes MemoryManager 有 **one-external-provider limit** — 同时只能一个
外部 provider 跑. 切 catfish-memory 会替换其它外部 provider (holographic /
hindsight / honcho 等). hermes builtin (~/.hermes/memories/) 不受影响.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("catfish.memory.plugin")


def register(ctx) -> None:
    """5/19 凌晨 BL-CATFISH-MEMORY-PROVIDER-REGISTER:
    hermes plugin loader 通过 `register(ctx)` 函数实例化 provider.

    没这函数 hermes 报: 'Memory provider 'catfish-memory' loaded but no
    provider instance found'. 跟 holographic plugin 同 pattern.
    """
    try:
        # 真 plugin 加载 path (hermes 调用) — relative import work
        from .catfish_memory import CatfishMemoryProvider
    except ImportError:
        # pytest / dev import fallback (catfish_memory 在 sys.path)
        from catfish_memory import CatfishMemoryProvider  # type: ignore[no-redef]

    provider = CatfishMemoryProvider()
    ctx.register_memory_provider(provider)
    logger.info("catfish-memory plugin registered ✓")

    # 5/20 BL-CATFISH-TODO-SYNC v0.1.6: catfish-memory 顺手 exec catfish-todo-sync
    # 的 catfish_todo_sync.py 文件, 直接调 _apply_patch 应用 monkey-patch.
    # 用 importlib.util.spec_from_file_location 绕过 "包名带连字符不能 import" 问题.
    # 跟 catfish-memory 自身解耦, 失败完全静默. INFO log 让鸿波看到诊断.
    try:
        from pathlib import Path
        import importlib.util
        _todo_sync_py = (
            Path(__file__).parent.parent / "catfish-todo-sync" / "catfish_todo_sync.py"
        )
        if _todo_sync_py.exists():
            _spec = importlib.util.spec_from_file_location(
                "_catfish_todo_sync_inline", _todo_sync_py
            )
            if _spec and _spec.loader:
                _mod = importlib.util.module_from_spec(_spec)
                _spec.loader.exec_module(_mod)
                _patched = _mod._apply_patch()
                if _patched:
                    logger.info("catfish-todo-sync: monkey-patch applied ✓")
                else:
                    logger.info(
                        "catfish-todo-sync: patch skipped (already patched or "
                        "tools.todo_tool not importable)"
                    )
        else:
            logger.debug(
                "catfish-todo-sync source not found at %s", _todo_sync_py
            )
    except Exception as _todo_sync_err:
        logger.warning(
            "catfish-todo-sync trigger failed (ignored): %s", _todo_sync_err
        )


# 双 import 兼容 — pytest / IDE 用绝对 import 拿 class
try:
    from .catfish_memory import CatfishMemoryProvider
except ImportError:
    from catfish_memory import CatfishMemoryProvider  # type: ignore[no-redef]


__all__ = ["CatfishMemoryProvider", "register"]
