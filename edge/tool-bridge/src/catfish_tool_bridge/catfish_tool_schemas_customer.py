"""catfish_customer —— 客户档案 + 跟进雷达 (10/12, docs/SALES-FOLLOWUP-PLAN.md)。

一个工具多个 action: 核心工具每加一个都占 gateway 的 tool cap (DEFAULT_MAX_TOOLS),
客户这一套合成一个只占一格。实现在 customers.py / customer_radar.py。
"""
from __future__ import annotations

from typing import Any, Dict, List

CUSTOMER_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "catfish_customer",
        "description": (
            "销售的**客户档案 + 跟进雷达**。客户 = 知识库里 entity_type=customer 的页面 "
            "(阶段 / 负责人 / 联系人 / 最近联系 / 下一步 / 时间线), 只存在员工本机。\n\n"
            "**何时调 (主动)**: 处理完任何**客户**相关的邮件、导入的微信聊天、会议纪要后, "
            "立刻 action=log 把要点记进该客户时间线 —— 这是跟进雷达的唯一数据来源, 不记雷达就看不到。"
            "每条只记一件事, text 尽量用原话要点 (≤160 字)。分类:\n"
            "  · who=客户, kind=问题: 客户问了 / 要了东西, 需要我方回复\n"
            "  · who=我方, kind=承诺: 我方答应了要做的事 (「明天发报价」「周三前确认」), **必须带 due**\n"
            "  · who=我方, kind=报价: 发出报价 (写金额 / 版本)\n"
            "  · 其余 kind=信息\n"
            "我方后来兑现了承诺 → action=done (text=那条承诺的原文片段)。\n\n"
            "**红线**: ① 新建客户、改阶段只能**提议**: 先不带 confirmed 调, 把返回的 proposal 念给员工, "
            "员工同意才带 confirmed=true 再调。② 认不出是哪个客户 (返回 not found / 多个候选) 就问员工, "
            "**不要记到别的客户上**。③ 不在个人微信里替销售发消息, 只起草。\n\n"
            "action: upsert=建/改客户字段 · log=记时间线 · done=承诺标已办 · get=看一个客户 · "
            "list=按阶段列客户 · radar=跟进雷达 (客户在等回复 / 我答应的到期 / 客户冷了 / 报价没回音) · "
            "template=看销售模板 (阶段名、天数)。阶段名以 template 为准, 不同商品的团队阶段不同。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["upsert", "log", "done", "get", "list", "radar", "template"]},
                "name": {"type": "string", "description": "upsert: 客户公司全名"},
                "customer": {"type": "string", "description": "log/done/get: 客户名 (或用 email / wechat 定位)"},
                "email": {"type": "string", "description": "按联系人邮箱 (同公司域名也算) 定位客户"},
                "wechat": {"type": "string", "description": "按微信备注名 / 群名定位客户"},
                "stage": {"type": "string", "description": "upsert: 阶段 (必须是销售模板里的); list: 按阶段筛"},
                "owner": {"type": "string", "description": "负责销售"},
                "contacts": {
                    "type": "array",
                    "items": {"type": "object", "properties": {
                        "name": {"type": "string"}, "role": {"type": "string"},
                        "wechat": {"type": "string"}, "email": {"type": "string"},
                    }},
                    "description": "upsert: 联系人 (整组替换)",
                },
                "next_action": {"type": "string", "description": "upsert: 下一步一句话"},
                "next_action_due": {"type": "string", "description": "upsert: 下一步截止 YYYY-MM-DD"},
                "deal_size": {"type": "string", "description": "upsert: 预计金额, 如「12万/年」"},
                "confirmed": {"type": "boolean", "description": "新建客户 / 改阶段: 员工已明确同意才 true"},
                "date": {"type": "string", "description": "log: 这件事发生的日期 YYYY-MM-DD, 默认今天"},
                "channel": {"type": "string", "enum": ["邮件", "微信", "会议", "电话", "其他"]},
                "who": {"type": "string", "enum": ["客户", "我方"]},
                "kind": {"type": "string", "enum": ["问题", "承诺", "报价", "信息"]},
                "text": {"type": "string", "description": "log: 原话要点; done: 要标完成那条承诺的原文片段"},
                "due": {"type": "string", "description": "log 承诺: 截止日 YYYY-MM-DD"},
                "limit": {"type": "integer", "description": "get: 时间线返回条数, 默认 30"},
            },
            "required": ["action"],
        },
        "emoji": "🤝",
        "toolset": "catfish_native",
        "available": True,
    },
]
