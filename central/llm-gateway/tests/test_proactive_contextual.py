"""BL-E13.5 Phase B: generate_contextual_starter 单测.

3 个 signal_kind 各跑一次, 验证:
- prompt 里含 signal context 字段 (动作/日期/分钟)
- 校验 unknown signal_kind → fallback
- 模型不可用 (no candidates) → fallback
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def proactive():
    from catfish_gateway import proactive
    return proactive


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
async def test_generate_contextual_no_candidates_returns_fallback(proactive):
    """catalog 没可用 chat 模型 → 返 fallback."""
    with patch("catfish_gateway.internal_models.pick_internal_models_ordered", return_value=[]):
        r = await proactive.generate_contextual_starter(
            "silence",
            {"minutes_ago": 35, "last_user_text": "去做", "action_hits": "去"},
        )
        assert r["source"] == "fallback"
        assert r["starter"] == ""
        assert "catalog 没可用 chat 模型" in r["context_hint"]


@pytest.mark.asyncio
async def test_generate_contextual_llm_success(proactive):
    """LLM 成功返回 → source='llm'."""
    fake_model = type("M", (), {"name": "test-model"})()

    class FakeResponse:
        status_code = 200

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

    with patch("catfish_gateway.internal_models.pick_internal_models_ordered", return_value=[fake_model]):
        with patch("httpx.AsyncClient", return_value=FakeClient()):
            r = await proactive.generate_contextual_starter(
                "silence",
                {"minutes_ago": 35, "last_user_text": "弄上会材料", "action_hits": "弄"},
            )
            assert r["source"] == "llm"
            assert r["starter"] == "卡哪了, 我帮你看看上会材料?"


@pytest.mark.asyncio
async def test_generate_contextual_llm_5xx_falls_back(proactive):
    """LLM 5xx 全候选都失败 → fallback."""
    fake_model = type("M", (), {"name": "test-model"})()

    class FakeResponse:
        status_code = 503

        def json(self):
            return {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            return FakeResponse()

    with patch("catfish_gateway.internal_models.pick_internal_models_ordered", return_value=[fake_model]):
        with patch("httpx.AsyncClient", return_value=FakeClient()):
            r = await proactive.generate_contextual_starter(
                "silence",
                {"minutes_ago": 35, "last_user_text": "X", "action_hits": "Y"},
            )
            assert r["source"] == "fallback"
            assert r["starter"] == ""
            assert "503" in r["context_hint"]
