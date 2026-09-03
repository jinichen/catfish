"""P48: tool_call 多包一层时只做确定性的单层纠形。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import plugin_deferred_tool_guard as guard  # noqa: E402


@pytest.fixture
def patched_resolver(monkeypatch):
    calls = []

    def original(args):
        calls.append(args)
        name = str(args.get("name") or "").strip()
        if not name:
            return None, {}, "tool_call requires a 'name' argument"
        arguments = args.get("arguments", {})
        if not isinstance(arguments, (dict, str)):
            return None, {}, "tool_call 'arguments' must be an object"
        return name, arguments, None

    fake = type(sys)("tools.tool_search")
    fake.resolve_underlying_call = original
    tools_pkg = type(sys)("tools")
    tools_pkg.tool_search = fake
    monkeypatch.setitem(sys.modules, "tools", tools_pkg)
    monkeypatch.setitem(sys.modules, "tools.tool_search", fake)

    guard._patch_p48_tool_call_argument_shape()
    return fake, calls


def test_correct_shape_is_not_copied_or_changed(patched_resolver):
    fake, calls = patched_resolver
    args = {"name": "some_tool", "arguments": {"url": "https://example.com"}}

    assert fake.resolve_underlying_call(args) == (
        "some_tool",
        {"url": "https://example.com"},
        None,
    )
    assert calls[-1] is args


def test_one_nested_bridge_layer_is_repaired(patched_resolver):
    fake, calls = patched_resolver

    got = fake.resolve_underlying_call({
        "arguments": {
            "name": "some_tool",
            "arguments": {"selector": "#submit"},
        }
    })

    assert got == ("some_tool", {"selector": "#submit"}, None)
    assert calls[-1] == {
        "name": "some_tool",
        "arguments": {"selector": "#submit"},
    }


def test_nested_json_string_is_left_for_upstream_parser(patched_resolver):
    fake, calls = patched_resolver

    fake.resolve_underlying_call({
        "arguments": {"name": "some_tool", "arguments": '{"limit": 10}'},
    })

    assert calls[-1] == {"name": "some_tool", "arguments": '{"limit": 10}'}


def test_missing_nested_name_is_not_guessed(patched_resolver):
    fake, calls = patched_resolver
    args = {"arguments": {"arguments": {"query": "x"}}}

    _name, _arguments, error = fake.resolve_underlying_call(args)

    assert "requires a 'name'" in error
    assert calls[-1] is args


def test_top_level_name_wins_over_nested_shape(patched_resolver):
    fake, calls = patched_resolver
    args = {
        "name": "correct_tool",
        "arguments": {"name": "wrong_tool", "arguments": {}},
    }

    fake.resolve_underlying_call(args)

    assert calls[-1] is args
    assert calls[-1]["name"] == "correct_tool"


def test_patch_is_idempotent(patched_resolver):
    fake, _calls = patched_resolver
    first = fake.resolve_underlying_call

    guard._patch_p48_tool_call_argument_shape()

    assert fake.resolve_underlying_call is first
