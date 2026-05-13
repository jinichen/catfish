"""_is_context_overflowed / _context_overflow_friendly_error 单元测试.

历史: 5/13 加 BL-FIX23-L8-overflow-fix → v2 → L9 → 鸿波"乱七八糟" 整套 BL-FIX23
retry guard 删除. _is_context_overflowed 函数本身保留 — 只作为 metric / 监控用,
不再驱动 stream break. 旧的"集成层 (静态分析 retry 块)" 测试都删了 (retry 块
源码不再存在).
"""
from __future__ import annotations

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
    """model.context_window=0 → 退化."""
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
    assert "167%" in msg
    assert "214,100" in msg
    assert "128,000" in msg


def test_friendly_error_tells_user_what_to_do():
    m = _FakeModel("test", 128_000)
    msg = app_module._context_overflow_friendly_error(m, 214_100)
    assert "新建会话" in msg
    assert "Cmd+N" in msg or "新对话" in msg
    assert "gemini-pro" in msg or "Gemini" in msg.lower() or "/model" in msg


def test_friendly_error_no_context_window():
    m = _FakeModel("test", 0)
    msg = app_module._context_overflow_friendly_error(m, 200_000)
    assert "0%" in msg or "新建会话" in msg


# ─── 阈值常量 ────────────────────────────────────────────────


def test_threshold_is_95_percent():
    assert app_module._CONTEXT_OVERFLOW_RETRY_THRESHOLD == 0.95
