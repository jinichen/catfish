"""BL-FIX2 (5/8) — 把 role=tool 含 image 的 content 自动重组成 user multipart.

# 解决什么

上游 Qwen 平台 OpenAI 兼容 adapter 的 Go gRPC protobuf 解析**不接受 role=tool
的 multimodal content**. catfish_screenshot 工具返 `data_uri: "data:image/png;base64,..."`,
useChat 把整 dict JSON.stringify 塞进 role=tool 的 content (~500KB string),
上游解析炸 BadRequest 400 (空 reason/message, Go gRPC 风格).

OpenAI 标准:
  - role=user 的 content 可以是 list[{type: text}, {type: image_url}] (multipart)
  - role=tool 的 content 必须是 string 文本

# 修法

在 chat_completions 入口前加一个 transform pass:
  1. 扫 messages 里 role=tool 的 content
  2. 检测 content 是 JSON string 且含 'data_uri' / 'data' / image-like 字段
  3. **拆出 image** + 改写 tool message: content 只留文本元数据 (path / summary)
  4. **紧接着插一条** role=user message: [{text: '附图见下'}, {image_url: {url: data_uri}}]

这样:
  - LLM 行为完全不变 — 它仍"调 catfish_screenshot → 下一轮看 image"
  - 上游 Qwen 收到 image 在 role=user 里 (合规) → protobuf 不撞
  - hop 后 LLM 看的还是 multimodal input, 跟训练时学的一致

# 在哪个 inject 位置

跟 multimodal_guard 同 phase 但**晚一步** — multimodal_guard 是检测 user 直传图
+ 切 vision 模型, multimodal_tool_unwrap 是把 tool 角色含图重组. 顺序:

  identity_inject → session_facts → ... → multimodal_guard (含图切 vision)
  → **multimodal_tool_unwrap (本模块, 重组 tool 含图)**
  → _invoke_chat_completion

# 设计选择

1. **不动原 tool message 的 tool_call_id** — 上游需要它对应 assistant tool_calls
2. **tool message content 改文本概要** (path / summary), 不删整个 message
3. **image 在新插入的 user message 里**, content list 里第 1 个 part 是简短 text 提示
4. **解析失败优雅 fallback** — 不是 JSON / 没 data_uri 字段 → 不动, 让原行为继续
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("catfish.gateway.multimodal_tool_unwrap")

#: 检测 string 含 image 的快速 marker (避免每条消息都 JSON parse)
_IMAGE_MARKERS = (
    "data:image/",         # data URI
    '"data_uri"',          # catfish_screenshot 返字段
    '"image_url"',          # OpenAI 风格嵌套
)


def _looks_like_tool_content_with_image(content: Any) -> bool:
    """快速预筛 — 是 string 且含 image marker."""
    if not isinstance(content, str):
        return False
    if len(content) < 200:
        # data_uri base64 至少几百字节, 太短不可能
        return False
    return any(m in content for m in _IMAGE_MARKERS)


def _extract_image_from_tool_content(content: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """尝试从 tool content (JSON string) 里拆出:
       - cleaned_dict: 保留 path / summary / 元数据, 删 data / data_uri 字段 → 重新 JSON 给 tool message
       - image_payload: {url, alt_text} 给 user multipart 用

    返 (None, None) 表示解析不出来, 调用方应 fallback (不重组).
    """
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return None, None
    if not isinstance(data, dict):
        return None, None

    data_uri = data.get("data_uri")
    raw_b64 = data.get("data")
    fmt = data.get("format", "png")

    image_url: str | None = None
    if isinstance(data_uri, str) and data_uri.startswith("data:"):
        image_url = data_uri
    elif isinstance(raw_b64, str) and len(raw_b64) > 100:
        image_url = f"data:image/{fmt};base64,{raw_b64}"
    else:
        return None, None  # 没找到能用的 image, 不重组

    # 拆 — cleaned 保留路径 + summary 给 LLM 知道发生了啥
    cleaned: dict[str, Any] = {
        k: v for k, v in data.items()
        if k not in ("data", "data_uri")
    }
    cleaned["_image_relocated"] = "image 已移到下一条 user multipart message (上游 protobuf 兼容)"

    image_payload = {
        "url": image_url,
        "alt_text": data.get("summary") or data.get("reason") or "tool screenshot",
    }
    return cleaned, image_payload


def unwrap_tool_images(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """主转换函数.

    扫 messages, 找 role=tool 含 image, 重组成 (cleaned_tool_msg + user_multipart_msg).
    不改原 list, 返新 list. 幂等 — 已重组的下次调用不会再动.
    """
    if not messages:
        return messages

    out: list[dict[str, Any]] = []
    rewrites = 0
    for msg in messages:
        if not isinstance(msg, dict):
            out.append(msg)
            continue
        if msg.get("role") != "tool":
            out.append(msg)
            continue
        content = msg.get("content")
        if not _looks_like_tool_content_with_image(content):
            out.append(msg)
            continue
        cleaned, image_payload = _extract_image_from_tool_content(content)  # type: ignore[arg-type]
        if cleaned is None or image_payload is None:
            out.append(msg)
            continue
        # 改写 tool message: content 改成 cleaned (含 path / summary, 无 image)
        rewritten_tool = dict(msg)
        rewritten_tool["content"] = json.dumps(cleaned, ensure_ascii=False)
        out.append(rewritten_tool)

        # 紧接着插 user multipart message
        user_multipart = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"上一条 tool 调用截图: {image_payload['alt_text']}. "
                        f"这是图片本身 (OpenAI multipart 标准, 因为上游 protobuf "
                        f"不接受 tool 角色含 image), 你看下画面回答员工."
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {"url": image_payload["url"]},
                },
            ],
        }
        out.append(user_multipart)
        rewrites += 1

    if rewrites > 0:
        logger.info(
            "BL-FIX2 multimodal_tool_unwrap: 重组 %d 条 tool 含图 → user multipart",
            rewrites,
        )
    return out
