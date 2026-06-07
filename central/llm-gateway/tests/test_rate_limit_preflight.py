"""Rate limit preflight test — 6/7 BL-GROQ-TPM-PREFLIGHT.

跟 rate_limit_preflight.py 配套. 测核心拦截逻辑 + alternative 建议.

不测真上游, 不测 fastapi 路由 (那是 integration test 范畴).
"""
from __future__ import annotations

import pytest

from catfish_gateway.config import (
    Config,
    ModelConfig,
    RateLimitsConfig,
    UpstreamConfig,
)
from catfish_gateway.rate_limit_preflight import preflight_check_rate_limits
from fastapi import HTTPException


def _make_model(
    name: str,
    *,
    tpm: int | None = None,
    tier: str = "private",
    rl_tier: str | None = None,
    mode: str = "chat",
    recommended_for: list[str] | None = None,
) -> ModelConfig:
    """test 用 ModelConfig 工厂."""
    rl = None
    if tpm is not None:
        rl = RateLimitsConfig(tpm=tpm, tier=rl_tier)
    return ModelConfig(
        name=name,
        tier=tier,
        display_name=name,
        mode=mode,
        upstream=UpstreamConfig(model=f"upstream/{name}"),
        rate_limits=rl,
        recommended_for=recommended_for or [],
    )


def _make_config(*models: ModelConfig) -> Config:
    return Config(models=list(models))


def test_preflight_no_rate_limits_passes():
    """model 没配 rate_limits → 通过 (内网 / 私有部署常态)."""
    m = _make_model("local")
    preflight_check_rate_limits(m, prompt_estimate=100_000)


def test_preflight_no_tpm_passes():
    """rate_limits 配了但 tpm=None → 通过."""
    m = _make_model("x", tpm=None)
    preflight_check_rate_limits(m, prompt_estimate=100_000)


def test_preflight_below_threshold_passes():
    """prompt 小 → 通过. cap = tpm × 0.9 = 7200, 7000 < 7200."""
    m = _make_model("groq", tpm=8000, rl_tier="groq-free")
    preflight_check_rate_limits(m, prompt_estimate=7000)


def test_preflight_at_threshold_passes():
    """边缘: 刚到 cap (= tpm × 0.9) 通过."""
    m = _make_model("groq", tpm=8000, rl_tier="groq-free")
    preflight_check_rate_limits(m, prompt_estimate=7200)


def test_preflight_over_threshold_raises_413():
    """超 cap → 413. 鸿波 22:43 实盘 95K vs 8K (12 倍)."""
    m = _make_model("groq", tpm=8000, rl_tier="groq-free")
    with pytest.raises(HTTPException) as exc_info:
        preflight_check_rate_limits(m, prompt_estimate=95000)
    assert exc_info.value.status_code == 413
    detail = exc_info.value.detail
    assert "95,000" in detail or "95000" in detail
    assert "8,000" in detail or "8000" in detail
    assert "groq-free" in detail


def test_preflight_includes_tier_upgrade_hint():
    """groq-free tier → detail 含 Groq Dev Tier 升级提示."""
    m = _make_model("groq", tpm=8000, rl_tier="groq-free")
    with pytest.raises(HTTPException) as exc_info:
        preflight_check_rate_limits(m, prompt_estimate=95000)
    assert "Groq Dev Tier" in exc_info.value.detail
    assert "60K" in exc_info.value.detail


def test_preflight_suggests_alternative_with_no_rate_limits():
    """有替代 model (private 没配 rate_limits) → detail 提推荐."""
    groq = _make_model("groq-public", tpm=8000, tier="public", rl_tier="groq-free")
    local = _make_model("catfish-private-main", tier="private")  # 无 rate_limits
    config = _make_config(groq, local)

    with pytest.raises(HTTPException) as exc_info:
        preflight_check_rate_limits(groq, prompt_estimate=95000, config=config)
    assert "catfish-private-main" in exc_info.value.detail


def test_preflight_alternatives_skip_manual_only():
    """recommended_for=client_demo_optional 的 model 不该作 alternative 建议."""
    groq = _make_model("groq-public", tpm=8000, rl_tier="groq-free")
    demo = _make_model(
        "demo-only",
        tier="public",
        recommended_for=["client_demo_optional"],
    )
    config = _make_config(groq, demo)

    with pytest.raises(HTTPException) as exc_info:
        preflight_check_rate_limits(groq, prompt_estimate=95000, config=config)
    assert "demo-only" not in exc_info.value.detail


def test_preflight_alternatives_skip_same_mode_mismatch():
    """embedding model 不能替代 chat model (mode 不同)."""
    chat_groq = _make_model("groq-chat", tpm=8000, rl_tier="groq-free", mode="chat")
    embed = _make_model("embed-model", tier="private", mode="embedding")
    config = _make_config(chat_groq, embed)

    with pytest.raises(HTTPException) as exc_info:
        preflight_check_rate_limits(chat_groq, prompt_estimate=95000, config=config)
    assert "embed-model" not in exc_info.value.detail


def test_preflight_alternatives_skip_smaller_tpm():
    """替代 model TPM 不够大也不该推荐 (推过去仍然 413)."""
    groq_120b = _make_model("groq-120b", tpm=8000, tier="public", rl_tier="groq-free")
    groq_20b = _make_model("groq-20b", tpm=6000, tier="public", rl_tier="groq-free")
    config = _make_config(groq_120b, groq_20b)

    with pytest.raises(HTTPException) as exc_info:
        preflight_check_rate_limits(groq_120b, prompt_estimate=95000, config=config)
    assert "groq-20b" not in exc_info.value.detail
