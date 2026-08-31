"""知识库读写 + 发布 + 溯源 —— 10 个 tool 的 JSON Schema。

8/15 从 catfish_tool_schemas.py 拆出: 那个文件是**一整个 3362 行的
列表字面量** (78 个 schema), 越过 800 红线 4 倍。零逻辑、零变量引用,
所以按类别切成 8 份, 由 catfish_tool_schemas.py 拼回去。

加新 tool: schema 加到对应类别文件, impl 加 catfish_tools.py 或子 module,
dispatch 加 catfish_tools._dispatch_native_inner —— **dispatch 不重组**
(这条红线是 5/20 拆分时定的, 见 catfish_tool_schemas.py 文件头)。
"""
from __future__ import annotations

from typing import Any, Dict, List


WIKI_TOOLS: List[Dict[str, Any]] = [
    # ── P3.3.18 (6/10) Wiki Hub publish / install / unpublish ────────────
    # manifesto 公理 2 例外条款明示允许员工主动 push wiki 到团队 marketplace
    # (CATFISH-CENTRAL-MANIFESTO.md line 34-36).
    {
        "name": "catfish_wiki_publish",
        "description": (
            "★ 把员工本机一条 wiki 笔记 (~/.catfish/wiki/{entities,concepts,queries}/X.md) "
            "发布到部门 wiki-hub (全部门可见).\n\n"
            "✅ 调用时机:\n"
            "  - 员工说'把这条 wiki 分享给部门' / '让 X 部门看下我这条笔记'\n"
            "  - **绝不**在没员工 explicit 确认时调用 (跟 skill_publish 同纪律)\n"
            "  - 员工自动学的 wiki (catfish-memory plugin 后台抽的) 不要主动 publish, "
            "    要员工亲自看完同意才发\n\n"
            "🛑 强警告对员工说 (publish 前必转告):\n"
            "  '部门 wiki 已 pull 的副本你管不了 (manifesto 公理 4 — 中央不能反向触及员工本机). "
            "  哪怕你后面撤回, 5 个同事本机各有副本, 信息已扩散. 你确定吗?'\n\n"
            "input:\n"
            "  - wiki_rel_path: 本机 rel_path, 以 'wiki/' 开头 (例 'wiki/entities/老李.md')\n"
            "  - namespace: 部门 namespace, 必须 'dept/<部门>' 格式 (例 'dept/finance')\n"
            "    用 catfish_today_summary 拿 department 字段拼\n"
            "  - acknowledge_warnings (可选): false 时撞 PII/内网/敏感词警告就拒. true 跳警告\n"
            "  - file_id (可选): 重发同一 wiki 时传上次拿到的, 让中央 update 同 row\n\n"
            "扫描行为 (跟 skill_publish 不同):\n"
            "  - 凭据扫: 命中**永拒** (wiki 写密码是 mistake)\n"
            "  - PII / 内网 URL / 敏感词扫: 命中**只警告**, 返 warnings 字段, 默认拒\n"
            "    LLM 必须把 warnings 转告员工, 员工 confirm 后 LLM 才能加\n"
            "    acknowledge_warnings=true retry. wiki 内容不能自动脱敏\n"
            "    (脱敏后笔记就没意义), 员工要自己拍.\n"
            "  - 敏感词扫从 ~/.catfish/wiki/sensitive_terms.txt 读员工自配 list\n\n"
            "成功返:\n"
            "  {ok:true, namespace, file_id, hub_url, published_at, summary, acknowledged_warnings?}\n"
            "警告 (默认拒):\n"
            "  {ok:false, scan_phase:'warnings', warnings:[...], acknowledge_warnings_available:true}\n"
            "凭据撞:\n"
            "  {ok:false, scan_phase:'credentials', error}\n\n"
            "底层: POST gateway /v1/wiki/documents/{namespace} JSON body, 跟 mcp-registry "
            "同 OIDC 鉴权 (X-Catfish-User-Sub 注入)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wiki_rel_path": {
                    "type": "string",
                    "description": "本机 wiki rel_path, 'wiki/' 开头 (例 'wiki/entities/老李.md')",
                },
                "namespace": {
                    "type": "string",
                    "description": "部门 namespace, 必须 'dept/<部门>' (例 'dept/finance')",
                },
                "acknowledge_warnings": {
                    "type": "boolean",
                    "description": (
                        "默认 false. 撞 PII / 内网 / 敏感词警告就拒. true 跳警告强 publish. "
                        "第一次失败拿到 warnings 后, 转告员工同意, 才能加这个参数 retry."
                    ),
                    "default": False,
                },
                "file_id": {
                    "type": "string",
                    "description": (
                        "可选. 重发同一 wiki (含修改) 时传上次拿到的 file_id, "
                        "服务端 upsert 同 row. 不传则服务端分配新 UUID."
                    ),
                },
            },
            "required": ["wiki_rel_path", "namespace"],
        },
        "emoji": "📤",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_wiki_install",
        "description": (
            "★ 从部门 wiki-hub 拉一条 wiki 装本机 (~/.catfish/wiki-shared/<ns>/<file_id>.md).\n\n"
            "✅ 调用时机:\n"
            "  - 员工在 WikiHubCard 浏览部门 wiki 后说'把这条装到本机'\n"
            "  - 员工说'查下部门里关于 X 客户的笔记'时, 找到 hub 上的相关 wiki 后\n\n"
            "input:\n"
            "  - hub_namespace: 'dept/<部门>'\n"
            "  - hub_file_id: 服务端分配的 file_id (从 list_documents 拿)\n\n"
            "Stale 拒绝:\n"
            "  如果该 wiki 已被原作者撤回 (stale_after_unpublish=true), 中央 body 已清零, "
            "  本工具拒装并告诉员工.\n\n"
            "装上后:\n"
            "  - WikiTree 'wiki-shared/<ns>' 下能看到 (Phase 3 加 UI)\n"
            "  - 文件 read-only (员工不能修改部门 wiki, 改了 publish 也不会触发更新)\n"
            "  - 写 install_meta sidecar (.meta.json) 记 publisher / pull 时间, 用于 stale 检查"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hub_namespace": {"type": "string", "description": "部门 namespace ('dept/finance' 等)"},
                "hub_file_id": {"type": "string", "description": "服务端 file_id (从 list_documents 拿)"},
            },
            "required": ["hub_namespace", "hub_file_id"],
        },
        "emoji": "📥",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_wiki_unpublish",
        "description": (
            "★ 撤回员工自己 publish 过的某条部门 wiki.\n\n"
            "✅ 调用时机:\n"
            "  - 员工说'撤回我之前 publish 的那条 X' / '别让部门看了'\n"
            "  - **必须**确认是员工本人发的 (服务端会拦 — 只能撤自己的, admin 例外)\n\n"
            "🛑 Manifesto 公理 4 提醒员工 (撤回前转告):\n"
            "  '中央那一份会清零 + 标 stale. 但已经 pull 装本机的同事副本不动 — "
            "  manifesto 禁中央触及员工本机. 信息已扩散这条撤不回, 心理上要接受.'\n\n"
            "input:\n"
            "  - hub_namespace: 'dept/<部门>'\n"
            "  - hub_file_id: 要撤回的 file_id\n"
            "  - reason (可选): 撤回原因, 写 audit\n\n"
            "成功:\n"
            "  - 中央 PG row 保留 (audit 需要), body_md / frontmatter 清零\n"
            "  - 中央 FS 镜像物理删\n"
            "  - 标 stale_after_unpublish=true. 客户端下次 list 看到 stale 标"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hub_namespace": {"type": "string", "description": "部门 namespace"},
                "hub_file_id": {"type": "string", "description": "要撤回的 file_id"},
                "reason": {"type": "string", "description": "撤回原因 (写 audit, 可空)"},
            },
            "required": ["hub_namespace", "hub_file_id"],
        },
        "emoji": "🗑️",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── P3.5.35 (6/18 鸿波 catch 'chat 是不是已经接了 wiki_search? + 装到本机后部门 wiki 不就是自家了吗') ──
    {
        "name": "catfish_wiki_search",
        "description": (
            "★★ 搜员工本机 wiki — 含自家 (~/.catfish/wiki/{entities,concepts,queries}) + "
            "装机部门 wiki (~/.catfish/wiki-shared/dept/<部门>/). BM25 char-level, 100% 离线.\n\n"
            "✅ 调用场景:\n"
            "  - 员工问 '我 wiki 里关于 X 写过啥?' / '之前记的 Y 在哪?'\n"
            "  - 员工问 '我们公司关于 Z 的流程' (本部门 / 跨部门规定)\n"
            "  - 员工问 '客户 W 在 wiki 里是不是有 entity?' \n"
            "  - 员工问 '部门 wiki 关于资质评估写了啥' (装机部门 wiki)\n\n"
            "❌ 不调用:\n"
            "  - 找 catfish 内部 (manifesto/patent/moat) — 用 catfish_search_docs\n"
            "  - 找 hermes skill — 用 catfish_search_skills\n"
            "  - 找邮件 / 会话 / 附件 — 走对应 catfish_email_search / catfish_session_messages_search / catfish_attachments_search\n\n"
            "返参:\n"
            "  - matches: top-K [{name, title, kind (entity/concept/query), source (own/dept/<部门>), head 1500字, rel_path, score}]\n"
            "  - count, total_indexed, summary, latency_ms\n"
            "  - 想看全文: 让员工 Wiki tab 打开 rel_path (LLM 没 wiki_read tool, 客户端 UI 操作)\n\n"
            "🔒 隐私: 直读 ~/.catfish/, 不走 gateway, 不上行中央. 跟 manifesto 公理 2 一致.\n"
            "  装机部门 wiki 物理上已在员工本机, 跟自家边界相同 (6/18 鸿波 audit catch).\n\n"
            "💡 思路 (跟 catfish_search_docs 同 Progressive Disclosure):\n"
            "  Tier 1 - 员工 Wiki tab 自己浏览\n"
            "  Tier 2 (本 tool) - LLM BM25 搜 + 拿 head 1500 字 + 路径\n"
            "  Tier 3 - 员工根据 rel_path 在 Wiki tab 打开看全文"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜的关键字 / 自然语言 ('openai 价格' / '资质评估流程' / '客户机房 IP' / '入职手续' 等)",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返多少份 (默认 5, 上限 15)",
                },
            },
            "required": ["query"],
        },
        "emoji": "📚",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── 8/3: wiki 读写闭环 ─────────────────────
    #
    # 起因是一次真实的绕圈: 员工让存一份材料进知识库, 存完知识体系 TAB 一直
    # 看不到。鲶鱼当时只有 search + ingest 两个工具, 没有任何一个能回答
    # 「TAB 现在到底有哪些条目」, 只好去翻 sqlite / embeddings / sync_turn 日志
    # 反推, 结论几乎全错。
    #
    # 补 list 是为了让它能查证而不是推测; 补 create 是因为 ingest 只能丢进
    # raw/sources/ 等后台蒸馏, 而 TAB 从不读那个目录 —— 员工要的「存了马上能
    # 看到」以前根本没有对应的工具。
    {
        "name": "catfish_wiki_list",
        "description": (
            "★★ 列员工个人知识库的全部条目 —— 返回的就是知识体系 TAB 显示的内容.\n\n"
            "✅ 调用场景:\n"
            "  - 员工说「知识库里怎么没有 X」/「存进去了但看不到」→ 先调这个查证, 不要靠猜\n"
            "  - 建条目前查有没有重名 / 该 update 还是 create\n"
            "  - 员工问「我知识库里都有啥」\n\n"
            "⚠ 这个工具跟 TAB 同源 (Companion 的 wiki_list_files, 直接扫\n"
            "  ~/.catfish/wiki/{entities,concepts,queries} 三个目录的 .md).\n"
            "  不在这个列表里 = TAB 里也看不到, 反之亦然. 没有第二个真相源 ——\n"
            "  wiki_embeddings.db 是语义搜索用的, 不决定 TAB 显示什么.\n\n"
            "  raw/sources/ 里的东西不在这里 —— 那是 ingest 的暂存区, 要等后台\n"
            "  蒸馏成 entity 才会出现.\n\n"
            "返参: {ok, total, items: [{rel_path, kind, slug, title, subtype, tags, "
            "sources, size_bytes, mtime}], truncated}"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": "只看某一类: entity / concept / query. 不传 = 全部",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返多少条 (默认 200, 按 mtime 新的在前)",
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
            "★★ 在员工个人知识库建一个新条目 (entity / concept) —— 写完立刻可见.\n\n"
            "跟 catfish_wiki_ingest 的分工 (最容易搞错的一点):\n"
            "  - catfish_wiki_ingest = 存一份原始材料进 raw/sources/, 等后台蒸馏出\n"
            "    实体, 24h 内. 蒸馏完成前知识体系 TAB 看不到它.\n"
            "  - catfish_wiki_create (本工具) = 现在就要一个知识库条目. 直接写\n"
            "    wiki/entities/ 或 concepts/, 员工切走再切回 TAB 就能看到.\n\n"
            "员工说「存进知识库」而且希望马上能看到 → 用这个.\n"
            "员工给的是一份文件/附件, 只是想归档留底 → 用 catfish_wiki_ingest.\n"
            "两个都想要 → 两个都调 (ingest 留原件, create 建可见条目).\n\n"
            "⚠ 建之前先调 catfish_wiki_list 看有没有重名. 已存在会报错让你改走\n"
            "  catfish_wiki_update; 规范化等价的名字 (中电系-资质 vs 中电系资质) 也会\n"
            "  被拦, 防同一个东西躺两份文件.\n\n"
            "⚠ title 支持中文, 文件名直接用中文, 不要自己转拼音.\n\n"
            "返参: {ok, rel_path, bytes, created} 或 {ok: false, error, rel_path?}"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": "entity (具体的人/公司/系统/证书) 或 concept (流程/方法/概念)",
                },
                "title": {
                    "type": "string",
                    "description": "条目标题, 中文即可 (会直接作为文件名, 只替换 / : * ? 这类文件系统敏感字符)",
                },
                "body": {
                    "type": "string",
                    "description": "正文 markdown (不含 frontmatter 和一级标题 —— 那两样会自动加)",
                },
                "subtype": {
                    "type": "string",
                    "description": "必填细分类型, 写进 entity_type / concept_type (如 person / org / process / 资质)",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "标签",
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
                                    "rel": {"type": "string"},
                                },
                                "required": ["name", "rel"],
                                "additionalProperties": False,
                            },
                        ]
                    },
                    "description": "关联条目；优先传 {name, rel}，只传标题会进入待确认状态",
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
            "读个人知识库某个条目的全文 —— 用于自检「我刚写进去的到底成了什么样」.\n\n"
            "rel_path 从 catfish_wiki_list 或 catfish_wiki_search 的返参里拿\n"
            "(形如 wiki/entities/中电系资质对标对齐矩阵.md).\n\n"
            "返参含 visible_in_tab —— 直接告诉你这个文件在不在知识体系 TAB 里,\n"
            "省得再去猜为什么员工看不到."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rel_path": {
                    "type": "string",
                    "description": "相对 ~/.catfish/ 的路径, 必须 wiki/ 开头",
                },
            },
            "required": ["rel_path"],
        },
        "emoji": "📖",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_wiki_update",
        "description": (
            "改个人知识库里已有的条目 —— 整文件覆写, 不是追加.\n\n"
            "所以要先 catfish_wiki_read 拿到现有全文, 在它基础上改, 再整篇传回来.\n"
            "直接传一段新内容会把原来的全冲掉.\n\n"
            "建新条目用 catfish_wiki_create (那条路有重名检测), 这个只改已存在的.\n\n"
            "⚠ 返参里的 warning: 如果覆写后的内容太短又没有 frontmatter, 会命中\n"
            "  墓碑规则被 TAB 隐藏 —— 那种「写成功了但看不见」会当场告诉你."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rel_path": {
                    "type": "string",
                    "description": "相对 ~/.catfish/ 的路径, 必须 wiki/ 开头",
                },
                "content": {
                    "type": "string",
                    "description": "完整的新内容 (含 frontmatter). 这是覆写.",
                },
            },
            "required": ["rel_path", "content"],
        },
        "emoji": "✏️",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_wiki_trace",
        "description": (
            "★★ 查一条知识库条目**是从哪来的** —— 顺着 sources → journal → 原始对话.\n\n"
            "为什么重要: 知识库 66% 的条目是 employee_journal 蒸馏出来的, 而 journal\n"
            "本身又是对话摘要. 从原始对话到条目一共过了四次 LLM (summarize →\n"
            "analysis → generation → merge), 失真是压缩的物理必然.\n\n"
            "✅ 调用场景:\n"
            "  - 员工说「这条写得不对」/「这个数字哪来的」/「我什么时候说过这个」\n"
            "  - 你自己引用知识库某条做判断前, 拿不准可信度\n"
            "  - 员工问某条是人写的还是 AI 蒸馏的\n\n"
            "⚠ **不要替员工判断对错**. 把原始对话摆出来让他自己看 —— 他才是那次\n"
            "  对话的当事人, 你不是.\n\n"
            "⚠⚠ **provenance 字段决定这份证据有多硬, 引用前必须看**:\n"
            "  · recorded          —— sources 记着 journal:日期, 顺链接到了真实会话\n"
            "  · recorded-material —— sources 指向 raw/sources 的原始材料, 直接看那份\n"
            "  · inferred          —— **按标题检索出来的, 不是记录的来源**. 命中只说明\n"
            "     那些材料提到了同一个词, **不能证明条目是从它们蒸馏来的**. 转述时必须\n"
            "     把这一点说给员工, 不许当成确证.\n"
            "  · none              —— 查不到. 照实说查不到, 不许编。\n\n"
            "实测覆盖 (鸿波机器 255 条): recorded 28% / recorded-material 25% /\n"
            "inferred 38% / none 7%.\n\n"
            "verdict 还会说清链断在哪: 靠 session id 后缀匹配的会标出来 (存量 journal\n"
            "只记了后 6 位, 同一天内基本唯一但不保证)。\n\n"
            "with_messages=true 才拉原始对话正文, 默认只给会话清单 (省 token)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rel_path": {
                    "type": "string",
                    "description": "条目路径, 从 catfish_wiki_list / catfish_wiki_search 拿",
                },
                "with_messages": {
                    "type": "boolean",
                    "description": "是否拉原始对话正文 (默认 false, 只给会话清单)",
                },
            },
            "required": ["rel_path"],
        },
        "emoji": "🔍",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── P3.5.177 (7/6 鸿波军规审判): 员工显式入库 wiki (kill P16 auto-ingest) ──
    #
    # 老 P16 (6/5) fire-and-forget: 员工上传文件 → ChatInput 严格自动写
    # ~/.catfish/wiki/raw/sources/ → sync_turn 3b 严格自动抽 entity/concept.
    # 员工 7/6 反馈"不是所有文档都要进知识库, 严格员工显式说才入库".
    #
    # P3.5.177 fix:
    # 1. 严格删 ChatInput.tsx:200-205 fire-and-forget for-loop (员工 send 时
    #    不自动 ingest — 上传只是 chat context, 不污染 wiki).
    # 2. 严格加本 tool: LLM 严格识别员工"入库/存 wiki/记住这个文档" 语义 → 调
    #    本 tool → 写 sources/ → sync_turn 3b 严格自动扫 → Analysis + Generation
    #    LLM 严格抽 entity/concept → wiki/entities/ + wiki/concepts/.
    # 3. 严格加 SOUL guidance: 员工明说"入库" 严格 LLM 才调 (不是默认自动).
    #
    # 严格 LLM 判定原则 (AI-first, 不 hardcode 关键词):
    # - 员工**明说**要存到知识库/wiki/记住这个 → 调本 tool
    # - 员工只是**分享文件供你参考** → 不调 (只走 chat context)
    # - 员工**问题里带附件** (e.g. "看看这份合同能不能签") → 不调 (聊天场景, 不入库)
    # - 员工犹豫 → **不调, 问员工"这个要存到知识库吗?"** (员工主权军规)
    {
        "name": "catfish_wiki_ingest",
        "description": (
            "**员工显式** 要求把 chat 附件存到个人 wiki 知识库时才调. LLM 严格判 "
            "员工语义, 不 hardcode 关键词.\n\n"
            "**触发场景 (员工明说要存)**:\n"
            "  - '把这份组织架构存到知识库'\n"
            "  - '这份合同存 wiki'\n"
            "  - '记住这个文档, 以后可能会问'\n"
            "  - '这个入库'\n"
            "  - '把这份材料加进我的知识体系'\n\n"
            "**不触发场景 (员工只是分享给你参考)**:\n"
            "  - '看看这份合同能不能签' — 只是问, 不要入库\n"
            "  - '这是今天开会的记录, 帮我总结' — 只是分析, 不要入库\n"
            "  - 员工不确定 → **问员工 '这个要存到知识库吗?'**, 不擅自调 (员工主权军规)\n\n"
            "**调用后**: 文件复制到 ~/.catfish/wiki/raw/sources/, sync_turn 3b 后台扫\n"
            "→ LLM 抽 entity/concept 到 wiki/entities/ + concepts/ (24h 内).\n\n"
            "⚠⚠ **蒸馏完成前, 知识体系 TAB 看不到它**. TAB 只列\n"
            "  wiki/{entities,concepts,queries} 三个目录, 从不读 raw/sources/.\n"
            "  所以调完这个工具**不要**告诉员工「已经进知识库了, 去 TAB 看」——\n"
            "  他会看不到, 然后你会陷进一轮查不出原因的排查 (8/3 真实发生过).\n"
            "  如实说: 原件已归档, 条目要等后台蒸馏.\n\n"
            "💡 员工希望**马上能在 TAB 看到** → 改用 catfish_wiki_create 直接建条目.\n"
            "  两者不冲突: ingest 留原始材料存档, create 建立即可见的条目, 需要就都调.\n\n"
            "**注意**: 员工 chat 上传的附件, kept_path 严格从 message context 里拿 "
            "(前端 attachment.keptPath). parsed_text_path 严格是 preview 大文件的 sidecar\n"
            "(前端 attachment.parsedTextPath), 优先用它 (binary xlsx/pdf 严格拿不到\n"
            "原文, sidecar 已 parse 出文本)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kept_path": {
                    "type": "string",
                    "description": (
                        "原文件 path (chat attachment.keptPath). 员工 chat 上传后 "
                        "parse_file_from_b64 严格 ship 到 ~/.catfish/uploads/. 严格员工上传 "
                        "严格 attachment.keptPath 直接传."
                    ),
                },
                "parsed_text_path": {
                    "type": "string",
                    "description": (
                        "严格 sidecar 严格 parsed 文本 path (chat attachment.parsedTextPath). "
                        "严格 binary file (xlsx/pdf/word) 严格 parse 出的纯文本 sidecar. "
                        "严格优先用它读全文, 若空 fallback 读 kept_path (小 text file)."
                    ),
                },
                "filename": {
                    "type": "string",
                    "description": "员工原文件名 (chat attachment.name), 用于 wiki source 严格 filename 字段展示.",
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "员工要求入库的具体理由 (员工原话或你严格转述, 30-100 字). "
                        "写到 wiki source frontmatter reason 字段, 供后续员工查. "
                        "例: '福富组织架构 2026 年 4 月版, 员工要建立知识体系'."
                    ),
                },
            },
            "required": ["kept_path", "filename"],
        },
        "emoji": "📥",
        "toolset": "catfish_native",
        "available": True,
    },
]
