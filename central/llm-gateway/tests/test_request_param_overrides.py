"""Request-scoped model overrides must not disable agent-loop thinking."""

from types import SimpleNamespace

from catfish_gateway.config import ModelConfig
from catfish_gateway.config_providers import merge_provider
from catfish_gateway.request_param_overrides import (
    applicable_overrides,
    effective_scope,
)


def _upstream(**kwargs):
    defaults = {
        "param_overrides": {"enable_thinking": False},
        "param_overrides_scope": "auto",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_legacy_thinking_toggle_is_scoped_to_forced_tool_choice():
    upstream = _upstream()

    assert effective_scope(upstream) == "forced_tool_choice"
    assert applicable_overrides(upstream, "auto") == {}
    assert applicable_overrides(upstream, "required") == {"enable_thinking": False}


def test_legacy_nested_self_hosted_toggle_is_scoped():
    upstream = _upstream(
        param_overrides={
            "extra_body": {
                "chat_template_kwargs": {"enable_thinking": False},
            },
        },
    )

    assert effective_scope(upstream) == "forced_tool_choice"
    assert applicable_overrides(upstream, None) == {}


def test_legacy_deepseek_thinking_setting_remains_universal():
    upstream = _upstream(
        param_overrides={"extra_body": {"thinking": {"type": "disabled"}}},
    )

    assert effective_scope(upstream) == "all"
    assert applicable_overrides(upstream, "auto") == upstream.param_overrides


def test_explicit_scope_wins_over_legacy_detection():
    upstream = _upstream(param_overrides_scope="all")

    assert effective_scope(upstream) == "all"
    assert applicable_overrides(upstream, "auto") == upstream.param_overrides


def test_provider_split_preserves_scope():
    row = {
        "name": "m",
        "tier": "private",
        "display_name": "m",
        "upstream": {
            "model": "openai/x",
            "provider": "p",
            "param_overrides": {"enable_thinking": False},
            "param_overrides_scope": "forced_tool_choice",
        },
    }
    merged, error = merge_provider(
        row,
        {"p": {"api_base": "http://localhost", "api_key_env": "K", "timeout": 60}},
    )

    assert error is None
    assert ModelConfig.model_validate(merged).upstream.param_overrides_scope == (
        "forced_tool_choice"
    )
