"""Session facts inject — 跟 tool-bridge 的 catfish_remember tool 配套.

# 为啥需要 (踩过坑 2026-04-28 鸿波 demo)
========================================
qwen_v3_5_122b_a10b 长对话 attention 飘, 员工 5 分钟前明确说的硬事实
(EIS=http, 密码 ref=keychain://x) 5 轮之后忘. SOUL.md "复述模式" 是软纪律
(靠模型自觉 quote), 不一定每次都执行.

工程级兜底: 模型听到员工硬事实时调 `catfish_remember(key, value)`
(tool-bridge 写 ~/.catfish/session_facts.json), gateway 在每次 chat 请求
**自动**把这个文件内容拼到 system prompt 末尾 — 永远在最近 token, 不依赖
attention.

# 跟 inject_identity 的关系
=========================
inject_identity_if_needed: 注入 SOUL.md / USER.md / MEMORY.md (跨 session 内容)
inject_session_facts:      注入 ~/.catfish/session_facts.json (当前 session 内容)

执行顺序:
    inject_identity → inject_session_facts → 其他 (sanitize_tools / multimodal_guard)

session_facts inject 在最后, 这样在 system prompt 里离 messages 最近, recency bias 最强.

# 文件不存在 / 损坏怎么办
=========================
gateway 永不抛 — 文件不存在就 inject 空字符串 (不动 messages).
文件损坏 (非 JSON / 非 dict) 也忽略, log warn 让 ops 看到.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.gateway.session_facts")


def _facts_path() -> Path:
    """~/.catfish/session_facts.json. 跟 tool-bridge SESSION_FACTS_PATH 同步."""
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or "."
    return Path(home) / ".catfish" / "session_facts.json"


def read_session_facts() -> dict[str, str]:
    """读 session_facts. 文件不存在 / 损坏 → 空 dict."""
    path = _facts_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning(
                "session_facts 文件不是 dict (%s) — 忽略. path=%s",
                type(data).__name__, path,
            )
            return {}
        # 只保留 str -> str
        return {
            str(k): str(v)
            for k, v in data.items()
            if isinstance(k, str) and isinstance(v, str)
        }
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.warning("读 session_facts 失败 (%s) — 忽略. path=%s", e, path)
        return {}


def render_facts_block(facts: dict[str, str]) -> str:
    """把 facts dict 渲染成 system prompt 末尾的文本块."""
    if not facts:
        return ""
    lines = [
        "## 当前 session 已确认的硬事实 (员工明确告诉过, gateway 自动注入)",
        "",
        "这些是员工在本 session 内明确告诉你的事实, 你**必须遵守**, 不要再问 / 不要忘 / 不要瞎猜:",
        "",
    ]
    for key, value in sorted(facts.items()):
        # 防 markdown 注入: value 里有反引号 / 换行可能破坏 prompt 结构, escape 一下
        safe_value = value.replace("\n", " ").replace("`", "'")
        lines.append(f"- **{key}**: {safe_value}")
    lines.append("")
    lines.append(
        "员工要更新这些事实 → 调 catfish_remember(key, value) 重存. "
        "session 结束员工自己 rm ~/.catfish/session_facts.json 清空."
    )
    return "\n".join(lines)


def inject_session_facts(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 session_facts 拼到最后一条 system message 末尾.

    设计:
      - 没 system message 时: 不加 (跟 inject_identity_if_needed 行为一致, 防止
        客户端不要求 system 时硬塞)
      - 有 system message: 把 facts block 追加到**最后一条** system 末尾
        (而不是开头, 因为 SOUL.md 在前, facts 是当前 session 的"补丁")
      - facts 为空: 直接返回原 messages, 不动

    Args:
        messages: OpenAI-style messages array (会被复制, 不 in-place 改原)

    Returns:
        新 messages 数组. 没 facts 或没 system 就直接返回原数组 (引用相同).
    """
    facts = read_session_facts()
    if not facts:
        return messages

    block = render_facts_block(facts)
    if not block:
        return messages

    # 找最后一条 system message
    new_messages = list(messages)
    last_system_idx = -1
    for i, msg in enumerate(new_messages):
        if isinstance(msg, dict) and msg.get("role") == "system":
            last_system_idx = i

    if last_system_idx < 0:
        # 没 system, 不强加. inject_identity_if_needed 应该已经加过了.
        # 这里跳过保持行为一致.
        return messages

    # 在最后一条 system 末尾追加
    sys_msg = dict(new_messages[last_system_idx])
    content = sys_msg.get("content", "")
    if isinstance(content, str):
        sys_msg["content"] = content.rstrip() + "\n\n" + block
    elif isinstance(content, list):
        # multimodal system (少见, 但稳妥处理): append 一个 text part
        new_content = list(content)
        new_content.append({"type": "text", "text": block})
        sys_msg["content"] = new_content
    else:
        # 不认识的 content 形态, 不动
        return messages

    new_messages[last_system_idx] = sys_msg
    return new_messages
