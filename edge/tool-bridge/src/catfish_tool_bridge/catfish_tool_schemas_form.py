"""catfish_form —— 一起填表 (收集表, 10/17, docs/FORM-COLLECT-PLAN.md)。

一个工具多个 action, 只占 gateway tool cap 一格。实现在 forms.py / forms_excel.py / forms_tool.py。
"""
from __future__ import annotations

from typing import Any, Dict, List

FORM_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "catfish_form",
        "description": (
            "**一起填表 (收集表)**: 一个人发起一张表, 请同事各填各的一份, 回收后在发起人本机汇总。"
            "表和回答都存员工本机 (`~/.catfish/forms/`), 中央只转交, 取走即清空。\n\n"
            "**员工要发起** (「把这张表发给 xx 填」「收集一下大家的 xx」): "
            "有 Excel 模板先 action=from_excel (path) 拿字段建议; 没有就按员工描述自己列字段 "
            "(type 只能 text / longtext / number / date / choice / multichoice, 选择题给 options)。"
            "然后 action=create (title / fields / deadline YYYY-MM-DD / note / recipients 邮箱列表 / template_path)。"
            "不知道同事邮箱用 action=directory (text=姓名或部门) 查。\n"
            "**发 / 催 / 结束会给同事发消息**: 先不带 confirmed 调 action=send / remind / close, 把返回的 proposal 念给员工, "
            "员工同意才带 confirmed=true 再调。\n"
            "**收集结果**: action=status 看谁交了谁没交; action=responses 拿全部回答 (按字段名一行一人) —— "
            "员工问「谁还没交」「哪些是 xx」「跟上次比」「写个汇总」都先调这个, 数字照返回的说, 不编; "
            "action=export 导出 Excel 汇总 (有模板按模板列顺序)。\n\n"
            "**员工收到别人的表、让你帮着填** (消息里会带 form_id): 先 action=get 看字段, "
            "再用员工**本机**的邮件 / 文件 / 知识库 / 任务找每一项的值; 找不到的**留空, 不要编**; "
            "最后 action=draft 存草稿 (values 按字段 key, sources 写每项出处: 哪封邮件 / 哪个文件)。"
            "**你不能替员工提交或拒填** —— 草稿存好后告诉员工去「协同」页的表单上核对后自己点提交。"
            "草稿只发表单里的值, 读过的原始材料不会发给发起人。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "get", "status", "responses", "from_excel", "create",
                                                      "update", "send", "remind", "close", "export", "draft",
                                                      "directory"]},
                "form_id": {"type": "string", "description": "表单编号 (f_YYYYMMDD_xxxxxxxx)"},
                "path": {"type": "string", "description": "from_excel: Excel 模板路径"},
                "template_path": {"type": "string", "description": "create: 从 Excel 建的表把模板路径带上, 导出按模板列顺序"},
                "title": {"type": "string", "description": "create / update: 表名"},
                "deadline": {"type": "string", "description": "create / update: 截止日 YYYY-MM-DD"},
                "note": {"type": "string", "description": "create / update: 给填表人的说明"},
                "recipients": {"type": "array", "items": {"type": "string"},
                               "description": "create / update: 收件人邮箱 (发出后只能加人)"},
                "fields": {
                    "type": "array",
                    "items": {"type": "object", "properties": {
                        "label": {"type": "string"}, "key": {"type": "string"},
                        "type": {"type": "string", "enum": ["text", "longtext", "number", "date", "choice", "multichoice"]},
                        "required": {"type": "boolean"}, "options": {"type": "array", "items": {"type": "string"}},
                        "hint": {"type": "string"}, "col": {"type": "integer"},
                    }, "required": ["label"]},
                    "description": "create / update: 字段 (from_excel 返回的可以原样传)",
                },
                "values": {"type": "object", "description": "draft: 按字段 key 的值 (日期 YYYY-MM-DD, 多选给数组)"},
                "sources": {"type": "object", "description": "draft: 每个字段 key 的出处说明"},
                "text": {"type": "string", "description": "remind: 催填附言; directory: 查谁 (姓名 / 部门 / 邮箱片段)"},
                "confirmed": {"type": "boolean", "description": "send / remind / close: 员工已明确同意才 true"},
            },
            "required": ["action"],
        },
    },
]
