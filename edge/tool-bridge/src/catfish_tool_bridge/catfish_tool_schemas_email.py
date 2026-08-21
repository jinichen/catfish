"""邮件读取与起草 —— 6 个 tool 的 JSON Schema。

8/15 从 catfish_tool_schemas.py 拆出: 那个文件是**一整个 3362 行的
列表字面量** (78 个 schema), 越过 800 红线 4 倍。零逻辑、零变量引用,
所以按类别切成 8 份, 由 catfish_tool_schemas.py 拼回去。

加新 tool: schema 加到对应类别文件, impl 加 catfish_tools.py 或子 module,
dispatch 加 catfish_tools._dispatch_native_inner —— **dispatch 不重组**
(这条红线是 5/20 拆分时定的, 见 catfish_tool_schemas.py 文件头)。
"""
from __future__ import annotations

from typing import Any, Dict, List


EMAIL_TOOLS: List[Dict[str, Any]] = [
    # ── BL-EMAIL-SEARCH-TOOL (5/18 鸿波"对话里检索没搜到邮件") ──────────
    {
        "name": "catfish_email_search",
        "description": (
            "★★★ 全文搜员工本地邮件 (Apple Mail + Foxmail 跨客户端跨账号). "
            "**chat-first 范式打通邮件检索** — 之前 LLM 只能搜文件 (local_search) "
            "+ 历史对话 (catfish_search_sessions), 邮件这条漏了, 现在补上.\n\n"
            "✅ 调用场景:\n"
            "  - 员工问 '上个月那封工资条邮件' → query='工资条'\n"
            "  - 员工问 '张三给我发的那个合同' → query='张三 合同'\n"
            "  - 员工问 '微信团队的通知邮件' → query='微信团队'\n"
            "  - 任何 '那封/上次/之前/前几天 X 邮件' 类索引诉求\n\n"
            "❌ 不调用:\n"
            "  - 找文件 → local_search\n"
            "  - 找历史对话 → catfish_search_sessions\n"
            "  - 列收件箱 / 看未读 → 让员工去 Companion 邮件 tab\n\n"
            "返参:\n"
            "  - matches: 命中邮件 list, 每条 {id, adapter, account, subject, "
            "    sender, date, is_read, snippet (前 200 字摘要)}\n"
            "  - count: 总命中数\n"
            "  - summary: 一句话归纳 (按 adapter 分组), 念给员工.\n"
            "  - ok: false 时含 error 字段说明原因 (CLI 没装 / 超时 / 等)\n\n"
            "🔒 隐私: 不读邮件正文 (只看 snippet), 不上行中央, 不跨员工. "
            "邮件正文红线: 永不缓存."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜的关键字 (LIKE 字面匹配, 中文 OK, 多关键字空格分)",
                },
                "folder": {
                    "type": "string",
                    "description": "搜哪个文件夹: '*' = 跨所有 (默认), 'Inbox' = 仅收件箱",
                },
                "account": {
                    "type": "string",
                    "description": "指定账号地址 (默认搜所有账号; 多账号场景缩小范围用)",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返多少封 (默认 20, 上限 50)",
                },
            },
            "required": ["query"],
        },
        "emoji": "📧",
        "toolset": "catfish_native",
        "available": True,
    },
    # P3.5.194 (7/7 鸿波军规审判): 员工主权授权读邮件正文 —— 补齐 email 三件套
    # (search 找 → read 拿正文/附件元 → attachment 取附件).
    {
        "name": "catfish_email_read",
        "description": (
            "★★★ 读单封邮件全文 + 附件元数据 (Apple Mail + Foxmail 跨客户端).\n\n"
            "配合 catfish_email_search 使用: search 拿 email_id → read 拿完整正文.\n\n"
            "✅ 调用场景 (员工必须**明确指令**才调, 不自动读):\n"
            "  - 员工说 '读一下林莹那封邮件的正文'\n"
            "  - 员工说 '把那封邮件里的附件都列出来'\n"
            "  - 员工说 '看看那封邮件里说什么'\n"
            "  - 员工需要对比邮件正文/附件跟手上文件是否一致\n\n"
            "❌ 不自动调用:\n"
            "  - 员工只问 '有没有 X 邮件' → catfish_email_search 就够\n"
            "  - 员工没明确说要读正文 → 不主动读 (员工主权 default)\n"
            "  - 已经从 search 的 snippet 里能答的 → 不重复调\n\n"
            "🔒 员工主权约束:\n"
            "  - 邮件正文只在员工本次 chat 上下文可见, 用完就走\n"
            "  - **禁止**主动把邮件正文塞进 catfish_wiki_ingest / memory 蒸馏管道 (除非员工明确说'把这封邮件入库')\n"
            "  - 每次读一封, 不批量读, 防勒索 prompt injection\n\n"
            "返参:\n"
            "  - subject / sender / recipients / cc / date / folder / adapter / account\n"
            "  - body_text: 完整纯文本正文 (超 40k 字截断, body_text_truncated=true)\n"
            "  - has_attachments / attachments: [{filename, size_bytes, content_type}]\n"
            "  - attachments_count: 附件数量 (方便 LLM 语义决策 '有 3 个附件, 要不要取?')\n"
            "  - ok=false 时 error 字段说明原因"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "email_id": {
                    "type": "string",
                    "description": "邮件 id (从 catfish_email_search 返的 matches[i].id 拿, 含 adapter 前缀如 'foxmail-mac|...' 或 'apple_mail|...')",
                },
                "mark_read": {
                    "type": "boolean",
                    "description": "读完自动标已读 (默认 true, 跟主流邮件客户端一致). 只是想看不改状态传 false.",
                },
            },
            "required": ["email_id"],
        },
        "emoji": "📖",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_email_attachment",
        "description": (
            "★★★ 导出邮件附件到本地 tmp, 返 path (员工可点开 or LLM 后续入库).\n\n"
            "配合 catfish_email_read 使用: read 拿附件列表 → attachment 取具体一个.\n\n"
            "✅ 调用场景 (员工必须**明确指令**才调):\n"
            "  - 员工说 '把邮件里的 2024企业所得税.pdf 下下来'\n"
            "  - 员工说 '取一下那封邮件的附件'\n"
            "  - 员工需要对比附件内容 or 入库 wiki\n\n"
            "❌ 不自动调用:\n"
            "  - 员工没明确要附件 → 不主动取\n"
            "  - 一次一个附件, 不批量取 (员工主权 default)\n"
            "  - 大附件 (>10MB) 前先问员工是否确定要取\n\n"
            "🔒 员工主权约束:\n"
            "  - 导出到 tmp 目录 (系统自动清理)\n"
            "  - **禁止**主动 catfish_wiki_ingest 入库 (除非员工明确说'入库')\n"
            "  - 员工可直接用 path 在 Companion UI 里点开\n\n"
            "返参:\n"
            "  - path: 导出后的本地文件绝对路径 (供员工点开; 前端会自动渲染成可点链接)\n"
            "  - filename: 原附件文件名\n"
            "  - size_bytes: 文件大小 (磁盘 stat, 帮 LLM 判断是否要入库)\n"
            "  - ok=false 时 error 字段说明 (附件不存在 / CLI 失败 / 超时)"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "email_id": {
                    "type": "string",
                    "description": "邮件 id (从 catfish_email_search 或 catfish_email_read 拿)",
                },
                "filename": {
                    "type": "string",
                    "description": "附件文件名 (从 catfish_email_read 返的 attachments[i].filename 挑, 一次一个)",
                },
            },
            "required": ["email_id", "filename"],
        },
        "emoji": "📎",
        "toolset": "catfish_native",
        "available": True,
    },
    # ─── BL-ADVISOR (5/21 Phase 7): 6 个智能参谋 tool ─────────────────────
    # 设计稿: docs/CATFISH-ADVISOR-DESIGN.md §4
    # 5/21 鸿波: catfish 绝不代行, 只起草到 outputs/ + 给选项. tool 只做 IO,
    # LLM 主调用方 generate 内容传给 tool. tool 不二次调 LLM (简版).
    {
        "name": "catfish_draft_email_reply",
        "description": (
            "起草邮件回信草稿到 ~/.catfish/outputs/<today>/reply-*.md, 不替员工发.\n\n"
            "P3.5.40 (6/18 鸿波 audit huashu-design 后催 'Junior Designer 早 show'):\n"
            "  支持两阶段 phase 字段, 防 LLM 凭空造员工没说的细节 (例 '上次电话提的预算 800 万').\n\n"
            "✅ phase='assumptions' (推荐先调): LLM 列出**不确定项 questions** 给员工答,\n"
            "  + assumptions (已假设的) + outline (计划结构) + 可选 content (草稿初稿).\n"
            "  存 reply-{rec}-{tone}-questions.md, 员工 catch 早期错误后, 再 phase='final' 调一次.\n"
            "  调用场景: 涉及具体数字 / 关系人 / 历史决定时. 涉及董事长 / 客户名 / 项目细节时.\n\n"
            "✅ phase='final' (默认, 老 caller 兼容): content 必填, 直接存 reply-{rec}-{tone}.md.\n"
            "  调用场景: 内容简单确定 (例 '感谢您的反馈, 我们会跟进') / 员工已经答完 questions.\n\n"
            "多口径 = 不同 tone 各调一次. 每个 tone 可以 assumptions → final 两次."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tone": {
                    "type": "string",
                    "enum": ["strict", "balanced", "friendly", "formal", "urgent", "hold"],
                    "description": "口径风格. strict=不松口, balanced=平衡, hold=暂缓.",
                },
                "thread_id": {"type": "string", "description": "邮件 thread id (元数据)"},
                "recipient": {"type": "string", "description": "收件人"},
                "subject": {"type": "string", "description": "邮件主题"},
                "phase": {
                    "type": "string",
                    "enum": ["assumptions", "final"],
                    "description": (
                        "P3.5.40 起 — 'assumptions': 先列 questions 给员工答; "
                        "'final' (默认): 直接写正文存盘"
                    ),
                },
                "content": {
                    "type": "string",
                    "description": (
                        "LLM generate 的回信正文. phase=final 必填. "
                        "phase=assumptions 时可空 / 可放草稿初稿 (员工 catch 后 refine)"
                    ),
                },
                "questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "phase=assumptions 必填. LLM 列需要员工答的不确定项. "
                        "例 '上次电话提的预算具体数字' / '是否要 cc 张主任' / '客户公司全称'"
                    ),
                },
                "assumptions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "phase=assumptions 可选. LLM 已假设的内容 (员工 catch 这些对不对). "
                        "例 '默认假设员工要 hold 这单' / '默认假设项目时间表是 9 月底'"
                    ),
                },
                "outline": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "phase=assumptions 可选. LLM 计划的回信结构. "
                        "例 '1. 致谢 2. 确认 3 点 3. 提下次会议'"
                    ),
                },
                "compliance_notes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "(可选) check_compliance 跑出的合规提示",
                },
            },
            "required": ["tone", "thread_id", "recipient", "subject"],
        },
        "emoji": "✉️",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_draft_meeting_brief",
        "description": (
            "起草会议汇报材料 brief 到 ~/.catfish/outputs/<today>/meeting-brief-*.md. "
            "LLM generate 好 markdown brief, 标 highlighted_uncertain 让员工开会前确认."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "日历 event id"},
                "event_title": {"type": "string", "description": "会议标题"},
                "content": {"type": "string", "description": "LLM generate 的 brief 正文 (markdown)"},
                "highlighted_uncertain": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "(可选) 待员工确认的数字/内容点",
                },
            },
            "required": ["event_id", "event_title", "content"],
        },
        "emoji": "📄",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_compose_followup_list",
        "description": (
            "起草项目催办名单 + 多种沟通口径 → ~/.catfish/outputs/<today>/followup-*.md. "
            "LLM 已 generate 含多人/多 tone 的 markdown."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "项目名"},
                "decision_ref": {"type": "string", "description": "(可选) 哪次会议拍的"},
                "content": {"type": "string", "description": "LLM generate 的催办名单 markdown"},
            },
            "required": ["project", "content"],
        },
        "emoji": "📨",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_email_create_draft",
        "description": (
            "把**员工已点头的定稿**放进邮件客户端草稿箱 (Mail.app Drafts)。"
            "**只建草稿, 绝不发送** —— 发送动作永远由员工在客户端里自己点, "
            "这是能力边界不是约定: 系统根本没有发送工具。\n\n"
            "✅ 调用场景:\n"
            "  - 对话里把回信文案改定了, 员工说'就这样/可以/发吧' → 落草稿箱, "
            "然后告诉员工: 草稿在草稿箱, 核对后自己点发送\n"
            "  - 回复某封邮件时**必传 in_reply_to** (原邮件 id), 客户端才能串上 thread\n\n"
            "❌ 不该调用:\n"
            "  - 文案还没给员工看过 / 员工没点头 —— 先用 catfish_draft_email_reply "
            "出稿讨论, 定了再落\n"
            "  - 员工说'发吧'≠替他发: 落草稿箱后要明确告诉员工「没有发送, "
            "去草稿箱核对后自己点」, 别让他以为已经发出去了\n\n"
            "**返回**: {ok, draft_id, summary}. summary 原样转述给员工。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "收件人, 多人逗号分隔"},
                "subject": {"type": "string", "description": "主题 (回复时带 Re: 前缀)"},
                "body": {"type": "string", "description": "正文定稿 (员工点头过的那版)"},
                "cc": {"type": "string", "description": "(可选) 抄送, 多人逗号"},
                "in_reply_to": {
                    "type": "string",
                    "description": "(回复场景必传) 原邮件 id — 客户端靠它串 thread",
                },
                "account": {"type": "string", "description": "(可选) 从哪个账号起草"},
            },
            "required": ["to", "subject", "body"],
        },
        "emoji": "📮",
        "toolset": "catfish_native",
        "available": True,
    },
]
