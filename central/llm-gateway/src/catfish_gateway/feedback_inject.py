"""BL-MM6 feedback inject — 把员工最近的 👎/改 反馈拼到 system prompt 末尾.

# 为啥需要

跟 BL-MM5 主动学习 (SOUL 软纪律) 配合, 这条是**显式 + 工程级**:
  - SOUL BL-MM5: LLM 自觉观察员工偏好, 攒 3 次主动问后落 memory_save
  - 本模块: 员工**显式**点 👎 / 改, 写到 ~/.catfish/feedback.jsonl  # noqa: BOUNDARY (docstring, 真路径取决于 edge 写入方)
    gateway 把最近 N 条 negative feedback inject 到 system prompt
  - 两者互补: 软纪律 + 硬反馈, 让 LLM 越用越懂员工

设计取舍:
  - 只 inject negative (👎 + 改) — 👍 全 inject 浪费 token, 也不教模型啥
  - 只看最近 7 天 — 太老的 feedback 跟当前模型行为脱节
  - 截上限 10 条 — 防 token 爆
  - 内容截断 — comment 800 字, preview 200 字

跟 inject_employee_journal / inject_session_facts 同模式, 透明降级 (文件没就 noop).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.gateway.feedback_inject")

# 只看最近 N 天的 feedback
MAX_AGE_DAYS = int(os.environ.get("CATFISH_FEEDBACK_MAX_AGE_DAYS") or 7)
# 最多 inject N 条
MAX_ITEMS = int(os.environ.get("CATFISH_FEEDBACK_MAX_ITEMS") or 10)


def _feedback_path() -> Path:
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or "."
    return Path(home) / ".catfish" / "feedback.jsonl"


def read_recent_negative() -> list[dict[str, Any]]:
    """读 feedback.jsonl, 返最近 7 天的 negative (👎 / 改) 反馈, 按 ts 倒序."""
    path = _feedback_path()
    if not path.exists():
        return []

    cutoff = time.time() - MAX_AGE_DAYS * 86400
    out: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(ev, dict):
                    continue
                kind = ev.get("kind")
                if kind not in ("thumb_down", "edit"):
                    continue
                ts = ev.get("ts")
                if not isinstance(ts, (int, float)) or ts < cutoff:
                    continue
                out.append(ev)
    except OSError as e:
        logger.warning("读 feedback.jsonl 失败 (%s) - 忽略", e)
        return []

    # ts 倒序
    out.sort(key=lambda e: e.get("ts", 0), reverse=True)
    return out[:MAX_ITEMS]


def render_feedback_block(items: list[dict[str, Any]]) -> str:
    """把 negative feedback 渲染成 system prompt 末尾的文本块.

    格式:
        ## 员工最近给你的反馈 (你**必须**参考改进)
        - 👎 [3 天前] (内容: ...) 评论: 太啰嗦了, 直接给结论
        - ✏️ [昨天] (内容: ...) 评论: 改成不带表情符号
        - 👎 [刚才]: (没写理由)
        ...
    """
    if not items:
        return ""

    lines: list[str] = [
        "## 员工最近给你的反馈 (你**必须**参考改进)",
        "",
        "员工对你之前回复显式打了 👎 或 改. 这些是**真员工 explicit feedback**, "
        "比 SOUL 软纪律权重更高. 你回复时:",
        "1. 主动避免重复犯同样的错 (例: 之前嫌啰嗦 → 这次给结论先)",
        "2. 风格倾向跟改的一致 (例: 之前嫌带表情 → 这次不带)",
        "3. **不要**在回复里 quote 这些 feedback (员工已经看过, 重复显啰嗦)",
        "",
    ]
    now = time.time()
    for ev in items:
        kind = ev.get("kind")
        ts = ev.get("ts")
        comment = ev.get("comment") or ""
        preview = ev.get("preview") or ""
        # 时间标注
        if isinstance(ts, (int, float)) and ts > 0:
            diff = now - ts
            if diff < 3600:
                time_label = "刚才"
            elif diff < 86400:
                time_label = f"{int(diff / 3600)} 小时前"
            else:
                time_label = f"{int(diff / 86400)} 天前"
        else:
            time_label = "时间未知"

        emoji = "👎" if kind == "thumb_down" else "✏️"

        # 防 markdown 注入: 反引号 + 换行截掉
        comment_safe = comment.replace("\n", " ").replace("`", "'")
        preview_safe = preview.replace("\n", " ").replace("`", "'")
        if len(comment_safe) > 300:
            comment_safe = comment_safe[:300] + "…"
        if len(preview_safe) > 100:
            preview_safe = preview_safe[:100] + "…"

        if comment_safe:
            lines.append(f"- {emoji} [{time_label}] (你之前说: {preview_safe}) 员工反馈: {comment_safe}")
        else:
            lines.append(f"- {emoji} [{time_label}] (你之前说: {preview_safe}) (没写理由)")

    return "\n".join(lines)


def inject_feedback(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 feedback block 拼到最后一条 system message 末尾.

    跟 inject_employee_journal / inject_session_facts 同模式:
      - 没 system message 时不强加
      - feedback 为空时直接返回原 messages
      - in-place safe (复制 dict, 不动原)
    """
    items = read_recent_negative()
    if not items:
        return messages

    block = render_feedback_block(items)
    if not block:
        return messages

    new_messages = list(messages)
    last_system_idx = -1
    for i, msg in enumerate(new_messages):
        if isinstance(msg, dict) and msg.get("role") == "system":
            last_system_idx = i

    if last_system_idx < 0:
        return messages

    sys_msg = dict(new_messages[last_system_idx])
    content = sys_msg.get("content", "")
    if isinstance(content, str):
        sys_msg["content"] = content.rstrip() + "\n\n" + block
    elif isinstance(content, list):
        new_content = list(content)
        new_content.append({"type": "text", "text": block})
        sys_msg["content"] = new_content
    else:
        return messages

    new_messages[last_system_idx] = sys_msg
    logger.info("inject_feedback: 注入 %d 条 negative 反馈, block %d 字节",
                len(items), len(block))
    return new_messages
