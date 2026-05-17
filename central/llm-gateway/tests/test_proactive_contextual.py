"""BL-E13.5 Phase B: generate_contextual_starter 单测.

3 个 signal_kind 各跑一次, 验证:
- prompt 里含 signal context 字段 (动作/日期/分钟)
- 校验 unknown signal_kind → fallback
- 拿不到员工 model → fallback (BL-INTERNAL-MODEL-FOLLOW-USER-FULL 5/17)
- LLM 200 / 5xx 行为

注: 5/17 BL-INTERNAL-MODEL-FOLLOW-USER-FULL 起, generate_contextual_starter 严格 1
candidate (员工最近 session model). 老 pick_internal_models_ordered mock 已废.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def proactive():
    from catfish_gateway import proactive
    return proactive


def _patch_user_model(monkeypatch, *, model_name: str | None = "test-model"):
    """patch user_model_resolver — 同 test_proactive_fallback._patch_user_model.

    model_name=None 模拟员工没最近 session model (新员工 / 老 schema).
    """
    fake_model = SimpleNamespace(
        name=model_name,
        mode="chat",
        upstream=SimpleNamespace(is_available=True),
    ) if model_name else None
    monkeypatch.setattr(
        "catfish_gateway.user_model_resolver.get_user_last_session_model",
        lambda email: model_name,
    )
    monkeypatch.setattr(
        "catfish_gateway.user_model_resolver.resolve_model_obj",
        lambda name, config: fake_model if name == model_name else None,
    )
    monkeypatch.setattr(
        "catfish_gateway.config.load_config",
        lambda: SimpleNamespace(models=[fake_model] if fake_model else []),
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
    r = await proactive.generate_contextual_starter("unknown_kind", {})
    assert r["source"] == "fallback"
    assert r["starter"] == ""
    assert "unknown signal_kind" in r["context_hint"]


@pytest.mark.asyncio
async def test_generate_contextual_no_user_model_returns_fallback(proactive, monkeypatch):
    """员工没最近 session model (新员工 / 老 schema) → fallback.

    BL-INTERNAL-MODEL-FOLLOW-USER-FULL (5/17): 不偷偷用别的 model. context_hint
    里写明 fallback 原因, 让 ops 能查.
    """
    _patch_user_model(monkeypatch, model_name=None)
    r = await proactive.generate_contextual_starter(
        "silence",
        {"minutes_ago": 35, "last_user_text": "去做", "action_hits": "去"},
        user_email="newbie@example.com",
    )
    assert r["source"] == "fallback"
    assert r["starter"] == ""
    assert "最近 session model" in r["context_hint"]


@pytest.mark.asyncio
async def test_generate_contextual_llm_success(proactive, monkeypatch):
    """拿到员工 model + LLM 200 → source='llm'."""
    _patch_user_model(monkeypatch, model_name="catfish-private-main")

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
        )
        assert r["source"] == "llm"
        assert r["starter"] == "卡哪了, 我帮你看看上会材料?"


@pytest.mark.asyncio
async def test_generate_contextual_llm_5xx_falls_back(proactive, monkeypatch):
    """LLM 5xx → fallback (严格 1 candidate 不切, BL-INTERNAL-MODEL-FOLLOW-USER-FULL)."""
    _patch_user_model(monkeypatch, model_name="catfish-private-main")

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
        )
        assert r["source"] == "fallback"
        assert r["starter"] == ""
        assert "503" in r["context_hint"]
