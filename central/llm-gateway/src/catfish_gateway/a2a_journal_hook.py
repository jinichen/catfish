"""BL-FED2.4 (5/12 鸿波拍板) — A2A 反馈环.

# 真问题

BL-FED2.3 跑通跨员工路由 — alice 问 → bob 答. 但 bob 这次"帮 alice 解决问题"
**没留任何痕迹** — 下次 bob 跑 catfish_extract_expertise 抽专长时, journal 里只
有 bob 自己的工作记录, 没有"被外部咨询"的事实, expertise tag 不会自动成长.

期望: bob 答完一次 → bob 自己 mac 上的 employee_journal.md 自动多一条:
  ## 2026-05-12 14:30 - A2A 协助 alice@ffcs.cn
  - 主题: expert_consult:资质审核
  - 问题: 资质审核的发票怎么开?
  - 回答: 5 个 chunk, 简要内容...

下次 bob 跑 catfish_extract_expertise → LLM 看到 [a2a-help] 标签 → 抽出新 tag
"资质审核咨询" 或加强现有 "资质审核" 的 confidence — **形成自学习闭环**.

# 隐私边界

- 写在 **bob 自己的** mac (~/.catfish/employee_journal.md), 不是中央
- alice 的问题字符 (params.question) 截断到 200 字符 (跟 audit 同标准)
- 答案只记 chunks_count + 头 100 字符摘要, **不记完整 answer** (避免 bob 的私密
  回答永久落盘 — 流式答完 SSE 关了就该消失)
- purpose 透传 (来自 alice 端 expert_consult tag 或 a2a_ask 调用方)
- 失败静默 — 不影响 a2a 主流程的成功响应

# 为什么不直接复用 audit 日志?

audit 是 ops 视角 (谁调谁, 多少 chunk, 多少 ms), 给运维看. journal 是 employee
视角 (今天我帮谁解决了什么), 给员工自己 + 下次 expertise extract 看. 两个目的
两个文件, 不混.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("catfish.gateway.a2a_journal_hook")


# 答案摘要长度 — 别太长, journal 不是答案存档
_ANSWER_PREVIEW_CHARS = 100
# 问题截断 — 跟 audit 一致
_QUESTION_TRUNCATE_CHARS = 200


def append_a2a_help_entry(
    *,
    from_sub: str,
    question: str,
    purpose: str = "",
    answer_preview: str = "",
    chunks_count: int = 0,
    duration_ms: int = 0,
    timestamp: Optional[datetime] = None,
) -> bool:
    """B 端答完 A 后, 在 B 自己的 journal 追加一条 [a2a-help] 记录.

    返 True 写成功, False 失败 (静默, 不抛 — a2a 主流程不能因 journal 失败而 500).
    """
    if not from_sub:
        return False
    if not question:
        return False
    try:
        # lazy import 避免循环依赖
        from .employee_journal import append_to_journal  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        logger.warning("BL-FED2.4: import append_to_journal 失败: %s", e)
        return False

    ts = (timestamp or datetime.now()).strftime("%Y-%m-%d %H:%M")
    truncated_q = (question or "").strip()
    if len(truncated_q) > _QUESTION_TRUNCATE_CHARS:
        truncated_q = truncated_q[:_QUESTION_TRUNCATE_CHARS] + "..."

    preview = (answer_preview or "").strip()
    if len(preview) > _ANSWER_PREVIEW_CHARS:
        preview = preview[:_ANSWER_PREVIEW_CHARS] + "..."

    purpose_line = f"- 主题: {purpose}" if purpose else "- 主题: (未指定)"
    preview_line = f"- 答案摘要: {preview}" if preview else "- 答案摘要: (流式, 未保留)"

    # **结构化 prefix `[a2a-help]`** — expertise.py extract prompt 会识别这个标签.
    entry = (
        f"## {ts} - [a2a-help] 协助 {from_sub}\n"
        f"{purpose_line}\n"
        f"- 问题: {truncated_q}\n"
        f"{preview_line}\n"
        f"- chunks: {chunks_count}, duration: {duration_ms}ms\n"
    )

    try:
        append_to_journal(entry)
        logger.info(
            "BL-FED2.4: a2a-help journal 追加 from=%s purpose=%s chunks=%d",
            from_sub, purpose, chunks_count,
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("BL-FED2.4: journal 写失败 (不影响 a2a 主流程): %s", e)
        return False


__all__ = ["append_a2a_help_entry"]
