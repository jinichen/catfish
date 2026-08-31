"""P47 私有辅助调用和标题任务的回归测试。"""
from __future__ import annotations

import asyncio

import private_auxiliary_guard as guard


def test_catfish_private_model_is_closed_set():
    assert guard.is_private_model("catfish-private-vision")
    assert guard.is_private_model(" CATFISH-PRIVATE-MAIN ")
    assert not guard.is_private_model("catfish-public-deepseek-flash")
    assert not guard.is_private_model("")


def test_private_runtime_blocks_public_fallback_before_resolution():
    calls = []

    class Aux:
        @staticmethod
        def _normalize_main_runtime(runtime):
            return runtime or {}

        @staticmethod
        def _call_llm_impl(*, main_runtime=None, **_kwargs):
            return Aux._try_payment_fallback("custom", task="title_generation")

        @staticmethod
        async def _async_call_llm_impl(*, main_runtime=None, **_kwargs):
            return Aux._try_payment_fallback("custom", task="title_generation")

        @staticmethod
        def _try_configured_fallback_chain(*args, **kwargs):
            calls.append("configured")
            return "public"

        @staticmethod
        def _try_main_fallback_chain(*args, **kwargs):
            calls.append("main")
            return "public"

        @staticmethod
        def _try_payment_fallback(*args, **kwargs):
            calls.append("payment")
            return "public"

        @staticmethod
        def _try_main_agent_model_fallback(*args, **kwargs):
            calls.append("agent")
            return "public"

        @staticmethod
        def _try_configured_fallback_for_unavailable_client(*args, **kwargs):
            calls.append("unavailable")
            return "public"

    guard.patch(Aux)
    result = Aux._call_llm_impl(
        main_runtime={"model": "catfish-private-vision"}
    )
    assert result == (None, None, "")
    assert calls == []


def test_public_runtime_keeps_original_fallback():
    class Aux:
        @staticmethod
        def _normalize_main_runtime(runtime):
            return runtime or {}

        @staticmethod
        def _call_llm_impl(*, main_runtime=None, **_kwargs):
            return Aux._try_payment_fallback("custom", task="title_generation")

        @staticmethod
        async def _async_call_llm_impl(*, main_runtime=None, **_kwargs):
            return Aux._try_payment_fallback("custom", task="title_generation")

        @staticmethod
        def _try_configured_fallback_chain(*args, **kwargs):
            return None, None, ""

        @staticmethod
        def _try_main_fallback_chain(*args, **kwargs):
            return None, None, ""

        @staticmethod
        def _try_payment_fallback(*args, **kwargs):
            return "public-client", "catfish-public-deepseek-flash", "deepseek"

        @staticmethod
        def _try_main_agent_model_fallback(*args, **kwargs):
            return None, None, ""

        @staticmethod
        def _try_configured_fallback_for_unavailable_client(*args, **kwargs):
            return None, None, ""

    guard.patch(Aux)
    assert Aux._call_llm_impl(main_runtime={"model": "catfish-public-deepseek-flash"})[1] == "catfish-public-deepseek-flash"


def test_async_private_runtime_also_blocks_fallback():
    class Aux:
        @staticmethod
        def _normalize_main_runtime(runtime):
            return runtime or {}

        @staticmethod
        def _call_llm_impl(**_kwargs):
            return None

        @staticmethod
        async def _async_call_llm_impl(*, main_runtime=None, **_kwargs):
            return Aux._try_payment_fallback("custom", task="title_generation")

        @staticmethod
        def _try_configured_fallback_chain(*args, **kwargs):
            return "public"

        _try_main_fallback_chain = _try_configured_fallback_chain
        _try_payment_fallback = _try_configured_fallback_chain
        _try_main_agent_model_fallback = _try_configured_fallback_chain
        _try_configured_fallback_for_unavailable_client = _try_configured_fallback_chain

    guard.patch(Aux)
    result = asyncio.run(
        Aux._async_call_llm_impl(main_runtime={"model": "catfish-private-main"})
    )
    assert result == (None, None, "")


def test_private_and_internal_service_title_are_local_only():
    assert guard.should_skip_title("catfish-private-main", "companion-chat")
    assert guard.should_skip_title("catfish-public-qwen-flash", "companion-advisor")
    assert not guard.should_skip_title("catfish-public-qwen-flash", "companion-chat")
