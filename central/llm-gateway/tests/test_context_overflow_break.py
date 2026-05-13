"""BL-FIX23-L8-overflow-fix (5/13 鸿波"谎报生成") — context > 95% window 时
BL-FIX23 L8 retry 强制 skip + 推 SSE 友好错误.

真问题: prompt_tokens 162-167% 超 model.context_window, 上游 silent truncate
LLM 看不到完整 tool result, 反复"谎报 execute_code 成功". retry 让 prompt 越涨
越多 (rule of thumb: 1 次 retry 加 1-2K tokens, 长 session 几次后 prompt 涨 10K+).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from catfish_gateway import app as app_module


# ─── _is_context_overflowed ─────────────────────────────────


class _FakeModel:
    """模拟 ChosenModel — 只需 context_window 字段."""
    def __init__(self, name: str, context_window: int):
        self.name = name
        self.context_window = context_window


def test_overflow_under_threshold_returns_false():
    m = _FakeModel("test", 128_000)
    # 90% < 95% 阈值
    assert app_module._is_context_overflowed(m, 115_200) is False


def test_overflow_at_threshold_returns_true():
    m = _FakeModel("test", 128_000)
    # 95% 正好
    assert app_module._is_context_overflowed(m, 121_600) is True


def test_overflow_above_threshold_returns_true():
    m = _FakeModel("test", 128_000)
    # 鸿波场景: 167%
    assert app_module._is_context_overflowed(m, 214_100) is True


def test_overflow_no_context_window_returns_false():
    """model.context_window=0 → 退化, 走原 retry 逻辑."""
    m = _FakeModel("test", 0)
    assert app_module._is_context_overflowed(m, 200_000) is False


def test_overflow_no_prompt_tokens_returns_false():
    """prompt_tokens=0 (没 usage 信息) → 退化."""
    m = _FakeModel("test", 128_000)
    assert app_module._is_context_overflowed(m, 0) is False


def test_overflow_missing_attr_returns_false():
    """没 context_window 属性 → 退化."""
    class M:
        name = "test"
    assert app_module._is_context_overflowed(M(), 200_000) is False


# ─── _context_overflow_friendly_error ───────────────────────


def test_friendly_error_includes_pct():
    m = _FakeModel("test", 128_000)
    msg = app_module._context_overflow_friendly_error(m, 214_100)
    # 含百分比
    assert "167%" in msg
    # 含具体 token 数
    assert "214,100" in msg
    assert "128,000" in msg


def test_friendly_error_tells_user_what_to_do():
    """员工应该一眼看到怎么解."""
    m = _FakeModel("test", 128_000)
    msg = app_module._context_overflow_friendly_error(m, 214_100)
    # 给具体动作
    assert "新建会话" in msg
    assert "Cmd+N" in msg or "新对话" in msg
    assert "gemini-pro" in msg or "Gemini" in msg.lower() or "/model" in msg
    assert "/compress" in msg


def test_friendly_error_mentions_tool_result_truncation():
    """解释 LLM 为啥反复'谎报' (上游 truncate 看不到 tool 结果)."""
    m = _FakeModel("test", 128_000)
    msg = app_module._context_overflow_friendly_error(m, 200_000)
    assert "truncate" in msg.lower() or "截断" in msg
    assert "tool" in msg.lower() or "工具" in msg or "execute_code" in msg


def test_friendly_error_no_context_window():
    """退化场景 — context_window=0 不崩, 给 0% 占位."""
    m = _FakeModel("test", 0)
    msg = app_module._context_overflow_friendly_error(m, 200_000)
    # 不抛异常, 仍含基本文案
    assert "0%" in msg or "新建会话" in msg


# ─── 阈值常量 ────────────────────────────────────────────────


def test_threshold_is_95_percent():
    """阈值 95% — 给 5% 安全 margin (gateway 估的 token 数 vs 上游真实计数有差)."""
    assert app_module._CONTEXT_OVERFLOW_RETRY_THRESHOLD == 0.95


# ─── 集成层 (静态分析 retry 块) ──────────────────────────────


@pytest.fixture(scope="module")
def app_src() -> str:
    return Path(app_module.__file__).read_text(encoding="utf-8")


def test_overflow_check_before_retry_decision(app_src):
    """_is_context_overflowed 必须在 BL-FIX23 L8 retry 决策**之前**判, 不是之后."""
    # 找 retry should_retry 行号
    retry_idx = app_src.find("should_retry = (")
    assert retry_idx >= 0
    # 找 _is_context_overflowed 调用
    overflow_idx = app_src.find("_is_context_overflowed(used_model")
    assert overflow_idx >= 0, "BL-FIX23 L8 retry 路径没接 _is_context_overflowed 检查"
    # overflow 检查必须在 should_retry 之前
    assert overflow_idx < retry_idx, (
        "_is_context_overflowed 应该在 should_retry 之前判, 否则 retry 仍发再涨 prompt"
    )


def test_overflow_block_yields_friendly_error(app_src):
    """超限时推 SSE 友好错误, 不是干 break."""
    # 找 overflow break 块
    idx = app_src.find("BL-FIX23 L8 skip retry — CONTEXT OVERFLOW")
    assert idx >= 0
    block = app_src[idx:idx + 1500]
    assert "_context_overflow_friendly_error" in block, "应该推 friendly error"
    assert "yield" in block, "必须 yield SSE 给客户端"
    assert "[DONE]" in block, "yield [DONE] 收尾 SSE"
    assert "break" in block, "break 跳出 fallback 循环"
