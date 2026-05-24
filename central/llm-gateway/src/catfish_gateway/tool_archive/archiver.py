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
    """role=tool + content 是 str + 没被 archive 过 (避免重复打).

    BL-ARCHIVE-SKIP-INSTRUCTIONAL (5/15 鸿波 'agent 拿到归档摘要以为完事'):
    catfish_run_skill 返回 instructional skill 指令 (含 `is_instructional: true`
    + preferred_template + output_target) 时**永不归档**. 否则归档摘要把"这是
    指令型 skill, LLM 需要接力"的语义抹掉, 写成"已生成 PPT" 误导 agent 死循环.
    """
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
    # 指令型 skill 返回不归档 — 摘要会丢 preferred_template / output_target 等
    # 关键接力指令字段, agent 没法干活. 用 substring 检测 (返回值是 JSON, 含
    # `"is_instructional": true` 标记).
    if m.get("name") == "catfish_run_skill" and '"is_instructional": true' in c:
        return False
    return True


def archive_tool_messages(
    messages: list[dict[str, Any]],
    *,
    user_email: str,
    session_id: str,
    origin_model: str | None = None,
    threshold: int | None = None,
) -> list[dict[str, Any]]:
    """对超阈值的 role=tool message 写 archive + 替换 content.

    返回新 list (deepcopy). 不改原 messages.

    threshold None → 从 features.threshold_bytes() 取.
    origin_model: 当前 chat 用的 model name. summary_worker 摘要时优先用同款.
                  (鸿波 5/11 决策: 私有部署 token 不要钱, 用 chat 同款最省事)
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
            "origin_model": origin_model,  # 5/11 fix2: chat 用啥模型 summary 也用啥
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
    origin_model: str | None = None,
) -> list[dict[str, Any]]:
    """gateway app.py 调的统一入口.

    5/22 BL-CENTRAL-EDGE-TOOL-ARCHIVE Phase 6a (鸿波): gateway archive **强制停**.
    边缘端 (catfish-tool-bridge adapter.py) 已经在 dispatch_tool 出口截胡, role=tool
    message 内 content 进 gateway 时已经是 "[已归档: archive_ref=...]" 替换文本.
    这层 gateway prepare_tool_messages 现在只做 fallback: 真有漏网的 (e.g. 第三方 plugin
    直接调 LLM 没经过 catfish-tool-bridge), 走 FIX41 truncate 硬切, **不写 PG**.

    走 env CATFISH_GATEWAY_TOOL_ARCHIVE_ENABLE 紧急回滚: 设 1 临时恢复老 PG archive
    (debug 用, 默认 0 强制停).

    路径:
      env=0 (默认): FIX41 truncate, 不写 PG ← BOUNDARY 合规
      env=1 (回滚): 老 archive 模式 (debug 用)
    """
    import os  # noqa: PLC0415
    pg_archive_enabled = os.environ.get(
        "CATFISH_GATEWAY_TOOL_ARCHIVE_ENABLE", "0"
    ).strip().lower() in ("1", "true", "yes", "on")

    if not pg_archive_enabled:
        # 5/22 BL-CENTRAL-EDGE-TOOL-ARCHIVE Phase 6a 默认路径: edge 已截胡, gateway
        # 不再 PG archive. 漏网的 oversized 走 FIX41 硬切.
        from ..tool_msg_truncator import truncate_tool_messages  # noqa: PLC0415
        return truncate_tool_messages(messages)

    # 老路径 (env=1 回滚, debug 用) ──────────────────────────────
    if not features.is_archive_enabled(user_email):
        from ..tool_msg_truncator import truncate_tool_messages  # noqa: PLC0415
        return truncate_tool_messages(messages)

    sid = session_id or derive_session_id(
        user_email, messages=messages, conversation_id=conversation_id,
    )

    try:
        return archive_tool_messages(
            messages, user_email=user_email, session_id=sid,
            origin_model=origin_model,
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
