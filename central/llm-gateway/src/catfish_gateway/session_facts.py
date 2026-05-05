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

# Schema v2 (BL-MM2 五一 sprint 5/5 晚)
=====================================
catfish_remember 改成版本化 (不 silent overwrite). 磁盘格式:
    {
      "key1": [
        {"value": "v1", "ts": 1714867200.0, "prev_value": null},
        {"value": "v2", "ts": 1714867260.0, "prev_value": "v1"}
      ],
      ...
    }
list 末尾是 current. 这里读出来后 inject 时:
  - 单 revision → 普通 "- key: value"
  - 多 revision → "- key: <current> (已更新 N 次, 上次值: <prev>)"
让模型显式看到旧值, 配合 SOUL BL-MM1 复述纪律.

向后兼容旧 schema {"key": "string_value"} — 当作单 revision 处理.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.gateway.session_facts")

# revision = {"value": str, "ts": float, "prev_value": str | None}
# FactsMap = dict[str, list[revision]]


def _facts_path() -> Path:
    """~/.catfish/session_facts.json. 跟 tool-bridge SESSION_FACTS_PATH 同步."""
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or "."
    return Path(home) / ".catfish" / "session_facts.json"


def _normalize_revision(r: Any) -> dict[str, Any] | None:
    """把磁盘上一条 revision 规整成 {value, ts, prev_value}. 不合法返 None."""
    if not isinstance(r, dict):
        return None
    val = r.get("value")
    if not isinstance(val, str):
        return None
    ts = r.get("ts")
    if not isinstance(ts, (int, float)):
        ts = 0.0
    prev = r.get("prev_value")
    if prev is not None and not isinstance(prev, str):
        prev = None
    return {"value": val, "ts": float(ts), "prev_value": prev}


def read_session_facts() -> dict[str, list[dict[str, Any]]]:
    """读 session_facts, 返回 v2 schema (key → revision list).

    向后兼容旧 schema (string value): 包成单 revision list.
    文件不存在 / 损坏 → 空 dict.
    """
    path = _facts_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.warning("读 session_facts 失败 (%s) — 忽略. path=%s", e, path)
        return {}
    if not isinstance(data, dict):
        logger.warning(
            "session_facts 文件不是 dict (%s) — 忽略. path=%s",
            type(data).__name__, path,
        )
        return {}

    out: dict[str, list[dict[str, Any]]] = {}
    for k, v in data.items():
        if not isinstance(k, str):
            continue
        if isinstance(v, str):
            # 旧 schema: 单 string. 包成单 revision (ts=0 表示未知).
            out[k] = [{"value": v, "ts": 0.0, "prev_value": None}]
        elif isinstance(v, list):
            revs = []
            for r in v:
                norm = _normalize_revision(r)
                if norm is not None:
                    revs.append(norm)
            if revs:
                out[k] = revs
        # 其他类型跳过
    return out


def _current(revisions: list[dict[str, Any]]) -> str:
    """从 revision list 取当前值. 空 list → 空串."""
    if not revisions:
        return ""
    val = revisions[-1].get("value", "")
    return val if isinstance(val, str) else ""


def _safe_inline(s: str) -> str:
    """防 markdown 注入: 反引号 + 换行可能破坏 prompt 结构."""
    return s.replace("\n", " ").replace("`", "'")


def render_facts_block(facts: dict[str, list[dict[str, Any]]]) -> str:
    """把 facts (revision list) 渲染成 system prompt 末尾的文本块.

    多 revision 时显示 "上次值: X", 让模型按 SOUL BL-MM1 quote 旧值.
    """
    if not facts:
        return ""
    lines = [
        "## 当前 session 已确认的硬事实 (员工明确告诉过, gateway 自动注入)",
        "",
        "这些是员工在本 session 内明确告诉你的事实, 你**必须遵守**, 不要再问 / 不要忘 / 不要瞎猜:",
        "",
    ]
    for key, revisions in sorted(facts.items()):
        if not revisions:
            continue
        current_val = _safe_inline(_current(revisions))
        rev_count = len(revisions)
        if rev_count <= 1:
            lines.append(f"- **{key}**: {current_val}")
        else:
            # 多 revision: 显式给模型看上次值, 配合 BL-MM1 复述纪律.
            prev = revisions[-1].get("prev_value")
            if isinstance(prev, str) and prev:
                prev_safe = _safe_inline(prev)
                lines.append(
                    f"- **{key}**: {current_val} "
                    f"_(已更新 {rev_count} 次, 上次值: `{prev_safe}` — "
                    f"按 BL-MM1 纪律, 回员工时主动 quote 旧值)_"
                )
            else:
                lines.append(
                    f"- **{key}**: {current_val} _(已更新 {rev_count} 次)_"
                )
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
