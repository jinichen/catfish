"""Rate limit preflight — 6/7 BL-GROQ-TPM-PREFLIGHT.

# 真问题

鸿波 6/7 22:43 撞 Groq Free Plan TPM=8K, 单次 95K 请求 → 上游 413 +
rate_limit_exceeded. Catfish 之前没 preflight, 让员工看到 LiteLLM stack
trace + 上游英文错误, UX 差.

# 修法

在 chat completion litellm.acompletion() 之前 check 估算 prompt_tokens > model
rate_limits.tpm × 0.9 (留 10% 给 output). 超就 raise HTTPException 413 + 中文
友好错误 + 替代 model 建议. 跟 LiteLLM 永久错不重试一致 (不是 transient).

# 跟 fallback.py 的关系

fallback.py 管**上游业务错 retry 链**, 这个模块管**preflight 拦截**. 早一步
拦下来省 1-2 次上游往返 + log spam + 员工等待时间.

# 跟 manifesto 公理 4 关系

中央服务**主动告诉员工"请求会超 TPM"**, 不让员工被 stack trace 砸. 是 catfish
作为客户端代理的合理服务范围 (员工知情, 自己决定怎么 reduce context).
"""
from __future__ import annotations

import logging

from fastapi import HTTPException

from .config import Config, ModelConfig

logger = logging.getLogger("catfish.gateway.rate_limit_preflight")

# TPM 利用率上限 — 留 10% 给 LLM output. 实际超 90% 即拒绝, 防 borderline 撞 413.
# 鸿波 22:43 实盘 95K vs 8K, 即使留 buffer 也救不回 (12 倍 overshoot), 但
# 边缘 case (8.5K vs 8K) 这个 buffer 防误拒.
_TPM_UTILIZATION_THRESHOLD = 0.9


def preflight_check_rate_limits(
    model: ModelConfig,
    prompt_estimate: int,
    config: Config | None = None,
) -> None:
    """chat completion 前 check 是否会撞上游 rate limit.

    超 model.rate_limits.tpm × 0.9 → 直接 raise 413 + 友好中文错误.
    没配 rate_limits → 静默通过 (内网 / 私有部署常不限速).

    Args:
        model: 选中的 model config
        prompt_estimate: estimate_prompt_tokens(messages, ...) 返回值
        config: 完整 catalog config, 用来找替代 model 建议 (可空)

    Raises:
        HTTPException(413): 超限. detail 含友好中文 + 替代 model 列表
    """
    rl = model.rate_limits
    if rl is None or rl.tpm is None:
        return  # 没配限速 → 通过 (大部分私有模型 / 未配 Groq Free)

    cap = int(rl.tpm * _TPM_UTILIZATION_THRESHOLD)
    if prompt_estimate <= cap:
        return

    # 超限 — 找替代 model 建议
    alternatives = _suggest_alternatives(model, prompt_estimate, config) if config else []

    tier_hint = ""
    if rl.tier == "groq-free":
        tier_hint = " · 升级 Groq Dev Tier 可解锁 60K TPM (https://console.groq.com/settings/billing)"
    elif rl.tier and "free" in rl.tier:
        tier_hint = f" · 升级 {rl.tier} 的付费 tier 可解锁更高限额"

    alt_hint = ""
    if alternatives:
        alt_names = " / ".join(a.name for a in alternatives[:3])
        alt_hint = f" · 推荐切到: {alt_names}"

    msg = (
        f"请求约 {prompt_estimate:,} tokens, 超过 {model.display_name} 的 TPM 限额 "
        f"{rl.tpm:,} (tier={rl.tier or '未知'}). 请: "
        f"1) 减少对话历史 / 删大附件; "
        f"2) 换长 context 模型{alt_hint}"
        f"{tier_hint}"
    )
    logger.info(
        "BL-GROQ-TPM-PREFLIGHT: 拦截 model=%s prompt_est=%d tpm=%d tier=%s",
        model.name, prompt_estimate, rl.tpm, rl.tier,
    )
    raise HTTPException(status_code=413, detail=msg)


def _suggest_alternatives(
    current: ModelConfig,
    prompt_estimate: int,
    config: Config,
) -> list[ModelConfig]:
    """找能容下当前 prompt 的同 mode 替代 model.

    选择规则:
      - 同 mode (chat / embedding)
      - 不是 current 本身
      - rate_limits 没配 (= 不限速) 或 tpm * 0.9 >= prompt_estimate
      - 优先 private tier (本机模型快 + 没限速)
      - 排除 manual-only (e.g. recommended_for=['client_demo_optional'])
    """
    out: list[ModelConfig] = []
    for m in config.models:
        if m.name == current.name:
            continue
        if m.mode != current.mode:
            continue
        # 跳过 manual-only / demo only
        if "client_demo_optional" in (m.recommended_for or []):
            continue
        rl = m.rate_limits
        if rl and rl.tpm and int(rl.tpm * _TPM_UTILIZATION_THRESHOLD) < prompt_estimate:
            continue
        out.append(m)
    # private 优先 (鸿波偏好)
    out.sort(key=lambda m: (0 if m.tier == "private" else 1, m.name))
    return out


__all__ = ["preflight_check_rate_limits"]
