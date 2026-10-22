"""catfish_expert —— 推荐员工切到对口的本机专家 (10/18, docs/EXPERT-PACK-PLAN.md)。只占 tool cap 一格。"""
from __future__ import annotations

from typing import Any, Dict, List

EXPERT_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "catfish_expert",
        "description": (
            "推荐员工切到对口的专家 (只管某类事的小鲶分身, 已装的列在系统提示里)。"
            "请求明显归某专家管时调 action=suggest, 对话里会出现切换按钮, 员工点了才切。"
            "只推荐, 不代办; 简单问题照常回答。list 看有哪些专家。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "suggest"]},
                "expert_id": {"type": "string", "description": "suggest: 专家 id"},
                "reason": {"type": "string", "description": "suggest: 一句话说明为什么交给它"},
            },
            "required": ["action"],
        },
    },
]
