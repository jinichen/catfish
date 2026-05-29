"""catfish-xcatfish-user hermes plugin: 注入 X-Catfish-User 多租户 header.

# 加载

hermes 启动时 plugin loader 扫 `$HERMES_HOME/plugins/catfish-xcatfish-user/`,
看到本 __init__.py 的 `register(ctx)` 函数, 调它. register 调
plugin.install() — install() 跑 _verify_patch_targets() (fail loud) 然后应用
11 处 monkey-patch.

跟 catfish-memory 同 pattern. 注意 dash 包名 (catfish-xcatfish-user) 跟 catfish-memory
一样会让 hermes plugin loader 的 spec_from_file_location 装载方式下 relative
import 不稳, 用 3 段 fallback (relative → absolute → spec_from_file_location).

# 跟 catfish-memory 区别

catfish-memory 是 hermes MemoryProvider plugin (走 ctx.register_memory_provider).
本 plugin 没注册任何 hermes API, 纯 monkey-patch — register(ctx) 就是个空壳,
真正工作在 plugin.install().
"""
from __future__ import annotations

import logging

logger = logging.getLogger("catfish.xcatfish_user")


def _load_plugin_module():
    """三段 fallback 加载 plugin.py module (跟 catfish-memory 同套路).

    5/28 catfish-memory 踩的 P0 bug: dash 包名 + spec_from_file_location 加载方式
    导致 relative / absolute import 全失败, __init__.py exec 中断, register
    没被定义. 我们这个 plugin 也踩同坑, 同套路兜底.
    """
    try:
        from . import plugin as _mod
        return _mod
    except (ImportError, SystemError, ValueError):
        pass
    try:
        import plugin as _mod  # type: ignore[no-redef]
        return _mod
    except ImportError:
        pass
    # 兜底: 按文件路径 import
    from pathlib import Path
    import importlib.util
    import sys as _sys
    _plugin_dir = str(Path(__file__).parent)
    _py = Path(__file__).parent / "plugin.py"
    if not _py.exists():
        raise ImportError(f"plugin.py 不存在: {_py}")
    _added = _plugin_dir not in _sys.path
    if _added:
        _sys.path.insert(0, _plugin_dir)
    try:
        _spec = importlib.util.spec_from_file_location("_catfish_xcatfish_user_impl", _py)
        if not _spec or not _spec.loader:
            raise ImportError(f"spec_from_file_location 失败: {_py}")
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        return _mod
    finally:
        if _added and _plugin_dir in _sys.path:
            _sys.path.remove(_plugin_dir)


def register(ctx) -> None:
    """hermes plugin loader 入口. 调 plugin.install() 应用 monkey-patch.

    ctx 是 hermes plugin context (含 register_memory_provider / register_skill 等).
    本 plugin 不用 ctx, 收下不用.
    """
    try:
        _mod = _load_plugin_module()
        _mod.install()
        logger.info("catfish-xcatfish-user plugin registered ✓")
    except Exception as e:
        # 这里 raise 是故意的 — 让 hermes 启动失败而不是 silent 跨员工串数据.
        # 加 log 让 ops 一眼看到原因.
        logger.error(
            "catfish-xcatfish-user 加载失败, hermes 启动会中断 (这是故意的, "
            "避免 silent 跨员工串数据 P0 漏洞): %s",
            e,
        )
        raise


# 双 import 兼容 — pytest / IDE 用绝对 import 拿 plugin module
try:
    _plugin_mod_at_import_time = _load_plugin_module()
    install = _plugin_mod_at_import_time.install
except Exception as _e:
    logger.warning(
        "顶部 import plugin module 失败 (register 时再 lazy load): %s", _e
    )
    install = None  # type: ignore[assignment]


__all__ = ["register", "install"]
