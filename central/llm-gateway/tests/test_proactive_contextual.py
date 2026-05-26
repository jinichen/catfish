"""BL-E13.5 Phase B: generate_contextual_starter 单测 (5/26 改造后).

# 5/26 改造 (BL-PROACTIVE-DECOUPLE)

老 generate_contextual_starter 自己读员工 hermes state.db 拿最近 model. 5/26 audit
砍 — Companion 调时透传 `model_name` body 字段, gateway 不自查 state.db.

# 这个 test 覆盖什么 (5/26 重写后)

  - prompt 拼装 (signal_kind=silence/deadline/focus + context dict)
  - context 字段缺失 → prompt 写明 missing, 不抛
  - unknown signal_kind → fallback
  - 5/26: 没传 model_name → fallback (gateway 不自查)
  - 传了 model_name + LLM 200 → source='llm'
  - 传了 model_name + 5xx → fallback (严格 1 candidate)
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture
def proactive():
    from catfish_gateway import proactive
    return proactive


def _patch_model_resolve(monkeypatch, *, model_name: str = "test-model",
                         available: bool = True):
    """patch resolve_model_obj + load_config — 跟 test_proactive_fallback 同款.

    5/26 后: gateway 不再调 get_user_last_session_model (那是 stub 抛 RuntimeError).
    """
    fake_model = SimpleNamespace(
        name=model_name,
        mode="chat",
        upstream=SimpleNamespace(is_available=available),
    )
    monkeypatch.setattr(
        "catfish_gateway.user_model_resolver.resolve_model_obj",
        lambda name, config: fake_model if (name == model_name and available) else None,
    )
    monkeypatch.setattr(
        "catfish_gateway.config.load_config",
        lambda: SimpleNamespace(models=[fake_model]),
    )
    return fake_model


def test_build_contextual_user_prompt_silence(proactive):
    now = datetime(2026, 5, 6, 14, 0)
    p = proactive._build_contextual_user_prompt(
        "silence",
        {"minutes_ago": 35, "last_user_text": "我去开会一下", "action_hits": "去/开"},
        now,
    )
    assert "35 分钟前说了一句" in p
    assert "我去开会一下" in p
    assert "去/开" in p
    assert "1 句话" in p


def test_build_contextual_user_prompt_deadline(proactive):
    now = datetime(2026, 5, 6, 14, 0)
    p = proactive._build_contextual_user_prompt(
        "deadline",
        {"days_until": 2, "date_str": "5/8", "journal_excerpt": "上会材料 5/8 前要交"},
        now,
    )
    assert "2 天后" in p
    assert "5/8" in p
    assert "上会材料" in p


def test_build_contextual_user_prompt_focus(proactive):
    now = datetime(2026, 5, 6, 14, 0)
    p = proactive._build_contextual_user_prompt(
        "focus",
        {"minutes_away": 35, "last_user_text": "我去开会"},
        now,
    )
    assert "离开 35 分钟" in p
    assert "我去开会" in p
    assert "刚回来" in p


def test_build_contextual_user_prompt_missing_field(proactive):
    """context 字段缺失不应抛异常, prompt 写明 missing."""
    now = datetime(2026, 5, 6, 14, 0)
    p = proactive._build_contextual_user_prompt("silence", {}, now)
    assert "缺 context 字段" in p


@pytest.mark.asyncio
async def test_generate_contextual_unknown_kind_returns_fallback(proactive):
    r = await proactive.generate_contextual_starter("unknown_kind", {},
                                                    model_name="any-model")
    assert r["source"] == "fallback"
    assert r["starter"] == ""
    assert "unknown signal_kind" in r["context_hint"]


@pytest.mark.asyncio
async def test_generate_contextual_no_model_name_returns_fallback(proactive):
    """5/26: Companion 没传 model_name → fallback (gateway 不再自查 state.db)."""
    r = await proactive.generate_contextual_starter(
        "silence",
        {"minutes_ago": 35, "last_user_text": "去做", "action_hits": "去"},
        user_email="newbie@example.com",
        model_name=None,
    )
    assert r["source"] == "fallback"
    assert r["starter"] == ""
    assert "Companion 没传 model_name" in r["context_hint"]


@pytest.mark.asyncio
async def test_generate_contextual_model_unavailable_returns_fallback(proactive, monkeypatch):
    """传了 model_name 但 model 不可达 (resolve_model_obj 返 None) → fallback."""
    _patch_model_resolve(monkeypatch, model_name="catfish-private-main", available=False)
    r = await proactive.generate_contextual_starter(
        "silence",
        {"minutes_ago": 35, "last_user_text": "去做", "action_hits": "去"},
        user_email="zhang@example.com",
        model_name="catfish-private-main",
    )
    assert r["source"] == "fallback"
    assert r["starter"] == ""
    assert "不可达" in r["context_hint"]


@pytest.mark.asyncio
async def test_generate_contextual_llm_success(proactive, monkeypatch):
    """传了 model_name + LLM 200 → source='llm'."""
    _patch_model_resolve(monkeypatch, model_name="catfish-private-main")

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "choices": [{
                    "message": {"content": "卡哪了, 我帮你看看上会材料?"},
                }]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            return FakeResponse()

    with patch("httpx.AsyncClient", return_value=FakeClient()):
        r = await proactive.generate_contextual_starter(
            "silence",
            {"minutes_ago": 35, "last_user_text": "弄上会材料", "action_hits": "弄"},
            user_email="zhang@example.com",
            model_name="catfish-private-main",
        )
        assert r["source"] == "llm"
        assert r["starter"] == "卡哪了, 我帮你看看上会材料?"


@pytest.mark.asyncio
async def test_generate_contextual_llm_5xx_falls_back(proactive, monkeypatch):
    """LLM 5xx → fallback (严格 1 candidate 不切)."""
    _patch_model_resolve(monkeypatch, model_name="catfish-private-main")

    class FakeResponse:
        status_code = 503
        text = ""

        def json(self):
            return {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            return FakeResponse()

    with patch("httpx.AsyncClient", return_value=FakeClient()):
        r = await proactive.generate_contextual_starter(
            "silence",
            {"minutes_ago": 35, "last_user_text": "X", "action_hits": "Y"},
            user_email="zhang@example.com",
            model_name="catfish-private-main",
        )
        assert r["source"] == "fallback"
        assert r["starter"] == ""
        assert "503" in r["context_hint"]
