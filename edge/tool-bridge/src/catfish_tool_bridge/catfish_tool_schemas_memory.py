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
    # 5/16 V3 (BL-MEMORY-FULL-HERMES): 默认记忆走 hermes memory tool, 本工具降为 edge case。
    # 经验比例: 100 个 chat 里 catfish_remember < 10 次, memory.add > 30 次; 反过来就是判断错了。
    {
        "name": "catfish_remember",
        "description": (
            "仅本次对话内有效的临时记事, 跨 session 会丢。"
            "员工让你记东西时默认用 memory(action='add', ...), 拿不准也用 memory。"
            "只在三种情况用本工具: 员工明说只在本次对话内记; 本次操作中反复要用的临时凭据引用或进度; "
            "纠正你在本次对话里的误解。"
            "情绪客套、你的推测、健康/财务/感情/政治等红线信息一律不记。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "snake_case, 1-100 字符",
                },
                "value": {
                    "type": "string",
                    "description": "1-1000 字符",
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
            "读员工跨 session 的长期画像 (writing_style / work_pattern / personality)。"
            "对话开始时调一次, 或员工问'你了解我吗'时调; 别每轮都调。"
            "locked=true 表示员工锁定, 不能再 propose; proposed_value 非空表示员工还没确认, 别当真; "
            "evidence_count 越大越可信。"
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "emoji": "👤",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_user_profile_propose",
        "description": (
            "从员工言行推断出画像 trait 时累积一条 evidence, 不立即写入。"
            "同值独立 evidence 满 3 次会返回 should_confirm, 你再用自然语言问员工, "
            "员工同意后才调 catfish_user_profile_confirm。"
            "单次行为别推断; evidence 须引用真实对话; 每 session 最多主动确认 1 次。\n"
            "field=取值: writing_style.tone=formal/casual/直接/委婉/幽默, .length_pref=短/中/长, "
            ".bullet_pref=列表/段落/混合; work_pattern.peak_hours=自由文本, "
            ".task_pref=列清单/看图表/纯文字/对照表, .review_pref=先看摘要/全量看/只看异常; "
            "personality.pace=急/缓, .feedback_style=大点拨/细节确认/结果导向, .deference=平等/尊重正式/随意。"
            "严禁 propose personal.* (健康/财务/感情等)。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "description": "字段名, 如 'writing_style.tone'"},
                "value": {"type": "string"},
                "evidence": {
                    "type": "string",
                    "description": "员工原话或具体对话上下文, 1-500 字",
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
            "把员工明确同意的画像 trait 落盘。触发: propose 后员工说'对/是的'; "
            "或员工在 Dashboard 画像卡里直接编辑。"
            "员工没明确同意不调 (沉默≠同意); 红线字段员工没亲口说, 不替他确认。"
            "同 field 再确认会覆盖, 旧值返回在 previous_value。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "field": {"type": "string"},
                "value": {"type": "string"},
                "locked": {"type": "boolean", "description": "默认 false; true=锁住, 之后拒绝 propose"},
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
            "按员工明确要求清除画像: 给 field 只清该字段 (如 'personality.pace'), 不给则清全部。"
            "员工没明说绝不自己清。"
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
            "读员工文书风格指纹 (句长/高频词/标点/列表偏好/样本句), 用来模仿员工语气写正式文档。"
            "写汇报/周报/立项材料前, 或员工说'按我的风格写'时调; 普通闲聊不调。"
            "返回 exists=false 表示还没生成, 调 catfish_style_fingerprint_refresh。"
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "emoji": "✍️",
        "toolset": "catfish_native",
        "available": True,
    },
    # 实现细节: 数据源是 local_search 索引 (~/.catfish/search.db), 范围 = Companion 「搜索范围」卡;
    # 收 .md/.txt/.docx/.pdf/.pptx 中文占比够高的, 跳过 <200 字, 取 mtime 最新 500 篇;
    # 时间衰减 30 天 1.0 / 90 天 0.5 / 180 天 0.25 / 更老 0.1。返回带 funnel 和 total_docs=0 时的 hint。
    {
        "name": "catfish_style_fingerprint_refresh",
        "description": (
            "从本地搜索索引 (员工在 Companion 配的搜索范围) 重建文书风格指纹, 无参数。"
            "员工要求更新对其写作风格的认识、首次 onboarding、或新增较多文档后调; "
            "别每次写文档前都调。"
            "返回 error='index_unavailable' 表示还没建本地索引, 要引导员工先建索引, 别说'没找到文档'。"
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "emoji": "🔄",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_style_fingerprint_clear",
        "description": (
            "清除文书风格指纹。仅在员工明说'别用我的历史风格了/重新认识我的写作'时调, 别自作主张。"
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "emoji": "🗑️",
        "toolset": "catfish_native",
        "available": True,
    },
    # ============================================================
    # BL-MEMORY-DEDUPE-COMPRESS (5/17 凌晨, P2 #1+#2 lite 版)
    # dedupe: jieba 分词 + Jaccard 相似度 (默认 ≥0.6) 判重, 返回 [{entries, suggested_merge, similarity}]。
    # compress: limit 为 USER 1375 / MEMORY 2200 chars, >80% 时提议;
    #   返回 {usage_pct, oldest_n_entries, suggested_summary, would_save_chars}。
    # ============================================================
    {
        "name": "catfish_memory_dedupe",
        "description": (
            "扫描 hermes 记忆 (USER.md / MEMORY.md) 找语义重复条目, 返回合并建议, 本身不改盘。"
            "员工说'帮我整理记忆/记忆看着乱', 或条目较多时调。"
            "建议须先给员工确认, 再用 memory(action='remove'/'replace') 修改, 绝不自己删改。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "enum": ["user", "memory", "both"],
                    "description": "扫哪个 hermes 记忆文件",
                    "default": "both",
                },
                "threshold": {
                    "type": "number",
                    "description": "相似度阈值 0.0-1.0, 越高越严",
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
            "检查 hermes 记忆文件用量, 超过上限 80% 时提议把最老的 N 条合并成一条摘要, "
            "只返回建议, 本身不改盘。员工说'记忆满了/画像太多了'或用量告警时调。"
            "摘要须先给员工确认, 绝不自己执行压缩。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "enum": ["user", "memory"],
                    "description": "压哪个 hermes 记忆文件",
                },
                "oldest_n": {
                    "type": "integer",
                    "description": "取最老的 N 条作压缩候选",
                    "default": 5,
                },
            },
            "required": ["target"],
        },
        "emoji": "🗜️",
        "toolset": "catfish_native",
        "available": True,
    },
    # 数据源 ~/.catfish/decisions.jsonl, 第一版 substring 匹配。
    {
        "name": "catfish_recall_decision_history",
        "description": (
            "按主题 (可加相关人/项目) 检索员工过往拍板的决策口径。"
            "员工问'之前怎么定的/上次对 X 的口径是什么', 或你给建议、起草回复涉及已有决定时先调, "
            "让新建议跟历史决定一致、不自相矛盾。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "主题关键词, 子串匹配"},
                "person": {"type": "string"},
                "project": {"type": "string"},
                "limit": {
                    "type": "integer",
                    "description": "默认 5, 上限 50",
                    "default": 5,
                },
            },
            "required": ["topic"],
        },
        "emoji": "🔍",
        "toolset": "catfish_native",
        "available": True,
    },
    # 台账 ~/.catfish/advisor_closed.jsonl, 跟卡片「完成」按钮共用。早安生成卡片时不读长期记忆和
    # journal (只读本周邮件/日程/任务库 + 台账), 所以改 advisor_cache.json / distilled_facts.md /
    # journal 关不掉卡, 删缓存反而让系统忘记关过。匹配: 同 uid、标题去括号后相同或互相包含、
    # 或来源邮件主题 (去【提醒】/回复:/转发: 前缀) 相同。done/ignored 保留 30 天, snoozed 到明天 0 点。
    {
        "name": "catfish_advisor_close_task",
        "description": (
            "把早安卡片上的一件事标记为完成/不用管/今天先不看, 以后不再出现 (与卡片「完成」按钮同一份台账)。"
            "员工在聊天里说某张卡片的事已做完/已填好/办完了/不用管/别再提醒时直接调, 别让员工自己去点按钮。"
            "别为了关卡片去改记忆、journal 或缓存文件, 那样无效。"
            "返回 matched_card=null 时, 用 current_cards 里的准确标题重试; 员工反悔用 reopen=true。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "卡片原标题 (或员工说的那件事)"},
                "task_uid": {"type": "string", "description": "卡片 taskUid, 知道就填, 比标题准"},
                "status": {
                    "type": "string", "enum": ["done", "ignored", "snoozed"], "default": "done",
                    "description": "done=已完成; ignored=不用管/误报; snoozed=今天先不看",
                },
                "reopen": {"type": "boolean", "default": False, "description": "true=撤销之前的关闭"},
                "note": {"type": "string", "description": "一句话依据"},
            },
            "required": ["title"],
        },
        "emoji": "✅",
        "toolset": "catfish_native",
        "available": True,
    },
    # 清理范围: ~/.catfish/distilled_facts.md (按行), employee_journal.md (按 `## ` 段),
    # ~/.hermes/memories/*.md (按行), decisions.jsonl (按行), profile.json 的 keyPeople/keyProjects,
    # advisor_cache.json (unlink 触发重算)。永不动 profile_hints.md / session_goal.txt / ~/.hermes/state.db。
    # 真删前备份到 .forget_backup/<ts>/, 误删 24h 内可手工恢复。不留排除清单, 同名新人会从邮件/日历重新学。
    {
        "name": "catfish_forget_about",
        "description": (
            "跨所有记忆存储物理删除某关键词 (人/项目/客户) 的相关内容: 蒸馏事实、journal、hermes 记忆、"
            "决策记录、画像里的关键人/项目, 并让早安缓存重算。"
            "只在员工明说'忘了X/把X相关的全删掉'时调; 没明说绝不主动清, 请求模糊先追问具体关键词。"
            "必须两步: 先 confirm=false 预演, 把各处命中数报给员工; 员工明确同意后再 confirm=true 真删 (自动备份)。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "要清的人名/项目名/客户名, 2-100 字",
                    "minLength": 2,
                    "maxLength": 100,
                },
                "confirm": {
                    "type": "boolean",
                    "description": "false(默认)=只统计; true=真删, 须员工先同意",
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
            "汇总小鲶今天的活动: 对话数、工具调用数、新增/更新的 memory、新增 skill、token 消耗。"
            "员工问'今天学了什么/今天做了啥/今日活动'时调这个, 不要用 session_search 或 memory_recall。"
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
