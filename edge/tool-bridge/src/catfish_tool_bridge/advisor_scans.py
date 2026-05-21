"""BL-ADVISOR-SCANS (5/21 Phase 7 第 3 步): 2 个扫描 tool 实现.

设计稿 §4.4 + §4.5:
  - catfish_check_compliance: 央国企合规风险扫描 (ISO / 审计 / 法务 / 财务)
  - catfish_political_sensitivity_scan: 政治敏感扫描 (上级 / 平级 / 关键客户)

第一版保守原则 (5/21 鸿波 4 拍): 只 flag 风险 + 给建议, 不强行覆盖所有 case.
keyword-based 简单匹配, V2+ 可换 LLM 智能扫描.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.advisor_scans")


# ── 合规关键词库 ─────────────────────────────────────────────────
#
# 第一版手写, 央国企典型场景. 加权 severity:
#   high: 明确合规红线 (违反 → 处分 / 法律风险)
#   medium: 易被审计追问的口径 (e.g. 资质范围 / 财务披露)
#   low: 一般风险 (e.g. 金额未脱敏)

_COMPLIANCE_RULES = [
    # (keyword/pattern, severity, type, reason_template, suggestion)
    ("资质方案", "medium", "iso_audit_relevant",
     "提及资质方案范围, 类似回复曾被 ISO 审计追问",
     "保留邮件原文留档, 引用上次班子会决议作依据"),
    ("ISO", "medium", "iso_audit_relevant",
     "涉及 ISO 体系相关内容",
     "留档备查, 跟去年口径核对一致"),
    ("审计", "high", "audit_review",
     "明确提及审计场景",
     "口径必须跟历史决议一致, 任何创新表述待审计部门确认"),
    ("合规", "medium", "compliance_general",
     "明确提及合规相关内容",
     "建议跑过法务部门 review"),
    ("党组", "high", "party_committee_decision",
     "涉及党组决议, 严格按上级口径",
     "不擅自解读, 引用原文"),
    ("国资委", "high", "supervisory_authority",
     "涉及监管单位指示",
     "严格按监管口径, 留档"),
    ("政策", "medium", "policy_relevant",
     "涉及政策解读",
     "引用具体政策文号, 不口头转述"),
    ("法务", "medium", "legal_review",
     "涉及法务相关内容",
     "建议先咨询法务再发"),
    # 财务数字相关
    ("万元", "low", "financial_amount",
     "邮件中含金额未脱敏",
     "如非必要可改为 '约 XX' 模糊表述, 或加'内部数据请勿外传'"),
    ("亿元", "low", "financial_amount",
     "邮件中含较大金额未脱敏",
     "同上"),
    ("预算", "low", "financial_amount",
     "涉及预算数字",
     "确认对方有权限看, 或脱敏"),
]


def check_compliance(args: dict[str, Any]) -> dict[str, Any]:
    """扫一段文本的合规风险.

    Args:
      args.content: str — 要扫的文本
      args.context: str (optional) — 涉及哪个项目 / 客户 (元数据, log 用)

    Returns:
      {
        "flags": [
          {"type": "...", "severity": "...", "reason": "...",
           "matched_keyword": "...", "suggestion": "..."},
          ...
        ],
        "total_count": int,
        "highest_severity": "high" | "medium" | "low" | "none"
      }
    """
    content = str(args.get("content", ""))
    context = str(args.get("context", ""))

    if not content.strip():
        return {"flags": [], "total_count": 0, "highest_severity": "none"}

    content_lower = content.lower()
    flags: list[dict[str, Any]] = []
    severity_order = {"high": 3, "medium": 2, "low": 1, "none": 0}
    highest_sev = "none"

    for keyword, severity, type_, reason, suggestion in _COMPLIANCE_RULES:
        if keyword.lower() in content_lower:
            flags.append({
                "type": type_,
                "severity": severity,
                "reason": reason,
                "matched_keyword": keyword,
                "suggestion": suggestion,
            })
            if severity_order[severity] > severity_order[highest_sev]:
                highest_sev = severity

    logger.info(
        "[check_compliance] context=%s content_bytes=%d flags=%d highest=%s",
        context[:40], len(content), len(flags), highest_sev,
    )
    return {
        "flags": flags,
        "total_count": len(flags),
        "highest_severity": highest_sev,
    }


# ── 政治敏感关键词库 ────────────────────────────────────────────────
#
# 第一版保守: 只 flag + 给"重新措辞" 建议, 不强行替换. 凡涉及红线事项一律降级
# 为"提醒人工核对".

_POLITICAL_RULES = [
    # (keyword/pattern, severity, type, reason, suggested_phrasings)
    ("希望", "low", "upper_level_tone",
     "对上级用 '希望' 偏弱, 可能传达决心不足",
     ["拟于", "计划", "将于"]),
    ("尽量", "low", "upper_level_tone",
     "对上级 '尽量' 显被动",
     ["确保", "按时", "保质"]),
    ("可能", "low", "upper_level_tone",
     "对上级 '可能' 显犹豫",
     ["预计", "拟", "按计划"]),
    ("反对", "high", "peer_conflict",
     "明确表达反对意见, 易引发对立",
     ["建议进一步研究", "拟提出不同视角"]),
    ("无法", "medium", "upper_level_tone",
     "对上级 '无法' 显推诿",
     ["在 X 条件下可实现", "需 Y 支持后推进"]),
]


def political_sensitivity_scan(args: dict[str, Any]) -> dict[str, Any]:
    """扫一段文本的政治敏感 / 措辞风险.

    Args:
      args.content: str — 文本
      args.related_people: list[dict] (optional) — 涉及的人
        [{"name": "李局", "relation": "上级"}, ...]
      args.tier: str (optional) — 员工职级 "frontline"/"mid"/"senior".
        senior 时 high-severity flag 直接降级"提醒人工核对", 不给 suggested_phrasings.

    Returns:
      {
        "flags": [
          {"type": "...", "severity": "...", "person": "...",
           "reason": "...", "matched_keyword": "...",
           "suggested_phrasings": [...]},
          ...
        ],
        "total_count": int,
        "highest_severity": "high" | "medium" | "low" | "none",
        "advisory_only": bool   # senior tier 时 true, UI 渲染"提醒"而不是"建议改"
      }
    """
    content = str(args.get("content", ""))
    related_people = args.get("related_people") or []
    tier = str(args.get("tier", "mid"))

    if not content.strip():
        return {
            "flags": [], "total_count": 0,
            "highest_severity": "none", "advisory_only": False,
        }

    content_lower = content.lower()
    flags: list[dict[str, Any]] = []
    severity_order = {"high": 3, "medium": 2, "low": 1, "none": 0}
    highest_sev = "none"

    # 找上级 (relation 含 "上级" / "总" / "领导") — 决定 upper_level_tone 是否触发
    has_upper = any(
        isinstance(p, dict) and isinstance(p.get("relation"), str)
        and any(s in p["relation"] for s in ["上级", "领导"])
        for p in related_people
    )

    for keyword, severity, type_, reason, suggested in _POLITICAL_RULES:
        if keyword in content_lower:
            # upper_level_tone 类只在涉及上级时触发, 否则忽略
            if type_ == "upper_level_tone" and not has_upper:
                continue
            flag = {
                "type": type_,
                "severity": severity,
                "reason": reason,
                "matched_keyword": keyword,
                "suggested_phrasings": suggested,
            }
            # 标第一个上级名 (UI 显)
            if type_ == "upper_level_tone":
                for p in related_people:
                    if isinstance(p, dict) and any(
                        s in (p.get("relation") or "")
                        for s in ["上级", "领导"]
                    ):
                        flag["person"] = p.get("name", "")
                        break
            flags.append(flag)
            if severity_order[severity] > severity_order[highest_sev]:
                highest_sev = severity

    # senior tier: high-severity 降级为"提醒人工核对", 保守原则
    advisory_only = (tier == "senior" and highest_sev == "high")

    logger.info(
        "[political_scan] tier=%s related=%d content_bytes=%d flags=%d highest=%s advisory=%s",
        tier, len(related_people), len(content), len(flags), highest_sev, advisory_only,
    )
    return {
        "flags": flags,
        "total_count": len(flags),
        "highest_severity": highest_sev,
        "advisory_only": advisory_only,
    }
