"""Regression for September 15: retry budgets were all clipped to 512."""
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from catfish_gateway.context_preflight import check_context_fits
from catfish_gateway.llm_params import _apply_max_tokens


@pytest.mark.parametrize("requested,expected", [(8192, 8192), (16384, 16384), (32768, 25952)])
def test_long_prompt_retry_budget(monkeypatch, requested, expected):
    monkeypatch.setenv("CATFISH_MAX_TOKENS_SAFETY", "2048")
    monkeypatch.setattr("catfish_gateway.fallback.estimate_prompt_tokens", lambda *a, **kw: 100000)
    model = NS(name="test", context_window=128000, max_output_tokens=32768,
               upstream=NS(model="openai/test"))
    params = {"messages": [], "tools": [{"type": "function"}], "max_tokens": requested}
    assert check_context_fits(100000, model) is None
    _apply_max_tokens(params, model)
    assert params["max_tokens"] == expected


def test_exhausted_capacity_is_not_manufactured(monkeypatch):
    monkeypatch.setenv("CATFISH_MAX_TOKENS_SAFETY", "2048")
    monkeypatch.setattr("catfish_gateway.fallback.estimate_prompt_tokens", lambda *a, **kw: 127000)
    model = NS(name="test", context_window=128000, upstream=NS(model="openai/test"))
    assert check_context_fits(127000, model) is not None
    with pytest.raises(HTTPException) as exc:
        _apply_max_tokens({"messages": [], "max_tokens": 8192}, model)
    assert exc.value.status_code == 413


@pytest.mark.parametrize("reserve", ["2048", "8192", "invalid", "-1"])
def test_shared_budget_boundary(monkeypatch, reserve):
    from catfish_gateway.context_preflight import remaining_output_tokens

    monkeypatch.setenv("CATFISH_MAX_TOKENS_SAFETY", reserve)
    safety = {"2048": 2048, "8192": 8192, "invalid": 2048, "-1": 0}[reserve]
    model = NS(name="test", context_window=128000, upstream=NS(model="openai/test"))
    estimate = 128000 - safety - 512
    monkeypatch.setattr("catfish_gateway.fallback.estimate_prompt_tokens", lambda *a, **kw: estimate)
    assert check_context_fits(estimate, model) is None
    assert remaining_output_tokens(estimate, model) == 512
    params = {"messages": [], "max_tokens": 8192}
    _apply_max_tokens(params, model)
    assert params["max_tokens"] == 512
    assert check_context_fits(estimate + 1, model) is not None


def test_tool_estimation_and_small_output_cap(monkeypatch):
    tools = [{"type": "function", "function": {"name": "classify"}}]

    def estimate(messages, *, model, tools):
        assert model == "openai/test"
        assert tools[0]["function"]["name"] == "classify"
        return 100000

    monkeypatch.setattr("catfish_gateway.fallback.estimate_prompt_tokens", estimate)
    monkeypatch.setenv("CATFISH_PROMPT_BUFFER_FACTOR", "1.3")
    model = NS(name="test", context_window=128000, max_output_tokens=256,
               upstream=NS(model="openai/test"))
    for requested, expected in [(64, 64), (8192, 256)]:
        params = {"messages": [], "tools": tools, "max_tokens": requested}
        _apply_max_tokens(params, model)
        assert params["max_tokens"] == expected
