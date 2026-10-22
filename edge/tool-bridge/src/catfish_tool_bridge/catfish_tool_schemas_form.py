"""catfish_form —— 一起填表 (收集表, 10/17, docs/FORM-COLLECT-PLAN.md)。

一个工具多个 action, 只占 gateway tool cap 一格。实现在 forms.py / forms_excel.py / forms_tool.py。
"""
from __future__ import annotations

from typing import Any, Dict, List

FORM_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "catfish_form",
        "description": (
            "收集表: 发起人发表请同事各填一份, 回收后本机汇总。\n"
            "发起: 有 Excel 模板先 from_excel 拿字段, 否则按描述列字段 (选择题给 options), 再 create; 邮箱不知道用 directory 查。"
            "send / remind / close 会发消息给同事: 先不带 confirmed 调, 把 proposal 念给员工, 同意才 confirmed=true。\n"
            "结果: status 看谁交了; responses 拿全部回答 (统计/汇总都先调它, 数字照返回说); export 导出 Excel。\n"
            "帮员工填别人的表 (消息带 form_id): get 看字段 → 从本机邮件/文件/知识库找值, 找不到留空不要编 → draft 存草稿并写 sources。"
            "你不能替员工提交或拒填, 让他去「协同」页核对后自己提交。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "get", "status", "responses", "from_excel", "create",
                                                      "update", "send", "remind", "close", "export", "draft",
                                                      "directory"]},
                "form_id": {"type": "string", "description": "表单编号 (f_YYYYMMDD_xxxxxxxx)"},
                "path": {"type": "string", "description": "from_excel: Excel 模板路径"},
                "template_path": {"type": "string", "description": "create: 来自 Excel 模板时带上"},
                "title": {"type": "string", "description": "表名"},
                "deadline": {"type": "string", "description": "截止日 YYYY-MM-DD"},
                "note": {"type": "string", "description": "给填表人的说明"},
                "recipients": {"type": "array", "items": {"type": "string"},
                               "description": "收件人邮箱 (发出后只能加人)"},
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
                "text": {"type": "string", "description": "remind: 附言; directory: 姓名/部门/邮箱片段"},
                "confirmed": {"type": "boolean", "description": "员工已明确同意才 true"},
            },
            "required": ["action"],
        },
    },
]
