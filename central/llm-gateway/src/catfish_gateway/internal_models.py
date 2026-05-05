"""内部 LLM 用例选模型 — BL-F14 (5/4 鸿波拍板).

# 为啥要这个

Gateway 内部多个组件要调 LLM (不是员工主对话):
  - session_summarizer  — 跨 session 总结写 journal
  - proactive (BL-E13)  — 主动闲聊 starter 生成
  - a2a_server          — A2A federation 辅助任务

之前每个都写死 `model="openai/qwen3.5-flash-2026-02-23"` 或类似, 跟 catalog 不同步.
catalog 改了 yaml (e.g. 删某模型 / 改 name) → 内部 LLM 调用全死.

# 设计

按 **use_case tag** 选, 不指名. catalog 里每个模型在 `recommended_for` 加对应 tag:
  - `summarizer` — 适合做总结 (要求: 不死板, 中文 OK, 速度快)
  - `proactive_starter` — 适合主动闲聊 (要求: 自然口语, 不正式)
  - `a2a_aux` — A2A 辅助任务 (要求: 通用 chat)

调用方:
  >>> model = pick_internal_model("summarizer", config)
  >>> if model:
  ...     # 用 model.name 调本机 gateway HTTP loopback (BL-F12 走法)

# 优先级 (从高到低)

1. **env override** `CATFISH_<USE_CASE>_MODEL`: per-deployment 强制指定模型名
   (e.g. summarizer 想专用一个便宜模型, 主 chat 用旗舰). env 指了 catalog 没的 → 降级到 step 2 + warn.
2. **tag 匹配 + private 优先**: catalog 里 recommended_for 含 use_case tag 的, **tier=private** 排前 (跟"数据不出公司"卖点一致), 同 tier 按 yaml 顺序, 取第一个可达.
3. **兜底**: 任何 mode=chat + 可达的模型, 仍 private 优先. 没 tag 也接受.
4. **都没**: 返 None, 调用方该 log warn + 跳过.

# 为啥 private 优先 (5/4 鸿波 explicit)

catfish 核心 positioning 是"数据不出公司". 内部 LLM 用例 (尤其 summarizer 看 journal /
proactive 看 employee 工作上下文) 数据敏感度高, **必须先用内网部署的私有模型**, 内网挂了
才用公网 (qwen-flash / gemini / deepseek). yaml 已经按这个顺序排:
  - 第 1: catfish-private-main (内网 qwen 122b)
  - 第 2: catfish-private-vision (内网 vision)
  - 第 3+: 公网 qwen-flash / deepseek / gemini

我们按 yaml 顺序选, 自然实现 "private 优先".
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config, ModelConfig

logger = logging.getLogger("catfish.gateway.internal_models")

# 已知的 use case 名 (用作文档 + env key 校验, 不强制白名单)
KNOWN_USE_CASES = frozenset({
    "summarizer",         # session_summarizer 跨 session 总结
    "proactive_starter",  # BL-E13 主动闲聊 starter
    "a2a_aux",            # A2A federation 辅助任务
})


def _env_key(use_case: str) -> str:
    """e.g. 'summarizer' → 'CATFISH_SUMMARIZER_MODEL'"""
    return f"CATFISH_{use_case.upper()}_MODEL"


def _is_chat_available(m: "ModelConfig") -> bool:
    """模型是否能做 chat 调用"""
    return m.mode == "chat" and m.upstream.is_available


def _sort_private_first(models: list["ModelConfig"]) -> list["ModelConfig"]:
    """tier=private 排前, 同 tier 保持 yaml 顺序 (Python sort 稳定)"""
    return sorted(models, key=lambda m: 0 if m.tier == "private" else 1)


def pick_internal_models_ordered(
    use_case: str, config: "Config"
) -> list["ModelConfig"]:
    """返**按优先级排序**的候选列表 (private 优先, tag 匹配优先).

    BL-F15 (5/5): summarizer/proactive 收到 429 quota_exceeded 时按这个列表切下一个.
    架构 bug 兜底: gateway quota check 在 with_fallback 之前, 直接抛 429, fallback
    chain 不会接. caller 自己按候选列表 try.

    返回顺序:
      1. tag 匹配 + private (按 yaml 顺序)
      2. tag 匹配 + public (按 yaml 顺序)
      3. 兜底 + private (无 tag 但能 chat 的)
      4. 兜底 + public

    env override 强制选某个: 只返这一个, 不再列其他.
    都不可达: 返空列表.

    跟 pick_internal_model() 关系: 后者是返这个列表的第一个 (或 None).
    """
    # env override 强制
    env_model_name = os.environ.get(_env_key(use_case), "").strip()
    if env_model_name:
        for m in config.models:
            if m.name == env_model_name and _is_chat_available(m):
                return [m]
        # env 指了但不可用 → 走 tag 选 (不返空, 让 caller 仍有候选)

    tagged_private = []
    tagged_public = []
    fallback_private = []
    fallback_public = []
    for m in config.models:
        if not _is_chat_available(m):
            continue
        has_tag = use_case in (m.recommended_for or [])
        if has_tag:
            (tagged_private if m.tier == "private" else tagged_public).append(m)
        else:
            (fallback_private if m.tier == "private" else fallback_public).append(m)
    return tagged_private + tagged_public + fallback_private + fallback_public


def pick_internal_model(use_case: str, config: "Config") -> "ModelConfig | None":
    """按 use_case tag + private 优先选模型.

    Args:
        use_case: 用例名, 例 'summarizer' / 'proactive_starter' / 'a2a_aux'
        config: gateway 主配置 (含 models 列表)

    Returns:
        ModelConfig | None — None 表示 catalog 完全没可用模型, 调用方该 skip.
    """
    if use_case not in KNOWN_USE_CASES:
        # 不在白名单不阻塞, 只 warn (允许将来加新 use case 不同步本文件)
        logger.debug("pick_internal_model: 未注册 use_case=%r (允许)", use_case)

    # ─── Step 1: env override ───
    env_model_name = os.environ.get(_env_key(use_case), "").strip()
    if env_model_name:
        for m in config.models:
            if m.name == env_model_name:
                if _is_chat_available(m):
                    logger.info(
                        "pick_internal_model: use_case=%s 走 env override → %s",
                        use_case, m.name,
                    )
                    return m
                else:
                    logger.warning(
                        "pick_internal_model: env %s=%s 但模型不可用 (mode=%s, available=%s), 降级 tag 选",
                        _env_key(use_case), env_model_name,
                        m.mode, m.upstream.is_available,
                    )
                break  # 跳出, 不再尝试 env, 走 step 2
        else:
            logger.warning(
                "pick_internal_model: env %s=%s 但 catalog 没此模型, 降级 tag 选",
                _env_key(use_case), env_model_name,
            )

    # ─── Step 2: tag 匹配 + private 优先 ───
    tagged = [
        m for m in config.models
        if _is_chat_available(m)
        and use_case in (m.recommended_for or [])
    ]
    if tagged:
        chosen = _sort_private_first(tagged)[0]
        logger.info(
            "pick_internal_model: use_case=%s 按 tag 选 → %s (tier=%s)",
            use_case, chosen.name, chosen.tier,
        )
        return chosen

    # ─── Step 3: 兜底 — 任何 chat + 可达, 仍 private 优先 ───
    fallback_pool = [m for m in config.models if _is_chat_available(m)]
    if fallback_pool:
        chosen = _sort_private_first(fallback_pool)[0]
        logger.warning(
            "pick_internal_model: use_case=%s 没找到 tag 匹配, 兜底 → %s (tier=%s). "
            "建议在 catalog 给一个模型 recommended_for 加 '%s' tag.",
            use_case, chosen.name, chosen.tier, use_case,
        )
        return chosen

    # ─── Step 4: 完全没可用模型 ───
    logger.error(
        "pick_internal_model: use_case=%s — catalog 一个可用 chat 模型都没 (api key 全没配?). "
        "调用方应跳过该 use case.",
        use_case,
    )
    return None
