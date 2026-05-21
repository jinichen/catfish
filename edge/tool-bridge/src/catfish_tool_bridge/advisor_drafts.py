"""BL-ADVISOR-DRAFTS (5/21 Phase 7 第 3 步): 3 个起草 tool 实现.

设计稿 §4.1-4.3:
  - catfish_draft_email_reply: 起草邮件回信 (1-3 个口径) → outputs/<today>/reply-*.md
  - catfish_draft_meeting_brief: 起草汇报材料 → outputs/<today>/meeting-brief-*.md
  - catfish_compose_followup_list: 起草催办名单 → outputs/<today>/followup-*.md

策略 (5/21 鸿波"绝不代行"约束 + 简化): tool 不调 LLM 二次, **LLM 主调用方
generate content 传给 tool, tool 只保存到 outputs/**. LLM 输出多个口径就调多次.

输入 schema 见 catfish_tool_schemas.py.

后续 (Phase 7 V2+): 可加 LLM-based 智能起草, e.g. tool 内部 RAG 历史回信 + 调
模板. 当前简版能跑通先.
"""
from __future__ import annotations

import logging
from typing import Any

from .advisor_io import save_draft

logger = logging.getLogger("catfish.tool_bridge.advisor_drafts")


def draft_email_reply(args: dict[str, Any]) -> dict[str, Any]:
    """起草邮件回信 (调用方 LLM 一次只起一个口径, 多个口径调多次).

    Args:
      args.tone: str — "strict" / "balanced" / "friendly" / "formal" / "urgent" / "hold"
      args.thread_id: str — 邮件 thread (元数据, 写入草稿头部, 给员工对照)
      args.recipient: str — 收件人 (元数据)
      args.subject: str — 邮件主题
      args.content: str — LLM 已 generate 好的回信正文
      args.compliance_notes: list[str] (optional) — LLM 自己跑过 check_compliance 的结果

    Returns:
      {"path": "...", "filename": "...", "tone": "...", "bytes": int}
    """
    tone = str(args.get("tone", "balanced"))
    thread_id = str(args.get("thread_id", "unknown"))
    recipient = str(args.get("recipient", "unknown"))
    subject = str(args.get("subject", "(无主题)"))
    body = str(args.get("content", ""))
    compliance_notes = args.get("compliance_notes") or []

    if not body.strip():
        return {"error": "content 为空, 拒存草稿"}

    # 文件名: reply-<recipient_short>-<tone>.md
    rec_short = "".join(c for c in recipient if c.isalnum())[:20] or "unknown"
    filename = f"reply-{rec_short}-{tone}.md"

    header = (
        f"# 邮件回信草稿 ({tone})\n\n"
        f"- 收件人: {recipient}\n"
        f"- 主题: {subject}\n"
        f"- thread_id: {thread_id}\n"
        f"- catfish 起草时间: {_now_iso()}\n"
    )
    if compliance_notes:
        header += "\n## 合规提示\n\n"
        for n in compliance_notes:
            header += f"- {n}\n"
    full = f"{header}\n---\n\n{body}\n"

    try:
        path = save_draft(filename, full)
    except (ValueError, OSError) as e:
        return {"error": f"save_draft 失败: {e}"}
    return {
        "path": path,
        "filename": filename,
        "tone": tone,
        "bytes": len(full.encode("utf-8")),
    }


def draft_meeting_brief(args: dict[str, Any]) -> dict[str, Any]:
    """起草会议汇报材料 / brief.

    Args:
      args.event_id: str — 日历 event id (元数据)
      args.event_title: str — 会议标题
      args.content: str — LLM 已 generate 好的 brief 正文 (markdown)
      args.highlighted_uncertain: list[str] (optional) — 待员工确认的数字 / 内容点

    Returns:
      {"path": "...", "filename": "...", "bytes": int, "uncertain_count": int}
    """
    event_id = str(args.get("event_id", "unknown"))
    event_title = str(args.get("event_title", "(无标题)"))
    body = str(args.get("content", ""))
    uncertain = args.get("highlighted_uncertain") or []

    if not body.strip():
        return {"error": "content 为空, 拒存草稿"}

    title_short = "".join(c for c in event_title if c.isalnum() or c in "-_")[:30] or "meeting"
    filename = f"meeting-brief-{title_short}.md"

    header = (
        f"# 会议汇报材料草稿\n\n"
        f"- 会议: {event_title}\n"
        f"- event_id: {event_id}\n"
        f"- catfish 起草时间: {_now_iso()}\n"
    )
    if uncertain:
        header += "\n## ⚠️ 待员工确认的内容点\n\n"
        for u in uncertain:
            header += f"- {u}\n"
    full = f"{header}\n---\n\n{body}\n"

    try:
        path = save_draft(filename, full)
    except (ValueError, OSError) as e:
        return {"error": f"save_draft 失败: {e}"}
    return {
        "path": path,
        "filename": filename,
        "bytes": len(full.encode("utf-8")),
        "uncertain_count": len(uncertain),
    }


def compose_followup_list(args: dict[str, Any]) -> dict[str, Any]:
    """起草催办名单 + 多种沟通口径.

    Args:
      args.project: str — 项目名
      args.decision_ref: str (optional) — 哪次会议拍的 (元数据)
      args.content: str — LLM 已 generate 好的催办名单 markdown (含多人/多 tone)

    Returns:
      {"path": "...", "filename": "...", "bytes": int}
    """
    project = str(args.get("project", "unknown"))
    decision_ref = str(args.get("decision_ref", ""))
    body = str(args.get("content", ""))

    if not body.strip():
        return {"error": "content 为空, 拒存草稿"}

    proj_short = "".join(c for c in project if c.isalnum())[:20] or "project"
    filename = f"followup-{proj_short}.md"

    header = (
        f"# 项目催办名单 — {project}\n\n"
        f"- 项目: {project}\n"
    )
    if decision_ref:
        header += f"- 源决议: {decision_ref}\n"
    header += f"- catfish 起草时间: {_now_iso()}\n"
    full = f"{header}\n---\n\n{body}\n"

    try:
        path = save_draft(filename, full)
    except (ValueError, OSError) as e:
        return {"error": f"save_draft 失败: {e}"}
    return {
        "path": path,
        "filename": filename,
        "bytes": len(full.encode("utf-8")),
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
