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
            "**员工自己搭工作流**: 把一件反复做的事拆成步骤, 每步是一块积木, 存成工作流 skill, 以后一句话 (或定时) 整条跑完。\n\n"
            "**员工描述一件反复做的事、说「串起来 / 以后自动做 / 每周帮我…」时**:\n"
            "1. action=catalog 看有哪些积木: actions (鲶鱼自己会的: 查邮件 / 搜文件 / 建任务 / 存草稿 / 发收集表 / 推送到微信…, "
            "level=outward 的会对外) 和 skills (员工已经教会的 skill, 带参数)。\n"
            "2. 拆步骤, 每步选一块: `skill: <ns/name>` 或 `action: <name>` 或 `xiaonian: <让小鲶判断什么>` (+ `returns: {字段: text|number|list|bool|date}`, "
            "这一步只看 `input` 给它的数据, 不能调工具)。步骤之间用 `${steps.<id>.<字段>}` 传数据, 员工每次可能改的值做成 "
            "`params` 用 `${params.<名字>}`; 对列表里每一项做一次同一步用 `for_each: ${steps.<id>.<列表>}` + `${item}` / `${item.字段}`。"
            "做完要检查的写 `expect` (ok / count_min / files_min / text_contains / page_contains)。\n"
            "3. **有一步没有积木能做** (例如要在某个内部网页上操作) → 明说这步还不会, 请员工先教一个 skill, 教完再接上; 不要硬凑。\n"
            "4. action=draft 存草稿 (会校验, 有 problems 就改)。把步骤念给员工听, **特别说明哪几步会停下等他确认**。\n"
            "5. 员工同意 → action=trial 试跑一次 → 员工满意 → action=save (先不带 confirmed 拿 proposal 念给员工, 同意再带 confirmed=true)。\n\n"
            "**对外的步骤** (发收集表、发布知识库、推送到微信…) 跑到之前会停下 (status=waiting_confirm), **只能员工在鲶鱼界面里点确认**, "
            "你没有确认的办法, 也不要让员工把确认的话发给你代办 —— 告诉他去界面确认。\n"
            "**跑失败了** (status=failed): 把第几步、为什么告诉员工; 原因排除后 action=resume (run_id) 从那一步接着跑, 前面成功的不重跑。\n"
            "action=list 看已有工作流和草稿, action=runs / status 看运行记录。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["catalog", "list", "draft", "trial", "save", "runs", "status", "resume"]},
                "name": {"type": "string", "description": "draft / trial / save: 工作流名字, 小写字母数字横线 (例 weekly-qual-mail)"},
                "namespace": {"type": "string", "enum": ["personal", "department"], "description": "draft: 默认 personal"},
                "description": {"type": "string", "description": "draft: 一句话 —— 什么时候用这个工作流"},
                "triggers": {"type": "array", "items": {"type": "string"}, "description": "draft: 员工可能怎么说 (例「理一下资质邮件」)"},
                "workflow": {
                    "type": "object",
                    "description": ("draft: {kind: workflow, version, params: {名字: {type, default, required, label}}, "
                                    "steps: [{id, skill|action|xiaonian, params, input, returns, for_each, expect, confirm}]}"),
                },
                "params": {"type": "object", "description": "trial: 这次试跑的参数"},
                "run_id": {"type": "string", "description": "status / resume: 运行编号"},
                "status": {"type": "string", "description": "runs: 只看某个状态 (done / failed / waiting_confirm / declined)"},
                "limit": {"type": "integer"},
                "confirmed": {"type": "boolean", "description": "save: 员工已明确同意保存才 true"},
            },
            "required": ["action"],
        },
    },
]
