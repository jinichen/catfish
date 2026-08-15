"""记忆 / 员工画像 / 文风指纹 / 今日汇总 —— 13 个 tool 的 JSON Schema。

8/15 从 catfish_tool_schemas.py 拆出: 那个文件是**一整个 3362 行的
列表字面量** (78 个 schema), 越过 800 红线 4 倍。零逻辑、零变量引用,
所以按类别切成 8 份, 由 catfish_tool_schemas.py 拼回去。

加新 tool: schema 加到对应类别文件, impl 加 catfish_tools.py 或子 module,
dispatch 加 catfish_tools._dispatch_native_inner —— **dispatch 不重组**
(这条红线是 5/20 拆分时定的, 见 catfish_tool_schemas.py 文件头)。
"""
from __future__ import annotations

from typing import Any, Dict, List


MEMORY_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "catfish_remember",
        "description": (
            "⚠️ **edge case 工具**, 跨 session **必失忆**. 绝大多数情况用 `memory(action='add', ...)`, "
            "**不**用本工具.\n\n"
            "5/16 V3 (鸿波拍板 BL-MEMORY-FULL-HERMES): **默认用 memory**. catfish_remember 是 "
            "**罕见 edge case**, 只在 3 种场景:\n"
            "  1. 员工**明说**'只本 session 内' → catfish_remember\n"
            "  2. 当下操作凭据 (本 session 5 轮内反复用, 不该跨 session 持久):\n"
            "     - 'EIS 密码 ref 是 keychain://eis_x' (操作完不该长期记)\n"
            "     - '本次教学 step 3 暂停' (教学完不该长期记)\n"
            "  3. 员工纠正你的 in-session 误解: 'tool-bridge 死了不是我请求错'\n\n"
            "**任何**其它'记下 / 记一下 / 帮我记' → memory(action='add', ...), **不**用本工具:\n"
            "  - 员工说 '记下要给徐舒淇单页' → memory (task 跨 session)\n"
            "  - 员工说 '我领导张总很严' → memory (人物长期)\n"
            "  - 员工说 '我老婆叫小芳' → memory\n"
            "  - 员工说 '我习惯列表型公文' → memory\n"
            "  - 员工说 '我们 4/29 拍板投资策略' → memory\n"
            "  - 你不确定 → **memory** (永久不丢比临时丢强, 保险)\n\n"
            "❌ 任何工具都不该记的:\n"
            "  - 情绪/客套 ('好烦' / '辛苦') → 不是事实\n"
            "  - 推测的 → 必须是员工**明确**说的硬事实\n"
            "  - 红线 (健康 / 财务 / 感情 / 政治) → 永不记\n\n"
            "key: snake_case 1-100 字符. value: 1-1000 字符.\n"
            "**比例自检**: 你 100 个 chat 应该 catfish_remember < 10, memory.add > 30. "
            "反过来你判断错了."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "事实的 key, snake_case, 1-100 字符",
                },
                "value": {
                    "type": "string",
                    "description": "事实的 value, 1-1000 字符",
                },
            },
            "required": ["key", "value"],
        },
        "emoji": "📌",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-MM7 user_profile (5/6) — 跨 session 长期画像, 跟 catfish_remember 区分 ──
    {
        "name": "catfish_user_profile_get",
        "description": (
            "★ 读员工长期画像 (writing_style / work_pattern / personality 等). "
            "**跨 session 持久**, 跟 catfish_remember 不同 — 那个是 session 内硬事实.\n\n"
            "✅ 调用时机: chat 开始时调一次 (拿当前画像注入对话风格), 或员工问 "
            "'你怎么看我' / '你了解我吗' 时.\n\n"
            "返回字段含 evidence_count / locked / proposed_value, 帮你判断:\n"
            "  - locked=true: 员工锁了, 不能 propose 改\n"
            "  - proposed_value 非空: 员工还没 confirm, 别拿这个值当真\n"
            "  - evidence_count: 越大越可信\n\n"
            "❌ 别在每次回复都调 — chat 开始 1 次就够, 后续从 system prompt 拿."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "emoji": "👤",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_user_profile_propose",
        "description": (
            "★ 看到员工言行能推断出 trait 时, 调这个累积 evidence (不立即写)\n"
            "**累积 ≥ 3 次同 value 的独立 evidence** 后, 工具会返 'should_confirm', "
            "你才该跟员工自然语言确认 ('我感觉你写汇报偏直接, 对吗?'); 员工说同意, "
            "你才调 catfish_user_profile_confirm 落盘.\n\n"
            "✅ 允许的 field (枚举 value):\n"
            "  - writing_style.tone: formal / casual / 直接 / 委婉 / 幽默\n"
            "  - writing_style.length_pref: 短 / 中 / 长\n"
            "  - writing_style.bullet_pref: 列表 / 段落 / 混合\n"
            "  - work_pattern.peak_hours: 自由文本 (例 '9-12 / 14-18')\n"
            "  - work_pattern.task_pref: 列清单 / 看图表 / 纯文字 / 对照表\n"
            "  - work_pattern.review_pref: 先看摘要 / 全量看 / 只看异常\n"
            "  - personality.pace: 急 / 缓\n"
            "  - personality.feedback_style: 大点拨 / 细节确认 / 结果导向\n"
            "  - personality.deference: 平等 / 尊重正式 / 随意\n\n"
            "❌ 红线字段 (严禁 propose, 员工自己 confirm 才能存):\n"
            "  - personal.health / .financial / .relationship / .political / .religious / .family\n\n"
            "❌ 不该调用:\n"
            "  - 员工一次行为就推断 ('员工今天打字快 → personality.pace=急') — 太武断, 累 3 次再说\n"
            "  - 编造 evidence — 必须 quote 真实对话片段\n"
            "  - 评论员工生活 — 红线\n\n"
            "频率: 每 session ≤ 1 次主动 propose (满阈值后), 防 spam."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "description": "字段名, 例 'writing_style.tone'"},
                "value": {"type": "string", "description": "推断的值"},
                "evidence": {
                    "type": "string",
                    "description": "本次 evidence — quote 员工原话或具体对话上下文 (1-500 字)",
                },
            },
            "required": ["field", "value", "evidence"],
        },
        "emoji": "🔍",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_user_profile_confirm",
        "description": (
            "★ 把员工确认过的画像 trait 落盘. 两种触发:\n"
            "  1. propose 后员工自然语言说同意 ('对', '是', '说得对'), 你调这个落盘\n"
            "  2. 员工 Dashboard UserProfileCard 直接编辑 (UI 触发)\n\n"
            "locked=true: 员工要求'锁住别再改' — 之后 propose 此字段会被拒\n"
            "覆盖语义: 同 field 再 confirm 会覆盖, 旧值返在 previous_value\n\n"
            "❌ 不该调用:\n"
            "  - 员工没明确说同意 — 别假定 (silence ≠ consent)\n"
            "  - 红线字段员工没显式说 — 别帮员工 confirm 健康/感情/政治/宗教等"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "field": {"type": "string"},
                "value": {"type": "string"},
                "locked": {"type": "boolean", "description": "默认 false, true=锁住不再 propose"},
            },
            "required": ["field", "value"],
        },
        "emoji": "✅",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_user_profile_clear",
        "description": (
            "★ 清除画像. field 给值 = 清那一个; field 为空 = 清全部.\n\n"
            "✅ 调用场景:\n"
            "  - 员工说 '清掉你对我的所有印象' → clear({}) 全部清\n"
            "  - 员工说 '别记我急性子那条' → clear({field: 'personality.pace'})\n\n"
            "❌ 不该调用:\n"
            "  - 自作主张 — 必须员工显式说"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "field": {
                    "type": "string",
                    "description": "字段名, 空字符串 = 清全部",
                },
            },
            "required": [],
        },
        "emoji": "🗑️",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-MM8 style_fingerprint (5/6) — 写文档时模仿员工历史风格 ──
    {
        "name": "catfish_style_fingerprint_get",
        "description": (
            "★ 读员工文书风格指纹 — 写汇报/周报/立项前调一次, 拿到风格描述\n"
            "(平均句长 / 高频词 / 标点偏好 / 列表 vs 散文 / 样本句) 注入 system prompt,\n"
            "让 LLM 模仿员工历史文档语气. 跟 user_profile 互补 (前者显式 trait, 这个隐式特征).\n\n"
            "✅ 调用时机:\n"
            "  - leadership-briefing / weekly-report / project-approval skill render 前\n"
            "  - 员工说 '帮我按我习惯的风格写一份...' 时\n\n"
            "❌ 别在 chat 普通问答时调 — 风格指纹是给写正式文档用的, 闲聊不需要.\n\n"
            "返回 exists=false 表示员工还没生成过 fingerprint, 调 refresh 触发一次扫描."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "emoji": "✍️",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_style_fingerprint_refresh",
        "description": (
            "★ 从本地搜索索引重建文书风格指纹. 无参数.\n\n"
            "数据源: local_search 索引 (~/.catfish/search.db) — 目录范围就是员工在\n"
            "Companion '📂 搜索范围' 卡里配的那些, 不用也不能在这里另指目录.\n"
            "收: .md/.txt/.docx/.pdf/.pptx 里中文占比够高的 (挡代码和英文技术文档).\n"
            "约束: 跳过 < 200 字; 取 mtime 最新的 500 篇.\n"
            "时间衰减: 30 天内权重 1.0, 90 天 0.5, 180 天 0.25, 更老 0.1.\n\n"
            "✅ 调用时机:\n"
            "  - 员工说 '更新一下你对我写作风格的认识'\n"
            "  - 员工写完一份新汇报后, 主动 refresh (10-20 个文档变化时)\n"
            "  - 第一次启动 (员工 onboarding 时)\n\n"
            "❌ 频率: 不要每次写文档前都 refresh — 文档没变前指纹一样, 白跑一趟.\n"
            "    一周一次或员工显式要求时再调.\n\n"
            "返回里带 funnel (索引里的文书类 → 太短 → 中文占比不足 → 最终留下) 和\n"
            "total_docs=0 时的 hint. error='index_unavailable' 表示员工还没建过\n"
            "本地索引 — 这时候别说'没找到文档', 要让他先去建索引."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "emoji": "🔄",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_style_fingerprint_clear",
        "description": (
            "★ 清掉文书风格指纹 (员工 reset 用).\n\n"
            "✅ 员工说 '别用我的历史风格了' / '从零开始重新认识我的写作'.\n"
            "❌ 自作主张别清."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "emoji": "🗑️",
        "toolset": "catfish_native",
        "available": True,
    },
    # ============================================================
    # BL-MEMORY-DEDUPE-COMPRESS (5/17 凌晨, P2 #1+#2 lite 版)
    # ============================================================
    {
        "name": "catfish_memory_dedupe",
        "description": (
            "扫描 hermes USER.md / MEMORY.md 找语义重复的 entry, 用 jieba 分词 + "
            "Jaccard 相似度 ≥ 0.6 判定. 返**建议列表**给你 (LLM), 你跟员工确认后才"
            "用 memory(action=remove) / memory(action=replace) 真改盘.\n\n"
            "**何时调**:\n"
            "- 仪表盘 '我的 hermes memory' 卡显示 entries 数 ≥ 10 时主动调一次\n"
            "- 员工说 '我的 memory 看着乱' / '帮我整理一下记忆' 时调\n"
            "- audit 日志显示 chars 涨但实际信息没增多 (BL-MM1 narrate 嫌疑) 时\n\n"
            "**输入**:\n"
            "  target: 'user' | 'memory' (扫哪个文件, 不传扫两个)\n"
            "  threshold: 0.0-1.0 (默认 0.6, 越高越严)\n\n"
            "**输出**: list of {entries: [...], suggested_merge: '...', similarity: 0.x}\n\n"
            "**绝不**: 自己删 / 自己 replace. 必须先回员工 review."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "enum": ["user", "memory", "both"],
                    "description": "扫哪个 hermes memory 文件",
                    "default": "both",
                },
                "threshold": {
                    "type": "number",
                    "description": "Jaccard 相似度阈值 (0.0-1.0). 默认 0.6.",
                    "default": 0.6,
                },
            },
            "required": [],
        },
        "emoji": "🔍",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_memory_compress",
        "description": (
            "扫描 hermes USER.md / MEMORY.md 看 chars 使用率, 如果 > 80% limit "
            "(USER 1100/1375 或 MEMORY 1760/2200), 提议把**最老的 N 条**合并成"
            "一条摘要 entry. 返建议给你 (LLM), 跟员工确认后才真改盘.\n\n"
            "**何时调**:\n"
            "- audit script 报警 chars 接近 limit 时\n"
            "- 员工说 '记忆满了 / 记忆要爆 / 我的画像太多了' 时\n\n"
            "**输入**:\n"
            "  target: 'user' | 'memory' (压哪个文件)\n"
            "  oldest_n: 最老的 N 条作为压缩候选 (默认 5)\n\n"
            "**输出**: {usage_pct, oldest_n_entries, suggested_summary, would_save_chars}\n\n"
            "**绝不**: 自己执行 add+remove 压缩, 必须先回员工 review summary 文本."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "enum": ["user", "memory"],
                    "description": "压哪个 hermes memory 文件",
                },
                "oldest_n": {
                    "type": "integer",
                    "description": "选最老的 N 条压缩 (默认 5)",
                    "default": 5,
                },
            },
            "required": ["target"],
        },
        "emoji": "🗜️",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_recall_decision_history",
        "description": (
            "按 topic + 可选 person/project 检索 ~/.catfish/decisions.jsonl 过往决策口径. "
            "让现在的建议跟历史一致 (不背离). substring 匹配, 第一版."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "主题关键词 (必填)"},
                "person": {"type": "string", "description": "(可选) 相关人"},
                "project": {"type": "string", "description": "(可选) 相关项目"},
                "limit": {
                    "type": "integer",
                    "description": "最多返几条 (默认 5, 上限 50)",
                    "default": 5,
                },
            },
            "required": ["topic"],
        },
        "emoji": "🔍",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_forget_about",
        "description": (
            "**跨源物理清除**特定关键词的记忆 (人/项目/客户/任何标识). 一次扫干净 5 类存储:\n"
            "  - ~/.catfish/distilled_facts.md (按行删)\n"
            "  - ~/.catfish/employee_journal.md (按 `## ` 段删整段)\n"
            "  - ~/.hermes/memories/*.md (按行删)\n"
            "  - ~/.catfish/decisions.jsonl (按行删 JSON)\n"
            "  - ~/.catfish/profile.json 的 keyPeople / keyProjects (按字段删项)\n"
            "  - ~/.catfish/advisor_cache.json (unlink, 触发 advisor 下次重算)\n\n"
            "**何时调**:\n"
            "  - 员工明说 '忘了老李' / '老李是测试数据, 清干净' / '把张三相关全删掉'\n"
            "  - 员工纠正 '这条信息进错了, 别再蒸馏' 且涉及具体人/项目\n"
            "  - 员工换岗后说 '之前 XX 项目的全清掉'\n\n"
            "**何时不要调**:\n"
            "  - 员工没明说 → 永远不主动清\n"
            "  - 模糊请求 ('删点东西' / '清一下') → 反问到具体关键词\n"
            "  - keyword < 2 字 → 拒 (误伤面太大)\n\n"
            "**安全协议** (强制 2 步):\n"
            "  1. **第一次**调一定 confirm=False (dry_run), 拿回 removed 计数报员工: "
            "'扫到 distilled_facts 3 行, journal 12 段, decisions 0 条. 确认删?'\n"
            "  2. 员工**明确**点头 ('确认' / '删' / '是的') → 再调一次 confirm=True 真删\n\n"
            "**永远不动**: profile_hints.md (员工显式标的), session_goal.txt (太短), "
            "~/.hermes/state.db (内容已抽到 journal, 清 journal 够).\n\n"
            "**所有真删都带 .forget_backup/<ts>/ 备份**, 误删 24h 内可手工恢复.\n\n"
            "**未来反弹**: 真清干净, 不留排除清单. 如果未来真有同名人 (新同事老李), "
            "从邮件/日历重新学习, 自然进系统."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "要清的关键词 (人名/项目名/客户名/标识). 至少 2 字, 不超 100 字.",
                    "minLength": 2,
                    "maxLength": 100,
                },
                "confirm": {
                    "type": "boolean",
                    "description": (
                        "True = 真删并备份. False (默认) = dry-run, 只统计不动文件. "
                        "**默认先 False 报员工**, 员工点头后再 True."
                    ),
                    "default": False,
                },
            },
            "required": ["keyword"],
        },
        "emoji": "🧹",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_today_summary",
        "description": (
            "看小鲶今天学到了什么:今天的对话数、工具调用次数、新增/更新的 "
            "memory 条目、新增的 skill、token 消耗总量。当员工问"
            "「今天学了什么」「今天做了啥」「今日活动」「今天有什么新进展」"
            "「小鲶今天怎么样」之类的问题时调用这个 tool, 而不是 "
            "session_search 或 memory_recall —— 那两个是给你自己翻历史的, "
            "回答员工的「今日」相关问题就用 catfish_today_summary。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "emoji": "📊",
        "toolset": "catfish_native",
        "available": True,
    },
]
