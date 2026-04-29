"""tool_capability_guard 测试 — 配置驱动 (model.supports_tool_use), 不硬编码黑名单.

覆盖:
  - supports_tool_use=False + skill 意图 → reroute 到 supports_tool_use=True 的备选
  - supports_tool_use=True (默认) → 不动
  - supports_tool_use=False 但没 skill 意图 → 不动 (允许聊天)
  - catalog 没 supports_tool_use=True 的备选 → 不动 (warn log)
  - 同 tier 优先选, 没有再跨 tier
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from catfish_gateway.tool_capability_guard import (  # noqa: E402
    model_supports_tools,
    pick_tool_capable_alternative,
    route_to_tool_capable_if_needed,
)


# ── Stub ──────────────────────────────────────────────────────


@dataclass
class _StubModel:
    name: str
    supports_tool_use: bool = True
    tier: str = "public"
    mode: str = "chat"
    display_name: str = ""


@dataclass
class _StubConfig:
    models: list = field(default_factory=list)


# ── model_supports_tools ────────────────────────────────────────


def test_model_supports_tools_default_true():
    """没显式设置时, 默认信任 (返 True)."""
    @dataclass
    class M:
        name: str = "x"  # 没 supports_tool_use 字段

    assert model_supports_tools(M()) is True


def test_model_supports_tools_explicit_false():
    m = _StubModel("x", supports_tool_use=False)
    assert model_supports_tools(m) is False


def test_model_supports_tools_none():
    assert model_supports_tools(None) is True


# ── pick_alternative ───────────────────────────────────────────


def test_pick_alternative_returns_first_supporting():
    config = _StubConfig(models=[
        _StubModel("a", supports_tool_use=False),
        _StubModel("b", supports_tool_use=True),
        _StubModel("c", supports_tool_use=True),
    ])
    current = _StubModel("a", supports_tool_use=False)
    alt = pick_tool_capable_alternative(config, current)
    assert alt.name == "b"  # 第一个 supports_tool_use=True 的


def test_pick_alternative_skips_current():
    config = _StubConfig(models=[
        _StubModel("a", supports_tool_use=True),
        _StubModel("b", supports_tool_use=True),
    ])
    current = _StubModel("a", supports_tool_use=True)
    alt = pick_tool_capable_alternative(config, current)
    assert alt.name == "b"


def test_pick_alternative_prefers_same_tier():
    """同 tier (private) 优先于跨 tier."""
    config = _StubConfig(models=[
        _StubModel("public-1", tier="public"),
        _StubModel("private-1", tier="private"),
        _StubModel("public-2", tier="public"),
    ])
    current = _StubModel("private-current", supports_tool_use=False, tier="private")
    alt = pick_tool_capable_alternative(config, current)
    assert alt.name == "private-1"  # 同 tier private 优先


def test_pick_alternative_falls_back_to_other_tier():
    """同 tier 没有, 跨 tier 找."""
    config = _StubConfig(models=[
        _StubModel("public-only", tier="public"),
    ])
    current = _StubModel("private-x", supports_tool_use=False, tier="private")
    alt = pick_tool_capable_alternative(config, current)
    assert alt.name == "public-only"


def test_pick_alternative_skips_non_chat():
    """embedding 之类的 mode 不能当对话备选."""
    config = _StubConfig(models=[
        _StubModel("emb", mode="embedding"),
        _StubModel("chat-good", mode="chat"),
    ])
    current = _StubModel("bad", supports_tool_use=False)
    alt = pick_tool_capable_alternative(config, current)
    assert alt.name == "chat-good"


def test_pick_alternative_returns_none_when_no_candidate():
    config = _StubConfig(models=[
        _StubModel("only-bad", supports_tool_use=False),
    ])
    current = _StubModel("bad", supports_tool_use=False)
    assert pick_tool_capable_alternative(config, current) is None


# ── route_to_tool_capable_if_needed ────────────────────────────


def test_reroute_when_intent_and_no_tool_support():
    config = _StubConfig(models=[
        _StubModel("flash", supports_tool_use=True, display_name="Qwen Flash"),
    ])
    current = _StubModel(
        "qwen122b", supports_tool_use=False, display_name="Qwen 122B"
    )
    body = {
        "model": "qwen122b",
        "messages": [
            {"role": "user", "content": "写一份本周周报"},
        ],
    }
    new_model, hint = route_to_tool_capable_if_needed(body, config, current)
    assert new_model is not None
    assert new_model.name == "flash"
    assert "supports_tool_use=False" in hint
    assert body["model"] == "flash"  # in-place


def test_no_reroute_when_intent_but_supports_tools():
    """模型 supports_tool_use=True (默认) → 即使 skill 意图也不动."""
    config = _StubConfig(models=[
        _StubModel("alt", supports_tool_use=True),
    ])
    current = _StubModel("good", supports_tool_use=True)
    body = {
        "model": "good",
        "messages": [{"role": "user", "content": "写一份本周周报"}],
    }
    new_model, hint = route_to_tool_capable_if_needed(body, config, current)
    assert new_model is None
    assert hint is None


def test_no_reroute_when_no_intent_even_bad_model():
    """坏模型但用户聊天 → 不动 (允许 nichat)."""
    config = _StubConfig(models=[
        _StubModel("alt", supports_tool_use=True),
    ])
    current = _StubModel("bad", supports_tool_use=False)
    body = {
        "model": "bad",
        "messages": [{"role": "user", "content": "今天天气怎么样"}],
    }
    new_model, hint = route_to_tool_capable_if_needed(body, config, current)
    assert new_model is None
    assert body["model"] == "bad"


def test_no_reroute_when_no_alternative():
    """坏模型 + skill 意图但 catalog 没备选 → 不动 (warn log)."""
    config = _StubConfig(models=[
        _StubModel("only-bad", supports_tool_use=False),
    ])
    current = _StubModel("only-bad", supports_tool_use=False)
    body = {
        "model": "only-bad",
        "messages": [{"role": "user", "content": "写汇报"}],
    }
    new_model, hint = route_to_tool_capable_if_needed(body, config, current)
    assert new_model is None


def test_intent_keywords_coverage():
    """各种 skill 触发关键词都触发 reroute."""
    config = _StubConfig(models=[
        _StubModel("good", supports_tool_use=True),
    ])
    current = _StubModel("bad", supports_tool_use=False)

    for intent in ["写周报", "写汇报材料", "请示件", "立项报告", "上报材料"]:
        body = {
            "model": "bad",
            "messages": [{"role": "user", "content": intent}],
        }
        new_model, _ = route_to_tool_capable_if_needed(body, config, current)
        assert new_model is not None, f"{intent!r} 没触发 reroute"
