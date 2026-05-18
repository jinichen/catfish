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


# 双 import 兼容 — pytest / IDE 用绝对 import 拿 class
try:
    from .catfish_memory import CatfishMemoryProvider
except ImportError:
    from catfish_memory import CatfishMemoryProvider  # type: ignore[no-redef]


__all__ = ["CatfishMemoryProvider", "register"]
