"""BL-FALLBACK-500 (5/14 凌晨) — yaml 级回归测.

跑法: cd central/llm-gateway && PYTHONPATH=src python -m pytest tests/test_fallback_500_policy.py -q

# 背景

5/14 凌晨鸿波撞 deepseek-v4-flash 500 没自动切, 排查发现 fallback chain 的
on_errors trigger 列表历史漏 500. 修法: 公网链 (qwen-flash / deepseek-flash /
gemini-pro / gemini-flash) 加 500 trigger; 内网链 (private-main / private-vision)
**不加** 500 — 防内网内容因 500 走公网, 违 SOUL_FFCS 国央企保密原则.

# 这个测试守护啥

下次有人改 models.yaml (e.g. "新加 fallback / 调整 trigger") 时, 如果不小心:
  - 公网链漏了 500 → fallback 链对 500 不工作, 用户撞 500 没自动切
  - 内网链加了 500 → 内网 prompt 因 500 → 公网, 客户 IT 看到日志炸

任何一边不对都让 CI 红, 强制改的人重新评估再 ship.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


PUBLIC_MODELS_REQUIRING_500: set[str] = {
    "catfish-public-qwen-flash",
    "catfish-public-deepseek-flash",
    "catfish-public-gemini-pro",
    "catfish-public-gemini-flash",
}

PRIVATE_MODELS_REJECTING_500: set[str] = {
    "catfish-private-main",
    "catfish-private-vision",
}


@pytest.fixture
def models_yaml() -> dict:
    """读真 config/models.yaml (不是 .example), 这是生产配置."""
    p = (
        Path(__file__).resolve().parent.parent
        / "config"
        / "models.yaml"
    )
    assert p.exists(), f"models.yaml 不存在: {p}"
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _model_by_name(cfg: dict, name: str) -> dict | None:
    for m in cfg.get("models", []) or []:
        if m.get("name") == name:
            return m
    return None


# ── 公网链必须含 500 ─────────────────────────────────────


@pytest.mark.parametrize("model_name", sorted(PUBLIC_MODELS_REQUIRING_500))
def test_public_chain_contains_500(models_yaml, model_name):
    """公网链 trigger 必须含 500 — 公网到公网切没保密问题, 用户体验优先."""
    m = _model_by_name(models_yaml, model_name)
    assert m is not None, f"模型 {model_name} 在 models.yaml 不存在"
    fb = m.get("fallback")
    assert fb is not None, f"{model_name} 没配 fallback"
    on_errors = fb.get("on_errors") or []
    assert 500 in on_errors, (
        f"{model_name} 是公网模型, on_errors 必须含 500. "
        f"BL-FALLBACK-500 (5/14): 鸿波撞 deepseek 500 没切, 修法是 4 条公网链全加 500. "
        f"当前 on_errors = {on_errors}"
    )


# ── 内网链必须不含 500 (防泄密) ──────────────────────────


@pytest.mark.parametrize("model_name", sorted(PRIVATE_MODELS_REJECTING_500))
def test_private_chain_rejects_500(models_yaml, model_name):
    """内网链 trigger 不能含 500 — 内网 500 走公网 = 内网 prompt 泄到公网,
    违反 SOUL_FFCS 国央企保密原则.
    """
    m = _model_by_name(models_yaml, model_name)
    assert m is not None, f"模型 {model_name} 在 models.yaml 不存在"
    fb = m.get("fallback")
    if fb is None:
        # 没 fallback 当然没 500, 没问题
        return
    on_errors = fb.get("on_errors") or []
    assert 500 not in on_errors, (
        f"{model_name} 是内网模型, on_errors 不能含 500. "
        f"内网 500 → 公网链 = 内网 prompt 泄到公网, 违 SOUL_FFCS 保密原则. "
        f"当前 on_errors = {on_errors}. "
        f"如果真要加, 必须先评估 prompt 内容是否能走公网."
    )


# ── 完整性 sanity check ──────────────────────────────────


def test_all_named_models_exist(models_yaml):
    """守护: PUBLIC_MODELS_REQUIRING_500 / PRIVATE_MODELS_REJECTING_500 里
    列的模型必须真在 models.yaml 里 — 防模型改名后这个测变成空跑.
    """
    yaml_names = {
        m.get("name") for m in (models_yaml.get("models") or []) if m.get("name")
    }
    missing_pub = PUBLIC_MODELS_REQUIRING_500 - yaml_names
    missing_priv = PRIVATE_MODELS_REJECTING_500 - yaml_names
    assert not missing_pub, (
        f"PUBLIC_MODELS_REQUIRING_500 里以下模型在 models.yaml 不存在 — "
        f"模型可能改名了, 这个测保护就失效了, 请改本文件: {missing_pub}"
    )
    assert not missing_priv, (
        f"PRIVATE_MODELS_REJECTING_500 里以下模型在 models.yaml 不存在 — "
        f"模型可能改名了, 这个测保护就失效了, 请改本文件: {missing_priv}"
    )
