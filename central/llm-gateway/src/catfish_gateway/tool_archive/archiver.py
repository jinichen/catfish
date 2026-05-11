"""BL-Q3-ARCHIVE — 主流程: 检测超阈值 → 写 archive → 替换 prompt content (5/11).

替代 BL-FIX41 硬切. 设计文档 §5.

关键不变量:
  - 消息数量保持 (Hermes ReAct / OpenAI tool-calling 协议要求)
  - tool_call_id 不动 (assistant ↔ tool 配对)
  - content 类型仍是 str (LLM 期望)

write 走同步 (psycopg sync, μs 级 latency), 不要在 chat 主链路引 async 复杂度.
摘要是 worker 后台干的, 写不阻塞 chat.
"""
from __future__ import annotations

import hashlib
import logging
import time
from copy import deepcopy
from typing import Any

from . import db, features, prompts

logger = logging.getLogger("catfish.gateway.tool_archive.archiver")

#: 兼容老引用 — 真实值跟 features.threshold_bytes() 走 (env 可调)
THRESHOLD_BYTES = 4_000


def derive_session_id(
    user_email: str,
    messages: list[dict] | None = None,
    conversation_id: str | None = None,
) -> str:
    """派生 session_id.

    优先级:
      1. conversation_id 显式传 (Companion 后续若加 header 时用)
      2. messages 首条 system + 第一个 user message hash (BL-F12 同款思路, 跨
         同会话不同请求稳定)
      3. user_email + 当天日期 (兜底, 同员工同天聚到一起)
    """
    if conversation_id:
        return f"{user_email}:{conversation_id}"

    if messages:
        # 取首个 user message 的前 200 字 hash, 同会话内不同请求结果稳定
        first_user = next(
            (
                m.get("content", "") for m in messages
                if isinstance(m, dict) and m.get("role") == "user"
                and isinstance(m.get("content"), str)
            ),
            None,
        )
        if first_user:
            h = hashlib.sha256(first_user[:500].encode("utf-8")).hexdigest()[:12]
            return f"{user_email}:{h}"

    # 兜底: 按天
    from datetime import date  # noqa: PLC0415
    return f"{user_email}:{date.today().isoformat()}"


def _compute_ref(content: str, tool_call_id: str | None) -> str:
    """ref = sha256(content + ":" + tool_call_id)[:16].

    16 字 (64 bit) 撞概率 < 1e-9 / 1B archive. 含 tool_call_id 避免不同调用同
    content 撞 ref (e.g. 同一 grep 命令两次跑出同 stdout).
    """
    h = hashlib.sha256()
    h.update(content.encode("utf-8"))
    h.update(b":")
    h.update((tool_call_id or "").encode("utf-8"))
    return h.hexdigest()[:16]


def _count_lines(content: str) -> int:
    return content.count("\n") + (0 if content.endswith("\n") else 1)


def _is_archive_candidate(m: dict) -> bool:
    """role=tool + content 是 str + 没被 archive 过 (避免重复打)."""
    if not isinstance(m, dict):
        return False
    if m.get("role") != "tool":
        return False
    c = m.get("content")
    if not isinstance(c, str):
        return False
    # 已经是 archive 替换文本 (跑两次的话) — 跳过
    if c.startswith("[已归档: archive_ref="):
        return False
    return True


def archive_tool_messages(
    messages: list[dict[str, Any]],
    *,
    user_email: str,
    session_id: str,
    threshold: int | None = None,
) -> list[dict[str, Any]]:
    """对超阈值的 role=tool message 写 archive + 替换 content.

    返回新 list (deepcopy). 不改原 messages.

    threshold None → 从 features.threshold_bytes() 取.
    """
    if not messages:
        return messages

    thr = threshold if threshold is not None else features.threshold_bytes()
    out = deepcopy(messages)
    archived = 0
    saved_bytes = 0

    for i, m in enumerate(out):
        if not _is_archive_candidate(m):
            continue
        content: str = m["content"]
        nbytes = len(content.encode("utf-8"))
        if nbytes < thr:
            continue

        tool_call_id = m.get("tool_call_id") or m.get("id")
        tool_name = m.get("name")
        ref = _compute_ref(content, tool_call_id)

        # 写 archive (同步, 但 PG psycopg.connect 在 mac 本地 < 10ms)
        ok, backend = db.upsert_archive({
            "ref": ref,
            "session_id": session_id,
            "user_email": user_email,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "content": content,
            "content_bytes": nbytes,
            "lines": _count_lines(content),
        })

        if not ok:
            # archive 双写都挂 — 走 fallback (caller 决定降 FIX41 还是原样过)
            logger.error(
                "archive 写挂 ref=%s user=%s tool=%s — 跳过, content 不动",
                ref, user_email, tool_name,
            )
            continue

        # 替换 content 为引用文本. summary 这时还没有 (worker 异步), 显示"生成中".
        # 后续若 LLM 再发同一 message 列表 (re-send history), prompt 会重建,
        # 那时 summary 已经 ready 就显示出来 — 不需要内联等.
        replacement = prompts.build_replacement_text(
            ref=ref,
            content=content,
            tool_name=tool_name,
            content_bytes=nbytes,
            lines=_count_lines(content),
            summary=None,  # worker 后台填
            summary_error=None,
        )
        out[i]["content"] = replacement
        archived += 1
        saved_bytes += nbytes - len(replacement.encode("utf-8"))

    if archived > 0:
        logger.info(
            "BL-Q3-ARCHIVE: 归档 %d 条 tool message, 省 %d 字节 (~%dK tokens). "
            "user=%s session=%s threshold=%d backend=%s",
            archived, saved_bytes, saved_bytes // 4000,
            user_email, session_id[:40], thr,
            backend if archived else "n/a",
        )

    return out


def prepare_tool_messages(
    messages: list[dict[str, Any]],
    *,
    user_email: str,
    session_id: str | None = None,
    conversation_id: str | None = None,
) -> list[dict[str, Any]]:
    """gateway app.py 调的统一入口.

    路径:
      enabled & write 成功 → archive 模式
      enabled & write 挂  → 降 FIX41 硬切 (features.fallback_to_fix41)
      disabled            → FIX41 硬切 (老路径)

    session_id 没传 → 自动按 user_email + first user message hash 派生
    """
    if not features.is_archive_enabled(user_email):
        # 老路径 — FIX41 硬切. import 延后避免循环.
        from ..tool_msg_truncator import truncate_tool_messages  # noqa: PLC0415
        return truncate_tool_messages(messages)

    sid = session_id or derive_session_id(
        user_email, messages=messages, conversation_id=conversation_id,
    )

    try:
        return archive_tool_messages(
            messages, user_email=user_email, session_id=sid,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "archive_tool_messages 异常, 降级 FIX41 硬切: %s", e,
        )
        if features.fallback_to_fix41():
            from ..tool_msg_truncator import truncate_tool_messages  # noqa: PLC0415
            return truncate_tool_messages(messages)
        raise


__all__ = [
    "THRESHOLD_BYTES",
    "archive_tool_messages",
    "prepare_tool_messages",
]
