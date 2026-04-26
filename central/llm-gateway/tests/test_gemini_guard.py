"""gemini_guard 单测 —— 防 Gemini 输出 native tool_code 的 system 注入。

行为契约:
    Gemini 家族 (gemini/* 开头) + 有 system message → append guard 指令
    其它模型 / 没 system → 不动
    重复调用 → 幂等 (不重复 append)
    多模态 system content (list of parts) → 找第一个 text part 加; 全是 image 则补一个 text part
    异常 content 类型 → 静默跳过, 不抛
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from catfish_gateway.gemini_guard import (
    GEMINI_GUARD_INSTRUCTION,
    _is_gemini,
    harden_for_gemini,
)


# ---------- helpers ----------

def _model(upstream_name: str, name: str = "test"):
    """构造一个跟 ModelConfig duck-type 兼容的对象。"""
    return SimpleNamespace(
        name=name,
        upstream=SimpleNamespace(model=upstream_name),
    )


# ---------- _is_gemini ----------

@pytest.mark.parametrize(
    "upstream_name, expected",
    [
        ("gemini/gemini-2.5-flash", True),
        ("gemini/gemini-3.1-pro-preview", True),
        ("Gemini/gemini-2.5-pro", True),  # 大小写不敏感
        ("openai/qwen_v3_5_122b_a10b", False),
        ("anthropic/claude-sonnet-4.6", False),
        ("bedrock/anthropic.claude", False),
        ("", False),
    ],
)
def test_is_gemini(upstream_name: str, expected: bool) -> None:
    assert _is_gemini(_model(upstream_name)) is expected


# ---------- harden: non-gemini 不动 ----------

def test_non_gemini_unchanged() -> None:
    body = {
        "messages": [
            {"role": "system", "content": "你是 catfish."},
            {"role": "user", "content": "hi"},
        ],
    }
    out = harden_for_gemini(body, _model("openai/qwen"))
    assert out["messages"][0]["content"] == "你是 catfish."  # 完全没动


# ---------- harden: gemini + system → append ----------

def test_gemini_string_system_appended() -> None:
    body = {
        "messages": [
            {"role": "system", "content": "you are catfish."},
            {"role": "user", "content": "hi"},
        ],
    }
    out = harden_for_gemini(body, _model("gemini/gemini-2.5-flash"))
    sys_content = out["messages"][0]["content"]
    assert sys_content.startswith("you are catfish.")
    assert "[CATFISH-GEMINI-GUARD]" in sys_content
    assert "tool_code" in sys_content


def test_gemini_idempotent_on_string() -> None:
    """重复调用不应该 append 第二遍"""
    body = {
        "messages": [
            {"role": "system", "content": "base."},
        ],
    }
    model = _model("gemini/gemini-2.5-flash")
    once = harden_for_gemini(body, model)
    once_content = once["messages"][0]["content"]
    twice = harden_for_gemini(once, model)
    assert twice["messages"][0]["content"] == once_content
    # 只出现一次 marker
    assert once_content.count("[CATFISH-GEMINI-GUARD]") == 1


# ---------- harden: gemini + 没 system → 不强加 ----------

def test_gemini_no_system_skip() -> None:
    body = {
        "messages": [
            {"role": "user", "content": "hi"},
        ],
    }
    out = harden_for_gemini(body, _model("gemini/gemini-2.5-flash"))
    # 没 system 就不动, 不该自动塞一条进来
    assert all(m.get("role") != "system" for m in out["messages"])


# ---------- harden: 多模态 system content ----------

def test_gemini_multimodal_system_appends_to_text_part() -> None:
    body = {
        "messages": [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "base instructions"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            },
            {"role": "user", "content": "go"},
        ],
    }
    out = harden_for_gemini(body, _model("gemini/gemini-2.5-flash"))
    parts = out["messages"][0]["content"]
    text_part = next(p for p in parts if p.get("type") == "text")
    assert "[CATFISH-GEMINI-GUARD]" in text_part["text"]
    assert text_part["text"].startswith("base instructions")
    # image part 不动
    img_part = next(p for p in parts if p.get("type") == "image_url")
    assert img_part == {"type": "image_url", "image_url": {"url": "data:..."}}


def test_gemini_multimodal_no_text_part_appends_one() -> None:
    """全是 image, 没 text part → 加一个 text part"""
    body = {
        "messages": [
            {
                "role": "system",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            },
        ],
    }
    out = harden_for_gemini(body, _model("gemini/gemini-2.5-flash"))
    parts = out["messages"][0]["content"]
    assert len(parts) == 2
    text = next(p for p in parts if p.get("type") == "text")
    assert "[CATFISH-GEMINI-GUARD]" in text["text"]


def test_gemini_multimodal_idempotent() -> None:
    body = {
        "messages": [
            {
                "role": "system",
                "content": [{"type": "text", "text": "x"}],
            },
        ],
    }
    model = _model("gemini/gemini-2.5-flash")
    once = harden_for_gemini(body, model)
    twice = harden_for_gemini(once, model)
    text = twice["messages"][0]["content"][0]["text"]
    assert text.count("[CATFISH-GEMINI-GUARD]") == 1


# ---------- harden: 异常输入 ----------

def test_gemini_unexpected_content_type_silently_skip() -> None:
    """content 是 None / 数字 等怪东西不抛, 原样返回"""
    body = {
        "messages": [
            {"role": "system", "content": None},
            {"role": "user", "content": "hi"},
        ],
    }
    out = harden_for_gemini(body, _model("gemini/gemini-2.5-flash"))
    assert out["messages"][0]["content"] is None  # 原样


def test_messages_not_list() -> None:
    """body['messages'] 不是 list 时不抛"""
    body = {"messages": "not a list"}
    out = harden_for_gemini(body, _model("gemini/gemini-2.5-flash"))
    assert out["messages"] == "not a list"


def test_no_messages_field() -> None:
    body = {"model": "x"}
    out = harden_for_gemini(body, _model("gemini/gemini-2.5-flash"))
    assert out == {"model": "x"}


# ---------- guard instruction 内容 sanity ----------

def test_guard_text_mentions_key_concepts() -> None:
    """这条指令必须明确提到 tool_code, 不然 Gemini 可能没 get 到"""
    text = GEMINI_GUARD_INSTRUCTION
    assert "tool_code" in text
    # 双语版 —— 中英文都要在
    assert "tool_calls" in text or "OpenAI" in text
    assert "禁止" in text or "DO NOT" in text.upper()
