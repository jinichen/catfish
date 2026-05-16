"""含图请求自动 route 到 vision 模型的 guard.

# 为啥需要
==========
模型 (例: qwen_v3_5_122b_a10b / catfish-private-main) 调 catfish_screenshot 后,
把图片塞回 messages 给自己看. 但这些**不是 vision 模型**, 上游收到 multimodal
content list 直接 protobuf 解析炸:

  catfish-private-main (Qwen3 122B 主力, 非 vision)
    ← messages = [{role: user, content: [{type: text}, {type: image_url}]}]
    ← 上游 Go protobuf parse → "syntax error (line 1:1): invalid value Invalid"
    ← BadRequestError 400

员工就只看到一句"上游报错", 不知道是谁的错, demo 卡住.

# 修法
======
chat_completions 入口前检测:
  - messages 含 image_url / image_data / 多模态 content list ?
  - 当前 model 是 supports_vision=False ?

两个都是 → **自动 reroute**: 在同 tier (private/public) 找第一个 supports_vision=True
的模型, 改 body.model + 返回新 model. response metadata 加 hint, 让员工知道.

# 设计选择
==========
1. 不强行 fail, 不 raise 异常 — 自动 reroute 体验好得多, 员工无感
2. 同 tier 优先 (private→private 找, 找不到再 public). 避免私有数据飞外网
3. 找不到 vision 模型 → 不 reroute, 让原始请求过去, 让上游自己报错 (不会比现在差)
4. 在 sanitize_tools 之前调用 — sanitize_tools 可能修改 tools, 不影响 messages
5. 不动 messages 内容 — 图片就是要给 vision 看的, 不能剥
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from .config import Config, ModelConfig

logger = logging.getLogger("catfish.gateway.multimodal_guard")


def has_multimodal_content(messages: Iterable[dict] | None) -> bool:
    """检测 OpenAI-style messages 数组是否含图 (multimodal content).

    OpenAI / DashScope / Gemini 都用同一种格式:
        {"role": "user", "content": [
            {"type": "text", "text": "..."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
        ]}

    Args:
        messages: chat completion messages array

    Returns:
        True 如果**任意 1 条** message 的 content 是 list 且**含至少 1 个**
        type=image_url / image / image_data / input_image 的 part. 否则 False.
        (不依赖具体 type 名 — 不同 provider 名字不同)
    """
    if not messages:
        return False
    image_types = {"image_url", "image", "image_data", "input_image"}
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in image_types:
                return True
    return False


def pick_vision_alternative(
    config: Config, current_model: ModelConfig
) -> ModelConfig | None:
    """在 catalog 里找一个支持 vision 的模型替代 current_model.

    顺序:
      1. 同 tier (private/public) 第一个 supports_vision=True 的
      2. 不同 tier 的 (兜底, 可能私有数据飞外网)
      3. 找不到 → None (上层 fallback 到原始请求 + 让上游自己抛错)

    跳过 current_model 自己 (虽然它都没 vision, 不会撞, 但保守)
    跳过 mode != "chat" 的 (embedding 模型不能 chat)
    """
    same_tier_candidates = []
    other_tier_candidates = []
    for m in config.models:
        if m.name == current_model.name:
            continue
        if m.mode != "chat":
            continue
        if not m.supports_vision:
            continue
        if m.tier == current_model.tier:
            same_tier_candidates.append(m)
        else:
            other_tier_candidates.append(m)
    # 优先同 tier (隐私 / 数据主权)
    if same_tier_candidates:
        return same_tier_candidates[0]
    if other_tier_candidates:
        return other_tier_candidates[0]
    return None


def route_to_vision_if_needed(
    body: dict[str, Any],
    config: Config,
    current_model: ModelConfig,
) -> tuple[ModelConfig | None, str | None]:
    """如果 body.messages 含图 + current_model 不支持 vision → 自动 reroute.

    Args:
        body: chat completion request body (会被 in-place 改 body["model"])
        config: gateway 配置 (读 catalog)
        current_model: 当前要调的 model (从 _resolve_model 出来的)

    Returns:
        (new_model, hint) 或 (None, None) 如果不需要 reroute / 找不到替代.

        - new_model: 新选的 vision 模型 (config.ModelConfig). 调用方应该用这个
          替换 model 变量 + 把 model_name 改掉.
        - hint: 给员工看的字符串, 解释为啥换了模型. 可以塞 response metadata
          或者 audit log.

    Side-effect:
        如果 reroute, 会修改 body["model"] = new_model.name (in-place).
        因为下游 litellm 会再读 body["model"], 不改的话还是用旧名.
    """
    messages = body.get("messages")
    if not has_multimodal_content(messages):
        return None, None

    if current_model.supports_vision:
        # 模型自己就支持 vision, 不需要管
        return None, None

    alt = pick_vision_alternative(config, current_model)
    if alt is None:
        # 找不到替代, 让原始请求过去 (上游会自己 400 — 跟当前行为一样, 不变差)
        logger.warning(
            "请求含图但 model=%s 不支持 vision, 也找不到替代模型 → 原样发上游 (会 400)",
            current_model.name,
        )
        return None, None

    logger.info(
        "请求含图, model=%s 不支持 vision → 自动 reroute 到 %s (tier=%s)",
        current_model.name, alt.name, alt.tier,
    )
    body["model"] = alt.name
    hint = (
        f"⚠ 你请求里含图片, 但 {current_model.name} 不是 vision 模型 "
        f"(会撞上游 400). 自动切到 {alt.name} ({alt.display_name}). "
        f"下次直接选 vision 模型, 体验更好."
    )
    return alt, hint
