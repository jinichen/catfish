"""catfish_workflow —— 员工自己搭工作流 (10/17, docs/WORKFLOW-SKILL-PLAN.md)。

一个工具多个 action, 只占 gateway tool cap 一格。实现在 workflow_tool.py / skill_workflow.py。
跑一个已保存的工作流用 catfish_run_skill (跟别的 skill 一样)。
"""
from __future__ import annotations

from typing import Any, Dict, List

WORKFLOW_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "catfish_workflow",
        "description": (
            "员工搭工作流: 把反复做的事拆成步骤存成 skill, 以后一句话或定时跑完。员工说「串起来 / 以后自动做 / 每周帮我…」时用; 跑已保存的用 catfish_run_skill。\n"
            "流程: catalog 看积木 → 拆步骤 (每步 skill / action / xiaonian; 步间传值 ${steps.<id>.<字段>}, 可变值 ${params.<名>}, 逐项 for_each + ${item}) → "
            "draft (有 problems 就改), 念给员工并说明哪几步会停下等确认 → trial 试跑 → save (先不带 confirmed 拿 proposal, 员工同意再 confirmed=true)。\n"
            "没积木能做的步骤明说, 请员工先教 skill。对外步骤停在 waiting_confirm, 只能员工在界面确认, 你不能代确认。"
            "失败告诉员工第几步、为什么, 排除后 resume (run_id) 续跑。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["catalog", "list", "draft", "trial", "save", "runs", "status", "resume"]},
                "name": {"type": "string", "description": "工作流名, 小写字母数字横线, 例 weekly-qual-mail"},
                "namespace": {"type": "string", "enum": ["personal", "department"], "description": "draft: 默认 personal"},
                "title": {"type": "string", "description": "draft: 给员工看的中文名, 4-12 字"},
                "description": {"type": "string", "description": "draft: 一句话说明什么时候用"},
                "triggers": {"type": "array", "items": {"type": "string"}, "description": "draft: 员工可能的说法"},
                "workflow": {
                    "type": "object",
                    "description": ("draft: {kind: workflow, version, params, steps: [{id, skill|action|xiaonian, "
                                    "params, input, returns, for_each, expect, confirm}]}"),
                },
                "params": {"type": "object", "description": "trial: 这次试跑的参数"},
                "run_id": {"type": "string", "description": "status / resume: 运行编号"},
                "status": {"type": "string", "description": "runs: done / failed / waiting_confirm / declined"},
                "limit": {"type": "integer"},
                "confirmed": {"type": "boolean", "description": "save: 员工已明确同意保存才 true"},
            },
            "required": ["action"],
        },
    },
]
