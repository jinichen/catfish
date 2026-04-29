"""tool_capability_guard — model.supports_tool_use=False + skill 意图 → 自动切到能调工具的模型.

# 为啥需要 (踩过坑 2026-04-29 鸿波 demo 反复翻车)

某些模型 (尤其 **qwen 122b 量化版**) 在 OpenAI 兼容 tool_calls 接口上**不调
工具**, 即便 tools 列表传到位 + system prompt 强制提示也无视, **直接幻觉**
"已生成 X 文件" 但磁盘上根本什么都没. 客户 demo 时致命.

# 设计 — 配置驱动, 不硬编码黑名单

`ModelConfig.supports_tool_use: bool` 字段已经在 catalog (models.yaml) 里. 把
qwen 122b 标 `supports_tool_use: false`, gateway 检测员工触发 skill 意图时:
  - 当前模型 supports_tool_use=False → 从 catalog 找 supports_tool_use=True
    的备选, 自动 reroute
  - 当前模型 supports_tool_use=True → 不动

跟 multimodal_guard.py 同套路 (含图自动切 supports_vision=True 的模型).

# 跟 fallback 链的关系

  fallback 链 (FallbackConfig):  上游 5xx / timeout 时切.
                                  即"上游报错"时触发.
  tool_capability_guard:        意图 + 能力不匹配时切.
                                  即"上游正常但能力不够"时触发.

两者互补 — 一个管"模型不可用", 一个管"模型能力不行 (在特定任务上)".

# 备选选择策略

按优先级:
  1. 同 tier (private→private, 留住数据隐私)
  2. 跨 tier 找一个 supports_tool_use=True 的
  3. 都没有 → 不切, 让原始请求过去 (大概率翻车, 但比"找不到"好)
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from .config import Config, ModelConfig
from .skill_guard import has_skill_intent

logger = logging.getLogger("catfish.gateway.tool_capability_guard")


def model_supports_tools(model: ModelConfig | None) -> bool:
    """安全获取 model.supports_tool_use, None / 缺字段都视为 True (默认信任).

    设计: 默认 True 是因为大部分模型都行, 个别不行的在 catalog 显式标 false.
    """
    if model is None:
        return True
    return getattr(model, "supports_tool_use", True)


def pick_tool_capable_alternative(
    config: Config, current_model: ModelConfig
) -> ModelConfig | None:
    """从 catalog 找一个 supports_tool_use=True 的备选, 排除当前模型.

    优先级: 同 tier > 跨 tier. 同 tier 内按 catalog 顺序找第一个.
    """
    candidates = [
        m for m in config.models
        if m.name != current_model.name
        and m.mode == "chat"
        and model_supports_tools(m)
    ]
    if not candidates:
        return None

    # 同 tier 优先
    same_tier = [m for m in candidates if m.tier == current_model.tier]
    if same_tier:
        return same_tier[0]
    return candidates[0]


def route_to_tool_capable_if_needed(
    body: dict[str, Any],
    config: Config,
    current_model: ModelConfig,
) -> tuple[ModelConfig | None, str | None]:
    """如果 user message 触发 skill 意图 + current_model 不支持 tool_use → 自动 reroute.

    Args:
        body: chat completion request body (会被 in-place 改 body["model"])
        config: gateway 配置 (读 catalog)
        current_model: 当前要调的 model

    Returns:
        (new_model, hint) 或 (None, None) 如果不需要 reroute.

    Side-effect:
        如果 reroute, 修改 body["model"] = new_model.name.
    """
    # 当前模型支持 tool calling → 不动
    if model_supports_tools(current_model):
        return None, None

    messages = body.get("messages")
    if not _has_tool_intent(messages):
        # 没触发 skill 意图, 不切 (允许员工纯聊天, 即便选了 tool 不行的模型)
        return None, None

    alt = pick_tool_capable_alternative(config, current_model)
    if alt is None:
        logger.warning(
            "model=%s supports_tool_use=False, 用户触发 skill 意图, 但 catalog 里"
            "找不到 supports_tool_use=True 的备选 → 原样发 (大概率翻车)",
            current_model.name,
        )
        return None, None

    logger.info(
        "tool_capability_guard: model=%s supports_tool_use=False, "
        "用户触发 skill 意图 → 自动切到 %s (tier=%s)",
        current_model.name, alt.name, alt.tier,
    )
    body["model"] = alt.name
    hint = (
        f"⚠ 你选的 {current_model.name} 在 catalog 里标记 supports_tool_use=False "
        f"(实测会幻觉'已生成'但实际不调工具). 检测到你要生成文档, "
        f"自动切到 {alt.name} ({alt.display_name}). "
        f"想稳, 直接选 supports_tool_use=true 的模型."
    )
    return alt, hint


def _has_tool_intent(messages: Iterable[dict] | None) -> bool:
    """触发任何 catfish skill 意图? 复用 skill_guard 的检测."""
    if not messages:
        return False
    return has_skill_intent(list(messages))


__all__ = [
    "model_supports_tools",
    "pick_tool_capable_alternative",
    "route_to_tool_capable_if_needed",
]
