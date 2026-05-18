"""catfish-memory — hermes MemoryProvider plugin 聚合 catfish 边缘数据源.

详见 README.md + docs/MEMORY-OWNERSHIP-ARCHITECTURE.md.

# 加载方式

hermes 启动时 `plugins/memory/__init__.py:discover_memory_providers()` 扫
`$HERMES_HOME/plugins/catfish-memory/` 看到本 __init__.py, 导入 CatfishMemoryProvider.

# 激活 (config.yaml)

    memory:
      provider: catfish-memory

注意 hermes MemoryManager 有 **one-external-provider limit** — 同时只能一个
外部 provider 跑. 切 catfish-memory 会替换其它外部 provider (holographic /
hindsight / honcho 等). hermes builtin (~/.hermes/memories/) 不受影响.

# import 双兼容

hermes plugin loader 用 importlib spec, package context 设置, relative
import work. pytest / IDE 直接 import 这个文件作 package, package context
没设, relative import 挂 — try/except 双兼容.
"""

try:
    # hermes plugin loader 调进来时走这条
    from .catfish_memory import CatfishMemoryProvider
except ImportError:
    # pytest / dev import 走 absolute fallback
    from catfish_memory import CatfishMemoryProvider  # type: ignore[no-redef]

__all__ = ["CatfishMemoryProvider"]
