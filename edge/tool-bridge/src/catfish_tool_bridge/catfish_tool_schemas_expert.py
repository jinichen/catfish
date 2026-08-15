"""专长抽取 / 专家咨询 / 合规与政治敏感扫描 —— 6 个 tool 的 JSON Schema。

8/15 从 catfish_tool_schemas.py 拆出: 那个文件是**一整个 3362 行的
列表字面量** (78 个 schema), 越过 800 红线 4 倍。零逻辑、零变量引用,
所以按类别切成 8 份, 由 catfish_tool_schemas.py 拼回去。

加新 tool: schema 加到对应类别文件, impl 加 catfish_tools.py 或子 module,
dispatch 加 catfish_tools._dispatch_native_inner —— **dispatch 不重组**
(这条红线是 5/20 拆分时定的, 见 catfish_tool_schemas.py 文件头)。
"""
from __future__ import annotations

from typing import Any, Dict, List


EXPERT_TOOLS: List[Dict[str, Any]] = [
    # ── BL-FED2.1 (5/12 鸿波拍板) 专长从 employee_journal 自动抽 ──
    {
        "name": "catfish_extract_expertise",
        "description": (
            "★ 从 ~/.catfish/employee_journal.md 自动抽员工专长 tag, "
            "写到 ~/.catfish/expertise.yaml. **隐私边界**: yaml 留员工本机, "
            "中央 registry 只看 confirmed 后的 tag 字符串, 不看 evidence/aliases.\n\n"
            "✅ 调用场景:\n"
            "  - 员工说 '看我都会啥' / '更新我的专长黄页' / '抽一下专长'\n"
            "  - journal 累积 ≥1 周后第一次抽\n"
            "  - 周复盘后想刷新黄页 (新干的活进 tag)\n\n"
            "调完之后**必须**告诉员工有 N 个 tag 待 review, 让他用 catfish_confirm_expertise "
            "通过/拒/改名. 没 confirm 的 tag 不会进 BL-FED2.2 黄页.\n\n"
            "**参数**: max_tags (默认 20)"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "max_tags": {
                    "type": "integer",
                    "description": "最多抽多少个 tag (默认 20, 避免噪音).",
                },
            },
            "required": [],
        },
        "emoji": "📚",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_list_expertise",
        "description": (
            "看 ~/.catfish/expertise.yaml 当前所有专长 tag 及 status. "
            "可以按 status 过滤 (pending / confirmed / rejected).\n\n"
            "**参数**: status_filter (可选)"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status_filter": {
                    "type": "string",
                    "description": "过滤 status: 'pending' / 'confirmed' / 'rejected', 不填看全部.",
                    "enum": ["pending", "confirmed", "rejected"],
                },
            },
            "required": [],
        },
        "emoji": "📋",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-FED2.3 (5/12 鸿波拍板) 跨员工路由 ──
    {
        "name": "catfish_expert_consult",
        "description": (
            "★★★ 跨员工路由 — 给定专长 tag, 自动黄页查 + A2A 委托给懂的同事. "
            "BL-FED2.3 卖点: '员工问 X 怎么搞 → 鲶鱼自动找懂的同事问 → 流式返答案'.\n\n"
            "✅ 调用场景:\n"
            "  - 员工问 '资质审核怎么搞?' → expertise_tag='资质审核', question 透传员工原话\n"
            "  - 员工问 '@bob 怎么处理这种发票?' (指定人) → preferred_sub='bob@ffcs.cn'\n"
            "  - 员工问 '小李最近在忙啥' → 跟你无关, **不要**调本工具\n\n"
            "❌ 不该调用:\n"
            "  - 你自己能答的问题 (本机 LLM/skill 优先, 别什么都甩给同事)\n"
            "  - 没人懂的领域 (会返 ok=false 黄页空)\n"
            "  - 八卦/打听人 (走 ALLOW.md 会被拒, 别浪费配额)\n\n"
            "**自动选目标策略**:\n"
            "  1. preferred_sub 传了 → 必须问他 (不在线也强转)\n"
            "  2. 没传 → 排除你自己, 选第一个在线员工 (匹配按 BL-FED2.2 排序: 在线优先)\n"
            "  3. 全离线 → 返友好错误, 让员工换时间问 / 显式 preferred 强转\n\n"
            "**返参重点**:\n"
            "  - routed_to: 实际转给谁 (展示给员工 — '我帮你问了 bob@ffcs.cn')\n"
            "  - answer: 同事鲶鱼的回答 (流式合并后)\n"
            "  - matched_count / online_count: 黄页里多少候选 (帮员工建立信任)\n\n"
            "**ALLOW.md 拒答**: 对方机器自动拦截 (隐私/八卦/超授权), 透传拒答理由."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "expertise_tag": {
                    "type": "string",
                    "description": "想问的领域 tag (大小写不敏感, 例 '资质审核' / '外勤报销')",
                },
                "question": {
                    "type": "string",
                    "description": "员工原话或精炼后的问题 (1-500 字符), 会透传给同事鲶鱼",
                },
                "preferred_sub": {
                    "type": "string",
                    "description": "(可选) 指定问谁 SSO sub, 不传走自动路由",
                },
                "purpose": {
                    "type": "string",
                    "description": "(可选) 用途分类, ALLOW.md 用 (例 'work_question' / 'compliance_check')",
                },
                "context_hint": {
                    "type": "string",
                    "description": "(可选) 背景说明 — 一两句话告诉对方鲶鱼为什么问 (例 '客户 X 周三要交资质材料')",
                },
            },
            "required": ["expertise_tag", "question"],
        },
        "emoji": "📞",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_confirm_expertise",
        "description": (
            "员工 review 一个 expertise tag — 👍 通过 / 👎 拒 / ✏️ 改名 / 加同义词. "
            "只有 status=confirmed 的 tag 才会进 BL-FED2.2 黄页, 是隐私边界的关键阀门.\n\n"
            "✅ 调用场景:\n"
            "  - 员工看完 catfish_list_expertise 后说 '资质这个对的' → status=confirmed\n"
            "  - 员工说 '资质改成资质管理' → new_tag='资质管理'\n"
            "  - 员工说 '加个简称叫 EIS' → add_aliases=['EIS']\n"
            "  - 员工说 '不准确, 删了' → status=rejected\n\n"
            "**参数**:\n"
            "  - tag (必填): tag 名 (大小写不敏感)\n"
            "  - status: pending / confirmed / rejected\n"
            "  - new_tag: 改名 (2-20 字符)\n"
            "  - add_aliases: list[str], 加同义词"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "description": "tag 名 (大小写不敏感)"},
                "status": {
                    "type": "string",
                    "description": "新 status",
                    "enum": ["pending", "confirmed", "rejected"],
                },
                "new_tag": {
                    "type": "string",
                    "description": "改 tag 名 (例 '资质' → '资质管理'), 2-20 字符",
                },
                "add_aliases": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "加同义词 (例 ['EIS', '工程信息系统'])",
                },
            },
            "required": ["tag"],
        },
        "emoji": "✅",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_check_compliance",
        "description": (
            "扫一段文本 (邮件草稿 / 汇报材料) 的央国企合规风险 (ISO/审计/法务/财务). "
            "返 flag 列表含 severity/type/matched_keyword/suggestion. 关键词匹配, 第一版."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "要扫的文本"},
                "context": {"type": "string", "description": "(可选) 涉及哪个项目/客户"},
            },
            "required": ["content"],
        },
        "emoji": "⚠️",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_political_sensitivity_scan",
        "description": (
            "扫文本对相关人 (上级/平级/客户) 的政治敏感度. 第一版保守, 只 flag + 给 "
            "suggested_phrasings. senior tier + high severity 时 advisory_only=true, "
            "UI 渲染'提醒人工核对'而不是'建议改'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "文本"},
                "related_people": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "relation": {"type": "string"},
                        },
                    },
                    "description": "(可选) 涉及的人 [{name, relation}]",
                },
                "tier": {
                    "type": "string",
                    "enum": ["frontline", "mid", "senior"],
                    "description": "(可选) 员工职级",
                },
            },
            "required": ["content"],
        },
        "emoji": "🎯",
        "toolset": "catfish_native",
        "available": True,
    },
]
