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
    """hermes plugin loader 入口. 延迟 install + pre_tool_call hook 真 fail-loud.

    # 6/1 BL-PLUGIN-HERMES-015-LAZY-INSTALL (鸿波 6/1 必须今晚)
    hermes 0.15.1 tools/skills_tool.py:850 顶部触发 discover_plugins, 主线程
    stack 在 model_tools partial init 中调 register(ctx). 直接 install 撞
    `from model_tools import get_tool_definitions` partial circular ImportError.

    修法:
    1. _verify_patch_targets 静态文件 grep (不 import, 不阻塞主线程)
    2. install 起后台线程, 等主流程 model_tools fully init 再跑
    3. pre_tool_call hook 兜底 — 真 LLM call 时检 _INSTALLED 没装就 raise

    fail-loud 时机变了: 不在 plugin 加载时 fail, 在第一次 tool call 时 fail.
    P0 安全语义不变 — 真用到 plugin 前一定先 check.
    """
    import threading
    import sys
    import time

    _mod = _load_plugin_module()

    # Step 1: 静态文件检查 patch target 存在 (不 import 真模块, 避开 partial)
    try:
        _mod._verify_patch_targets()
        logger.info("catfish-xcatfish-user: verify ✓ (静态文件)")
    except Exception as e:
        # 静态检查 fail = 真 hermes refactor 致命漂移
        logger.error("catfish-xcatfish-user verify (静态) fail: %s", e)
        raise

    # Step 2: 注册 pre_tool_call hook 真 fail-loud
    if hasattr(ctx, "register_hook"):
        try:
            ctx.register_hook("pre_tool_call", _mod.pre_tool_call_safety_check)
            logger.info("catfish-xcatfish-user: pre_tool_call hook registered ✓")
        except Exception as e:
            logger.warning(
                "catfish-xcatfish-user register_hook fail: %s (fail-loud 降级)", e
            )

    # Step 2.5: BL-MEMORY-ROUTER-A2-V3 (6/2 凌晨鸿波拍): 替换 hermes builtin memory tool.
    # browser_navigate 5/6 同 pattern. catfish-memory plugin (5/19 ship) 在 gateway
    # mode 不 load, 真 enforce 位置在这里 (catfish-xcatfish-user plugin 真装载).
    # 5 kind 路由 (identity/project_fact/workflow/journal/todo), LLM 自己填 kind,
    # 0 后端 LLM 调用, 0 性能损失.
    if hasattr(ctx, "register_tool"):
        try:
            # 懒加载 memory_router (跟 plugin.py / pre_tool_call_safety_check 同 pattern)
            from pathlib import Path as _Path
            import importlib.util as _iu
            _router_py = _Path(__file__).parent / "memory_router.py"
            _spec = _iu.spec_from_file_location(
                "_catfish_xcatfish_user_memory_router", _router_py
            )
            _router = _iu.module_from_spec(_spec)
            _spec.loader.exec_module(_router)

            ctx.register_tool(
                name="memory",
                toolset="memory",
                schema=_router.CATFISH_MEMORY_SCHEMA,
                handler=lambda args, **kw: _router.handle_memory_tool(args, **kw),
                override=True,
                emoji="🧠",
                description=(
                    "catfish 智能 memory 路由器 (替换 hermes builtin). "
                    "按 kind 路由 5 仓库: identity/project_fact/workflow/journal/todo."
                ),
            )
            logger.info(
                "catfish-xcatfish-user: memory tool override ✓ (5 kind 路由)"
            )
        except Exception as e:  # noqa: BLE001
            logger.error(
                "catfish-xcatfish-user: memory tool override 失败 (ignored): %s. "
                "退化到 hermes builtin (70%% 跑偏问题不修).",
                e,
            )

    # Step 2.6 (P3.4.B 6/15 鸿波): 撤掉 ctx.register_tool session_search override —
    # 实测无效, hermes builtin 反向覆盖 (memory tool 生效, session_search 不生效,
    # 实测 76-101s 同原生). P3.4.C 改成 plugin.py _patch_p16_session_search hard
    # monkey-patch 函数本体 (跟 P1-P15 同模式, 100% 真生效).

    # Step 2.7: 6/2 晚 BL-CORS-PREV-MODEL-SYNC — **同步**跑 P8/P9 CORS patch.
    #
    # 真生产事故 (鸿波 6/2 晚 截图全 chat 挂 "无法连接 hermes API: Load failed"):
    #
    # 真因 audit 完毕: P9 CORS patch 一直在 install() 里, 而 install() 跑在
    # _delayed_install 后台线程, 等 model_tools fully init. 但 hermes gateway 进程
    # (微信/飞书 + api_server 8642) **不 import model_tools** (那是 chat agent 用的) →
    # 30s timeout 后 install 跳过 → **P9 patch 从来没真跑过**.
    #
    # 5/18 BL-GATEWAY-SOFT-HANDOFF 加 X-Catfish-Prev-Model header 之后 16 天没出问题,
    # 是因为之前 Companion 走 gateway 8999 直连绕过 hermes CORS. 6/1 BL-API-PATH-AWARE-AUTH
    # 改走 hermes 8642 后, 真撞到这个一直没生效的 CORS 缺口.
    #
    # P9 patch 只依赖 gateway.platforms.api_server module (hermes 启动早期就 import),
    # **不依赖 model_tools**, 可以同步跑. 提前跑还保证 api_server bind 8642 前
    # _CORS_HEADERS 就是新值, 第一个 OPTIONS preflight 就含完整 allowlist.
    # 6/2 晚 round 2 (鸿波 22:00 真生产 400 audit): CORS 修通后 chat 撞 400
    # "service token (sub=client:hermes-cli) requires X-Catfish-User header" —
    # hermes proxy → gateway 转发没真透传 X-Catfish-User. 真因: P1/P7/P10 (X-Catfish-User
    # 透传链) 也在 install() 后台等 model_tools, 跟 P8/P9 同病, 永远没跑.
    #
    # 真扩 Step 2.7: 同步跑所有不依赖 model_tools 的 patch (P0/P1/P3/P4/P7/P8/P9/P10).
    # P5/P6/P11 (依赖 _create_agent → agent_init → model_tools) 仍走 install 后台
    # (那些是 chat agent 路径, gateway 进程不需要).
    try:
        _mod._patch_asyncio_executor_for_contextvars()  # P0
        _mod._patch_p1_agent_init()                      # P1
        _mod._patch_p3_auxiliary_client()                # P3
        _mod._patch_p4_auto_title_session()              # P4
        _mod._patch_p7_companion_proxy_route()           # P7 ← X-Catfish-User 透传真关键
        _mod._patch_p8_p9_cors()                         # P8/P9
        _mod._patch_p10_apply_client_headers_localhost()  # P10
        # P15.2 (6/6 鸿波 marathon): chat_approval middleware 必须在 Application()
        # init **之前**注册 _chat_approval_middleware 给 _patched_app_init 看. 走
        # delayed install (Step 3) 太晚 — Application() init 在 connect() trigger,
        # 早于 delayed install 完成. 同步跑这条让 module global 立即设上.
        _mod._patch_p15_2_chat_approval_route()
        logger.info(
            "catfish-xcatfish-user: 同步 patch ✓ (P0/P1/P3/P4/P7/P8/P9/P10/P15.2) — "
            "X-Catfish-User 透传 + CORS allowlist + chat_approval 真生效"
        )
    except Exception as e:  # noqa: BLE001
        logger.error(
            "catfish-xcatfish-user: 同步 patch 失败 (ignored): %s. "
            "X-Catfish-User 透传或 CORS allowlist 可能没生效, Companion chat 可能撞 400/403",
            e,
            exc_info=True,
        )

    # Step 3: 后台线程等主流程 ready 再 install
    # 6/1 修: ready 判定只看 model_tools fully init (主线程过了 partial init 段).
    # run_agent / agent.agent_init 是 lazy import (LLM call 时才 import), 不应作
    # ready 判定. install 内部 import 它们时主线程已不 partial, 不撞 circular.
    # 6/2 晚: P8/P9 同步跑了, install 仍跑全套 (会重复跑 P8/P9 但幂等 — dict update
    # 是 set-based merge, 重跑等于 no-op).
    def _delayed_install():
        max_wait_s = 30.0
        interval = 0.2
        elapsed = 0.0
        while elapsed < max_wait_s:
            mt = sys.modules.get("model_tools")
            # model_tools fully init = 主线程过了 partial init = install 内部 import
            # run_agent 不会再撞 circular
            if mt and hasattr(mt, "get_tool_definitions"):
                try:
                    _mod.install()
                    logger.info(
                        "catfish-xcatfish-user delayed install ✓ (waited %.1fs)",
                        elapsed,
                    )
                except Exception as e:
                    logger.error(
                        "catfish-xcatfish-user delayed install fail: %s",
                        e, exc_info=True,
                    )
                return
            time.sleep(interval)
            elapsed += interval
        logger.error(
            "catfish-xcatfish-user: 30s model_tools 未 fully init, install 跳过. "
            "pre_tool_call hook 真 LLM call 时会 raise (P0 兜底)."
        )

    t = threading.Thread(
        target=_delayed_install, daemon=True, name="catfish-xcatfish-installer"
    )
    t.start()
    logger.info(
        "catfish-xcatfish-user: register ✓ (后台等主流程 ready 再 install)"
    )


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
