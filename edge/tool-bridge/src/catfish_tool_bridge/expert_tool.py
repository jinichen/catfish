"""catfish_expert —— 主小鲶看已装的专家、推荐员工切过去 (docs/EXPERT-PACK-PLAN.md §3.3)。

**只推荐, 不转交**: suggest 只是返回一个「切到 X」的提议, Companion 在对话里把它画成按钮, 员工点了才切;
小鲶不会在员工不知道的专家里替他办事。专家自己的工具清单里没有这个工具 (expert_local.BASE_TOOLS 不含它)。
"""
from __future__ import annotations

from typing import Any

from . import expert_local


def dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
    action = str(args.get("action") or "").strip()
    experts = [e for e in expert_local.list_all() if e.get("enabled")]
    if action == "list":
        return {"ok": True, "experts": [{k: e.get(k) for k in ("id", "name", "description")} for e in experts]}
    if action == "suggest":
        eid = str(args.get("expert_id") or "").strip()
        hit = next((e for e in experts if e["id"] == eid), None)
        if hit is None:
            return {"ok": False, "error": f"没有启用的专家 {eid} (先 action=list 看有哪些)"}
        return {"ok": True, "suggestion": {"expert_id": hit["id"], "profile_id": hit["profile_id"], "name": hit["name"],
                                           "reason": str(args.get("reason") or "")[:120]},
                "note": f"界面会出一个「切到{hit['name']}」的按钮; 员工点了才切。你这边一句话说明为什么它更合适就行, 不要自己去办。"}
    return {"ok": False, "error": "action 只能是 list / suggest"}
