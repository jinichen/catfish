"""单测 BL-GATEWAY-SOFT-HANDOFF (5/18).

覆盖:
  - prev_model 缺 / 同名 → no-op
  - 新 model 支持 tools → 只加 system 标记, 不改 messages
  - 新 model 不支持 tools + 历史有 assistant.tool_calls → 转 inline 摘要
  - 新 model 不支持 tools + 历史有 role="tool" message → 转 assistant 文本
  - 没 system message → prepend 一条
  - 已有 system message → suffix 追加
"""
from __future__ import annotations

import pytest

from catfish_gateway.config import ModelConfig, UpstreamConfig
from catfish_gateway.model_handoff import apply_soft_handoff


def _mk_model(name: str, supports_tool_use: bool = True) -> ModelConfig:
    return ModelConfig(
        name=name,
        display_name=name,
        mode="chat",
        tier="public",
        upstream=UpstreamConfig(provider="openai", model=name),
        supports_tool_use=supports_tool_use,
    )


def _mk_config():
    """Config 占位 — apply_soft_handoff 暂未实际查 config (reserved 参数)."""
    return None  # 测试用 None 也行, 当前实现没碰 config


# ── No-op 路径 ───────────────────────────────────────────


def test_no_op_when_prev_model_missing():
    body = {"model": "gemini", "messages": [{"role": "user", "content": "hi"}]}
    new_body, hint = apply_soft_handoff(
        body, prev_model_name=None, new_model=_mk_model("gemini"), config=_mk_config()
    )
    assert hint is None
    assert new_body == body  # 没改


def test_no_op_when_prev_equals_new():
    body = {"model": "gemini", "messages": [{"role": "user", "content": "hi"}]}
    new_body, hint = apply_soft_handoff(
        body,
        prev_model_name="gemini",
        new_model=_mk_model("gemini"),
        config=_mk_config(),
    )
    assert hint is None
    assert new_body == body


def test_annotate_only_when_new_supports_tools():
    """切到 tool-capable 模型 → 不动 messages, 只 system 加 handoff 标记."""
    body = {
        "model": "deepseek",
        "messages": [
            {"role": "system", "content": "你是鲶鱼"},
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "search", "arguments": '{"q":"X"}'}}
            ]},
        ],
    }
    new_body, hint = apply_soft_handoff(
        body,
        prev_model_name="qwen",
        new_model=_mk_model("deepseek", supports_tool_use=True),
        config=_mk_config(),
    )
    assert hint is not None
    assert "annotate-only" in hint
    # tool_calls 字段保留 (新 model 支持)
    assert new_body["messages"][1]["tool_calls"]
    # system 末尾加了 handoff 标记
    assert "handoff" in new_body["messages"][0]["content"]
    assert "qwen" in new_body["messages"][0]["content"]
    assert "deepseek" in new_body["messages"][0]["content"]


# ── 真转译路径: 新 model 不支持 tools ─────────────────────


def test_assistant_tool_calls_transformed_to_inline():
    body = {
        "model": "nemotron",
        "messages": [
            {"role": "system", "content": "你是鲶鱼"},
            {"role": "user", "content": "search X"},
            {
                "role": "assistant",
                "content": "Let me search.",
                "tool_calls": [
                    {"function": {"name": "search", "arguments": '{"q":"X"}'}},
                    {"function": {"name": "fetch", "arguments": '{"url":"u"}'}},
                ],
            },
        ],
    }
    new_body, hint = apply_soft_handoff(
        body,
        prev_model_name="qwen",
        new_model=_mk_model("nemotron", supports_tool_use=False),
        config=_mk_config(),
    )
    assert hint is not None
    assert "transformed-2-calls" in hint  # 2 calls 转了
    assistant_msg = new_body["messages"][2]
    assert "tool_calls" not in assistant_msg  # 字段砍了
    assert "Let me search." in assistant_msg["content"]  # 原 content 保留
    assert "catfish.tool_used" in assistant_msg["content"]  # 转译前缀在
    assert "tool=search" in assistant_msg["content"]
    assert "tool=fetch" in assistant_msg["content"]


def test_tool_role_message_transformed_to_assistant():
    body = {
        "model": "nemotron",
        "messages": [
            {"role": "user", "content": "search X"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "search", "arguments": '{}'}}
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_abc",
                "name": "search",
                "content": '{"results": ["a", "b"]}',
            },
        ],
    }
    new_body, hint = apply_soft_handoff(
        body,
        prev_model_name="qwen",
        new_model=_mk_model("nemotron", supports_tool_use=False),
        config=_mk_config(),
    )
    assert hint is not None
    assert "transformed-1-calls-1-tool-msgs" in hint
    # body 没 system → prepend 一条 handoff system [0], 原 user [1], 转译 assistant [2],
    # 原 tool→assistant [3]
    result_msg = new_body["messages"][3]
    assert result_msg["role"] == "assistant"
    assert "catfish.tool_used" in result_msg["content"]
    assert "result tool=search" in result_msg["content"]
    assert "a" in result_msg["content"] and "b" in result_msg["content"]


def test_handoff_annotation_added_when_no_system_message():
    body = {
        "model": "nemotron",
        "messages": [
            {"role": "user", "content": "hi"},
        ],
    }
    new_body, hint = apply_soft_handoff(
        body,
        prev_model_name="qwen",
        new_model=_mk_model("nemotron", supports_tool_use=False),
        config=_mk_config(),
    )
    assert hint is not None
    # 应该 prepend 了一条 system
    assert new_body["messages"][0]["role"] == "system"
    assert "handoff" in new_body["messages"][0]["content"]
    assert "qwen" in new_body["messages"][0]["content"]


def test_handoff_annotation_appended_to_existing_system():
    body = {
        "model": "nemotron",
        "messages": [
            {"role": "system", "content": "你是鲶鱼"},
            {"role": "user", "content": "hi"},
        ],
    }
    new_body, hint = apply_soft_handoff(
        body,
        prev_model_name="qwen",
        new_model=_mk_model("nemotron", supports_tool_use=False),
        config=_mk_config(),
    )
    assert hint is not None
    # 原 system 应该保留 + 末尾加了 handoff 标记
    sys_msg = new_body["messages"][0]
    assert sys_msg["role"] == "system"
    assert sys_msg["content"].startswith("你是鲶鱼")
    assert "handoff" in sys_msg["content"]
    assert "新模型不支持 tools" in sys_msg["content"]  # lossy 提示


# ── 边缘 ─────────────────────────────────────────────────


def test_assistant_with_dict_content_handled_safely():
    """OpenAI 新 multi-part content 也别炸 (虽然实盘罕见)."""
    body = {
        "model": "nemotron",
        "messages": [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Looking..."}],
                "tool_calls": [{"function": {"name": "search", "arguments": "{}"}}],
            },
        ],
    }
    new_body, hint = apply_soft_handoff(
        body,
        prev_model_name="qwen",
        new_model=_mk_model("nemotron", supports_tool_use=False),
        config=_mk_config(),
    )
    assert hint is not None
    # 不挂 + content 转 str
    assert "tool_calls" not in new_body["messages"][0]
    assert isinstance(new_body["messages"][0]["content"], str)


def test_empty_messages_no_op():
    body = {"model": "nemotron", "messages": []}
    new_body, hint = apply_soft_handoff(
        body,
        prev_model_name="qwen",
        new_model=_mk_model("nemotron", supports_tool_use=False),
        config=_mk_config(),
    )
    # transformed-0-calls-0-tool-msgs, 但仍有 system prepend
    assert hint is not None
    assert new_body["messages"][0]["role"] == "system"
