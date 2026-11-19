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
            "客户档案 + 跟进雷达 + 采购比价。\n"
            "处理完客户邮件 / 微信 / 会议立刻 log 要点 (一条一事, ≤160 字原话)。"
            "who=客户 kind=问题; who=我方 kind=承诺 (带 due) / 报价; 其余 kind=信息。承诺兑现后 done。\n"
            "红线: 新建客户 / 改阶段先不带 confirmed, 念 proposal, 同意才 confirmed=true; 认不出客户就问; 不替员工发消息。\n"
            "upsert · get · list · radar · template · price · quote 出报价单 (价格表没有的先问, 不编价) · quotes · weekly。\n"
            "采购: 比报价先读邮件 / 附件, 每家抽 供应商/价/单位/含税否/交货/付款/运费/有效期/来源 "
            "(没写留空), 调 procure_compare (item+quotes+qty)。procure_deal 记成交 (说定了才 confirmed) · "
            "procure_history · procure_monthly 月报 (date) · procure_import (台账 path)。不拍板、不联系供应商。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["upsert", "log", "done", "get", "list", "radar", "template",
                                                      "price", "quote", "quotes", "weekly",
                                                      "procure_compare", "procure_deal", "procure_history", "procure_import", "procure_monthly"]},
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
                "date": {"type": "string", "description": "YYYY-MM-DD; log 发生日 / weekly 所在周"},
                "channel": {"type": "string", "enum": ["邮件", "微信", "会议", "电话", "其他"]},
                "who": {"type": "string", "enum": ["客户", "我方"]},
                "kind": {"type": "string", "enum": ["问题", "承诺", "报价", "信息"]},
                "text": {"type": "string", "description": "log: 原话要点; done: 承诺原文片段; price: 商品关键词"},
                "due": {"type": "string", "description": "log 承诺: 截止日 YYYY-MM-DD"},
                "limit": {"type": "integer", "description": "get: 条数, 默认 30"},
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
                "valid_days": {"type": "integer", "description": "quote: 有效期天数"},
                "note": {"type": "string", "description": "quote: 报价单备注"},
                "item": {"type": "string", "description": "procure: 品名"},
                "unit": {"type": "string", "description": "默认 吨"},
                "qty": {"type": "number"},
                "quotes": {
                    "type": "array",
                    "items": {"type": "object", "properties": {
                        "supplier": {"type": "string"}, "price": {"type": "number"},
                        "unit": {"type": "string"}, "tax_included": {"type": "boolean"},
                        "delivery": {"type": "string"}, "payment": {"type": "string"},
                        "freight": {"type": "string"}, "valid_until": {"type": "string"},
                        "source": {"type": "string"},
                    }, "required": ["supplier", "price"]},
                    "description": "procure_compare",
                },
                "supplier": {"type": "string"},
                "price": {"type": "number", "description": "procure_deal 含税"},
                "path": {"type": "string"},
            },
            "required": ["action"],
        },
        "emoji": "🤝",
        "toolset": "catfish_native",
        "available": True,
    },
]
