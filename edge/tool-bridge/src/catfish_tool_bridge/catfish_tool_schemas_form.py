"""catfish_form —— 一起填表 (收集表, 10/17, docs/FORM-COLLECT-PLAN.md) + 派任务给同事 (11/4, task_*)。

一个工具多个 action, 只占 gateway tool cap 一格 (已到上限 49, 派任务只好合进来)。
实现在 forms.py / forms_excel.py / forms_tool.py / colleague_tasks.py。
"""
from __future__ import annotations

from typing import Any, Dict, List

FORM_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "catfish_form",
        "description": (
            "收集表 + 派任务 (task_*) + 群里一起改文档 (revise_*)。\n"
            "收集表: from_excel 或按描述列字段 → create; 邮箱用 directory 查; responses 看结果, export 导出。"
            "替员工填表: get → 本机找值 (找不到留空) → draft (带 sources)。\n"
            "派任务: task_create (text=做什么, 定时/周期给 schedule) → task_send; task_results 看结果。"
            # 交回文件 (11/5) 的做法写在 mail_draft_id / files 参数说明和任务提示词里 —— 这段说明有长度上限 (test_tool_schema_budget)
            "做同事派的任务: 做完 task_draft 存 result; 定时/周期的这次用 task_period。\n"
            "改文档: revise_start (room_id+path) → revise_merge 看冲突 → decide → publish\n"
            "会发消息的 (send、remind、close、task_send/remind/cancel、revise_start/remind/publish): "
            "先不带 confirmed, 念 proposal, 同意才 confirmed=true。不能替员工提交、拒填、交回、拒绝。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "get", "status", "responses", "from_excel", "create",
                                                      "update", "send", "remind", "close", "export", "draft",
                                                      "directory", "task_list", "task_get", "task_results",
                                                      "task_create", "task_send", "task_remind", "task_cancel",
                                                      "task_draft", "task_period", "revise_list", "revise_start",
                                                      "revise_status", "revise_merge", "revise_decide", "revise_publish",
                                                      "revise_remind"]},
                "rev_id": {"type": "string", "description": "revise_*: 一轮修改的编号 (r_…)"},
                "cid": {"type": "string", "description": "revise_decide: 冲突编号; pick=选项 key / base / text (text 带上写好的文字)"},
                "pick": {"type": "string"},
                "schedule": {"type": "object", "description": "task_create 定时: {kind:once, at:YYYY-MM-DDTHH:MM}; "
                             "周期: {kind:cron, expr:5 段 cron, until:YYYY-MM-DD 和/或 times:次数} (必须有结束)"},
                "task_id": {"type": "string", "description": "任务编号 (t_YYYYMMDD_xxxxxxxx)"},
                "result": {"type": "string", "description": "task_draft / task_period: 要交回给发起人的完整结果"},
                "skipped": {"type": "array", "items": {"type": "string"},
                            "description": "task_period: 要员工确认才能做、这次没做的步骤, 每步一句"},
                "period": {"type": "integer", "description": "task_period: 接着做某一次时带上第几次 (更新那一次, 不新开)"},
                "mail_draft_id": {"type": "string",
                                  "description": "task_draft / task_period: 交回的是文件时, 先用 catfish_email_create_draft 起草发给发起人、"
                                                 "attachments 带上文件的邮件, 这里填那封草稿的 id"},
                "files": {"type": "array", "items": {"type": "string"},
                          "description": "task_draft / task_period: 那封邮件草稿里的文件名"},
                "form_id": {"type": "string", "description": "表单编号 (f_YYYYMMDD_xxxxxxxx)"},
                "path": {"type": "string", "description": "from_excel: Excel 模板路径; revise_start: 要请大家改的文档"},
                "template_path": {"type": "string", "description": "create: 来自 Excel 模板时带上"},
                "title": {"type": "string", "description": "表名"},
                "deadline": {"type": "string", "description": "截止 YYYY-MM-DD; 任务可带时分 YYYY-MM-DDTHH:MM"},
                "note": {"type": "string", "description": "给填表人的说明"},
                "recipients": {"type": "array", "items": {"type": "string"},
                               "description": "收件人邮箱 (发出后只能加人; 任务最多 20 人); revise_start: 请谁改"},
                "room_id": {"type": "string", "description": "create: 在哪个群里发 (r_…, 收件人须是群里已加入的人); 表卡进群, 谁交了群里看得到"},
                "fields": {
                    "type": "array",
                    "items": {"type": "object", "properties": {
                        "label": {"type": "string"}, "key": {"type": "string"},
                        "type": {"type": "string", "enum": ["text", "longtext", "number", "date", "choice", "multichoice"]},
                        "required": {"type": "boolean"}, "options": {"type": "array", "items": {"type": "string"}},
                        "hint": {"type": "string"}, "col": {"type": "integer"},
                    }, "required": ["label"]},
                    "description": "字段 (from_excel 返回的可原样传)",
                },
                "values": {"type": "object", "description": "draft: 按字段 key; 日期 YYYY-MM-DD, 多选给数组"},
                "sources": {"type": "object", "description": "draft: 每个字段 key 的出处"},
                "text": {"type": "string", "description": "remind / task_remind: 附言; directory: 姓名/部门/邮箱片段; task_create: 要同事做什么"},
                "confirmed": {"type": "boolean", "description": "员工已明确同意才 true"},
            },
            "required": ["action"],
        },
    },
]
