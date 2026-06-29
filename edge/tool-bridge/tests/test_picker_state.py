"""P3.5.29 Phase 8 (6/17 鸿波) — tool-bridge picker_state read_picker_model 单测.

跑法: cd edge/tool-bridge && python -m pytest tests/test_picker_state.py -v

覆盖:
- 文件不存在 → None (fail-silent)
- 完整 JSON {chat_model: "xxx"} → chat_model 返
- chat_model 字段缺 → None
- 空字符串 → None
- 非 dict (e.g. list) → None
- parse 错 → None
- env CATFISH_HOME override path
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from catfish_tool_bridge import picker_state


@pytest.fixture
def fake_catfish_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """~/.catfish/ 真fake**, env CATFISH_HOME 指过去."""
    home = tmp_path / ".catfish"
    home.mkdir()
    monkeypatch.setenv("CATFISH_HOME", str(home))
    return home


def _write_picker_state(home: Path, content: str) -> None:
    """写 picker_state.json."""
    (home / "picker_state.json").write_text(content, encoding="utf-8")


# ─── happy path ────────────────────────────────────────


def test_read_returns_chat_model_when_file_valid(fake_catfish_home):
    """完整 JSON {chat_model: 'xxx'} → 返 真值."""
    _write_picker_state(fake_catfish_home, json.dumps({"chat_model": "catfish-public-qwen-flash"}))
    assert picker_state.read_picker_model() == "catfish-public-qwen-flash"


def test_read_strips_whitespace(fake_catfish_home):
    """前后空格 真strip**."""
    _write_picker_state(fake_catfish_home, json.dumps({"chat_model": "  catfish-private-main  "}))
    assert picker_state.read_picker_model() == "catfish-private-main"


# ─── fail-silent paths ────────────────────────────────


def test_read_returns_none_when_file_missing(fake_catfish_home):
    """文件不存在 → None (fail-silent, caller fallback)."""
    # fake_catfish_home dir 已建, 但 picker_state.json 没写
    assert picker_state.read_picker_model() is None


def test_read_returns_none_when_chat_model_field_missing(fake_catfish_home):
    """JSON 真但 chat_model 字段缺** → None."""
    _write_picker_state(fake_catfish_home, json.dumps({"other_field": "value"}))
    assert picker_state.read_picker_model() is None


def test_read_returns_none_when_chat_model_empty_string(fake_catfish_home):
    """chat_model 真空字符串** → None (空算没设)."""
    _write_picker_state(fake_catfish_home, json.dumps({"chat_model": ""}))
    assert picker_state.read_picker_model() is None


def test_read_returns_none_when_chat_model_whitespace_only(fake_catfish_home):
    """chat_model 全空白 → None."""
    _write_picker_state(fake_catfish_home, json.dumps({"chat_model": "   "}))
    assert picker_state.read_picker_model() is None


def test_read_returns_none_when_chat_model_not_string(fake_catfish_home):
    """chat_model 非 str (int/list/dict) → None (type guard)."""
    _write_picker_state(fake_catfish_home, json.dumps({"chat_model": 123}))
    assert picker_state.read_picker_model() is None


def test_read_returns_none_when_json_corrupt(fake_catfish_home):
    """JSON parse 错 → None (fail-silent)."""
    _write_picker_state(fake_catfish_home, "{not valid json:")
    assert picker_state.read_picker_model() is None


def test_read_returns_none_when_json_is_list_not_dict(fake_catfish_home):
    """JSON 真list 不是 dict** → None (type guard)."""
    _write_picker_state(fake_catfish_home, json.dumps(["chat_model", "x"]))
    assert picker_state.read_picker_model() is None


# ─── env CATFISH_HOME path ────────────────────────────


def test_catfish_home_env_var_override(tmp_path, monkeypatch):
    """CATFISH_HOME env 指定真fake home**, path 指真写**."""
    custom = tmp_path / "custom-catfish"
    custom.mkdir()
    monkeypatch.setenv("CATFISH_HOME", str(custom))
    _write_picker_state(custom, json.dumps({"chat_model": "custom-model"}))
    assert picker_state.read_picker_model() == "custom-model"


# ─── caller chain pattern ─────────────────────────────


def test_caller_chain_pattern(fake_catfish_home, monkeypatch):
    """真 caller 真标准 chain pattern**: picker > role > 兜底.

    真ship 真 catfish_tools/install_and_ops/recmode aggregator 真用法.
    """
    from catfish_tool_bridge import role_resolver

    # reset role_resolver cache 确保 mock 生效
    role_resolver._reset_cache_for_tests()

    # 1. picker_state 有 → winning
    _write_picker_state(fake_catfish_home, json.dumps({"chat_model": "picker-model"}))
    monkeypatch.setattr(role_resolver, "resolve", lambda role: "role-model")
    chain_result = (
        picker_state.read_picker_model()
        or role_resolver.resolve("chat_default")
        or "fallback-model"
    )
    assert chain_result == "picker-model"

    # 2. picker_state 空 + role 有 → role winning
    (fake_catfish_home / "picker_state.json").unlink()
    chain_result = (
        picker_state.read_picker_model()
        or role_resolver.resolve("chat_default")
        or "fallback-model"
    )
    assert chain_result == "role-model"

    # 3. picker_state 空 + role 空 → 兜底 winning
    monkeypatch.setattr(role_resolver, "resolve", lambda role: None)
    chain_result = (
        picker_state.read_picker_model()
        or role_resolver.resolve("chat_default")
        or "fallback-model"
    )
    assert chain_result == "fallback-model"
