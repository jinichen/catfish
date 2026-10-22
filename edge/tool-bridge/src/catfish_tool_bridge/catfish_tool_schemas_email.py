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
    # ── BL-EMAIL-SEARCH-TOOL (5/18 "对话里检索没搜到邮件") ──────────
    # 返回 {ok, matches: [{id, adapter, account, subject, sender, date, is_read, snippet(前200字)}],
    # count, summary}; 只看 snippet 不读正文, 不上行中央, 正文永不缓存。
    {
        "name": "catfish_email_search",
        "description": (
            "全文搜索员工本地邮件 (Apple Mail + Foxmail, 跨账号), 返回命中列表 (id/发件人/主题/日期/摘要) "
            "和一句话 summary。员工提到'那封/上次/之前的 X 邮件'、找某人发来的邮件或某类通知时调。"
            "找文件用 local_search, 找历史对话用 catfish_search_sessions。"
            "要看正文, 用返回的 id 调 catfish_email_read。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "关键字, 字面匹配, 多个用空格分隔",
                },
                "folder": {
                    "type": "string",
                    "description": "'*'=全部(默认), 'Inbox', 'Drafts'(改草稿前找旧草稿 id)",
                },
                "account": {
                    "type": "string",
                    "description": "账号地址, 默认搜全部账号",
                },
                "limit": {
                    "type": "integer",
                    "description": "默认 20, 上限 50",
                },
            },
            "required": ["query"],
        },
        "emoji": "📧",
        "toolset": "catfish_native",
        "available": True,
    },
    # P3.5.194 (7/7): 员工主权授权读邮件正文 —— 补齐 email 三件套
    # (search 找 → read 拿正文/附件元 → attachment 取附件).
    # 返回 subject/sender/recipients/cc/date/folder/adapter/account, body_text (超 40k 截断,
    # body_text_truncated=true), attachments [{filename, size_bytes, content_type}], attachments_count。
    # 一次一封是为了防 prompt injection 批量外泄。
    {
        "name": "catfish_email_read",
        "description": (
            "读单封邮件的完整正文和附件列表, email_id 来自 catfish_email_search。"
            "员工明确要看正文、列附件或对照邮件内容时调; 只问有没有某封邮件或摘要已能回答时不调。"
            "一次读一封; 正文只用于本次对话, 员工没明说不要写入 memory 或 catfish_wiki_ingest。"
            "取附件用 catfish_email_attachment。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "email_id": {
                    "type": "string",
                    "description": "search 返回的 matches[i].id, 带 adapter 前缀原样传",
                },
                "mark_read": {
                    "type": "boolean",
                    "description": "默认 true 标已读; 只看不改状态传 false",
                },
            },
            "required": ["email_id"],
        },
        "emoji": "📖",
        "toolset": "catfish_native",
        "available": True,
    },
    # 导出到系统 tmp 目录 (自动清理); 返回 {ok, path, filename, size_bytes}, path 前端渲染成可点链接。
    {
        "name": "catfish_email_attachment",
        "description": (
            "把邮件的一个附件导出到本地临时目录, 返回 path 供员工点开。"
            "filename 取自 catfish_email_read 返回的 attachments。"
            "仅在员工明确要附件时调, 一次一个; 超过 10MB 先问员工; 员工没说入库就不要 catfish_wiki_ingest。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "email_id": {
                    "type": "string",
                    "description": "catfish_email_search / email_read 返回的 id",
                },
                "filename": {
                    "type": "string",
                    "description": "attachments[i].filename, 一次一个",
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
    # 5/21: catfish 绝不代行, 只起草到 outputs/ + 给选项. tool 只做 IO,
    # LLM 主调用方 generate 内容传给 tool. tool 不二次调 LLM (简版).
    # P3.5.40 (6/18): 加两阶段 phase, 防 LLM 编造员工没说的细节 (如凭空的预算数字)。
    # 输出: ~/.catfish/outputs/<today>/reply-{rec}-{tone}[-questions].md; phase 缺省=final 兼容老 caller。
    {
        "name": "catfish_draft_email_reply",
        "description": (
            "起草邮件回信, 存为本地 outputs 文件供员工讨论, 不进草稿箱、不发送。"
            "涉及具体数字、关系人、历史决定时先用 phase='assumptions' 列出 questions 让员工补全, 别编造细节; "
            "内容简单或员工已答完, 再用 phase='final' 写正文。多种口径就每个 tone 各调一次。"
            "员工点头定稿后才用 catfish_email_create_draft 放进草稿箱。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tone": {
                    "type": "string",
                    "enum": ["strict", "balanced", "friendly", "formal", "urgent", "hold"],
                    "description": "口径: strict=不松口, balanced=平衡, hold=暂缓",
                },
                "thread_id": {"type": "string", "description": "原邮件 thread id"},
                "recipient": {"type": "string"},
                "subject": {"type": "string"},
                "phase": {
                    "type": "string",
                    "enum": ["assumptions", "final"],
                    "description": "assumptions=先列待确认问题; final(默认)=写正文存盘",
                },
                "content": {
                    "type": "string",
                    "description": "回信正文; phase=final 必填",
                },
                "questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "phase=assumptions 必填: 需员工回答的不确定项",
                },
                "assumptions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "已做的假设, 供员工核对",
                },
                "outline": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "计划的回信结构",
                },
                "compliance_notes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "check_compliance 给出的合规提示",
                },
            },
            "required": ["tone", "thread_id", "recipient", "subject"],
        },
        "emoji": "✉️",
        "toolset": "catfish_native",
        "available": True,
    },
    # 输出: ~/.catfish/outputs/<today>/meeting-brief-*.md
    {
        "name": "catfish_draft_meeting_brief",
        "description": (
            "把你写好的会议汇报 brief (markdown) 存到本地 outputs。员工要为某个会议准备汇报材料时调; "
            "拿不准的数字/内容放进 highlighted_uncertain, 供员工会前确认。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "日历 event id"},
                "event_title": {"type": "string"},
                "content": {"type": "string", "description": "brief 正文 (markdown)"},
                "highlighted_uncertain": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "待员工确认的数字/内容点",
                },
            },
            "required": ["event_id", "event_title", "content"],
        },
        "emoji": "📄",
        "toolset": "catfish_native",
        "available": True,
    },
    # 输出: ~/.catfish/outputs/<today>/followup-*.md
    {
        "name": "catfish_compose_followup_list",
        "description": (
            "把你写好的项目催办名单 + 多种沟通口径 (markdown) 存到本地 outputs。"
            "员工要催项目进度、列跟进人和话术时调。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "decision_ref": {"type": "string", "description": "依据的会议/决定"},
                "content": {"type": "string", "description": "催办名单 markdown"},
            },
            "required": ["project", "content"],
        },
        "emoji": "📨",
        "toolset": "catfish_native",
        "available": True,
    },
    # 只建草稿是能力边界不是约定: 系统根本没有发送工具。返回 {ok, draft_id, summary}。
    {
        "name": "catfish_email_create_draft",
        "description": (
            "把员工已点头的定稿放进邮件客户端草稿箱, 只建草稿、绝不发送 (系统没有发送能力)。"
            "仅在员工看过文案并同意后调; 还在讨论先用 catfish_draft_email_reply。"
            "完成后明确告诉员工: 没有发送, 请到草稿箱核对后自己点发送。"
            "回复邮件必传 in_reply_to; 修改已有草稿传 replaces。返回的 summary 原样转述。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "收件人, 多人逗号分隔"},
                "subject": {"type": "string", "description": "回复时带 Re: 前缀"},
                "body": {"type": "string", "description": "员工点头过的定稿正文"},
                "cc": {"type": "string", "description": "抄送, 多人逗号分隔"},
                "in_reply_to": {
                    "type": "string",
                    "description": "回复时必传: 原邮件 id, 用于串 thread",
                },
                "account": {"type": "string", "description": "从哪个账号起草"},
                "replaces": {
                    "type": "string",
                    "description": "改已有草稿时传旧草稿 id, 不传会多出一封重复草稿",
                },
            },
            "required": ["to", "subject", "body"],
        },
        "emoji": "📮",
        "toolset": "catfish_native",
        "available": True,
    },
]
