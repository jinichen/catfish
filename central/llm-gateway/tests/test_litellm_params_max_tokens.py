"""BL-FIX23 L4a (5/9): _build_litellm_params 强制 max_tokens=4096 默认.

为啥要这测:
    鸿波 5/9 报"鲶鱼半截就停" — 真因是 Qwen vLLM 默认 max_tokens 太小
    (~600 token), 长 docx 输出被截 finish_reason=length, streaming 路径
    BL-A1.1 auto-continue 没接 (auto_continue.py 自己注释说了).

    L4a 兜底: client 没传 max_tokens 时 gateway 强制 4096. 防回归 — 别哪
    天有人改这条又被截.
"""
from __future__ import annotations

from types import SimpleNamespace

from catfish_gateway.app import _build_litellm_params


def _fake_model(name: str = "test-model"):
    """造一个 ConfigModel 替身 (只填 _build_litellm_params 用到的字段)."""
    return SimpleNamespace(
        name=name,
        upstream=SimpleNamespace(
            model="openai/test",
            api_key="test-key",
            api_base="http://127.0.0.1:9999/v1",
            timeout=60,
            param_overrides={},
        ),
    )


def test_max_tokens_default_4096_when_client_missing():
    """client 没传 max_tokens → 兜底 4096 (BL-FIX23 L4a)."""
    body = {"messages": [{"role": "user", "content": "hi"}], "stream": True}
    params = _build_litellm_params(body, _fake_model())
    assert params["max_tokens"] == 4096


def test_max_tokens_default_4096_when_client_passes_none():
    """client 显式传 max_tokens=None → 兜底 4096 (LiteLLM 把 None 当没传一样)."""
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
        "max_tokens": None,
    }
    params = _build_litellm_params(body, _fake_model())
    assert params["max_tokens"] == 4096


def test_max_tokens_client_value_preserved():
    """client 真传了 max_tokens (例如 summarizer 设 600) → 不动, 走 client 值."""
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 600,
    }
    params = _build_litellm_params(body, _fake_model())
    assert params["max_tokens"] == 600


def test_max_tokens_client_zero_preserved():
    """client 传 0 (虽然没意义) 不被覆盖 — 不要 auto-magic 改员工显式值."""
    body = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 0}
    params = _build_litellm_params(body, _fake_model())
    # 0 是 falsy 但不是 None — _build 不该兜底
    assert params["max_tokens"] == 0


def test_max_tokens_default_works_with_param_overrides():
    """param_overrides 不影响 max_tokens 兜底 (顺序: 默认 → param_overrides)."""
    model = _fake_model()
    model.upstream.param_overrides = {"temperature": 1.0}  # gemini 3 那种
    body = {"messages": [{"role": "user", "content": "hi"}]}
    params = _build_litellm_params(body, model)
    assert params["max_tokens"] == 4096
    assert params["temperature"] == 1.0
