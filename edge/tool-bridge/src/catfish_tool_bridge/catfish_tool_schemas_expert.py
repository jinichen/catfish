"""catfish_expert —— 推荐员工切到对口的本机专家 (10/18, docs/EXPERT-PACK-PLAN.md)。只占 tool cap 一格。"""
from __future__ import annotations

from typing import Any, Dict, List

EXPERT_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "catfish_expert",
        "description": (
            "员工可以在鲶鱼里搭**专家** (只管某类事的小鲶分身, 例「资质专员」: 有自己的工作流和选路规则)。"
            "系统提示里会列出已装的专家。员工的请求明显归某个专家管时 (例「帮我审一下资质待办」而资质专员管这个), "
            "调 action=suggest (expert_id, reason) —— 对话里会出「切到 X」的按钮, **员工点了才切**。"
            "只推荐, 不要替员工去专家那边办, 也不要因为有专家就拒绝回答简单问题。action=list 看有哪些专家。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "suggest"]},
                "expert_id": {"type": "string", "description": "suggest: 专家 id"},
                "reason": {"type": "string", "description": "suggest: 一句话, 为什么这件事交给它更合适"},
            },
            "required": ["action"],
        },
    },
]
