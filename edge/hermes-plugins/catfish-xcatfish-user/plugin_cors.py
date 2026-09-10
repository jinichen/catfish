"""CORS / 代理路由 / 中间件挂载 —— 从 plugin.py 拆出 (8/15)。

P7 (Companion proxy 路由) · P8/P9 (CORS + 中间件链) · P24 (CORS allowlist)。

⚠ 跟 plugin_approval 之间有一个**可变全局**要小心:

`_chat_approval_middleware` 由 plugin_approval 的 P15.2 用 `global` 赋值,
本模块在挂中间件链时要读它。**必须走模块属性访问**
(`plugin_approval._chat_approval_middleware`), 不能 `from ... import` ——
后者建的是绑定快照, P15.2 后来的 global 赋值它看不见, 结果就是审批中间件
永远不进链, 而且没有任何报错。
"""
from __future__ import annotations

import logging

# 跟 plugin.py 同名 —— logging.getLogger 同名返回同一对象
logger = logging.getLogger("catfish.xcatfish_user.plugin")

import os

from aiohttp import web

# 见模块 docstring: 这两个必须走**模块对象**, 不能 from ... import。
#   · plugin_approval._chat_approval_middleware —— P15.2 用 global 赋值,
#     快照绑定看不见后来的值
#   · plugin_ctx._ctx_mod()._request_stash_middleware —— 只是保持同一风格, 顺带避免
#     以后有人给它加 global
def _sib(name):
    """延迟取兄弟模块。

    # ⚠ 8/15 晚修的 bug —— 这个函数原来是坏的, 而且坏得很安静

    早上把 plugin.py 从 3226 行拆开时, 我写了这个 helper, body 是一行:

        from plugin import _import_sibling      # ← 裸 absolute import

    **那正是这个插件不能用的写法。** 目录名带 dash (`catfish-xcatfish-user`),
    hermes 用 `spec_from_file_location` + `submodule_search_locations` 加载,
    模块在 sys.modules 里叫 `catfish-xcatfish-user.plugin`, 没有叫 `plugin` 的。

    讽刺的是 plugin.py 里的 `_import_sibling` 有三段 fallback, 存在的理由就是
    这个 (5/28 那次"装好 9 天没工作"之后加的) —— 而我写的这个 helper, 名义上是
    "复用那三段", 实际用了三段要绕开的那一段。

    ## 为什么拖到晚上才发现

    hermes 进程从拆分之前就一直跑着, Python 把老模块缓存在内存里。8/15 18:03
    重启 gateway 之后才第一次加载新拆的模块, 日志里立刻冒出 4 条:

        P23 inbound: picker ... failed: No module named 'plugin'
        P1 post-init apply_headers failed: No module named 'plugin'
        P6/P11 _create_agent post-init failed: No module named 'plugin'

    三处都被 `except Exception` 包着, 只 warning 不抛 —— 员工看不出任何异常,
    只是 header 注入、picker 模型覆盖这些悄悄不干活了。

    单测也没抓住: 测试里 `sys.path.insert(0, PLUGIN_DIR)` 之后裸 import 是通的,
    生产的加载方式不通。**判据比真事窄**, 今天栽的第 N 次。

    # 现在的写法

    段 1 走相对 import —— 跟 plugin.py 的 `_import_sibling` 段 1 是**同一条路**,
    所以命中的是 sys.modules 里同一个 module 对象, 不会造出第二份
    (双 module 对象那个病今天在 catfish-memory 上专门防过)。

    段 2/3 保留原来的路径, 兜住 `__package__` 没设好的加载方式。
    """
    from importlib import import_module

    # 段 1: 相对 —— 生产上走的就是这条 (hermes 给了 submodule_search_locations)
    if __package__:
        try:
            return import_module(f".{name}", package=__package__)
        except (ImportError, SystemError, ValueError, TypeError):
            pass

    # 段 2: plugin 的三段 fallback (它自己能被裸 import 到时才通)
    try:
        from plugin import _import_sibling
        return _import_sibling(name)
    except ImportError:
        pass

    # 段 3: 按文件路径兜底 (跟 plugin._import_sibling 段 3 同款)
    import importlib.util
    import sys as _sys
    from pathlib import Path

    _py = Path(__file__).parent / f"{name}.py"
    if not _py.exists():
        raise ImportError(f"{name}.py 不存在: {_py}")
    _modname = f"_catfish_xcatfish_user_{name}"
    if _modname in _sys.modules:          # 防重复 exec 出第二个 module 对象
        return _sys.modules[_modname]
    _spec = importlib.util.spec_from_file_location(_modname, _py)
    if not _spec or not _spec.loader:
        raise ImportError(f"spec_from_file_location 失败: {_py}")
    _mod = importlib.util.module_from_spec(_spec)
    _sys.modules[_modname] = _mod
    _spec.loader.exec_module(_mod)
    return _mod


_approval = None
_ctx = None


def _approval_mod():
    global _approval  # noqa: PLW0603
    if _approval is None:
        _approval = _sib("plugin_approval")
    return _approval


def _ctx_mod():
    global _ctx  # noqa: PLW0603
    if _ctx is None:
        _ctx = _sib("plugin_ctx")
    return _ctx


# ── P8 / P9 ──────────────────────────────────────────────────────────────

_TAURI_ORIGINS = (
    # packaged Tauri app 真用 origin
    "tauri://localhost",
    "http://tauri.localhost",
    # 6/2 晚 BL-CORS-DEV-ORIGIN: dev mode 真用 origin — Companion vite dev server.
    # 鸿波 6/2 晚生产事故 audit: webview console "[vite] connecting..." 真证.
    #
    # 6/2 晚 BL-CORS-DEV-ORIGIN-1420 (鸿波 21:15 真 paste vite log 抓的): vite 真启动
    # log "Local: http://localhost:1420/" — Tauri 模板默认 vite 端口是 1420 (Tauri
    # 文档推荐, 跟 vite 标准 5173 不同). 我下午盲加 5173 是错的, 加 1420 才真生效.
    "http://localhost:1420",
    "http://127.0.0.1:1420",
    # 保留 5173 作 fallback (有些员工自己改了 vite.config.ts 用 5173)
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


_CATFISH_EXTRA_CORS_HEADERS = (
    "X-Catfish-User",
    "X-Catfish-Internal",
    # 6/2 晚 BL-CORS-PREV-MODEL: 真生产事故 — Companion chat.ts 真发 X-Catfish-Prev-Model
    # (跟 internal_models follow-user model 联动), hermes CORS preflight 拒, 整个
    # /v1/chat/completions 死. 浏览器 console:
    #   "Request header field X-Catfish-Prev-Model is not allowed by
    #    Access-Control-Allow-Headers"
    # 单条不在 allowlist 整 preflight 失败, 顺带把 X-Catfish-User 也报错. 加上.
    "X-Catfish-Prev-Model",
    "X-Catfish-Source",  # 顺手 — BL-RBAC-DAY4-HARDENING audit header, Companion 可能发
    "X-Hermes-Session-Id",
    "X-Hermes-Session-Key",
)


def _proxy_404_middleware_factory(handler_method_name="_handle_companion_proxy"):
    """生成 404 fallback middleware. handler_method_name = APIServerAdapter 上的
    proxy handler 方法名. 用 factory 是因为 middleware 需要绑 self 才能调
    self._handle_companion_proxy.
    """
    from aiohttp import web as _w

    @_w.middleware
    async def proxy_404_middleware(request, handler):
        try:
            return await handler(request)
        except _w.HTTPNotFound:
            # 路由没匹配 → 走 proxy
            adapter = request.app.get("_catfish_apiserver_adapter")
            if adapter is None or not hasattr(adapter, handler_method_name):
                raise  # 没 adapter 引用, 退回 404
            proxy_handler = getattr(adapter, handler_method_name)
            return await proxy_handler(request)

    return proxy_404_middleware


_proxy_404_middleware = _proxy_404_middleware_factory()


def _patch_p8_p9_cors() -> None:
    """
    P8: Tauri origin (Companion 桌面 app) 加进允许列表.
    P9: Allow-Headers 加 X-Catfish-* / X-Hermes-Session-* (Companion 发的自定义 header).
    P11: 顺便把 picker middleware 注册到 app.
    """
    from gateway.platforms.api_server import APIServerAdapter

    # P9: 扩 Allow-Headers.
    # hermes 0.15: _CORS_HEADERS 是 **module-level 常量** (api_server.py:502), 不
    # 是 class attribute. 直接 mutate module dict. 同时给 class 兼容性写一份 (有
    # 老版本可能在 class 上).
    try:
        from gateway.platforms import api_server as _api_server_mod
        if hasattr(_api_server_mod, "_CORS_HEADERS"):
            existing = _api_server_mod._CORS_HEADERS.get("Access-Control-Allow-Headers", "")
            existing_set = {h.strip() for h in existing.split(",") if h.strip()}
            for h in _CATFISH_EXTRA_CORS_HEADERS:
                existing_set.add(h)
            _api_server_mod._CORS_HEADERS["Access-Control-Allow-Headers"] = ", ".join(sorted(existing_set))
            logger.info("P9 module-level _CORS_HEADERS Allow-Headers 扩 ✓")
    except Exception as _e:
        logger.warning("P9 module-level _CORS_HEADERS 修改失败: %s", _e)

    # 老版本可能 class attribute (兼容)
    if hasattr(APIServerAdapter, "_CORS_HEADERS"):
        existing = APIServerAdapter._CORS_HEADERS.get("Access-Control-Allow-Headers", "")
        existing_set = {h.strip() for h in existing.split(",") if h.strip()}
        for h in _CATFISH_EXTRA_CORS_HEADERS:
            existing_set.add(h)
        APIServerAdapter._CORS_HEADERS["Access-Control-Allow-Headers"] = ", ".join(sorted(existing_set))

    # P8: 给 APIServerAdapter 装 _is_tauri_origin 方法 + wrap origin 检查
    def _is_tauri_origin(self, origin: str) -> bool:
        if not origin:
            return False
        return origin in _TAURI_ORIGINS

    APIServerAdapter._is_tauri_origin = _is_tauri_origin

    # 找 origin 校验函数 — 跨版本名字可能漂. 候选:
    for candidate in ("_origin_allowed", "_is_origin_allowed", "_cors_origin_allowed"):
        if hasattr(APIServerAdapter, candidate):
            _orig_origin_check = getattr(APIServerAdapter, candidate)

            def patched_origin_check(self, origin: str, _orig=_orig_origin_check) -> bool:
                if self._is_tauri_origin(origin):
                    return True
                return _orig(self, origin)

            setattr(APIServerAdapter, candidate, patched_origin_check)
            break
    else:
        logger.warning("P8: cannot find origin check method, Tauri origin NOT allowed via CORS")

    # 6/2 晚 BL-CORS-RETURN-HEADERS: wrap `_cors_headers_for_origin` 真返 CORS headers
    # 给 tauri/dev origin. 真生产事故续: 上一 commit 加 dev origin 到 _TAURI_ORIGINS,
    # `_origin_allowed` 真过, 但 hermes cors_middleware 之后调 `_cors_headers_for_origin`,
    # 这函数**没被 patch**, 看 self._cors_origins (空, 因 hermes config 没设) 返 None.
    # OPTIONS + cors_headers is None → 403.
    # 加 patch: tauri origin 时构造 headers (Allow-Origin = origin, Allow-Headers 来自
    # module-level _CORS_HEADERS 含 P9 加的 X-Catfish-* 全套).
    if hasattr(APIServerAdapter, "_cors_headers_for_origin"):
        _orig_cors_headers = APIServerAdapter._cors_headers_for_origin

        def patched_cors_headers(self, origin: str, _orig=_orig_cors_headers):
            # Tauri / dev origin: 自己构造 headers, 不走原函数 (它要求 _cors_origins
            # 非空才返). 直接用 module-level _CORS_HEADERS (含 P9 扩的 Allow-Headers).
            if self._is_tauri_origin(origin):
                from gateway.platforms import api_server as _api_server_mod
                headers = dict(_api_server_mod._CORS_HEADERS)
                headers["Access-Control-Allow-Origin"] = origin
                headers["Vary"] = "Origin"
                headers["Access-Control-Max-Age"] = "600"
                return headers
            # 非 tauri/dev origin: 走原 hermes 逻辑 (按 self._cors_origins 配)
            return _orig(self, origin)

        APIServerAdapter._cors_headers_for_origin = patched_cors_headers
        logger.info("P8 _cors_headers_for_origin patched ✓ (tauri/dev origin 真返 CORS headers)")
    else:
        logger.warning("P8: APIServerAdapter._cors_headers_for_origin 不存在, dev origin 仍会 403")

    # P11: 注册 stash middleware 到 self._app.
    # hermes 0.15: self._app 在 async connect() 里建 (api_server.py:4653
    # `self._app = web.Application(middlewares=mws, ...)`), 不是 __init__.
    # patch __init__ 时 _app 还没存在, 会 'NoneType.middlewares' 错.
    # 改 patch connect() — 在 orig connect 跑完后 (此时 _app 已建) append.
    # aiohttp web.Application 在 AppRunner.setup() 之后 freeze middlewares,
    # connect() wrap 已经晚了. 用 web.Application monkey-patch — 让构造时就把
    # 我们的 middleware 加进 middlewares 参数. 跨整个 process 影响每个新建的
    # Application, 但我们用 fence (检查构造参数有没有 cors_middleware /
    # security_headers_middleware) 只对 hermes api_server 的 Application 生效.
    import aiohttp.web as _aw

    if not getattr(_aw.Application.__init__, "_catfish_patched", False):
        _orig_app_init = _aw.Application.__init__

        def _patched_app_init(self, *args, middlewares=(), **kwargs):
            mws_list = list(middlewares) if middlewares else []
            is_hermes_app_local = False
            try:
                from gateway.platforms.api_server import (
                    cors_middleware,
                    security_headers_middleware,
                )
                # fence: 只对 hermes api_server App 注入. 别的 aiohttp Application
                # (Companion 本地 server / 别的 plugin) 不动.
                is_hermes_app_local = (
                    cors_middleware in mws_list
                    or security_headers_middleware in mws_list
                )
                if is_hermes_app_local:
                    if _ctx_mod()._request_stash_middleware not in mws_list:
                        mws_list.append(_ctx_mod()._request_stash_middleware)
                    if _proxy_404_middleware not in mws_list:
                        mws_list.append(_proxy_404_middleware)
                    # P15.2 (6/6): chat_approval middleware. 必须 register 阶段同步跑
                    # (__init__.py:Step 2.7), delayed install 太晚 — Application.__init__
                    # 在 connect() 立即 trigger, 早于 delayed install 3 分钟.
                    _appr_mw = _approval_mod()._chat_approval_middleware
                    if _appr_mw is not None and _appr_mw not in mws_list:
                        mws_list.append(_appr_mw)
                    logger.info(
                        "P7/P11/P15.2 middlewares injected via Application.__init__ fence ✓"
                    )
            except Exception as _e:
                logger.debug("middleware inject fence check failed: %s", _e)
            _orig_app_init(self, *args, middlewares=tuple(mws_list), **kwargs)
            if is_hermes_app_local:
                # ── P26 (P3.5.105 6/25 鸿波): cron RESTful endpoints ──
                #
                # 跟 P18 同时机注册 (router 未 freeze), handler 走 P7 stashed adapter.
                # 3 个 endpoint: pause / resume / delete.
                try:
                    async def _p26_cron_pause_handler(request):
                        adapter = request.app.get("_catfish_apiserver_adapter")
                        if adapter is None:
                            return _aw.json_response(
                                {"error": "P26 adapter not ready (P7 stash missing)"},
                                status=503,
                            )
                        return await adapter._handle_cron_pause(request)

                    async def _p26_cron_resume_handler(request):
                        adapter = request.app.get("_catfish_apiserver_adapter")
                        if adapter is None:
                            return _aw.json_response(
                                {"error": "P26 adapter not ready (P7 stash missing)"},
                                status=503,
                            )
                        return await adapter._handle_cron_resume(request)

                    async def _p26_cron_delete_handler(request):
                        adapter = request.app.get("_catfish_apiserver_adapter")
                        if adapter is None:
                            return _aw.json_response(
                                {"error": "P26 adapter not ready (P7 stash missing)"},
                                status=503,
                            )
                        return await adapter._handle_cron_delete(request)

                    self.router.add_post(
                        "/api/cron/jobs/{job_id}/pause", _p26_cron_pause_handler,
                    )
                    self.router.add_post(
                        "/api/cron/jobs/{job_id}/resume", _p26_cron_resume_handler,
                    )
                    self.router.add_delete(
                        "/api/cron/jobs/{job_id}", _p26_cron_delete_handler,
                    )
                    logger.info(
                        "P26 cron routes registered: POST /api/cron/jobs/{id}/pause"
                        " + /resume + DELETE /api/cron/jobs/{id} ✓"
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "P26 cron add_post/delete 失败 (via Application.__init__): %s",
                        e,
                    )
                # ── P44 (8/9 鸿波): agent 进度快照只读出口 ──
                #
                # 逻辑全在 activity_probe.py, 这里只负责"在 router freeze 之前挂上
                # 去"这一件事 —— 挂载时机是 plugin.py 独有的知识 (P18 6/17 踩过
                # frozen router), 探测逻辑不是, 所以分开放。
                #
                # 跟上面几个 P 不同: 它**不走 P7 stashed adapter**, 因为它不需要
                # adapter —— 数据在 GatewayRunner 上, 不在 APIServerAdapter 上。
                try:
                    from . import activity_probe  # noqa: PLC0415
                    activity_probe.register_routes(self.router)
                except Exception as e:  # noqa: BLE001
                    # 进度显示挂了是体验降级, 不是隐私漏洞 —— 不 fail loud。
                    # (P1-P11 那些错位会跨员工串数据, 那才必须让 hermes 起不来。)
                    logger.warning("P44 activity_probe 路由注册失败: %s", e)
                # ── P49 (9/10 鸿波): 横向协同待审批出口 ──
                # GET  /api/catfish/room-link/pending        B 的 Companion 读
                # POST /api/catfish/room-link/outputs/{id}   {"choice":"approve"|"deny"}
                #      叫醒挂起的 run: 批了 output 原样给 A, 拒了 run 以 failed 结束
                # 跟 P44 同一个 fence。路由挂不上只是 B 看不到待审批 —— 三个
                # patch 本身 (plugin.py 走 _try_patch) 照样生效, 红线不漏。
                try:
                    from . import plugin_room_link  # noqa: PLC0415
                    plugin_room_link.register_routes(self.router)
                except Exception as e:  # noqa: BLE001
                    logger.warning("P49 room-link 路由注册失败: %s", e)
                # ── P30 (P3.5.198 7/8 鸿波): wechat qr_login start/poll ──
                #
                # 跟 P26 同时机注册 (router 未 freeze), handler 走 P7 stashed
                # adapter. 2 个 endpoint:
                #   POST /api/platforms/wechat/qr_login/start (无 body)
                #   GET  /api/platforms/wechat/qr_login/poll?qrcode=<hex>
                # 前端 wechat_qr.ts v3 契约, adapter._handle_wechat_qr_*
                # 自己走 _check_auth.
                try:
                    async def _p30_wechat_qr_start_handler(request):
                        adapter = request.app.get("_catfish_apiserver_adapter")
                        if adapter is None:
                            return _aw.json_response(
                                {"error": "P30 adapter not ready (P7 stash missing)"},
                                status=503,
                            )
                        return await adapter._handle_wechat_qr_start(request)

                    async def _p30_wechat_qr_poll_handler(request):
                        adapter = request.app.get("_catfish_apiserver_adapter")
                        if adapter is None:
                            return _aw.json_response(
                                {"error": "P30 adapter not ready (P7 stash missing)"},
                                status=503,
                            )
                        return await adapter._handle_wechat_qr_poll(request)

                    self.router.add_post(
                        "/api/platforms/wechat/qr_login/start",
                        _p30_wechat_qr_start_handler,
                    )
                    self.router.add_get(
                        "/api/platforms/wechat/qr_login/poll",
                        _p30_wechat_qr_poll_handler,
                    )
                    logger.info(
                        "P30 wechat qr routes registered: "
                        "POST /api/platforms/wechat/qr_login/start + "
                        "GET /api/platforms/wechat/qr_login/poll ✓"
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "P30 wechat qr add_post/get 失败 (via Application.__init__): %s",
                        e,
                    )
            return None

        _patched_app_init._catfish_patched = True  # type: ignore[attr-defined]
        _aw.Application.__init__ = _patched_app_init

    # connect() wrap: 装 adapter 引用 (proxy_404_middleware 要它调
    # self._handle_companion_proxy). 不再 append middleware (上面构造时已加).
    _orig_connect = APIServerAdapter.connect

    async def patched_connect(self, *args, **kwargs):
        result = await _orig_connect(self, *args, **kwargs)
        try:
            if getattr(self, "_app", None) is not None:
                self._app["_catfish_apiserver_adapter"] = self
        except Exception as e:
            logger.warning("P7 adapter ref set failed: %s", e)
        return result

    APIServerAdapter.connect = patched_connect


# ── P7 ───────────────────────────────────────────────────────────────────

def _patch_p7_companion_proxy_route() -> None:
    """catch-all proxy 路由: Companion 调任何 catfish-gateway 不在 hermes native 路由
    的 endpoint, hermes 透传过去 (skill catalog / tool config / pairing 管理).

    实现: wrap APIServerAdapter 的路由注册函数, 末尾追加 catch-all.
    """
    from gateway.platforms.api_server import APIServerAdapter

    async def _handle_companion_proxy(self, request):
        """透传 request 到 catfish-gateway 8999, X-Catfish-User 沿用 request header.

        BL-PLUGIN-P7-PROXY-TOKEN-SWAP (6/1 鸿波实盘 audit):
        老实现透传 client Authorization, Companion 客户端用 hermes API_SERVER_KEY
        (64 hex), gateway 期 OIDC JWT, 不认 → 401 invalid token. 影响所有
        /api/me /api/audit/me /api/quota/me /api/proactive/* 端点.

        修: 替换 Authorization 成 service token (HERMES_SERVICE_TOKEN, sub=
        client:hermes-cli). hermes_cli 自己的 /v1/chat/completions 路径不走这,
        走内部 LiteLLM acompletion, model.api_key 拿 service token (同 token
        不同入口). User identity 走 X-Catfish-User 透传, gateway 用它取 user.

        前提: HERMES_SERVICE_TOKEN env 已配 (~/.hermes/.env, 由
        scripts/setup-catfish-edge.sh 装机时写). 没配则 swap 不发生, 老
        行为 — gateway 仍 401, 用户从错误看出 setup 没走完.
        """
        import aiohttp
        import os
        # BL-PLUGIN-P7-PROXY-TOKEN-SWAP (6/1 鸿波 audit): 砍硬编码, env 覆盖.
        # 老代码 `gateway_base = "http://127.0.0.1:8999"` 注释 "应该读 config 这里
        # hardcode 占位" 一直没改. 加 CATFISH_GATEWAY_URL env, 默认仍 localhost:8999
        # (开发机 / 标准装机). 生产环境 catfish-cli 装机 (setup-catfish-edge.sh)
        # 应该写这个 env 进 ~/.hermes/.env. TODO (P2): 改读 hermes config
        # `model.base_url`, 去 /v1 后缀.
        gateway_base = os.environ.get(
            "CATFISH_GATEWAY_URL", "http://127.0.0.1:8999"
        ).rstrip("/")
        target_url = f"{gateway_base}{request.path_qs}"
        body = await request.read()
        headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}

        # BL-PLUGIN-P7-PROXY-TOKEN-SWAP: 关键 swap.
        # P3.5.44 (鸿波 6/20 catch '不一次性解决留尾巴'): 老逻辑 os.environ.get 直接读,
        # token 30 天过期员工没设 cron 续 → 所有 /api/* cascade 401 (录屏/advisory/
        # proactive/fetchMe). 现在走 hermes_token_renewal.get_fresh_service_token,
        # 转发前自动检测剩余 < 5 天就调 catfish-identity mint 新, 写盘 + os.environ.
        # mint 失败 (没配 CLIENT_SECRET / IdP 挂) 仍 fallback 返当前 token, 跟老
        # 行为持平 (gateway 401 让员工看到错误信息, 不静默退化).
        from . import hermes_token_renewal  # noqa: PLC0415
        svc_token = await hermes_token_renewal.get_fresh_service_token()
        if svc_token:
            headers["Authorization"] = f"Bearer {svc_token}"
        else:
            logger.warning(
                "P7 proxy: HERMES_SERVICE_TOKEN env 没配, Authorization 透传 "
                "client Bearer. gateway 大概率 401. 跑 scripts/setup-catfish-edge.sh."
            )

        async with aiohttp.ClientSession() as session:
            async with session.request(
                method=request.method,
                url=target_url,
                headers=headers,
                data=body,
                allow_redirects=False,
            ) as upstream:
                resp = web.StreamResponse(
                    status=upstream.status,
                    headers={k: v for k, v in upstream.headers.items()
                             if k.lower() not in ("content-encoding", "transfer-encoding", "connection")},
                )
                await resp.prepare(request)
                async for chunk in upstream.content.iter_chunked(8192):
                    await resp.write(chunk)
                await resp.write_eof()
                return resp

    APIServerAdapter._handle_companion_proxy = _handle_companion_proxy

    # P7 改实现: aiohttp UrlDispatcher 不允许 catch-all (`/{proxy_path:.*}`) 跟
    # 已注册具体 routes 共存 (raises "method GET is already registered"). 改用
    # **404 fallback middleware** — 走完 routing dispatch, 看到 HTTPNotFound
    # 就 proxy 到 catfish-gateway. 等价效果, aiohttp 不抱怨.
    #
    # middleware 在 connect() wrap 里 append 到 _app.middlewares (跟 P11 stash
    # middleware 同位置注册).
    pass  # 真注册逻辑在 _patch_p7_companion_proxy_middleware (下面定义)


# ── P24 (P3.5.89, 6/23 鸿波): hermes API server CORS allowlist 扩 catfish header ─
#
# 真因 audit (今天 8 次瞎猜后真审 me.ts:630 + chat.ts:71 + api_server.py:545):
#
#   ① 用户点 🎓 教学按钮 → chat.ts:307-309 加 header X-Catfish-Teaching-Mode: 1
#   ② Companion (WKWebView) 发请求前自动 OPTIONS preflight, 把这 header 列入
#      Access-Control-Request-Headers
#   ③ hermes api_server.py:543 _CORS_HEADERS["Access-Control-Allow-Headers"] 写死:
#        "Authorization, Content-Type, Idempotency-Key"   ← 只 3 个 header
#   ④ X-Catfish-Teaching-Mode 不在 allowlist → preflight 非 200 → 浏览器抛
#      TypeError: Load failed → Companion 弹 "无法连接 hermes API"
#
# 实证 (今天):
#   - curl 直打 8642 用 hermes_key 返 200 ✓ (curl 不走 preflight, 直接 POST)
#   - Companion 走 preflight 触发 CORS block → Load failed
#   - me.ts:630 早注释过 "hermes proxy CORS allowlist 不含" (5/26 BL-PROACTIVE-DECOUPLE
#     当时把 X-Catfish-Journal-Tail-B64 等移到 body 绕过, 但 X-Catfish-Teaching-Mode 仍 header)
#
# Companion 发的全部 X-Catfish-* header (grep 实证 6/23):
#   X-Catfish-Agent-Name, X-Catfish-Agent-Personality,
#   X-Catfish-Journal-Tail-B64, X-Catfish-Last-Model,
#   X-Catfish-Prev-Model, X-Catfish-Source,
#   X-Catfish-Teaching-Mode, X-Catfish-User, X-Catfish-User-Dept
#
# 修法: in-place mutate _CORS_HEADERS["Access-Control-Allow-Headers"], append
# 所有 X-Catfish-* header. 跟 P22 同款 in-place 修 hermes module-level constant.
#
# fail-safe: api_server module 没导 / _CORS_HEADERS 不存在 / 非 dict → silent skip.

_CATFISH_CORS_HEADERS = [
    "X-Catfish-Agent-Name",
    "X-Catfish-Agent-Personality",
    "X-Catfish-Journal-Tail-B64",
    "X-Catfish-Last-Model",
    "X-Catfish-Prev-Model",
    "X-Catfish-Source",
    "X-Catfish-Teaching-Mode",
    "X-Catfish-User",
    "X-Catfish-User-Dept",
]


def _patch_p24_cors_allowlist() -> None:
    """扩 hermes api_server._CORS_HEADERS Access-Control-Allow-Headers 含 X-Catfish-*.

    hermes 默认只 allow 3 header (Authorization / Content-Type / Idempotency-Key).
    catfish 加 9 个 X-Catfish-* header 没同步进 allowlist, 浏览器 preflight block,
    Companion fetch throw TypeError: Load failed.

    修法: in-place append 所有 X-Catfish-* 到 allowlist string.
    """
    try:
        from gateway.platforms import api_server as _api  # noqa: PLC0415
    except ImportError as e:
        logger.warning("P24: hermes api_server module 没导, skip patch (%s)", e)
        return

    cors = getattr(_api, "_CORS_HEADERS", None)
    if cors is None or not isinstance(cors, dict):
        logger.warning(
            "P24: api_server._CORS_HEADERS 不存在 / 非 dict (got %s), skip. "
            "Companion 浏览器 preflight 会 block 教学按钮 + 其它 X-Catfish-* header.",
            type(cors).__name__,
        )
        return

    current = cors.get("Access-Control-Allow-Headers", "")
    existing = {h.strip() for h in current.split(",") if h.strip()}
    to_add = [h for h in _CATFISH_CORS_HEADERS if h not in existing]

    if not to_add:
        logger.info("P24 CORS allowlist: 已含全部 %d X-Catfish-* header (idempotent skip)",
                    len(_CATFISH_CORS_HEADERS))
        return

    merged = current + ", " + ", ".join(to_add) if current else ", ".join(to_add)
    cors["Access-Control-Allow-Headers"] = merged
    logger.info(
        "P24 CORS allowlist patched — 加 %d X-Catfish-* header 到 Access-Control-Allow-Headers ✓ "
        "(修浏览器 preflight block 教学按钮 + 自定义 header 导致 TypeError: Load failed)",
        len(to_add),
    )
