"""catfish-bookkeep — 本地语音记账 hermes plugin (P3.5.75).

# 加载方式

hermes 启动时 plugin loader 扫 `~/.hermes/plugins/catfish-bookkeep/`, 看到本
`__init__.py` 的 `register(ctx)` 函数, 调它. 跟 catfish-xcatfish-user 同 pattern
(kind=standalone). plugins.enabled 必须含 catfish-bookkeep 否则 hermes 不 load.

# 三段 fallback 加载

dash 包名 (catfish-bookkeep) + hermes plugin loader 用 spec_from_file_location
加载方式下 relative / absolute import 都不稳, 走三段 fallback (relative →
absolute → spec_from_file_location 按文件路径). 跟 catfish-memory __init__.py
同套路, 5/28 鸿波踩过 P0 bug.

# 跟其它 catfish plugin 对比

- catfish-memory: MemoryProvider plugin (ctx.register_memory_provider)
- catfish-xcatfish-user: standalone monkey-patch plugin (纯 plugin.install())
- catfish-bookkeep (本): standalone tool plugin (ctx.register_tool × 3)

3 个 tool 注册:
  - bookkeep_add — LLM 听到员工说收/支自动调
  - bookkeep_query — LLM 查历史
  - bookkeep_summarize — LLM 出汇总报表

数据落 ~/.catfish/bookkeep.jsonl, append-only, 0 数据出端.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("catfish.bookkeep.plugin")


def _load_bookkeep_module():
    """三段 fallback 加载 bookkeep.py module (跟 catfish-memory / catfish-xcatfish-user 同套路).

    5/28 catfish-memory 踩 P0 bug: dash 包名 + spec_from_file_location 加载方式
    导致 relative / absolute import 全失败, __init__.py exec 中断, register
    没被定义. 本 plugin 同坑同套路兜底.
    """
    try:
        from . import bookkeep as _mod
        return _mod
    except (ImportError, SystemError, ValueError):
        pass
    try:
        import bookkeep as _mod  # type: ignore[no-redef]
        return _mod
    except ImportError:
        pass
    # 兜底: 按文件路径 import (不依赖 sys.path / __package__)
    from pathlib import Path
    import importlib.util
    import sys as _sys
    _plugin_dir = str(Path(__file__).parent)
    _py = Path(__file__).parent / "bookkeep.py"
    if not _py.exists():
        raise ImportError(f"bookkeep.py 不存在: {_py}")
    _added = _plugin_dir not in _sys.path
    if _added:
        _sys.path.insert(0, _plugin_dir)
    try:
        _spec = importlib.util.spec_from_file_location("_catfish_bookkeep_impl", _py)
        if not _spec or not _spec.loader:
            raise ImportError(f"spec_from_file_location 失败: {_py}")
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        return _mod
    finally:
        if _added and _plugin_dir in _sys.path:
            _sys.path.remove(_plugin_dir)


def register(ctx) -> None:
    """hermes plugin loader 入口. 注册 3 个 bookkeep tool.

    跟 catfish-memory __init__.py register() 同款 ctx.register_tool 接口 (8
    keyword args: name / toolset / schema / handler / check_fn / override /
    emoji / description). v0.17 PluginContext.register_tool 真签名 audit 来自
    ~/.hermes/hermes-agent/hermes_cli/plugins.py.
    """
    _mod = _load_bookkeep_module()
    provider = _mod.CatfishBookkeepProvider()

    if not hasattr(ctx, "register_tool"):
        logger.error(
            "catfish-bookkeep: ctx.register_tool 不支持, plugin 跳过. "
            "hermes 版本太老, 需要 0.15+. 检查 PluginContext."
        )
        return

    # schema 是 module-level 函数 (不挂 provider class, 跟 catfish-memory 不同, 但简洁).
    # handler 是 provider method (单例 namespace 容器 + monkeypatch CATFISH_HOME 隔离).
    registrations = [
        (
            "bookkeep_add",
            _mod.get_bookkeep_add_schema(),
            provider.handle_bookkeep_add,
            _mod.BOOKKEEP_ADD_DESCRIPTION,
            "💸",
        ),
        (
            "bookkeep_query",
            _mod.get_bookkeep_query_schema(),
            provider.handle_bookkeep_query,
            _mod.BOOKKEEP_QUERY_DESCRIPTION,
            "🔍",
        ),
        (
            "bookkeep_summarize",
            _mod.get_bookkeep_summarize_schema(),
            provider.handle_bookkeep_summarize,
            _mod.BOOKKEEP_SUMMARIZE_DESCRIPTION,
            "📊",
        ),
    ]

    n_ok = 0
    for name, schema, handler, desc, emoji in registrations:
        try:
            ctx.register_tool(
                name=name,
                toolset="bookkeep",
                schema=schema,
                handler=lambda args, _h=handler, **kw: _h(args, **kw),
                check_fn=lambda _p=provider: _p.is_available(),
                override=False,  # bookkeep 是新 tool, 不覆盖 builtin
                emoji=emoji,
                description=desc,
            )
            n_ok += 1
            logger.info("catfish-bookkeep tool registered: %s %s", emoji, name)
        except Exception as e:  # noqa: BLE001
            logger.error(
                "catfish-bookkeep: 注册 tool '%s' 失败 (ignored): %s",
                name, e,
            )

    if n_ok == len(registrations):
        logger.info(
            "catfish-bookkeep plugin registered ✓ (%d/%d tools, jsonl=~/.catfish/bookkeep.jsonl)",
            n_ok, len(registrations),
        )
    else:
        logger.warning(
            "catfish-bookkeep plugin partially registered: %d/%d tools",
            n_ok, len(registrations),
        )


# 双 import 兼容 — pytest / IDE 用绝对 import 拿 class.
# 跟 catfish-memory __init__.py 同款, 失败 silent (设 None), 让 __init__.py 能
# 完整 exec, register() 函数被定义, 之后 register() 自己调 _load_bookkeep_module()
# 再试. 不然 module-level import 一炸 register 没被定义, hermes 报 "no entry".
try:
    _bookkeep = _load_bookkeep_module()
    CatfishBookkeepProvider = _bookkeep.CatfishBookkeepProvider
except Exception as _import_err:
    logger.warning(
        "顶部 import CatfishBookkeepProvider 失败 (register 时再 lazy load): %s",
        _import_err,
    )
    CatfishBookkeepProvider = None  # type: ignore[assignment]


__all__ = ["CatfishBookkeepProvider", "register"]
