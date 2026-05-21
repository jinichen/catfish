"""BL-ADVISOR-RECALL (5/21 Phase 7 第 3 步): 决策历史检索 tool.

设计稿 §4.6: catfish_recall_decision_history — 按 topic / person / project 检索
~/.catfish/decisions.jsonl, 给 LLM 现在的建议跟历史保持一致 (不背离).

第一版用 substring 匹配, V2+ 可加向量检索 / LLM 语义.
"""
from __future__ import annotations

import logging
from typing import Any

from .advisor_io import load_decisions

logger = logging.getLogger("catfish.tool_bridge.advisor_recall")


def recall_decision_history(args: dict[str, Any]) -> dict[str, Any]:
    """按 topic / person / project 检索过往决策口径.

    Args:
      args.topic: str — 主题关键词 (e.g. "资质方案范围")
      args.person: str (optional) — 相关人
      args.project: str (optional) — 相关项目
      args.limit: int (optional, default 5) — 最多返几条 (上限 50)

    Returns:
      {
        "decisions": [
          {
            "date": "2026-05-14",
            "session_ref": "catfish-history://...",
            "topic": "...",
            "person": "...",
            "decision_summary": "你跟 X 拍的边界: ...",
            "verbatim_excerpt": "..."
          },
          ...
        ],
        "total_matched": int
      }
    """
    topic = str(args.get("topic", "")).lower().strip()
    person = str(args.get("person", "")).lower().strip()
    project = str(args.get("project", "")).lower().strip()
    limit = min(int(args.get("limit", 5)), 50)

    if not topic:
        return {"decisions": [], "total_matched": 0, "error": "topic 必填"}

    all_records = load_decisions()
    matched: list[dict[str, Any]] = []

    for r in all_records:
        title = str(r.get("taskTitle", "")).lower()
        # topic 必须出现
        if topic not in title:
            continue
        # person 若给, 必须出现
        if person and person not in title:
            continue
        # project 若给, 必须出现
        if project and project not in title:
            continue

        # 构造返回结构 (设计稿 §4.6)
        ts = str(r.get("ts", ""))
        date = ts[:10] if len(ts) >= 10 else ts
        user_choice = r.get("userChoice")
        user_action = r.get("userAction")

        # 找用户选了的 option 的 summary 作 verbatim excerpt
        verbatim = ""
        if user_choice:
            for opt in r.get("optionsOffered", []):
                if isinstance(opt, dict) and opt.get("label") == user_choice:
                    verbatim = str(opt.get("summary", ""))
                    break

        decision_summary = _build_summary(r, user_choice, user_action)

        matched.append({
            "date": date,
            "session_ref": str(r.get("contextRefs", [None])[0]) if r.get("contextRefs") else "",
            "topic": r.get("taskTitle", ""),
            "person": person or "",
            "decision_summary": decision_summary,
            "verbatim_excerpt": verbatim,
            "draft_path_used": r.get("draftPathChosen") or "",
            "compliance_flags": r.get("complianceFlagsAtDecision") or [],
        })

    # 按 date 倒序
    matched.sort(key=lambda x: x.get("date", ""), reverse=True)
    total = len(matched)
    matched = matched[:limit]

    logger.info(
        "[recall_decision_history] topic=%s person=%s project=%s → matched=%d returned=%d",
        topic, person, project, total, len(matched),
    )
    return {"decisions": matched, "total_matched": total}


def _build_summary(record: dict[str, Any], choice: str | None, action: str | None) -> str:
    """从 decision record 构造一句话总结."""
    title = record.get("taskTitle", "(无标题)")
    if not choice:
        return f"{title}: 当时未选定 (员工自己重写或忽略)"
    parts = [f"{title}: 选了 {choice} 口径"]
    if action == "sent":
        parts.append("已发")
    elif action == "drafted_but_held":
        parts.append("起草但未发")
    elif action == "overridden":
        parts.append("员工最终重写")
    return ", ".join(parts)
