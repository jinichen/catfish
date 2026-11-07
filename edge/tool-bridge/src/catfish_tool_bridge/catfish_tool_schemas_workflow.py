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
            "员工搭工作流: 反复做的事拆成步骤存成 skill, 以后一句话或定时跑完 (员工说「串起来 / 每周帮我…」时用; 跑已保存的用 catfish_run_skill)。\n"
            "流程: catalog 看积木 → 拆步骤 (写法见 workflow 参数说明) → draft (有 problems 就改), 念给员工、说明哪几步停下等确认 → "
            "trial 试跑 → save (先不带 confirmed 拿 proposal, 员工同意再 confirmed=true)。\n"
            "没积木能做的步骤明说, 请员工先教 skill。对外步骤停在 waiting_confirm, 只能员工在界面确认。失败说第几步、为什么, 排除后 resume 续跑。\n"
            "员工一句话要搭专家 (「帮我搭个 X 专员, 负责…」) 用 build_expert (sentence 原话): 拆出专家和每件活, 起草成总览卡, 员工在卡上确认才落地。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["catalog", "list", "draft", "trial", "save", "runs", "status", "resume", "build_expert"]},
                "name": {"type": "string", "description": "工作流名, 小写字母数字横线, 例 weekly-qual-mail"},
                "namespace": {"type": "string", "enum": ["personal", "department"], "description": "draft: 默认 personal"},
                "title": {"type": "string", "description": "draft: 给员工看的中文名, 4-12 字"},
                "description": {"type": "string", "description": "draft: 一句话说明什么时候用"},
                "triggers": {"type": "array", "items": {"type": "string"}, "description": "draft: 员工可能的说法"},
                "workflow": {
                    "type": "object",
                    "description": ("draft: {kind: workflow, version, params, steps: [{id, skill|action|xiaonian, "
                                    "params, input, returns, for_each, expect, confirm, when}]}。步间传值 ${steps.<id>.<字段>}, "
                                    "可变值 ${params.<名>}, 逐项 for_each + ${item}; 有条件才做: when: {of: ${...}, is: "
                                    "not_empty|empty|true|false|eq|ne|gt|ge|lt|le|contains, to: 值}, 分支 = 两步写相反条件"),
                },
                "params": {"type": "object", "description": "trial: 这次试跑的参数"},
                "sentence": {"type": "string", "description": "build_expert: 员工的原话"},
                "run_id": {"type": "string", "description": "status / resume: 运行编号"},
                "status": {"type": "string", "description": "runs: done / failed / waiting_confirm / declined"},
                "limit": {"type": "integer"},
                "confirmed": {"type": "boolean", "description": "save: 员工已明确同意保存才 true"},
            },
            "required": ["action"],
        },
    },
]
