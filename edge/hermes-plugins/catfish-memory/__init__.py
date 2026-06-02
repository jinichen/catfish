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


def _load_provider_class():
    """三段 fallback 加载 CatfishMemoryProvider class.

    5/28 鸿波发现的 P0 bug — hermes plugin loader 用
    `importlib.util.spec_from_file_location` 加载 plugin __init__.py 时,
    spec name 不是真 Python package (因 plugin 目录名带 dash), 导致:
      - relative import `.catfish_memory` 失败 (无 __package__)
      - absolute import `catfish_memory` 失败 (不在 sys.path)
    整个 __init__.py exec 中断, `register()` 函数没被定义, hermes 报
    "Memory provider 'catfish-memory' loaded but no provider instance found".
    plugin 5/19 装好但**一直没真注册**, RecMode skill 列表从来没注入 LLM 上下文.

    三段 fallback: relative → absolute → **spec_from_file_location 按文件路径**.
    最后一段是无敌兜底, 不依赖任何 import 上下文.
    """
    try:
        from .catfish_memory import CatfishMemoryProvider as _Cls
        return _Cls
    except (ImportError, SystemError, ValueError):
        pass
    try:
        from catfish_memory import CatfishMemoryProvider as _Cls  # type: ignore[no-redef]
        return _Cls
    except ImportError:
        pass
    # 兜底: 按文件路径 import (不依赖 sys.path / __package__)
    # 5/28 鸿波: catfish_memory.py 内部还有 `from catfish_memory_helpers import ...`
    # 这种 sibling absolute import. spec_from_file_location 加载时若 sys.path 没
    # plugin 目录, sibling import 找不到. 临时把 plugin_dir 加 sys.path, exec
    # 完恢复. finally 保证不污染.
    from pathlib import Path
    import importlib.util
    import sys as _sys
    _plugin_dir = str(Path(__file__).parent)
    _py = Path(__file__).parent / "catfish_memory.py"
    if not _py.exists():
        raise ImportError(f"catfish_memory.py 不存在: {_py}")
    _added_to_path = _plugin_dir not in _sys.path
    if _added_to_path:
        _sys.path.insert(0, _plugin_dir)
    try:
        _spec = importlib.util.spec_from_file_location("_catfish_memory_impl", _py)
        if not _spec or not _spec.loader:
            raise ImportError(f"spec_from_file_location 失败: {_py}")
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        return _mod.CatfishMemoryProvider
    finally:
        if _added_to_path and _plugin_dir in _sys.path:
            _sys.path.remove(_plugin_dir)


def register(ctx) -> None:
    """5/19 凌晨 BL-CATFISH-MEMORY-PROVIDER-REGISTER:
    hermes plugin loader 通过 `register(ctx)` 函数实例化 provider.

    没这函数 hermes 报: 'Memory provider 'catfish-memory' loaded but no
    provider instance found'. 跟 holographic plugin 同 pattern.

    5/28 鸿波: import 加 spec_from_file_location 兜底, 修 plugin 一直没
    真注册的 P0 bug. 详见 _load_provider_class() docstring.
    """
    _Cls = _load_provider_class()
    provider = _Cls()
    ctx.register_memory_provider(provider)
    logger.info("catfish-memory plugin registered ✓")

    # 6/2 BL-MEMORY-ROUTER-A2 (鸿波 6/2 凌晨拍): catfish-memory 接管 memory tool.
    # 用 ctx.register_tool(override=True) 替换 hermes builtin memory tool (跟
    # browser_navigate 5/6 同 pattern), 5 kind 路由 (identity / project_fact /
    # workflow / journal / todo). 修 5/24 实盘 70% 跑偏问题.
    try:
        if hasattr(ctx, "register_tool"):
            ctx.register_tool(
                name="memory",
                toolset="memory",
                schema=provider.get_catfish_memory_schema(),
                handler=lambda args, **kw: provider.handle_memory_tool(args, **kw),
                check_fn=lambda: provider.is_available(),
                override=True,
                emoji="🧠",
                description=(
                    "catfish 智能 memory 路由器 (替换 hermes builtin). 按 kind 路由 5 仓库."
                ),
            )
            logger.info(
                "catfish-memory: 替换 hermes builtin memory tool ✓ (5 kind 路由)"
            )
        else:
            logger.warning(
                "catfish-memory: ctx.register_tool 不支持, memory tool override 跳过. "
                "hermes 0.15 起应该有, 检查 PluginContext."
            )
    except Exception as e:  # noqa: BLE001
        logger.error(
            "catfish-memory: memory tool override 失败 (ignored): %s. "
            "退化到 hermes builtin memory tool (70%% 跑偏问题不修).",
            e,
        )

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


# 双 import 兼容 — pytest / IDE 用绝对 import 拿 class.
# 5/28 鸿波: hermes plugin loader 用 spec_from_file_location 加载时两条 import
# 都会失败, 走 _load_provider_class() 兜底 (spec_from_file_location 按文件路径).
# 失败仍 silent (设 None), 让 __init__.py 能完整 exec, register() 函数被定义,
# 之后 register() 自己调 _load_provider_class() 再试. 不然 module-level import
# 一炸, register() 没被定义, hermes 报"loaded but no provider instance found".
try:
    CatfishMemoryProvider = _load_provider_class()
except Exception as _import_err:
    logger.warning(
        "顶部 import CatfishMemoryProvider 失败 (register 时再 lazy load): %s",
        _import_err,
    )
    CatfishMemoryProvider = None  # type: ignore[assignment]


__all__ = ["CatfishMemoryProvider", "register"]
