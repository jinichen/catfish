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
    # ── BL-Q3-ARCHIVE (5/11) tool message archive 读回 ──────────────
    {
        "name": "catfish_read_tool_archive",
        "description": (
            "★ 读已归档的 tool output 原文 (lossless 全文存**本机**, 默认 14 天保留).\n\n"
            "**触发**: prompt 里出现 `[已归档: archive_ref=...]` 且任务相关需要原文.\n\n"
            "✅ 必须调用:\n"
            "  - 员工问'刚才那个 X 在哪行 / 长什么样' → 用 grep 召回原文\n"
            "  - debug — 报错堆栈 / 中段 print / 中间状态 → 用 grep='Error'/'fail'\n"
            "  - 引用具体数字 / 段落 → 用 line_range 拿原文\n"
            "  - 复盘 / 总结 — 要原文支撑, 不能凭空编中段\n\n"
            "❌ 不该调用:\n"
            "  - 头尾 + 摘要已经够判断 (e.g. '上次 pytest 全过了' 类问题)\n"
            "  - 任务跟 archive 无关\n"
            "  - **不要无脑拉全文** — 大文件直接撑 context, 务必用 grep 或 line_range\n\n"
            "三种调用模式 (ref 是 **16 位十六进制**, 从 archive_ref= 原样抄):\n"
            "  1. catfish_read_tool_archive(ref='0123456789abcdef') — 全文, 默认截到 8000 字节\n"
            "  2. ..., line_range='40-80' — 按行号 ('47' 单行 / '40-' 到末尾 / '-80' 从开头)\n"
            "  3. ..., grep='KeyError' — **正则**匹配行 ± 2 行上下文\n"
            "  grep 和 line_range 同时给时, **grep 优先**, line_range 被忽略.\n\n"
            "底层: tool-bridge 直读本机 `~/.catfish/tool_archives/`, "
            "**不走 gateway / 不走 PG** —— 5/22 BL-CENTRAL-EDGE-TOOL-ARCHIVE 把它整个搬到 "
            "edge 了, 老的 PG 存法违反「中央端严禁看到员工端数据」.\n"
            "返 {ok, ref, content, total_lines, total_bytes, tool_name, summary, "
            "filters_applied, hint}; 失败返 {ok: false, error}.\n"
            "读不到的两种情况: ref 不存在 / 已过期; 或这条 archive 属于别的员工 "
            "(按 OIDC sub 比对, 防别人 Mac 上拷来的 archive 被读)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": (
                        "archive 引用, **16 位十六进制** (从 prompt 里的 archive_ref= "
                        "原样抄, 格式不对直接报错)"
                    ),
                },
                "line_range": {
                    "type": "string",
                    "description": (
                        "可选, 1-indexed 行号: 'N-M' / 单行 'N' / 'N-' 到末尾 / "
                        "'-M' 从开头。给了 grep 时本参数被忽略"
                    ),
                },
                "grep": {
                    "type": "string",
                    "description": (
                        "可选, **正则表达式** (不是子串), 召回匹配行 ± 2 行上下文。"
                        "推荐优先用它, 比拉全文省 context"
                    ),
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "可选, 返回字节上限, 默认 8000 (没有硬上限, 但别拉太大撑爆 context)",
                },
            },
            "required": ["ref"],
        },
        "emoji": "📂",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-FIX-SESSION-SEARCH (5/13 鸿波"历史会话搜不到") ──────────────
    {
        "name": "catfish_search_sessions",
        "description": (
            "★★★ 跨 session 搜员工本机 hermes 历史对话 (read-only sqlite). "
            "**优先用这个不要用 hermes 自带 session_search** — 后者可能只搜当前 session.\n\n"
            "✅ 调用场景:\n"
            "  - 员工问 '上次那个资质 Excel 我们说了啥' → query='资质 Excel'\n"
            "  - 员工问 '前两天讨论的 EIS 流程' → query='EIS' days_back=7\n"
            "  - LLM 自己想找 '我之前给小李回的资质标准' → query='资质标准'\n"
            "  - 任何 '那次/上次/之前/前几天' 类历史索引诉求\n\n"
            "❌ 不调用:\n"
            "  - 当前会话内的事实 (走 catfish_remember / session_facts)\n"
            "  - 员工偏好/画像 (走 catfish_user_profile_get)\n"
            "  - employee_journal 的事 (走该文件)\n\n"
            "返参:\n"
            "  - matches: 命中行 list, 每条 {session_id, session_title, role, "
            "    snippet (±200 字符上下文), created_iso}\n"
            "  - count: 总命中数\n"
            "  - session_count: 跨多少 session\n"
            "  - summary: 一句话归纳 (按 session 分组), 念给员工知道在哪些会话里找到\n\n"
            "🔒 隐私: 直读员工 mac 本地 ~/.hermes/state.db, 不上行中央, 不跨员工."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜的关键字 (大小写不敏感, LIKE 字面匹配, 中文 OK)",
                },
                "days_back": {
                    "type": "integer",
                    "description": "搜过去多少天的会话 (默认 30, 长任务可加大到 90/180)",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返多少命中行 (默认 20, 上限 100)",
                },
            },
            "required": ["query"],
        },
        "emoji": "🔍",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-FILE-SESSION-INDEX-V1 Phase 4 (5/30 鸿波"C 才是正解") ─────
    # 历史决策反转: Phase 2 (早) 自己写 BM25 sidecar 跨会话搜过度工程,
    # Phase 4 (晚) 把 ~/.catfish/uploads/ 加进 local_search 索引, 内容搜归一
    # 到 local_search FTS5. 本工具只剩元数据 / session 关联职责.
    {
        "name": "catfish_search_attachments",
        "description": (
            "★★★ 按**文件名**搜员工上传过的附件 + 拿到 session_id (反查"
            "'在哪个会话提到的'). 跨会话, 限定 user_id.\n\n"
            "✅ 调用场景:\n"
            "  - 员工问 '我之前传的客户合同 PDF 在哪个会话提到' → query='客户合同'\n"
            "  - LLM 自己想找 '员工有没有传过 XX 文档' → query='XX'\n"
            "  - 想知道某附件出现在哪几个会话 → 配 catfish_list_my_attachments\n\n"
            "❌ 不调用 (路由到别的工具):\n"
            "  - **找附件内容里某段话 / 跨附件语义搜 → local_search** (FTS5 全文索引,\n"
            "    Phase 4 把 ~/.catfish/uploads/ 加进 search-scope, 已覆盖)\n"
            "  - 找 AI 产出文件 → catfish_list_my_outputs\n"
            "  - 找历史对话文字 → catfish_search_sessions\n"
            "  - 找邮件 → catfish_email_search\n\n"
            "返参:\n"
            "  - matches: 命中 list, 每条 {id, session_id, name, kept_path, file_kind, created_iso}\n"
            "  - 想看附件全文 → local_search(query) 或 catfish_read_file(path=kept_path)\n\n"
            "🔒 隐私: user_id 必填, 物理隔离, 不串其它员工."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "员工 ID (email 或 openid@im.platform 合成). 必填.",
                },
                "query": {
                    "type": "string",
                    "description": "文件名关键字 (中文/英文 OK)",
                },
                "days_back": {
                    "type": "integer",
                    "description": "搜过去多少天 (默认 90)",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返多少条 (默认 20, 上限 100)",
                },
            },
            "required": ["user_id", "query"],
        },
        "emoji": "📎",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-FILE-SESSION-INDEX-V1 Phase 3 (5/30 反向索引) ─────────────
    {
        "name": "catfish_list_my_attachments",
        "description": (
            "★★★ 列员工所有上传过的附件 + 每个文件出现在哪些会话 (反向索引). "
            "**catfish_search_attachments 是按内容/名字搜, 本工具是按 owner 全列**.\n\n"
            "✅ 调用场景:\n"
            "  - 员工问 '我上传过的所有 Excel' → file_kind='xlsx'\n"
            "  - 员工问 '最近 30 天我用过的文档' → days_back=30\n"
            "  - 员工问 '我那个客户合同文档在哪几个会话引用了' → 在 files 里查 name + sessions\n"
            "  - LLM 自己想了解员工常用文件 → 不带 file_kind 拿全部\n\n"
            "❌ 不调用:\n"
            "  - 按内容关键词搜 → catfish_search_attachments\n"
            "  - 列 AI 产出文件 → catfish_list_my_outputs\n\n"
            "返参 files 每条: {name, file_kind, kept_path, reference_count, sessions: [{session_id, first_seen_iso}], first_seen_iso, last_seen_iso}\n"
            "按 last_seen 倒序 (最近用的在前). 同一文件去重, sessions 列出现 session 集合.\n\n"
            "🔒 隐私: user_id 必填, 物理隔离, 不串其它员工."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "员工 ID. 必填, 防跨员工串.",
                },
                "file_kind": {
                    "type": "string",
                    "description": "可选 filter: pdf / xlsx / docx / csv / txt / md / image / audio 等",
                },
                "days_back": {
                    "type": "integer",
                    "description": "搜过去多少天 (默认 90)",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返多少个文件 (默认 50, 上限 500)",
                },
            },
            "required": ["user_id"],
        },
        "emoji": "📚",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-STRATEGIC-DOC-SYNC Phase 3 (6/7 鸿波 audit) 战略 doc 搜索 ──
    {
        "name": "catfish_search_docs",
        "description": (
            "★★ 搜员工本机战略 / 设计 doc (~/.catfish/strategic_docs/*.md) — "
            "**当 system prompt 折叠区显示 'catfish strategic docs 还有 N 份'** 或员工问 "
            "catfish 自己内部设计 (manifesto / patent / moat / 沙盒 / advisory 等) 必用.\n\n"
            "✅ 调用场景:\n"
            "  - 员工问 'catfish manifesto 第几条说不能 push?' → query='manifesto'\n"
            "  - 员工问 'catfish 沙盒怎么做的?' → query='沙盒 audit'\n"
            "  - 员工问 '我们 patent 主打哪个方向?' → query='patent 方向 优先级'\n"
            "  - 员工问 'catfish 跟主流对比的护城河' → query='moat counter-positioning'\n\n"
            "❌ 不调用:\n"
            "  - prefetch 已经 inline 显示的 doc — 直接引用其中内容\n"
            "  - 找员工自己 wiki — 用 wiki_search_text / wiki_search_semantic\n"
            "  - 找邮件 / 会话 — 走对应的 catfish_* tool\n\n"
            "返参:\n"
            "  - matches: top-K doc {name, title, head 摘要 1500 字, path, score}\n"
            "  - count, total_indexed, summary, latency_ms\n\n"
            "🔒 隐私: 直读员工 mac 本机 ~/.catfish/strategic_docs/, 不走 gateway, "
            "不上行中央. 跟 manifesto 公理 2 一致.\n\n"
            "💡 思路 (跟 catfish_search_skills 同 Progressive Disclosure):\n"
            "  Tier 1 (system prompt strategic_docs 段) - 你已看见 head 关键段\n"
            "  Tier 2 (这工具) - 折叠区 BM25 搜 + 拿 head 1500 字 + 路径\n"
            "  Tier 3 (员工调 wiki_read 拿全文) - 客户端 UI 操作, LLM 无 tool"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜的关键字 / 自然语言 ('manifesto 公理' / 'sandbox fork bomb' / 'patent 方向' 等)",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返多少份 (默认 5, 上限 15)",
                },
            },
            "required": ["query"],
        },
        "emoji": "📘",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── 8/14 鸿波 catch「让小鲶找一份文件, 它说'本地文件搜索环境无法启动'」──
    #
    # 真因不是环境挂了, 是**它根本没有这个工具**: catfish-local-search 没注册成
    # MCP server, catfish-search 二进制也没装, 而 skills.rs:946 又禁止员工自加
    # catfish-* 的 MCP。索引本身好好的 (10,518 个文件, 当天还在写), 要找的文件
    # 也在里面 —— 只是没有任何路径能查到它。
    #
    # 所以补一个直读 ~/.catfish/search.db 的 native tool。tool-bridge 是被
    # autostart 自动注册的, 不依赖任何一次性安装脚本。详见 search_files.py。
    {
        "name": "catfish_search_files",
        "description": (
            "★★★ 搜员工本机**已索引的文件** (文件名 + 正文全文). 找不到文件时先用这个,\n"
            "不要直接说'文件不存在'.\n\n"
            "数据源: local_search 索引 (~/.catfish/search.db) — 范围就是员工在 Companion\n"
            "'📂 搜索范围' 卡里配的目录, 不能在这里另指目录.\n\n"
            "✅ 调用场景:\n"
            "  - 员工问 '我那份 XX 文件在哪' / '找一下关于 YY 的材料'\n"
            "  - 要引用员工历史产出 (周报 / 方案 / 台账) 但不知道路径\n"
            "  - 写文档前找参考: query 给主题词\n\n"
            "❌ 不要用它找: 邮件 (catfish_email_search) / 历史对话\n"
            "   (catfish_search_sessions) / 上传附件 (catfish_search_attachments) /\n"
            "   catfish 自己的设计文档 (catfish_search_docs) / 员工 wiki (catfish_wiki_search).\n\n"
            "⚠ 关键词长度: 索引用 trigram 分词, **少于 3 个字符的词搜不了正文**\n"
            "   (只能在文件名里匹配). 返回里的 short_terms 会列出被降级的词 ——\n"
            "   看到它就说明'没搜到'可能是词太短, **不是文件不存在**, 换个长点的词再试.\n\n"
            "返参: matches[{path, file_type, mtime, snippet, matched_by}], count,\n"
            "      summary, short_terms?, latency_ms.\n"
            "      matched_by = filename / content / content+filename.\n"
            "      error='index_unavailable' 表示员工还没建过索引 —— 这时候要让他去\n"
            "      Companion 仪表盘 → 服务/配额 → 搜索范围 加目录, 而不是说没找到.\n\n"
            "🔒 隐私: 直读员工 mac 本机 sqlite, 不出端, 不上传中央."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "关键词, 空格分隔多个词 (它们之间是 AND). "
                        "例: '业务场景 梳理' / '周报 陈鸿波' / '资质 对标矩阵'"
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "返多少个 (默认 10, 上限 50)",
                },
                "file_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "只要这些扩展名, 例 ['xlsx','docx']. 不给 = 不限",
                },
            },
            "required": ["query"],
        },
        "emoji": "🔍",
        "toolset": "catfish_native",
        "available": True,
    },
]
