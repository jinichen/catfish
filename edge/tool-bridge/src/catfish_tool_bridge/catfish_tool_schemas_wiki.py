"""知识库读写 + 发布 + 溯源 —— 10 个 tool 的 JSON Schema。

8/15 从 catfish_tool_schemas.py 拆出: 那个文件是**一整个 3362 行的
列表字面量** (78 个 schema), 越过 800 红线 4 倍。零逻辑、零变量引用,
所以按类别切成 8 份, 由 catfish_tool_schemas.py 拼回去。

加新 tool: schema 加到对应类别文件, impl 加 catfish_tools.py 或子 module,
dispatch 加 catfish_tools._dispatch_native_inner —— **dispatch 不重组**
(这条红线是 5/20 拆分时定的, 见 catfish_tool_schemas.py 文件头)。
"""
from __future__ import annotations


def _relation_enum() -> list[str]:
    """wiki 关系词: contracts 内置 + 员工自定义 + 兜底「关联」(跟写入侧 wiki_files._load_relation_vocab 同源)。"""
    from .wiki_files import _load_relation_vocab  # noqa: PLC0415
    relations, _aliases, fallback = _load_relation_vocab()
    words = sorted(relations, key=lambda w: (w not in _BUILTIN_ORDER, _BUILTIN_ORDER.index(w) if w in _BUILTIN_ORDER else 0, w))
    return (words or list(_BUILTIN_ORDER)) + [fallback]


_BUILTIN_ORDER = [
    "隶属", "包含", "负责", "参与", "协作", "依据", "遵循", "使用", "持有", "认证",
    "对标", "配套", "替代", "前置", "同类", "在办", "维护", "申报", "分析", "记录",
]

from typing import Any, Dict, List


WIKI_TOOLS: List[Dict[str, Any]] = [
    # ── Wiki Hub publish / install / unpublish ────────────
    # manifesto 公理 2 例外条款明示允许员工主动 push wiki 到团队 marketplace
    # (CATFISH-CENTRAL-MANIFESTO.md line 34-36).
    # 底层: POST gateway /v1/wiki/documents, 跟 mcp-registry 同 OIDC 鉴权.
    # 敏感词表读 ~/.catfish/wiki/sensitive_terms.txt.
    {
        "name": "catfish_wiki_publish",
        "description": (
            "把员工本机一条 wiki 笔记发布到他自己部门的 wiki-hub (仅同部门和管理员可见). "
            "只在员工明确要求分享时调, 后台自动学的 wiki 不要主动发.\n"
            "发布前必须告诉员工: 同事装到本机的副本无法撤回.\n"
            "凭据命中直接拒; PII/内网/敏感词命中返 warnings 并拒 —— 把 warnings 转告员工, "
            "他同意后再带 acknowledge_warnings=true 重试. 同一文件再发 = 更新."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wiki_rel_path": {
                    "type": "string",
                    "description": "'wiki/' 开头, 例 'wiki/entities/老李.md'",
                },
                "acknowledge_warnings": {
                    "type": "boolean",
                    "description": "员工看过 warnings 并同意后才设 true",
                    "default": False,
                },
                "file_id": {
                    "type": "string",
                    "description": "一般不传 (自动带上次的); 仅覆盖特定条目时传",
                },
            },
            "required": ["wiki_rel_path"],
        },
        "emoji": "📤",
        "toolset": "catfish_native",
        "available": True,
    },
    # 装到 ~/.catfish/wiki-shared/<ns>/<file_id>.md, 只读, 写 .meta.json sidecar 记 publisher/pull 时间.
    {
        "name": "catfish_wiki_install",
        "description": (
            "从部门 wiki-hub 拉一条 wiki 装到本机 (只读, 装后 catfish_wiki_search 可搜到). "
            "员工说'把这条装到本机'或要用 hub 上某条笔记时调. 只能装自己部门的; "
            "重复装 = 更新到最新版; 原作者已撤回的会被拒, 照实告诉员工."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hub_namespace": {"type": "string", "description": "'dept/<部门>', 原样用列表里拿到的"},
                "hub_file_id": {"type": "string", "description": "服务端 file_id"},
            },
            "required": ["hub_namespace", "hub_file_id"],
        },
        "emoji": "📥",
        "toolset": "catfish_native",
        "available": True,
    },
    # 撤回后中央 PG row 保留 (audit), body/frontmatter 清零, 标 stale_after_unpublish=true.
    {
        "name": "catfish_wiki_unpublish",
        "description": (
            "撤回员工自己发布过的部门 wiki (只能撤自己的). 员工说'撤回那条 / 别让部门看了'时调. "
            "撤回前告诉员工: 中央那份会清空, 但同事已装到本机的副本撤不回."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hub_namespace": {"type": "string", "description": "'dept/<部门>'"},
                "hub_file_id": {"type": "string"},
                "reason": {"type": "string", "description": "撤回原因, 可空"},
            },
            "required": ["hub_namespace", "hub_file_id"],
        },
        "emoji": "🗑️",
        "toolset": "catfish_native",
        "available": True,
    },
    # 范围: ~/.catfish/wiki/{entities,concepts,queries} + ~/.catfish/wiki-shared/dept/<部门>/.
    # BM25 char-level, 纯离线, 不走 gateway. 装机部门 wiki 物理在本机, 隐私边界同自家.
    {
        "name": "catfish_wiki_search",
        "description": (
            "按关键词搜员工个人知识库 (wiki), 含已装到本机的部门 wiki. 返回 top-K 条目的 "
            "开头约 1500 字和 rel_path.\n"
            "用于: '我 wiki 里关于 X 写过啥' / '之前记的 Y' / 公司或部门关于 Z 的流程规定 / "
            "某客户在知识库里有没有条目.\n"
            "要全文 → 拿 rel_path 调 catfish_wiki_read. 要列出全部条目 → catfish_wiki_list.\n"
            "不用于: catfish 设计文档 (catfish_search_docs) / skill (catfish_search_skills) / "
            "邮件、历史对话、附件、本机文件 (catfish_email_search / catfish_search_sessions / "
            "catfish_search_attachments / catfish_search_files)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "关键词或自然语言, 例 '资质评估流程'",
                },
                "top_k": {
                    "type": "integer",
                    "description": "默认 5, 上限 15",
                },
            },
            "required": ["query"],
        },
        "emoji": "📚",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── wiki 读写闭环 ─────────────────────
    #
    # 起因是一次真实的绕圈: 员工让存一份材料进知识库, 存完知识体系 TAB 一直
    # 看不到。鲶鱼当时只有 search + ingest 两个工具, 没有任何一个能回答
    # 「TAB 现在到底有哪些条目」, 只好去翻 sqlite / embeddings / sync_turn 日志
    # 反推, 结论几乎全错。
    #
    # 补 list 是为了让它能查证而不是推测; 补 create 是因为 ingest 只能丢进
    # raw/sources/ 等后台蒸馏, 而 TAB 从不读那个目录 —— 员工要的「存了马上能
    # 看到」以前根本没有对应的工具。
    #
    # list 跟 TAB 同源 (Companion wiki_list_files, 扫 ~/.catfish/wiki/{entities,concepts,queries}
    # 的 .md); wiki_embeddings.db 只用于语义搜索, 不决定 TAB 显示.
    {
        "name": "catfish_wiki_list",
        "description": (
            "列出员工个人知识库全部条目, 结果与知识体系 TAB 显示的完全一致 (不在列表 = TAB 看不到).\n"
            "用于: 员工说'知识库里怎么没有 X / 存了看不到'时先查证, 别猜; 建条目前查重名; "
            "员工问'知识库里都有啥'. ingest 后还没蒸馏的材料不在列表里.\n"
            "按内容找用 catfish_wiki_search."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": "entity / concept / query, 不传 = 全部",
                },
                "limit": {
                    "type": "integer",
                    "description": "默认 200, 新的在前",
                },
            },
            "required": [],
        },
        "emoji": "📇",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_wiki_create",
        "description": (
            "在员工个人知识库新建条目 (entity / concept), 写完 TAB 立刻可见.\n"
            "员工要'存进知识库'且希望马上看到 → 用这个; 只是把文件/附件归档留底 → "
            "catfish_wiki_ingest (要等后台蒸馏); 都要就两个都调. 改已有条目 → catfish_wiki_update.\n"
            "建前先 catfish_wiki_list 查重名 (近似同名也会被拦). title 直接用中文, 不要转拼音."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": "entity (人/公司/系统/证书) 或 concept (流程/方法)",
                },
                "title": {
                    "type": "string",
                    "description": "中文标题, 直接作文件名",
                },
                "body": {
                    "type": "string",
                    "description": "正文 markdown, 不含 frontmatter 和一级标题",
                },
                "subtype": {
                    "type": "string",
                    "description": "细分类型, 如 person / org / process / 资质",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "related": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "rel": {
                                        "type": "string",
                                        # 10/23: 内置词表 + 员工登记的自定义关系, 启动时从 contracts 读
                                        "enum": _relation_enum(),
                                        "description": (
                                            "本条目→对方. 隶属=本条目属于对方, 持有=公司→已取得的证书, "
                                            "在办=公司→正在办的证书, 维护=人→证书, 替代=新→旧. 都不贴切才用'关联'"
                                        ),
                                    },
                                },
                                "required": ["name", "rel"],
                                "additionalProperties": False,
                            },
                        ]
                    },
                    "description": "传 {name, rel}; 只传标题或 rel=关联 会进待确认",
                },
            },
            "required": ["kind", "title", "body", "subtype"],
        },
        "emoji": "📝",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_wiki_read",
        "description": (
            "读个人知识库某条目全文 + 它的全部关系 (relations: out 本条目→对方, in 别的条目→本条目, "
            "如「谁隶属于这个部门」看 in). rel_path 从 catfish_wiki_search / catfish_wiki_list 拿, "
            "或直接给 name (标题/别名). 返参 visible_in_tab 说明它是否在 TAB 里可见."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rel_path": {
                    "type": "string",
                    "description": "必须 wiki/ 开头, 例 wiki/entities/X.md",
                },
                "name": {
                    "type": "string",
                    "description": "不知道路径时给条目标题或别名 (完全一致)",
                },
            },
        },
        "emoji": "📖",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_wiki_update",
        "description": (
            "修改个人知识库已有条目 —— 整文件覆写, 不是追加. 必须先 catfish_wiki_read 取全文, "
            "在其上修改后整篇传回, 否则原内容全丢. 新建条目用 catfish_wiki_create. "
            "返参有 warning 说明覆写后会被 TAB 隐藏 (内容太短且无 frontmatter)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rel_path": {
                    "type": "string",
                    "description": "必须 wiki/ 开头",
                },
                "content": {
                    "type": "string",
                    "description": "完整新内容 (含 frontmatter), 覆写",
                },
            },
            "required": ["rel_path", "content"],
        },
        "emoji": "✏️",
        "toolset": "catfish_native",
        "available": True,
    },
    # 背景: 大部分条目是 employee_journal 蒸馏的, 从原始对话到条目过了四次 LLM
    # (summarize → analysis → generation → merge), 失真不可避免.
    # 实测覆盖 (255 条): recorded 28% / recorded-material 25% / inferred 38% / none 7%.
    # 存量 journal 只记 session id 后 6 位, verdict 会标出靠后缀匹配的链.
    {
        "name": "catfish_wiki_trace",
        "description": (
            "追溯知识库条目的来源, 找回原始对话/材料. 用于: 员工质疑'这条不对 / 数字哪来的 / "
            "我什么时候说过'; 你引用某条前拿不准可信度; 员工问是人写的还是 AI 蒸馏的.\n"
            "不要替员工判对错, 把原文摆给他看.\n"
            "引用前必看 provenance: recorded / recorded-material = 有记录的真实来源; "
            "inferred = 仅按标题检索到, 不能证明来源, 转述时必须讲明; none = 查不到, 照实说, 不许编."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rel_path": {
                    "type": "string",
                    "description": "从 catfish_wiki_list / catfish_wiki_search 拿",
                },
                "with_messages": {
                    "type": "boolean",
                    "description": "拉原始对话正文, 默认 false 只给会话清单",
                },
            },
            "required": ["rel_path"],
        },
        "emoji": "🔍",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── 员工显式入库 wiki (取代老的自动 ingest) ──
    #
    # 老方案 fire-and-forget: 员工上传文件 → ChatInput 自动写
    # ~/.catfish/wiki/raw/sources/ → sync_turn 3b 自动抽 entity/concept.
    # 员工反馈"不是所有文档都要进知识库, 员工显式说才入库", 于是:
    # 1. 删 ChatInput.tsx fire-and-forget (上传只是 chat context, 不污染 wiki).
    # 2. 加本 tool: 员工明说"入库/存 wiki/记住这个文档"才调 → 写 sources/ →
    #    sync_turn 3b 后台 Analysis + Generation 抽到 wiki/entities/ + concepts/ (24h 内).
    # 3. SOUL guidance 同步: 员工明说才调, 不是默认自动. AI-first, 不 hardcode 关键词.
    # 曾发生: 调完 ingest 告诉员工"去 TAB 看", 员工看不到, 陷入一轮查不出原因的排查.
    {
        "name": "catfish_wiki_ingest",
        "description": (
            "把员工 chat 里的附件原件归档进个人知识库 (raw/sources), 后台 24h 内蒸馏成条目.\n"
            "只在员工明确说要存时调: '存到知识库 / 存 wiki / 入库 / 记住这个文档'. "
            "只是让你看、分析、总结附件时不调; 拿不准就先问'要存到知识库吗?'.\n"
            "蒸馏前 TAB 看不到它, 不要说'已入库去 TAB 看', 应说'原件已归档, 条目待后台生成'. "
            "要马上可见 → 改用/同时调 catfish_wiki_create."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kept_path": {
                    "type": "string",
                    "description": "附件原文件路径 (attachment.keptPath)",
                },
                "parsed_text_path": {
                    "type": "string",
                    "description": "解析文本 sidecar (attachment.parsedTextPath), 有就传, 优先用",
                },
                "filename": {
                    "type": "string",
                    "description": "原文件名 (attachment.name)",
                },
                "reason": {
                    "type": "string",
                    "description": "入库理由, 员工原话或转述, 30-100 字",
                },
            },
            "required": ["kept_path", "filename"],
        },
        "emoji": "📥",
        "toolset": "catfish_native",
        "available": True,
    },
]
