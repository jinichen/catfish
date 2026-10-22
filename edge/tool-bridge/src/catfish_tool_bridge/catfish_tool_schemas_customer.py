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
            "销售客户档案 + 跟进雷达。\n"
            "主动: 处理完客户相关的邮件 / 微信 / 会议纪要后立刻 log 记要点 (一条一件事, ≤160 字原话), 雷达只看这个。"
            "who=客户 kind=问题 (等我方回复); who=我方 kind=承诺 (必须带 due) / 报价; 其余 kind=信息。承诺兑现后 done。\n"
            "红线: 新建客户、改阶段先不带 confirmed 调, 把 proposal 念给员工, 同意才 confirmed=true; 认不出客户就问, 不要记错客户; 不替销售发微信, 只起草。\n"
            "其他: upsert · get · list · radar · template (阶段名以它为准) · price · quote 出 Excel 报价单 (价格表没有的先问, 不要编价; 低于最低价需确认) · "
            "quotes · weekly 销售周报。报价单不替员工发, 员工说已发后再 log kind=报价。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["upsert", "log", "done", "get", "list", "radar", "template",
                                                      "price", "quote", "quotes", "weekly"]},
                "name": {"type": "string", "description": "upsert: 客户公司全名"},
                "customer": {"type": "string", "description": "log/done/get: 客户名 (或用 email / wechat 定位)"},
                "email": {"type": "string", "description": "按联系人邮箱 / 公司域名定位客户"},
                "wechat": {"type": "string", "description": "按微信备注名 / 群名定位客户"},
                "stage": {"type": "string", "description": "upsert: 模板里的阶段名; list: 按阶段筛"},
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
                "confirmed": {"type": "boolean", "description": "员工已明确同意才 true"},
                "date": {"type": "string", "description": "YYYY-MM-DD; log: 发生日 (默认今天); weekly: 该日所在周"},
                "channel": {"type": "string", "enum": ["邮件", "微信", "会议", "电话", "其他"]},
                "who": {"type": "string", "enum": ["客户", "我方"]},
                "kind": {"type": "string", "enum": ["问题", "承诺", "报价", "信息"]},
                "text": {"type": "string", "description": "log: 原话要点; done: 承诺原文片段; price: 商品关键词"},
                "due": {"type": "string", "description": "log 承诺: 截止日 YYYY-MM-DD"},
                "limit": {"type": "integer", "description": "get: 时间线返回条数, 默认 30"},
                "items": {
                    "type": "array",
                    "items": {"type": "object", "properties": {
                        "name": {"type": "string", "description": "价格表里的准确商品名"},
                        "spec": {"type": "string"}, "unit": {"type": "string"},
                        "qty": {"type": "number"},
                        "price": {"type": "number", "description": "单价; 不给 = 价格表标价"},
                        "discount": {"type": "number", "description": "0.9 = 九折; 不给 = 不打折"},
                        "note": {"type": "string"},
                    }, "required": ["name", "qty"]},
                    "description": "quote: 报价明细",
                },
                "valid_days": {"type": "integer", "description": "quote: 有效期天数, 默认按设置"},
                "note": {"type": "string", "description": "quote: 报价单备注"},
            },
            "required": ["action"],
        },
        "emoji": "🤝",
        "toolset": "catfish_native",
        "available": True,
    },
]
