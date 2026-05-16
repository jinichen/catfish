"""SessionHistoryProvider — 包 inject_session_history 成 MemoryProvider.

priority=50: session 元信息位置靠中, 在 skills_catalog (40) 之后, journal (60) 之前.
模型先看到"过去 7 天有这些 session" 再看 journal 具体内容.

复用 inject_session_history 模块:
  - _extract_user_query() → 抽当前 user message
  - get_relevant_sessions() → 优先 LIKE 召回
  - get_recent_sessions() → fallback 时间窗口
  - format_block() → 渲染 markdown

BL-MEMORY-FTS5-RECALL (5/16) 已经把召回从"灌全文"改成"按相关性 + jieba 中文分词".
本 Provider **只换接口**, 召回逻辑不动.
"""

from __future__ import annotations

import logging

from .. import InjectContext

logger = logging.getLogger("catfish.gateway.memory.providers.session_history")


class SessionHistoryProvider:
    """注入按相关性 / 时间召回的 session 元信息列表."""

    name = "session_history"
    priority = 50
    budget_bytes = 3000  # ~750 token, 5-10 个 session 列表够

    def prefetch(self, ctx: InjectContext) -> str | None:
        # lazy import 防循环
        from ...inject_session_history import (  # noqa: PLC0415
            _extract_user_query,
            format_block,
            get_recent_sessions,
            get_relevant_sessions,
        )

        # 1. 抽 user query (Registry 已经在 ctx.last_user_message 算好, 但 Provider
        # 保持自己抽能力 — Registry ctx 可能没 messages 时 fallback)
        user_query = ctx.last_user_message or _extract_user_query(ctx.messages)

        # 2. 优先 LIKE/FTS5 相关性召回
        sessions: list = []
        relevant_mode = False
        if user_query:
            try:
                sessions = get_relevant_sessions(user_query)
            except Exception as e:  # noqa: BLE001
                logger.warning("get_relevant_sessions 失败: %s", e)
                sessions = []
            if sessions:
                relevant_mode = True

        # 3. fallback 时间窗口
        if not sessions:
            try:
                sessions = get_recent_sessions()
            except Exception as e:  # noqa: BLE001
                logger.warning("get_recent_sessions 失败: %s", e)
                return None

        if not sessions:
            return None

        try:
            block = format_block(sessions, relevant_mode=relevant_mode)
        except Exception as e:  # noqa: BLE001
            logger.warning("format_block 失败: %s", e)
            return None
        if not block or not block.strip():
            return None
        return block
