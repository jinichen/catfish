"""inject_session_history — 跨 session 上下文档 1.

# 为啥需要 (鸿波 2026-04-30 反馈"跨对话信息割裂, 不像真实个体")

LLM 应用最深的痛点 — 每个 session 是孤岛, 模型不知道员工"上次/之前/上周"做了什么.
鸿波 195 条对话里协商出来的"4 段框架 + 表格附件化"决策, 新对话**等于零**.

# 怎么做 (档 1 — 极简版)

读 `~/.hermes/state.db` 取最近 7 天 session 元信息 (id / 时间 / 消息数 / 首条 user message),
注入 system prompt 顶部. 模型每次推理都看到这些**历史索引**, 至少**意识到**有这些 session 存在.

如果员工说"按上次格式写", 模型可以:
  1. 看注入的 session 概览 → 找到"4-29 协商汇报材料 4 段框架"那条
  2. 用 `session_search` 工具拉详细内容
  3. 引用具体决策

档 1 只解决"模型知道有历史"问题. 档 2 (employee_journal) 解决"模型知道历史里讲了啥".

# 跟其他 inject 的关系 (顺序很重要)

  identity → session_facts → stats_guard → skills_catalog → skill_guard →
  **session_history** (本模块, 在 chat 主体之前提供历史背景) → multimodal → tool_capability

放最后 — 它是"跨 session 元信息", 不影响其他 inject.

# Token 限制

最近 10 个 session × ~50 token/session = ~500 token. 可控.
"""

from __future__ import annotations

import logging
import sqlite3
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.gateway.session_history")

#: 看回过去多少天
DAYS_BACK = 7

#: 最多列多少个 session (防 token 爆炸)
MAX_SESSIONS = 10

#: 首条 user message 截断字符数
PREVIEW_CHARS = 100


def get_state_db_path() -> Path | None:
    """hermes session DB. 不存在返 None (hermes 没用过)."""
    p = Path.home() / ".hermes" / "state.db"
    return p if p.exists() else None


def get_recent_sessions() -> list[tuple[str, float, int, str | None, str | None]]:
    """读 state.db 最近 7 天 sessions, 返回 [(id, started_at, msg_count, title, first_user_msg)].

    安全: read-only 连接, 1 秒 timeout, 异常吞掉返空.
    """
    db_path = get_state_db_path()
    if db_path is None:
        return []

    cutoff_ts = (datetime.now() - timedelta(days=DAYS_BACK)).timestamp()

    try:
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True, timeout=1.0
        )
        rows = conn.execute(
            """
            SELECT
                s.id,
                s.started_at,
                s.message_count,
                s.title,
                (SELECT m.content
                 FROM messages m
                 WHERE m.session_id = s.id AND m.role = 'user'
                 ORDER BY m.id LIMIT 1) AS first_user_msg
            FROM sessions s
            WHERE s.started_at >= ?
              AND s.message_count > 1
            ORDER BY s.started_at DESC
            LIMIT ?
            """,
            (cutoff_ts, MAX_SESSIONS),
        ).fetchall()
        conn.close()
        return rows
    except sqlite3.Error as e:
        logger.warning("inject_session_history: 读 state.db 失败: %s", e)
        return []


def format_block(sessions: list[tuple]) -> str:
    """渲染成 system prompt 用的 markdown 块."""
    if not sessions:
        return ""

    lines = [
        "",
        "## 📅 员工最近 7 天 session 历史 (gateway 自动注入)",
        "",
        "员工的每次对话都在下面. **你必须意识到这些历史存在** —— "
        "员工说「上次 / 之前 / 那个 X / 上周 / 昨天 / 我们讨论过的 / 之前定的」时, "
        "**优先在这里找相关 session**, 必要时调 `session_search` 拉详细内容. "
        "**不要假装从零开始** — 员工会觉得你是 100 个素不相识的人轮流帮他.",
        "",
    ]
    for sid, started_at, msg_count, title, first_msg in sessions:
        try:
            date_str = datetime.fromtimestamp(started_at).strftime("%m-%d %H:%M")
        except Exception:
            date_str = "?"
        title_part = f" · 「{title}」" if title else ""
        preview = (first_msg or "(无 user message)").replace("\n", " ").strip()
        if len(preview) > PREVIEW_CHARS:
            preview = preview[:PREVIEW_CHARS] + "…"
        # 只显示 session id 前 8 位 (跟 hermes UI 一致)
        sid_short = sid[:21] if len(sid) > 21 else sid
        lines.append(f"- **{date_str}**{title_part} · `{sid_short}` · {msg_count} 条")
        lines.append(f"    > {preview}")
    lines.append("")
    return "\n".join(lines)


def inject_session_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """在最后一条 system message 末尾追加 session 历史块.

    没 messages / 没 system message / 没 history → 原样返.
    幂等 (block 已存在不重复加).
    """
    if not messages:
        return messages

    sessions = get_recent_sessions()
    block = format_block(sessions)
    if not block:
        return messages

    last_system_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "system":
            last_system_idx = i
            break
    if last_system_idx < 0:
        return messages

    cur = messages[last_system_idx].get("content", "")
    if not isinstance(cur, str):
        return messages
    # 幂等
    if "员工最近 7 天 session 历史" in cur:
        return messages

    out = deepcopy(messages)
    out[last_system_idx]["content"] = (
        out[last_system_idx]["content"].rstrip() + "\n" + block
    )
    if logger.isEnabledFor(logging.INFO):
        logger.info(
            "inject_session_history: 注入 %d 个 session, block %d 字节",
            len(sessions), len(block),
        )
    return out


__all__ = [
    "get_state_db_path",
    "get_recent_sessions",
    "format_block",
    "inject_session_history",
]
