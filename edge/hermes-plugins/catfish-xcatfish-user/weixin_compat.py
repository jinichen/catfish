"""微信适配器模块在哪 (11/06, hermes v0.23)。P28 / P56 / P60 打补丁、P30 / wechat_push 调函数都从这里取。

- v0.22 及以前: `gateway.platforms.weixin` (核心里的一个文件)。
- v0.23 起: 挪进内置平台插件 `plugins/platforms/weixin/adapter.py`, 由插件加载器**延迟**加载, 模块名是
  `hermes_plugins.<插件名>.adapter`。**不能**直接 `import plugins.platforms.weixin.adapter` 来打补丁 —— 那是另一份模块,
  里面的 `WeixinAdapter` 跟网关真正用的不是同一个类, 补丁静默无效 (上游给 signal / yuanbao 修过同样的病, 微信没修)。
  所以从平台注册表取网关真正用的那个类 (`platform_registry.get("weixin").adapter_factory`), 再按 `cls.__module__` 找模块。

`when_adapter_module(fn, label)`: 现在能拿到就马上 `fn(module)`; 微信还没注册 (我们的插件先于内置平台插件加载) 就挂在
`PlatformRegistry.register` 上, 微信一注册就补上。同一个模块对象同一个 label 只打一次。
"""
from __future__ import annotations

import logging
import sys
import threading
from typing import Any, Callable

logger = logging.getLogger("catfish.xcatfish_user.plugin")

_lock = threading.Lock()
_pending: list[tuple[Callable[[Any], None], str]] = []
_done: set[tuple[int, str]] = set()
_hooked = False


def legacy_module():
    """v0.22 的位置 (还在就用它)。"""
    try:
        from gateway.platforms import weixin  # noqa: PLC0415
        return weixin
    except ImportError:
        return None


def _module_of(entry) -> Any | None:
    cls = getattr(entry, "adapter_factory", None)
    name = getattr(cls, "__module__", "") or ""
    return sys.modules.get(name) if name else None


def current_module():
    """网关真正用的微信适配器模块; 还没加载返回 None。"""
    mod = legacy_module()
    if mod is not None:
        return mod
    try:
        from gateway.platform_registry import platform_registry  # noqa: PLC0415
        entry = platform_registry.get("weixin")   # 延迟注册的会在这里真加载
    except Exception as e:  # noqa: BLE001
        logger.debug("weixin_compat: 取平台注册表失败: %s", e)
        return None
    return _module_of(entry) if entry is not None else None


def function(name: str):
    """模块级函数 / 常量 (扫码绑定、主动推送用; 只调用不打补丁, 哪份模块都行)。拿不到抛 ImportError。"""
    mod = current_module()
    if mod is None:
        try:
            import plugins.platforms.weixin.adapter as mod  # noqa: PLC0415  兜底: 调函数用独立导入的一份也可以
        except ImportError as e:
            raise ImportError(f"找不到 hermes 的微信适配器: {e}") from e
    if not hasattr(mod, name):
        raise ImportError(f"hermes 微信适配器里没有 {name}")
    return getattr(mod, name)


def _apply(fn: Callable[[Any], None], label: str, mod) -> None:
    key = (id(mod), label)
    with _lock:
        if key in _done:
            return
        _done.add(key)
    logger.info("%s: 微信补丁打在 %s 上", label, getattr(mod, "__name__", "?"))
    try:
        fn(mod)
    except Exception:  # noqa: BLE001  一个补丁出错不拖垮注册
        logger.exception("%s: 打微信补丁失败", label)


def _hook_register() -> None:
    global _hooked
    from gateway.platform_registry import PlatformRegistry  # noqa: PLC0415
    if _hooked:
        return
    orig = PlatformRegistry.register

    def register(self, entry, *args, **kwargs):
        result = orig(self, entry, *args, **kwargs)
        if getattr(entry, "name", "") == "weixin":
            mod = _module_of(entry)
            if mod is not None:
                with _lock:
                    todo = list(_pending)
                for fn, label in todo:
                    _apply(fn, label, mod)
        return result

    PlatformRegistry.register = register
    _hooked = True


def when_adapter_module(fn: Callable[[Any], None], label: str) -> None:
    """拿到网关真正用的微信适配器模块就 fn(module)。"""
    mod = current_module()
    if mod is not None:
        _apply(fn, label, mod)
        return
    try:
        _hook_register()
    except Exception as e:  # noqa: BLE001
        raise ImportError(f"{label}: hermes 没有微信适配器, 也挂不上平台注册表 ({e})") from e
    with _lock:
        _pending.append((fn, label))
    logger.info("%s: 微信适配器还没加载, 等它注册时再补", label)
