"""检索 (会话 / 附件 / 文档 / 文件) + 归档回读 —— 6 个 tool 的 JSON Schema。

8/15 从 catfish_tool_schemas.py 拆出: 那个文件是**一整个 3362 行的
列表字面量** (78 个 schema), 越过 800 红线 4 倍。零逻辑、零变量引用,
所以按类别切成 8 份, 由 catfish_tool_schemas.py 拼回去。

加新 tool: schema 加到对应类别文件, impl 加 catfish_tools.py 或子 module,
dispatch 加 catfish_tools._dispatch_native_inner —— **dispatch 不重组**
(这条红线是 5/20 拆分时定的, 见 catfish_tool_schemas.py 文件头)。
"""
from __future__ import annotations

from typing import Any, Dict, List


SEARCH_TOOLS: List[Dict[str, Any]] = [
    # ── tool message archive 读回 ──────────────
    # 直读本机 ~/.catfish/tool_archives/, 不走 gateway / PG (老 PG 存法违反
    # 「中央端严禁看到员工端数据」, 已整体搬到 edge). 按 OIDC sub 校验归属,
    # 防别人 Mac 拷来的 archive 被读. 默认保留 14 天.
    {
        "name": "catfish_read_tool_archive",
        "description": (
            "读已归档的 tool 输出原文. prompt 里出现 `[已归档: archive_ref=...]` 且任务需要原文时调: "
            "员工问具体在哪行/长什么样、debug 报错堆栈、引用具体数字或段落、复盘需要原文支撑. "
            "头尾+摘要已够判断时不调.\n"
            "别拉全文撑爆 context: 优先 grep (正则, 返匹配行±2行), 或 line_range; 两者都给时 grep 优先. "
            "ref 已过期或不存在会报错."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": "16 位十六进制, 从 archive_ref= 原样抄",
                },
                "line_range": {
                    "type": "string",
                    "description": "1 起始: 'N-M' / 'N' / 'N-' / '-M'",
                },
                "grep": {
                    "type": "string",
                    "description": "正则 (非子串), 推荐优先用",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "默认 8000",
                },
            },
            "required": ["ref"],
        },
        "emoji": "📂",
        "toolset": "catfish_native",
        "available": True,
    },
    # 起因: 员工反馈"历史会话搜不到". 直读本机 ~/.hermes/state.db (read-only sqlite, LIKE 匹配),
    # 不上行中央, 不跨员工.
    {
        "name": "catfish_search_sessions",
        "description": (
            "跨会话搜员工本机的历史聊天记录, 返回命中片段及所在会话. "
            "找历史对话一律用这个, 不要用 hermes 自带的 session_search (可能只搜当前会话).\n"
            "用于: '上次/之前/前几天我们聊的 X' 类问题, 例 '上次那个资质 Excel 说了啥' → query='资质 Excel'.\n"
            "不用于: 当前会话里的事实 (catfish_remember)、员工偏好画像 (catfish_user_profile_get)、"
            "按文件名找上传过的附件 (catfish_search_attachments)、本机文件 (catfish_search_files)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "关键字, 字面匹配, 不区分大小写",
                },
                "days_back": {
                    "type": "integer",
                    "description": "默认 30, 可加大到 90/180",
                },
                "limit": {
                    "type": "integer",
                    "description": "默认 20, 上限 100",
                },
            },
            "required": ["query"],
        },
        "emoji": "🔍",
        "toolset": "catfish_native",
        "available": True,
    },
    # 历史决策反转: 早期自己写 BM25 sidecar 跨会话搜过度工程; 后来把 ~/.catfish/uploads/
    # 加进 local_search 索引, 内容搜归一到 local_search FTS5. 本工具只剩元数据 / session 关联职责.
    {
        "name": "catfish_search_attachments",
        "description": (
            "按文件名关键词搜员工在聊天里上传过的附件, 返回 kept_path 和所在会话 session_id. "
            "用于: '我之前传的客户合同在哪个会话' / 员工有没有传过某文档.\n"
            "不按名字、要全列或按类型列 → catfish_list_my_attachments; 搜文件正文内容 → catfish_search_files; "
            "AI 产出文件 → catfish_list_my_outputs; 历史对话文字 → catfish_search_sessions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "员工 ID (email 或 openid@im.platform)",
                },
                "query": {
                    "type": "string",
                    "description": "文件名关键字",
                },
                "days_back": {
                    "type": "integer",
                    "description": "默认 90",
                },
                "limit": {
                    "type": "integer",
                    "description": "默认 20, 上限 100",
                },
            },
            "required": ["user_id", "query"],
        },
        "emoji": "📎",
        "toolset": "catfish_native",
        "available": True,
    },
    # 反向索引: 文件 → 出现过的会话集合.
    {
        "name": "catfish_list_my_attachments",
        "description": (
            "列出员工上传过的全部附件 (去重, 最近用的在前), 每个附带出现过的会话列表. "
            "用于: '我上传过的所有 Excel' (file_kind='xlsx') / '最近 30 天用过的文档' / 某文件被哪些会话引用.\n"
            "按文件名搜 → catfish_search_attachments; AI 产出文件 → catfish_list_my_outputs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "员工 ID",
                },
                "file_kind": {
                    "type": "string",
                    "description": "pdf / xlsx / docx / csv / md / image / audio 等",
                },
                "days_back": {
                    "type": "integer",
                    "description": "默认 90",
                },
                "limit": {
                    "type": "integer",
                    "description": "默认 50, 上限 500",
                },
            },
            "required": ["user_id"],
        },
        "emoji": "📚",
        "toolset": "catfish_native",
        "available": True,
    },
    # 战略 doc 同步: 直读 ~/.catfish/strategic_docs/*.md, BM25, 不走 gateway (manifesto 公理 2).
    # Progressive Disclosure: system prompt 段给 head → 本工具搜折叠区 → 全文员工在 UI 看.
    {
        "name": "catfish_search_docs",
        "description": (
            "搜 catfish 产品自身的战略/设计文档 (manifesto、专利、护城河、沙盒、advisory 等). "
            "员工问 catfish 自己的内部设计, 或 system prompt 提示'catfish strategic docs 还有 N 份'时调. "
            "例 'manifesto 哪条说不能 push' → query='manifesto'.\n"
            "prompt 里已显示的文档直接引用, 不必再搜. 员工个人知识库 → catfish_wiki_search; "
            "员工本机业务文件 → catfish_search_files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "关键词或自然语言, 例 'manifesto 公理'",
                },
                "top_k": {
                    "type": "integer",
                    "description": "默认 5, 上限 15",
                },
            },
            "required": ["query"],
        },
        "emoji": "📘",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── 起因: 让小鲶找文件, 它说'本地文件搜索环境无法启动' ──
    #
    # 真因不是环境挂了, 是**它根本没有这个工具**: catfish-local-search 没注册成
    # MCP server, catfish-search 二进制也没装, 而 skills.rs:946 又禁止员工自加
    # catfish-* 的 MCP。索引本身好好的 (一万多个文件, 当天还在写), 要找的文件
    # 也在里面 —— 只是没有任何路径能查到它。
    #
    # 所以补一个直读 ~/.catfish/search.db (local_search 索引, trigram FTS) 的 native tool。
    # tool-bridge 是被 autostart 自动注册的, 不依赖任何一次性安装脚本。详见 search_files.py。
    {
        "name": "catfish_search_files",
        "description": (
            "搜员工本机已索引的文件 (文件名 + 正文全文), 范围是员工在'搜索范围'里配置的目录. "
            "找不到文件时先用这个, 搜过之前不要说'文件不存在'.\n"
            "用于: '我那份 XX 文件在哪' / '找关于 YY 的材料' / 找员工历史周报、方案做参考.\n"
            "不用于: 邮件 (catfish_email_search)、历史对话 (catfish_search_sessions)、"
            "按名找聊天附件 (catfish_search_attachments)、catfish 设计文档 (catfish_search_docs)、"
            "个人知识库 (catfish_wiki_search).\n"
            "少于 3 字的词只能匹配文件名; 返回 short_terms 时说明词太短, 换长词重试. "
            "error='index_unavailable' 表示还没建索引, 让员工去 Companion 设置→服务/配额→搜索范围 加目录."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "空格分隔多词 (AND), 例 '资质 对标矩阵'",
                },
                "limit": {
                    "type": "integer",
                    "description": "默认 10, 上限 50",
                },
                "file_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "扩展名, 例 ['xlsx','docx'], 不给 = 不限",
                },
            },
            "required": ["query"],
        },
        "emoji": "🔍",
        "toolset": "catfish_native",
        "available": True,
    },
]
