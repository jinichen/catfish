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
            "收集表 + 派任务给同事 (task_*)。\n"
            "收集表: 有 Excel 先 from_excel 拿字段, 否则按描述列字段, 再 create; 邮箱不知道用 directory 查。"
            "responses 看结果 (数字照返回说), export 导出。帮员工填别人的表: get → 从本机找值, 找不到留空 → draft (写 sources)。\n"
            "派任务: task_create (text=要做什么, 定时/周期给 schedule) → task_send; task_results 看结果。"
            # 交回文件 (11/5) 的做法写在 mail_draft_id / files 参数说明和任务提示词里 —— 这段说明有长度上限 (test_tool_schema_budget)
            "做同事派来的任务: 做完 task_draft 存 result; 定时/周期任务的这一次用 task_period (要人确认没做的步骤写 skipped)。\n"
            "send / remind / close / task_send / task_remind / task_cancel 会发消息给同事: 先不带 confirmed, 念 proposal, 同意才 confirmed=true。"
            "不能替员工提交、拒填、交回或拒绝, 也不要自己把结果发给对方, 让他去「协同」页点。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "get", "status", "responses", "from_excel", "create",
                                                      "update", "send", "remind", "close", "export", "draft",
                                                      "directory", "task_list", "task_get", "task_results",
                                                      "task_create", "task_send", "task_remind", "task_cancel",
                                                      "task_draft", "task_period"]},
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
                "path": {"type": "string", "description": "from_excel: Excel 模板路径"},
                "template_path": {"type": "string", "description": "create: 来自 Excel 模板时带上"},
                "title": {"type": "string", "description": "表名"},
                "deadline": {"type": "string", "description": "截止 YYYY-MM-DD; 任务可带时分 YYYY-MM-DDTHH:MM"},
                "note": {"type": "string", "description": "给填表人的说明"},
                "recipients": {"type": "array", "items": {"type": "string"},
                               "description": "收件人邮箱 (发出后只能加人; 任务最多 20 人)"},
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
