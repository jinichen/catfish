"""10/23: prompt 估算按上游真实用量校准 (token_calibration)。"""
from __future__ import annotations

import pytest

from catfish_gateway import token_calibration as tc
from catfish_gateway.context_preflight import check_context_fits


@pytest.fixture(autouse=True)
def _clean():
    tc._reset_for_tests()
    yield
    tc._reset_for_tests()


def test_学够才用_之前照旧():
    tc.record("openai/qwen", 10000, 6100)
    tc.record("openai/qwen", 10000, 6100)
    assert tc.factor("openai/qwen") == 1.0, "两次不够, 不改估算"
    tc.record("openai/qwen", 10000, 6100)
    assert tc.factor("openai/qwen") == pytest.approx(0.61 * 1.05)
    assert tc.apply("openai/qwen", 130000) == int(130000 * 0.61 * 1.05)


def test_按模型分别学():
    for _ in range(3):
        tc.record("openai/qwen", 10000, 6000)
        tc.record("deepseek/x", 10000, 10000)
    assert tc.factor("openai/qwen") < 0.7
    assert tc.factor("deepseek/x") == pytest.approx(1.05)
    assert tc.factor("没见过的") == 1.0


def test_小请求和异常值不带偏():
    for _ in range(5):
        tc.record("m", 500, 100)  # 太小, 不学
    assert tc.factor("m") == 1.0
    for _ in range(3):
        tc.record("m", 10000, 100)  # 离谱的 0.01 → 夹到 0.4
    assert tc.factor("m") == pytest.approx(0.4 * 1.05)


class _Model:
    name = "catfish-private-vision"
    context_window = 128000


def test_鸿波10月23的现场_校准后不再误报太长():
    """上游实报 72K, 通用分词器估 13 万: 不校准 → 413; 学到 0.6 后放行。"""
    raw = 130000
    assert check_context_fits(raw, _Model()) is not None
    for _ in range(3):
        tc.record("openai/qwen_v3_6_35b_a3b", 16330, 9980)  # 实测中文 1633→998 的比例
    assert check_context_fits(tc.apply("openai/qwen_v3_6_35b_a3b", raw), _Model()) is None
