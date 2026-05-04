"""pick_internal_model 单测 — BL-F14 (5/4 鸿波拍板).

为啥这个: gateway 内部多个组件 (summarizer / proactive / a2a) 调 LLM 不应写死模型名,
按 use_case tag + private 优先选. catalog 改了不用动 .py 代码.

测什么:
  - env override 强制
  - env 指了 catalog 没的 → 降级 tag
  - tag 匹配, private 优先
  - tag 匹配, 都不可达 → 降级兜底
  - 没 tag → 兜底 (任何 chat + 可达, private 优先)
  - 完全没可用模型 → 返 None
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from catfish_gateway.internal_models import pick_internal_model, KNOWN_USE_CASES


# ─── 用 dataclass-style 假 model 节省真 yaml 解析开销 ───


class FakeUpstream:
    def __init__(self, available: bool = True):
        self._available = available

    @property
    def is_available(self) -> bool:
        return self._available


class FakeModel:
    def __init__(
        self,
        name: str,
        tier: str = "private",
        mode: str = "chat",
        recommended_for: list[str] | None = None,
        available: bool = True,
    ):
        self.name = name
        self.tier = tier
        self.mode = mode
        self.recommended_for = recommended_for or []
        self.upstream = FakeUpstream(available=available)


class FakeConfig:
    def __init__(self, models: list[FakeModel]):
        self.models = models


# ─── helper ───


def _basic_catalog() -> FakeConfig:
    """跟实际 yaml 类似 — private 在前, public 后, 各种 tier"""
    return FakeConfig(models=[
        FakeModel("catfish-private-main", "private", "chat",
                  ["general", "chat", "summarizer", "proactive_starter", "a2a_aux"]),
        FakeModel("catfish-private-vision", "private", "chat",
                  ["vision"]),  # 没 summarizer tag
        FakeModel("catfish-private-embed", "private", "embedding",
                  ["embedding"]),  # mode!=chat, 不可选
        FakeModel("catfish-public-qwen-flash", "public", "chat",
                  ["general", "chat", "fast", "summarizer", "proactive_starter", "a2a_aux"]),
        FakeModel("catfish-public-deepseek-flash", "public", "chat",
                  ["general", "chat", "summarizer", "proactive_starter", "a2a_aux"]),
        FakeModel("catfish-public-gemini-pro", "public", "chat",
                  ["long_context", "vision"]),  # 没 use_case tag
    ])


# ─── 1. env override ───


def test_env_override_forces_specific_model(monkeypatch):
    monkeypatch.setenv("CATFISH_SUMMARIZER_MODEL", "catfish-public-deepseek-flash")
    chosen = pick_internal_model("summarizer", _basic_catalog())
    assert chosen is not None
    assert chosen.name == "catfish-public-deepseek-flash"


def test_env_override_unknown_model_falls_back_to_tag(monkeypatch):
    """env 指了 catalog 不存在的模型 → log warn + 走 tag 选"""
    monkeypatch.setenv("CATFISH_SUMMARIZER_MODEL", "nonexistent-model")
    chosen = pick_internal_model("summarizer", _basic_catalog())
    assert chosen is not None
    # 降级到 tag 选, 私有优先 → catfish-private-main
    assert chosen.name == "catfish-private-main"


def test_env_override_unavailable_model_falls_back_to_tag(monkeypatch):
    """env 指了存在但不可用 (api key 没配) 的 → 降级 tag"""
    cfg = FakeConfig(models=[
        FakeModel("catfish-public-qwen-flash", "public", "chat",
                  ["summarizer"], available=False),  # 不可用
        FakeModel("catfish-private-main", "private", "chat",
                  ["summarizer"], available=True),
    ])
    monkeypatch.setenv("CATFISH_SUMMARIZER_MODEL", "catfish-public-qwen-flash")
    chosen = pick_internal_model("summarizer", cfg)
    assert chosen is not None
    assert chosen.name == "catfish-private-main"  # 降级到 tag, 私有可用


# ─── 2. tag + private 优先 ───


def test_tag_match_prefers_private(monkeypatch):
    """tag 同时命中 private 和 public → 选 private"""
    monkeypatch.delenv("CATFISH_SUMMARIZER_MODEL", raising=False)
    chosen = pick_internal_model("summarizer", _basic_catalog())
    assert chosen is not None
    assert chosen.name == "catfish-private-main"
    assert chosen.tier == "private"


def test_tag_match_only_public_uses_public(monkeypatch):
    """tag 只命中 public (private 不带 tag 或不可用) → 用 public, 按 yaml 顺序"""
    monkeypatch.delenv("CATFISH_SUMMARIZER_MODEL", raising=False)
    cfg = FakeConfig(models=[
        FakeModel("catfish-private-main", "private", "chat",
                  ["general"]),  # 没 summarizer tag
        FakeModel("catfish-public-qwen-flash", "public", "chat",
                  ["summarizer"]),
        FakeModel("catfish-public-deepseek-flash", "public", "chat",
                  ["summarizer"]),
    ])
    chosen = pick_internal_model("summarizer", cfg)
    assert chosen is not None
    assert chosen.name == "catfish-public-qwen-flash"  # public, 按 yaml 顺序第一个


def test_tag_private_unavailable_uses_public(monkeypatch):
    """tag 命中 private 但不可达 → 切 public"""
    monkeypatch.delenv("CATFISH_SUMMARIZER_MODEL", raising=False)
    cfg = FakeConfig(models=[
        FakeModel("catfish-private-main", "private", "chat",
                  ["summarizer"], available=False),  # 私有不可达 (e.g. VPN 断)
        FakeModel("catfish-public-qwen-flash", "public", "chat",
                  ["summarizer"], available=True),
    ])
    chosen = pick_internal_model("summarizer", cfg)
    assert chosen is not None
    assert chosen.name == "catfish-public-qwen-flash"


def test_different_use_cases_pick_different_models(monkeypatch):
    """summarizer 跟 a2a_aux 的 tag 集合不同时, 选不同模型"""
    monkeypatch.delenv("CATFISH_SUMMARIZER_MODEL", raising=False)
    monkeypatch.delenv("CATFISH_A2A_AUX_MODEL", raising=False)
    cfg = FakeConfig(models=[
        FakeModel("only-sum", "private", "chat", ["summarizer"]),
        FakeModel("only-a2a", "public", "chat", ["a2a_aux"]),
    ])
    sum_pick = pick_internal_model("summarizer", cfg)
    a2a_pick = pick_internal_model("a2a_aux", cfg)
    assert sum_pick is not None and sum_pick.name == "only-sum"
    assert a2a_pick is not None and a2a_pick.name == "only-a2a"


# ─── 3. 兜底 — 任何 chat + 可达 ───


def test_no_tag_match_fallback_to_any_chat(monkeypatch):
    """没模型有该 use_case tag → 兜底用任何 chat + 可达, 仍 private 优先"""
    monkeypatch.delenv("CATFISH_SUMMARIZER_MODEL", raising=False)
    cfg = FakeConfig(models=[
        FakeModel("catfish-private-main", "private", "chat", ["general"]),  # 没 summarizer
        FakeModel("catfish-public-qwen-flash", "public", "chat", ["general"]),  # 没 summarizer
    ])
    chosen = pick_internal_model("summarizer", cfg)
    assert chosen is not None
    assert chosen.name == "catfish-private-main"  # 兜底, private 优先


def test_no_tag_match_only_public_available(monkeypatch):
    """没 tag, 私有也不可达 → 用 public 兜底"""
    monkeypatch.delenv("CATFISH_SUMMARIZER_MODEL", raising=False)
    cfg = FakeConfig(models=[
        FakeModel("catfish-private-main", "private", "chat", ["general"], available=False),
        FakeModel("catfish-public-qwen-flash", "public", "chat", ["general"]),
    ])
    chosen = pick_internal_model("summarizer", cfg)
    assert chosen is not None
    assert chosen.name == "catfish-public-qwen-flash"


# ─── 4. 完全没可用 ───


def test_no_chat_models_returns_none(monkeypatch):
    """catalog 全是 embedding 或不可达 → 返 None"""
    monkeypatch.delenv("CATFISH_SUMMARIZER_MODEL", raising=False)
    cfg = FakeConfig(models=[
        FakeModel("embed-only", "private", "embedding", ["embedding"]),
        FakeModel("not-available", "public", "chat", ["summarizer"], available=False),
    ])
    chosen = pick_internal_model("summarizer", cfg)
    assert chosen is None


def test_empty_catalog_returns_none():
    cfg = FakeConfig(models=[])
    assert pick_internal_model("summarizer", cfg) is None


# ─── 5. embedding 模型不该被选 (mode 必须 chat) ───


def test_embedding_model_never_picked():
    """embedding mode 不能用 chat completion, 即使带 tag 也跳过"""
    cfg = FakeConfig(models=[
        FakeModel("embed", "private", "embedding", ["summarizer"]),
        FakeModel("chat", "public", "chat", ["summarizer"]),
    ])
    chosen = pick_internal_model("summarizer", cfg)
    assert chosen is not None
    assert chosen.name == "chat"  # 不会拿 embedding


# ─── 6. KNOWN_USE_CASES 文档约定 ───


def test_known_use_cases_listed():
    """已知 use_case 都在 KNOWN_USE_CASES 里 (防新加调用方忘记登记)"""
    assert "summarizer" in KNOWN_USE_CASES
    assert "proactive_starter" in KNOWN_USE_CASES
    assert "a2a_aux" in KNOWN_USE_CASES


def test_unknown_use_case_still_works(monkeypatch):
    """未注册 use_case 不报错, 走 step 3 兜底 (允许将来加新 use case 不同步本文件)"""
    monkeypatch.delenv("CATFISH_FUTURE_THING_MODEL", raising=False)
    cfg = _basic_catalog()
    chosen = pick_internal_model("future_thing", cfg)
    # 没 tag 匹配 → 兜底任何 chat + 可达, private 优先
    assert chosen is not None
    assert chosen.name == "catfish-private-main"
