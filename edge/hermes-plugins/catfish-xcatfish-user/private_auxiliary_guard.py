"""私有模型辅助任务的 fail-closed 守卫 (P47).

Hermes auxiliary client 自带的连接错误 / 402 / 429 fallback 不知道 Catfish 的
隐私边界，可能把 ``catfish-private-*`` 请求改送到 ``catfish-auto``，再由网关
解析成公网模型。本模块只改变私有模型行为：原模型失败就失败，不尝试其他
provider；普通公网会话仍保留 Hermes 原行为。
"""
from __future__ import annotations

import contextvars
import functools
import logging
from typing import Any, Callable

logger = logging.getLogger("catfish.xcatfish_user.private_auxiliary_guard")

PRIVATE_MODEL_PREFIX = "catfish-private-"
_PRIVATE_RUNTIME = contextvars.ContextVar(
    "catfish_private_auxiliary_runtime", default=False
)


def is_private_model(model: Any) -> bool:
    """Catfish 私有模型的封闭判据。"""
    return str(model or "").strip().lower().startswith(PRIVATE_MODEL_PREFIX)


def runtime_is_private(runtime: Any, aux_module: Any = None) -> bool:
    """判断 main_runtime 是否明确选择了 Catfish 私有模型。"""
    data = runtime
    if aux_module is not None:
        try:
            data = aux_module._normalize_main_runtime(runtime)
        except Exception:  # pragma: no cover - Hermes 自身兼容兜底
            data = runtime
    return isinstance(data, dict) and is_private_model(data.get("model"))


def fallback_is_allowed(result: Any) -> bool:
    """私有模式只允许返回明确的私有候选。"""
    return (
        isinstance(result, tuple)
        and len(result) >= 2
        and is_private_model(result[1])
    )


def should_skip_title(model: Any, source: Any = "") -> bool:
    """标题是装饰性任务，私有模型和 Catfish 内部服务不应发 LLM 请求。"""
    internal_sources = {
        "companion-briefing-card",
        "companion-advisor",
        "companion-advisor-transform",
        "companion-wiki-suggest",
        "companion-email-draft",
        "companion-profile",
        "companion-email-scheduler",
        "companion-phishing-scan",
    }
    return is_private_model(model) or str(source or "").strip().lower() in internal_sources


def _no_fallback() -> tuple[None, None, str]:
    return None, None, ""


def patch(aux: Any) -> None:
    """给 Hermes auxiliary client 安装私有模型 fail-closed 规则。幂等。"""
    if getattr(aux, "_catfish_p47_private_auxiliary_patched", False):
        return

    sync_impl = getattr(aux, "_call_llm_impl", None)
    async_impl = getattr(aux, "_async_call_llm_impl", None)
    if not callable(sync_impl) or not callable(async_impl):
        raise AttributeError("Hermes auxiliary call implementation changed")

    @functools.wraps(sync_impl)
    def guarded_sync(*args: Any, **kwargs: Any) -> Any:
        token = _PRIVATE_RUNTIME.set(runtime_is_private(kwargs.get("main_runtime"), aux))
        try:
            return sync_impl(*args, **kwargs)
        finally:
            _PRIVATE_RUNTIME.reset(token)

    @functools.wraps(async_impl)
    async def guarded_async(*args: Any, **kwargs: Any) -> Any:
        token = _PRIVATE_RUNTIME.set(runtime_is_private(kwargs.get("main_runtime"), aux))
        try:
            return await async_impl(*args, **kwargs)
        finally:
            _PRIVATE_RUNTIME.reset(token)

    aux._call_llm_impl = guarded_sync
    aux._async_call_llm_impl = guarded_async

    # 这些函数内部会创建真正的 fallback client；私有模式下要在进入前截断，
    # 不能“先解析公网候选，再把候选丢掉”。
    fallback_names = (
        "_try_configured_fallback_chain",
        "_try_main_fallback_chain",
        "_try_payment_fallback",
        "_try_main_agent_model_fallback",
        "_try_configured_fallback_for_unavailable_client",
    )
    for name in fallback_names:
        original = getattr(aux, name, None)
        if not callable(original):
            raise AttributeError(f"Hermes auxiliary fallback target missing: {name}")

        @functools.wraps(original)
        def guarded_fallback(
            *args: Any, _original: Callable = original, **kwargs: Any
        ) -> Any:
            if _PRIVATE_RUNTIME.get():
                task = kwargs.get("task") or (args[0] if args else "call")
                logger.warning(
                    "P47: private auxiliary %s failed; public fallback blocked", task
                )
                return _no_fallback()
            return _original(*args, **kwargs)

        setattr(aux, name, guarded_fallback)

    aux._catfish_p47_private_auxiliary_patched = True
    logger.info("P47: private auxiliary fallback guard installed ✓")

