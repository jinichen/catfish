"""BL-FIX42 历史截图折叠 (5/11 鸿波 "一截图就卡住").

# 为啥

多张 catfish_browser_screenshot 截图累积 → 每张 base64 ~100-500KB → prompt
实际 5-10MB → 上游 122b 推理首字 18-28s, 总 latency 60-108s → Companion
macOS / Tauri 底层 fetch idle 默认 60-120s 超时 abort → "Fetch is aborted".

gateway log 实测:
  tool_with_image_marker=4 → 重组 4 条 tool 含图 → user multipart
  latency_ms=108725 status=ok ttft_ms=28374
  ↓
  客户端早已 abort, gateway 还在算

prompt_tokens 看似只 69K (token 估算不准, 图像 token 算少了), 真实请求体远超.

# 修法

multimodal_tool_unwrap 之后, fold 老的含图 user multipart:
  - 最近 1 张图: 完整保留 (LLM 当前轮必须看)
  - 之前的图: image_url part 替成纯文本 "[历史截图已折叠]"

跟 BL-Q3-ARCHIVE 的 tool message archive 配套, 一个砍 tool 文本一个砍 user
图片, 同思路.

# 不存图

历史截图意义不大 (页面已变, 看了也没用). 如果 LLM 真要重看, 再调
catfish_browser_screenshot 重截就好. 比 archive 存 base64 还复杂.

# 灰度

env CATFISH_HISTORY_IMAGE_FOLDING_ENABLED 默认 true.
env CATFISH_HISTORY_IMAGE_FOLDING_KEEP 默认 1 (保留最近几张).
"""
from __future__ import annotations

import logging
import os
from copy import deepcopy
from typing import Any

logger = logging.getLogger("catfish.gateway.tool_archive.image_folder")


def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key, "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def is_folding_enabled() -> bool:
    return _env_bool("CATFISH_HISTORY_IMAGE_FOLDING_ENABLED", True)


def keep_latest_count() -> int:
    try:
        return max(0, int(os.environ.get("CATFISH_HISTORY_IMAGE_FOLDING_KEEP", "1")))
    except ValueError:
        return 1


def _is_image_part(part: Any) -> bool:
    """OpenAI multimodal: {'type': 'image_url', 'image_url': {'url': '...'}}"""
    if not isinstance(part, dict):
        return False
    if part.get("type") == "image_url":
        return True
    # 兼容旧格式 (Anthropic-style image part)
    if part.get("type") == "image":
        return True
    return False


def _has_image_in_content(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    return any(_is_image_part(p) for p in content)


def _fold_message_images(message: dict, idx_for_log: int = -1) -> tuple[dict, int]:
    """对单条 user message, 把所有 image_url part 替成"已折叠"文本占位.

    返 (新 message, 折叠图数).
    """
    if not isinstance(message, dict):
        return message, 0
    content = message.get("content")
    if not isinstance(content, list):
        return message, 0

    new_content: list = []
    folded_count = 0
    folded_text_parts: list[str] = []
    for p in content:
        if _is_image_part(p):
            folded_count += 1
        else:
            new_content.append(p)

    if folded_count == 0:
        return message, 0

    # 一个汇总性 text part 替代所有图. 也可以一张一张占位, 这样省 token.
    folded_text_parts.append(
        f"[历史截图已折叠 — 此处原有 {folded_count} 张图片, 已为节省 context 移除. "
        f"如需重看请调 catfish_browser_screenshot 再截.]"
    )

    # 把折叠占位 append 到原来的 text part 之后
    # 如果原来一个 text part 都没, 就单独一条
    has_text = any(
        isinstance(p, dict) and p.get("type") == "text" for p in new_content
    )
    if has_text:
        new_content.append({"type": "text", "text": "\n".join(folded_text_parts)})
    else:
        new_content = [{"type": "text", "text": "\n".join(folded_text_parts)}]

    out = dict(message)
    out["content"] = new_content
    return out, folded_count


def fold_history_images(
    messages: list[dict[str, Any]],
    *,
    keep_latest: int | None = None,
) -> list[dict[str, Any]]:
    """对 messages 里所有 user multipart 含图, 折叠老的, 保留最近 N 张图.

    返新 list (deepcopy), 不改原 list.
    """
    if not messages:
        return messages

    keep = keep_latest if keep_latest is not None else keep_latest_count()

    # 1. 找所有 user 含图 message 的 index
    image_user_indices: list[int] = []
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            continue
        if m.get("role") != "user":
            continue
        if _has_image_in_content(m.get("content")):
            image_user_indices.append(i)

    if len(image_user_indices) <= keep:
        # 不到阈值不折
        return messages

    # 2. 保留最后 N 个, 其余折叠
    to_fold_indices = image_user_indices[:-keep] if keep > 0 else image_user_indices

    out = deepcopy(messages)
    total_folded = 0
    for idx in to_fold_indices:
        new_msg, n = _fold_message_images(out[idx], idx_for_log=idx)
        if n > 0:
            out[idx] = new_msg
            total_folded += n

    if total_folded > 0:
        logger.info(
            "BL-FIX42 fold_history_images: 折 %d 张历史截图 (来自 %d 条 user msg, "
            "保留最近 %d 张图所在 user msg). 防 prompt 过大致客户端 timeout abort.",
            total_folded, len(to_fold_indices), keep,
        )

    return out


__all__ = [
    "is_folding_enabled",
    "keep_latest_count",
    "fold_history_images",
]
