"""请求上下文传输通道 —— 从 plugin.py 拆出 (8/15)。

三条 ContextVar (X-Catfish-User / picker model / catfish_source) + 把它们从
inbound 请求塞进去的 middleware + 让 CV 跨 executor 线程的 asyncio patch。

**这是本目录的基座层**: 不 import 任何兄弟模块, 只被别人 import。
放这儿是因为 CV 被四五处用 (P5/P6/P11 / P10 / P42 / P44 / CORS), 留在
plugin.py 会让每个拆出去的模块都反向依赖它, 成环。
"""
from __future__ import annotations

import logging

# 跟 plugin.py 同名 —— logging.getLogger 同名返回同一对象
logger = logging.getLogger("catfish.xcatfish_user.plugin")

import contextvars as _cv

from aiohttp import web


# contextvar: 真独立的 X-Catfish-User / picker model 传输通道.
# middleware 从 request 提取 → set CV → handler async 链路全程能 get().
# _create_agent wrap 从 CV 兜底读, 不依赖 hermes caller 在 handler mid-method
# 提取再 kwarg 传 (那是 hermes 仓 5/27-5/29 patch 的活, plugin 不该假设它在).
#
# 为什么 contextvar 而不是 request["x"]: _create_agent 签名全 kwargs 没 request,
# wrap 拿不到. contextvar 在 asyncio 同 context 树自动透传, 任何深处函数都能 get.
CV_CF_USER: "_cv.ContextVar[str]" = _cv.ContextVar("catfish_outgoing_user", default="")


CV_PICKER_MODEL: "_cv.ContextVar[str]" = _cv.ContextVar("picker_model_override", default="")


# P41 (8/8 鸿波"advisor 到底调没调"): 把 Companion 标的 catfish_source 透过 hermes
# 带到网关。
#
# 病: Companion 的每条内部调用都在 URL 上标了来源
# (?catfish_source=companion-advisor / companion-profile / companion-email-draft),
# 但**只有直连 8999 的那几条标得住**。走 hermes agent loop 的 (advisor Call 1)
# 会被 hermes 重新 framing 成一次 agent run, 再由 hermes 自己的 OpenAI client
# 打给网关 —— query 没了, 网关侧只看到 source=unknown。
#
# 代价 (8/8 实盘): advisor Call 1 和员工聊天在网关日志里**完全无法区分** ——
# 都是 sub=client:hermes-cli / total=2 / tools_count=107 / prompt 70K 量级。
# 排查早安页卡死时我按 source grep 判成"advisor 从没被调用", 又把一次聊天
# 认成 advisor, 连错两次方向。真相是从 hermes 自己的 agent.log 的
# `conversation turn: ... msg='hi'` 才看出来的。
#
# 修法跟 X-Catfish-User 完全同构 (同一条 CV 通道 + 同一处 default_headers),
# 不新增 patch 点。网关侧一行不用改 —— app.py:2903 本来就先读
# `x-catfish-source` header, query 只是它的 fallback。
CV_CF_SOURCE: "_cv.ContextVar[str]" = _cv.ContextVar("catfish_source", default="")


_PICKER_PLACEHOLDERS = {"", "hermes-agent", None}


def _patch_asyncio_executor_for_contextvars():
    """让 asyncio loop.run_in_executor 自动 copy_context() 包 func.

    # 为什么需要

    hermes _run_agent 用 loop.run_in_executor(None, _run) 把 _run() (内含
    _create_agent) 推到 thread executor. middleware 在 asyncio main loop 设的
    CV (CV_CF_USER / CV_PICKER_MODEL) **不会自动跨到 executor thread**.
    contextvars 默认 thread-local; ThreadPoolExecutor 工人线程不继承提交方
    context.

    # 修法

    monkey-patch asyncio.BaseEventLoop.run_in_executor — 提交 func 前用
    copy_context().run() 包一层. 整 process 范围, 但安全 (其它代码也得益,
    没人 require executor 不见 CV).

    幂等: 重复 install 不二次 wrap.
    """
    import asyncio as _aio
    import functools as _functools

    # 不同 Python 版本路径不同: 3.11+ 顶层 asyncio.BaseEventLoop;
    # 兜底 asyncio.base_events.BaseEventLoop.
    _target_cls = None
    for _path in ("BaseEventLoop",):
        _t = getattr(_aio, _path, None)
        if _t is not None:
            _target_cls = _t
            break
    if _target_cls is None:
        try:
            from asyncio.base_events import BaseEventLoop as _BEL  # type: ignore
            _target_cls = _BEL
        except ImportError:
            logger.warning(
                "无法定位 asyncio BaseEventLoop, CV 跨 thread 不工作 "
                "(executor task 中 X-Catfish-User / picker 可能丢)"
            )
            return

    _orig = _target_cls.run_in_executor
    if getattr(_orig, "_catfish_cv_patched", False):
        return  # 已 patch, 跳过

    def _ctx_aware_run_in_executor(self, executor, func, *args):
        ctx = _cv.copy_context()

        @_functools.wraps(func)
        def _ctx_func(*a):
            return ctx.run(func, *a)

        return _orig(self, executor, _ctx_func, *args)

    _ctx_aware_run_in_executor._catfish_cv_patched = True  # type: ignore[attr-defined]
    _target_cls.run_in_executor = _ctx_aware_run_in_executor
    logger.info(
        "%s.run_in_executor wrapped with copy_context ✓",
        _target_cls.__module__ + "." + _target_cls.__name__,
    )


@web.middleware
async def _request_stash_middleware(request, handler):
    """拦 POST /v1/chat/completions /v1/responses:
       - X-Catfish-User header → CV_CF_USER
       - X-Catfish-Source header 或 ?catfish_source= query → CV_CF_SOURCE (P41 8/8)
       - body.model (非占位) → CV_PICKER_MODEL
       - 顺手塞 request[] (兼容 hermes 仓 patch 还在的场景)

    set 用 contextvars.copy_context 隔离 — 每个 request 独立, 不串.
    aiohttp middleware 默认在每个 request 自己的 asyncio.Task 跑, CV set 不影响
    其它并发 request.
    """
    if request.method == "POST" and request.path in ("/v1/chat/completions", "/v1/responses"):
        try:
            # X-Catfish-User header (大小写不敏感)
            cf_user = (request.headers.get("X-Catfish-User", "") or "").strip()
            if cf_user:
                CV_CF_USER.set(cf_user)
                request["catfish_outgoing_user"] = cf_user  # 兼容 hermes 仓 patch

            # P41 (8/8): 来源标记。header 优先, query 兜底 —— 跟网关
            # app.py:2903 的取值顺序保持一致。Companion 走 hermes 这条路时
            # 标在 query 上 (SERVICE_LLM_QUERY), 所以 query 分支才是常走的那条。
            cf_source = (
                (request.headers.get("X-Catfish-Source", "") or "").strip()
                or (request.query.get("catfish_source", "") or "").strip()
            )
            if cf_source:
                CV_CF_SOURCE.set(cf_source)
                request["catfish_source"] = cf_source

            # body.model — 需读 body. 读完塞回让 handler 再读 (aiohttp body 是 stream).
            body_bytes = await request.read()
            import json as _json
            try:
                body = _json.loads(body_bytes)
            except Exception:
                body = {}
            model = body.get("model")
            if isinstance(model, str) and model.strip() and model.strip() not in _PICKER_PLACEHOLDERS:
                model_clean = model.strip()
                CV_PICKER_MODEL.set(model_clean)
                request["catfish_model_override"] = model_clean
            request._read_bytes = body_bytes  # noqa: SLF001
        except Exception as e:
            logger.debug("request stash middleware failed: %s", e)
    return await handler(request)
