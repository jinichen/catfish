"""P3.3.30 (6/12 鸿波): truncate_tool_messages_dynamic 单测.

测重点:
  1. 未爆 ctx_window → 不截 (原样返, 解决周报反复忘记 root cause)
  2. 爆 ctx_window → 走静态截 (兜底)
  3. 算 token 异常 → 兜底静态截 (跟老行为一致, 不会 worse)
  4. 边界 case: ctx_window 不合法 / 太小
  5. tool chain 完整性 (message count 不变)
"""
from __future__ import annotations

from unittest.mock import patch

from catfish_gateway.tool_msg_truncator import (
    DYNAMIC_RESPONSE_RESERVE_TOKENS,
    DYNAMIC_SAFETY_MARGIN_TOKENS,
    MAX_BYTES_PER_TOOL,
    MIN_BYTES_TO_TRUNCATE,
    truncate_tool_messages_dynamic,
)


# ─── 基线: 短上下文不截 ──────────────────────────────────────────────

def test_under_budget_returns_original_unchanged():
    """token 总数远低于 budget → 原样返 (这是 dynamic 的核心价值).

    周报场景: ctx_window 128K, 历史 6 轮 tool result 每条 5-7KB = 总 30-40KB ≈
    15-20K tokens. budget = 128K - 5K response - 10K margin = 113K. 远不到爆.
    """
    msgs = [
        {"role": "system", "content": "you are a helpful assistant"},
        {"role": "user", "content": "改第 4 项"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "a", "type": "function", "function": {"name": "read_excel", "arguments": "{}"}}],
        },
        # 周报 7 项完整内容 ~5KB, 远超老 2K cap, 但远低于 128K budget
        {"role": "tool", "tool_call_id": "a", "content": "项目内容" * 800},
    ]

    out = truncate_tool_messages_dynamic(
        msgs,
        model_context_window=128_000,
        model_name="qwen_v3_5_122b_a10b",
    )

    # tool message 完整保留, 不截
    assert len(out) == len(msgs)
    assert out[3]["content"] == msgs[3]["content"]
    assert "已截断" not in out[3]["content"]


def test_under_budget_no_mutation_of_original():
    """deepcopy 保证 — caller 改返值不影响原 messages."""
    msgs = [
        {"role": "tool", "tool_call_id": "a", "content": "hello"},
    ]
    out = truncate_tool_messages_dynamic(
        msgs,
        model_context_window=128_000,
        model_name="qwen_v3_5_122b_a10b",
    )
    out[0]["content"] = "mutated"
    assert msgs[0]["content"] == "hello"


# ─── 爆 budget → 静态截兜底 ──────────────────────────────────────────

def test_over_budget_falls_back_to_static_truncation():
    """爆 budget → 走老 truncate_tool_messages 静态截.

    模拟: ctx_window 8K (小模型), 多个 tool 累计 token > 8K - 5K - 10K = 不可能
    (budget 是负的, 实际场景就是 ctx_window 太小, 走兜底分支).
    """
    big_content = "x" * 50_000  # 50KB
    msgs = [
        {"role": "tool", "tool_call_id": f"t{i}", "content": big_content}
        for i in range(10)
    ]

    out = truncate_tool_messages_dynamic(
        msgs,
        model_context_window=8_000,  # 故意小 → budget 算下来 < 0 → 兜底
        model_name="qwen_v3_5_122b_a10b",
    )

    # 消息数不变 (chain 完整性)
    assert len(out) == len(msgs)
    # 但每条 tool message 应该被截
    for m in out:
        assert "已截断" in m["content"]
        assert len(m["content"].encode("utf-8")) <= MIN_BYTES_TO_TRUNCATE


# ─── 异常 fallback ──────────────────────────────────────────────────

def test_estimate_token_exception_falls_back_to_static():
    """estimate_prompt_tokens 异常 → 静态截兜底 (跟老行为完全一致, 不能更糟)."""
    big_content = "x" * 50_000
    msgs = [
        {"role": "tool", "tool_call_id": "a", "content": big_content},
    ]

    with patch(
        "catfish_gateway.fallback.estimate_prompt_tokens",
        side_effect=RuntimeError("simulated tokenizer crash"),
    ):
        out = truncate_tool_messages_dynamic(
            msgs,
            model_context_window=128_000,
            model_name="qwen_v3_5_122b_a10b",
        )

    # 兜底 → 走 static 截
    assert len(out) == 1
    assert "已截断" in out[0]["content"]


# ─── 边界 ─────────────────────────────────────────────────────────────

def test_empty_messages():
    assert truncate_tool_messages_dynamic(
        [], model_context_window=128_000, model_name="x"
    ) == []


def test_zero_context_window_falls_back_to_static():
    """ctx_window=0 不合法 → 兜底静态截."""
    msgs = [{"role": "tool", "tool_call_id": "a", "content": "x" * 50_000}]
    out = truncate_tool_messages_dynamic(
        msgs, model_context_window=0, model_name="x"
    )
    assert "已截断" in out[0]["content"]


def test_tiny_context_window_falls_back_to_static():
    """ctx_window 太小 (< response_reserve + safety_margin = 15K) → 兜底."""
    msgs = [{"role": "tool", "tool_call_id": "a", "content": "x" * 50_000}]
    out = truncate_tool_messages_dynamic(
        msgs,
        model_context_window=DYNAMIC_RESPONSE_RESERVE_TOKENS  # noqa: W503
        + DYNAMIC_SAFETY_MARGIN_TOKENS
        - 1,
        model_name="x",
    )
    # budget 计算下来负, 兜底静态截
    assert "已截断" in out[0]["content"]


# ─── 完整 ReAct chain ──────────────────────────────────────────────

def test_react_chain_preserved_under_budget():
    """ReAct chain 完整: assistant ↔ tool 一对一, 不能掉一条."""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "run"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "x", "arguments": "{}"}},
                {"id": "b", "type": "function", "function": {"name": "y", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "a", "content": "result a" * 100},  # ~800B
        {"role": "tool", "tool_call_id": "b", "content": "result b" * 100},  # ~800B
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c", "type": "function", "function": {"name": "z", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "c", "content": "result c" * 100},
    ]

    out = truncate_tool_messages_dynamic(
        msgs, model_context_window=128_000, model_name="qwen_v3_5_122b_a10b"
    )

    # 数量一致
    assert len(out) == len(msgs)
    # tool_call_id 配对完整
    tool_call_ids = [
        m["tool_call_id"] for m in out if m.get("role") == "tool"
    ]
    assert tool_call_ids == ["a", "b", "c"]
    # tool content 没被截 (budget 够)
    for m in out:
        if m.get("role") == "tool":
            assert "已截断" not in m["content"]


# ─── 跟老 static 行为 BC ──────────────────────────────────────────

def test_module_exports_dynamic_function():
    """API surface — 老 caller 仍能 import old static, 新 caller 用 dynamic."""
    from catfish_gateway.tool_msg_truncator import (  # noqa: PLC0415
        truncate_tool_messages,
        truncate_tool_messages_dynamic,
    )
    assert callable(truncate_tool_messages)
    assert callable(truncate_tool_messages_dynamic)


def test_max_bytes_per_tool_bumped_to_20k():
    """P3.3.30: 老 2K cap → 20K. 验防误回滚."""
    assert MAX_BYTES_PER_TOOL == 20_000
