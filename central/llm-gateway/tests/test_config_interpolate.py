"""Env-var 插值 (config._interpolate_env) 单测。

设计目的:
    1. 让 models.yaml 不出现内网 IP / UUID / hostname / api_base, 全部走 env
    2. 同样的机制将来给 quota / rbac / 任何敏感配置统一通道

边界要覆盖:
    - 完整字符串就是 ${VAR} (整体替换)
    - 字符串里嵌入多个 ${VAR} (子串替换)
    - ${VAR:-default}, env 没设时用 default
    - ${VAR:-default}, env 设了用 env
    - ${VAR:-} (空 default) 也行
    - 缺必需 env → 立刻 RuntimeError, 报清楚是哪个变量
    - 嵌套 dict / list 递归
    - 非字符串 (int / bool / None) 原样
    - 一个字符串里 ${A}${B} 紧贴
"""
from __future__ import annotations

import pytest

from catfish_gateway.config import _interpolate_env


# ---------- 字符串边界 ----------

def test_plain_string_no_placeholder() -> None:
    assert _interpolate_env("hello world") == "hello world"


def test_full_string_is_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOO", "bar")
    assert _interpolate_env("${FOO}") == "bar"


def test_embedded_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST", "1.2.3.4")
    monkeypatch.setenv("PORT", "8080")
    assert _interpolate_env("http://${HOST}:${PORT}/v1") == "http://1.2.3.4:8080/v1"


def test_back_to_back_placeholders(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A", "x")
    monkeypatch.setenv("B", "y")
    assert _interpolate_env("${A}${B}") == "xy"


def test_default_used_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAYBE", raising=False)
    assert _interpolate_env("${MAYBE:-fallback}") == "fallback"


def test_default_ignored_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAYBE", "real")
    assert _interpolate_env("${MAYBE:-fallback}") == "real"


def test_empty_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMPTY_TARGET", raising=False)
    assert _interpolate_env("${EMPTY_TARGET:-}") == ""


def test_missing_required_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEFINITELY_NOT_SET", raising=False)
    with pytest.raises(RuntimeError, match="DEFINITELY_NOT_SET"):
        _interpolate_env("${DEFINITELY_NOT_SET}")


def test_default_with_special_chars(monkeypatch: pytest.MonkeyPatch) -> None:
    """default 里可以有冒号 / 斜杠 / 端口号 (只要没 } 字面)"""
    monkeypatch.delenv("URL_VAR", raising=False)
    assert (
        _interpolate_env("${URL_VAR:-http://127.0.0.1:8999/v1}")
        == "http://127.0.0.1:8999/v1"
    )


# ---------- 递归 ----------

def test_recurse_into_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST", "h.example")
    payload = {
        "a": "${HOST}",
        "b": {"nested": "${HOST}"},
    }
    out = _interpolate_env(payload)
    assert out == {"a": "h.example", "b": {"nested": "h.example"}}


def test_recurse_into_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V", "ok")
    assert _interpolate_env(["${V}", "literal", ["${V}"]]) == [
        "ok", "literal", ["ok"],
    ]


# ---------- 非字符串原样 ----------

@pytest.mark.parametrize("value", [42, True, False, None, 3.14])
def test_non_string_passes_through(value) -> None:
    assert _interpolate_env(value) == value


# ---------- load_config 端到端 ----------

def test_load_config_resolves_models_yaml(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """整套流程: 写一个 yaml -> set env -> load_config -> 拿到替换后的值"""
    from catfish_gateway.config import load_config

    yaml_path = tmp_path / "models.yaml"
    yaml_path.write_text(
        """
version: 1
models:
  - name: catfish-private-main
    tier: private
    display_name: "Test"
    mode: chat
    upstream:
      model: openai/test
      api_base: ${TEST_LLM_BASE}
      api_key_env: TEST_LLM_KEY
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_LLM_BASE", "http://upstream.test:9000/v1")
    cfg = load_config(yaml_path)
    assert len(cfg.models) == 1
    assert cfg.models[0].upstream.api_base == "http://upstream.test:9000/v1"


def test_load_config_with_default_when_env_missing(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """${VAR:-default} 在 env 缺失时不应抛, 用 default"""
    from catfish_gateway.config import load_config

    yaml_path = tmp_path / "models.yaml"
    yaml_path.write_text(
        """
version: 1
models:
  - name: m
    tier: private
    display_name: "M"
    mode: chat
    upstream:
      model: openai/x
      api_base: ${MAYBE_LLM_BASE:-http://default-host:9000/v1}
      api_key_env: TEST_LLM_KEY
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("MAYBE_LLM_BASE", raising=False)
    cfg = load_config(yaml_path)
    assert cfg.models[0].upstream.api_base == "http://default-host:9000/v1"
