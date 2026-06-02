"""BL-PROMPT-CACHE-PHASE1 (6/2 鸿波): _apply_prompt_cache_markers 单测.

跑法 (从 central/llm-gateway):
    PYTHONPATH=src python -m pytest tests/test_prompt_cache_markers.py -q

覆盖:
- _provider_supports_cache_marker: 6 个真 catfish provider 真支持/跳过
- _apply_prompt_cache_markers:
    * system content str → list-of-blocks + cache_control
    * system content list (multipart) → 最后 text block 加 cache_control
    * system 已有 cache_control 不重复
    * tools[-1] 加 cache_control, tools[0..-2] 不动
    * nvidia_nim / groq 跳过 (params 完全不变)
    * 没 messages / 没 tools / system 缺 0 副作用
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from catfish_gateway.app import (
    _apply_prompt_cache_markers,
    _provider_supports_cache_marker,
)


def _model(upstream_name: str) -> SimpleNamespace:
    return SimpleNamespace(upstream=SimpleNamespace(model=upstream_name))


# ── _provider_supports_cache_marker ────────────────────────────────


def test_provider_support_anthropic_via_openai_proto() -> None:
    """deepseek / dashscope qwen / 私有 vLLM qwen 走 openai/deepseek prefix → 支持
    (LiteLLM 转或 server 端 implicit / vLLM prefix cache 自动)."""
    assert _provider_supports_cache_marker("deepseek/deepseek-v4-flash") is True
    assert _provider_supports_cache_marker("openai/qwen_v3_5_122b_a10b") is True
    assert _provider_supports_cache_marker("openai/qwen3.7-max-2026-05-20") is True


def test_provider_support_gemini() -> None:
    """LiteLLM 转 cached_content."""
    assert _provider_supports_cache_marker("gemini/gemini-3.5-flash") is True
    assert _provider_supports_cache_marker("gemini/gemini-3.1-pro") is True


def test_provider_skip_nvidia_nim() -> None:
    """NIM 严格 schema 校验, 标记真可能 400 BadRequest."""
    assert _provider_supports_cache_marker(
        "nvidia_nim/deepseek-ai/deepseek-v4-flash"
    ) is False
    assert _provider_supports_cache_marker(
        "nvidia_nim/nvidia/llama-3.3-nemotron-super-49b-v1.5"
    ) is False


def test_provider_skip_groq() -> None:
    """Groq LPU 不支持 cache_control."""
    assert _provider_supports_cache_marker("groq/openai/gpt-oss-120b") is False


def test_provider_empty_or_none() -> None:
    """空字符串 / None 不该 crash."""
    assert _provider_supports_cache_marker("") is False
    assert _provider_supports_cache_marker(None) is False  # type: ignore[arg-type]


# ── _apply_prompt_cache_markers: 真改 params ────────────────────────


def test_system_str_converted_to_blocks_with_cache_control() -> None:
    """str content → list-of-blocks, cache_control 在最后块."""
    params = {
        "messages": [
            {"role": "system", "content": "You are catfish."},
            {"role": "user", "content": "hi"},
        ],
        "tools": [],
    }
    _apply_prompt_cache_markers(params, _model("deepseek/deepseek-v4-flash"))
    assert params["messages"][0]["content"] == [
        {
            "type": "text",
            "text": "You are catfish.",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    # user 不动
    assert params["messages"][1]["content"] == "hi"


def test_system_list_multipart_adds_cache_to_last_text() -> None:
    """system 已是 list (vision/multipart): 最后 text block 加 cache_control."""
    params = {
        "messages": [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "first"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                    {"type": "text", "text": "second"},
                ],
            },
        ],
        "tools": [],
    }
    _apply_prompt_cache_markers(params, _model("gemini/gemini-3.5-flash"))
    blocks = params["messages"][0]["content"]
    assert blocks[0] == {"type": "text", "text": "first"}  # 不动
    assert blocks[1].get("cache_control") is None  # image 不动
    assert blocks[2]["cache_control"] == {"type": "ephemeral"}  # 最后 text 加


def test_system_already_has_cache_control_no_duplicate() -> None:
    """幂等: 重复调不该改第二次."""
    params = {
        "messages": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": "x",
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            }
        ],
        "tools": [],
    }
    snap = copy.deepcopy(params)
    _apply_prompt_cache_markers(params, _model("deepseek/deepseek-v4-flash"))
    assert params == snap  # 0 变


def test_tools_last_gets_cache_control_others_untouched() -> None:
    """只最后 1 个 tool 加, 前面的 tools[0..-2] 不动."""
    params = {
        "messages": [{"role": "system", "content": "x"}],
        "tools": [
            {"type": "function", "function": {"name": "a"}},
            {"type": "function", "function": {"name": "b"}},
            {"type": "function", "function": {"name": "c"}},
        ],
    }
    _apply_prompt_cache_markers(params, _model("deepseek/deepseek-v4-flash"))
    assert "cache_control" not in params["tools"][0]
    assert "cache_control" not in params["tools"][1]
    assert params["tools"][2]["cache_control"] == {"type": "ephemeral"}


def test_nvidia_nim_params_completely_untouched() -> None:
    """NIM provider: 0 副作用, params 跟原样一致 (含 messages content 仍 str)."""
    params = {
        "messages": [
            {"role": "system", "content": "Sys."},
            {"role": "user", "content": "hi"},
        ],
        "tools": [
            {"type": "function", "function": {"name": "a"}},
            {"type": "function", "function": {"name": "b"}},
        ],
    }
    snap = copy.deepcopy(params)
    _apply_prompt_cache_markers(
        params, _model("nvidia_nim/deepseek-ai/deepseek-v4-flash"),
    )
    assert params == snap  # 完全不变


def test_groq_params_completely_untouched() -> None:
    """Groq: 0 副作用."""
    params = {
        "messages": [{"role": "system", "content": "Sys."}],
        "tools": [{"type": "function", "function": {"name": "a"}}],
    }
    snap = copy.deepcopy(params)
    _apply_prompt_cache_markers(params, _model("groq/openai/gpt-oss-120b"))
    assert params == snap


def test_no_messages_no_crash() -> None:
    """messages 缺 / 空 list 不该 crash."""
    p1 = {"tools": []}
    _apply_prompt_cache_markers(p1, _model("deepseek/deepseek-v4-flash"))
    p2 = {"messages": [], "tools": []}
    _apply_prompt_cache_markers(p2, _model("deepseek/deepseek-v4-flash"))
    assert p2["messages"] == []


def test_no_tools_no_crash() -> None:
    """tools 缺 / 空 list / None 不该 crash."""
    params = {"messages": [{"role": "system", "content": "x"}]}
    _apply_prompt_cache_markers(params, _model("deepseek/deepseek-v4-flash"))
    # system 仍被处理
    assert isinstance(params["messages"][0]["content"], list)


def test_first_message_not_system_skips_system_marker() -> None:
    """第一条不是 system (奇怪场景) → system 标记跳过, tools 仍标."""
    params = {
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "a"}}],
    }
    _apply_prompt_cache_markers(params, _model("deepseek/deepseek-v4-flash"))
    # user 不动
    assert params["messages"][0]["content"] == "hi"
    # tools 仍标
    assert params["tools"][0]["cache_control"] == {"type": "ephemeral"}


def test_empty_system_content_skipped() -> None:
    """system content 空字符串 → 不转 list (空 content 不该塞 cache marker)."""
    params = {
        "messages": [{"role": "system", "content": ""}],
        "tools": [],
    }
    _apply_prompt_cache_markers(params, _model("deepseek/deepseek-v4-flash"))
    # 仍是 str (没真有内容值得 cache)
    assert params["messages"][0]["content"] == ""
