"""Catfish 原生 tools schemas — 从 catfish_tools.py 抽出 (5/20 拆分超 6400 行规则).

50 个 tool 的 JSON Schema (name / description / parameters), 给 LLM 看的描述.
实现在 catfish_tools.py 里 (lazy import 各子 module).

拆分历史:
- 5/20 之前: 全在 catfish_tools.py (6454 行, 严重超 check_file_sizes.sh 800 红线)
- 5/20 末第一刀: 抽 2088 行 schemas 到本文件. catfish_tools.py 减到 ~4400 行
- 后续 sprint: 按 category 拆 impl (browser / skill / today / propose ...)

红线: 加新 tool 时, schema 加这里, impl 加 catfish_tools.py 或对应子 module,
dispatch 加 catfish_tools._dispatch_native_inner. **dispatch 走原文件, 不重组.**
"""
from __future__ import annotations

from typing import Any, Dict, List


CATFISH_NATIVE_TOOLS: List[Dict[str, Any]] = [
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
    {
        "name": "catfish_screenshot",
        "description": (
            "拍员工**整个 mac 屏幕** 一张图, 返回 base64 PNG. 跨 app / 跨窗口场景用. "
            "给视觉模型 (Qwen3.5 122B / Qwen3-VL / Gemini vision / Qwen-Flash 多模态) "
            "看员工 GUI 上的内容. \n\n"
            "⚠️ **看浏览器内容用 catfish_browser_screenshot, 不是这个**. 验证码 / 网页"
            "按钮 / 弹窗 / 页面布局 → 一律 `catfish_browser_screenshot` (Playwright 直截 "
            "Chrome tab, 不要权限不会失败). 这条 `catfish_screenshot` 走 mac screencapture, "
            "需要屏幕录制权限, 跨窗口场景才用.\n\n"
            "✅ 调用场景 (跨 app):\n"
            "  - 员工说「这个报错是什么意思」「我屏幕上 X 是什么」 (非浏览器内)\n"
            "  - 员工说「截屏看下」「你看一下我这边」 (非浏览器内)\n"
            "  - GUI 调试: 看 Finder / Excel / 别的 app 的按钮 / 对话框\n"
            "  - 跨窗口: 浏览器 + 别的 app 一起看\n\n"
            "❌ 不该调用:\n"
            "  - **看浏览器内容 → catfish_browser_screenshot** (验证码 / 网页都走那个)\n"
            "  - 看本地文件 → 用 read_file\n"
            "  - 员工没明确要求看屏幕但你「想看一下」——不要主动截\n\n"
            "🔒 默认 mode=fullscreen: 拍员工主屏当前内容. 0 权限 0 打扰. "
            "其他模式: interactive=员工框选区域, window=员工点选窗口. \n\n"
            "调用前 reason 一句话说明为啥, 员工会看到. \n\n"
            "**底层细节** (你不用关心): 工具返 data_uri (base64), gateway 自动把它重组成"
            "下一轮 user 的 multipart image (上游 Qwen OpenAI 兼容 adapter 标准格式), "
            "你下一轮直接当 multimodal input 看图分析."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["fullscreen", "interactive", "window"],
                    "default": "fullscreen",
                    "description": (
                        "fullscreen=全屏(默认, 零打扰零权限, 拍员工主屏当前内容). "
                        "interactive=员工框选(精确选区, 隐私优先). "
                        "window=员工点选某个窗口. "
                        "(注: active_window 模式已废弃 — 它走 osascript 'tell application System Events' 会反复弹 macOS Automation 权限对话框, 体验差)"
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "为什么要截图 — 一句话, 员工会看到",
                },
            },
            "required": ["reason"],
        },
        "emoji": "📸",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_browser_goto",
        "description": (
            "**仅用于 URL 导航** — 在 Chrome 当前 tab 打开一个 URL. 走 Playwright "
            "`page.goto()` (connect_over_cdp 复用 Companion 起的 Chrome). \n\n"
            "❌ **不要用这个工具跑 JavaScript / DOM 查询.** 想 `document.querySelector(...)` "
            "/ 读 DOM 用 **catfish_browser_evaluate**, 不是 goto. goto 的 `expression` "
            "字段**不存在**, 传了 schema 直接拒. \n\n"
            "✅ **浏览器导航永远用这个**, 不要用 hermes browser_navigate (那个直 CDP, 失败率高). \n\n"
            "wait_until 选项: 'load' (默认, 等所有资源加载完) / 'domcontentloaded' (只等 DOM, "
            "更快但 JS 可能没跑完) / 'networkidle' (等 500ms 无网络活动, 适合 SPA). \n\n"
            "返回真实页面 title + url, 让你验证 navigate 真生效."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "完整 URL, 例 'https://www.sohu.com'"},
                "wait_until": {
                    "type": "string",
                    "enum": ["load", "domcontentloaded", "networkidle"],
                    "default": "load",
                    "description": "等到什么状态才返回. SPA 用 networkidle, 普通页面 load",
                },
                "timeout_seconds": {
                    "type": "number",
                    "default": 30.0,
                    "description": "navigate 总超时 (秒). 默认 30s",
                },
            },
            "required": ["url"],
            # BL-TOOLBRIDGE-CONSOLE-TOOL (5/27 鸿波): 5/26 晚 LLM 死循环踩过坑 —
            # 模型传 expression=... 想跑 JS, schema 之前没拒, 后端检查 url 是空
            # 报"url 必填", LLM retry 再 retry 一直转. additionalProperties:false
            # 让未知字段在 schema 验证层拒, 强迫 LLM 看 description 切去 evaluate.
            "additionalProperties": False,
        },
        "emoji": "🌐",
        "toolset": "catfish_native",
        "available": True,
    },
    # BL-TOOLBRIDGE-CONSOLE-TOOL (5/27 鸿波): JS eval 工具 — 修 5/26 晚的死循环
    # 起源见 catfish_tools_browser.py::browser_evaluate. 双名: evaluate (规范) +
    # console (alias, LLM 习惯). dispatch 两个都路同一 impl.
    {
        "name": "catfish_browser_evaluate",
        "description": (
            "在 Chrome 当前 page 跑一段 **JavaScript** 表达式, 返结果值. 走 Playwright "
            "`page.evaluate()`, 跟在 DevTools Console 跑 JS 等价能力. \n\n"
            "✅ **想读 DOM / 查元素 / 跑 JS 算法都用这个**: \n"
            "  - `document.title` → 拿页面标题\n"
            "  - `document.querySelectorAll('a').length` → 数链接\n"
            "  - `document.querySelector('li[data-id=42]')?.textContent` → 取元素文本\n"
            "  - `Array.from(document.querySelectorAll('.item')).map(x=>x.innerText)` → 抽列表\n\n"
            "❌ **不要用 catfish_browser_goto 跑 JS** — 那个只接 URL.\n\n"
            "返的 value 是 JSON-safe (字符串 / 数字 / null / 数组 / 对象). DOM Node 之类"
            "不可序列化的会变 None 或字符串. 巨型字符串 / 对象会被截断.\n\n"
            "**安全**: 跟 DevTools 同权限. 跑前确认页面不是敏感页 (银行 / 政务)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": (
                        "JavaScript 表达式 (推荐) 或 IIFE. 例: 'document.title', "
                        "'(() => Array.from(document.querySelectorAll(\"li\")).map(x=>x.innerText))()'. "
                        "**不要传裸语句** (`let x = 1`), 那不是表达式会报 SyntaxError."
                    ),
                },
                "timeout_seconds": {
                    "type": "number",
                    "default": 10.0,
                    "description": "JS 执行超时 (秒). 默认 10s, 上限 30s.",
                },
            },
            "required": ["expression"],
            "additionalProperties": False,
        },
        "emoji": "🟨",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_browser_console",
        "description": (
            "**catfish_browser_evaluate 的 alias** — 推荐用 evaluate; 这个保留是因为"
            "有些 prior (Anthropic Computer Use / 老 Playwright MCP) 习惯叫 console. "
            "参数 / 返回 / 行为跟 catfish_browser_evaluate 完全一致."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "JavaScript 表达式, 跟 catfish_browser_evaluate 一致.",
                },
                "timeout_seconds": {
                    "type": "number",
                    "default": 10.0,
                },
            },
            "required": ["expression"],
            "additionalProperties": False,
        },
        "emoji": "🟨",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_browser_click",
        "description": (
            "点击页面元素. 两种模式 (二选一):\n\n"
            "**模式 1 — coordinates (5/11 BL-FIX44 新加, 推荐用于截图场景)**\n"
            "  传 coordinates=[x, y] 像素坐标, 走 Playwright mouse.click 直点.\n"
            "  ★ 当你刚 catfish_browser_screenshot 截了图, 视觉上看到按钮位置时用这个\n"
            "  ★ 完全绕开 selector 歧义 (避免抓到 placeholder/label 这种坑)\n"
            "  ★ 注意: 没 auto-waiting, 页面得已经渲染好\n"
            "  例: catfish_browser_click(coordinates=[450, 380])\n\n"
            "**模式 2 — selector (老模式, 适合无截图 / DOM 稳定的场景)**\n"
            "  走 Playwright `page.click(selector)` 内置 auto-waiting (等出现 + visible + clickable).\n"
            "  selector 用 CSS / role 语法:\n"
            "    - CSS: 'button#submit' / 'input[name=\"username\"]'\n"
            "    - role: 'role=button[name=\"提交\"]' (无障碍语义, 最稳)\n"
            "    - text: 'text=登录' (易歧义, 不推荐)\n"
            "  优先 role= 其次 CSS, **避免 text=** (鸿波 5/11 实测撞 placeholder 翻车).\n\n"
            "**怎么选**:\n"
            "  - 刚截了图 → coordinates (直接, 无歧义)\n"
            "  - 调过 catfish_browser_find_by_text → 用返的 top.selector (精确)\n"
            "  - 知道 DOM 结构 → selector\n"
            "  - 都不知道 → 先 catfish_browser_screenshot 看一眼\n\n"
            "**报错**: 提示在 error 字段, 还会建议改用哪条路径."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "Playwright selector (CSS / role=). 跟 coordinates 二选一.",
                },
                "coordinates": {
                    "type": "array",
                    "description": "[x, y] 像素坐标, 看截图找位置时用. 跟 selector 二选一. 例 [450, 380].",
                    "items": {"type": "integer"},
                    "minItems": 2,
                    "maxItems": 2,
                },
                "timeout_seconds": {
                    "type": "number",
                    "default": 30.0,
                    "description": "selector 模式等元素可点击的最长时间, 默认 30s. coordinates 模式忽略.",
                },
            },
        },
        "emoji": "🖱",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_browser_fill",
        "description": (
            "往输入框填文字. 走 Playwright `page.fill()`, auto-waiting 等输入框可写. "
            "适合 input / textarea / [contenteditable]. 自动清空原值再填, 不需要先 click.\n\n"
            "**填密码的两种方式**:\n"
            "  1. **推荐 secret_ref**: secret_ref='keychain://eis_password' (macOS) 或 "
            "'env://EIS_PASSWORD' (跨平台). tool-bridge 从安全源拉值, **密码永不进 LLM 上下文**, "
            "audit log 只记 secret_ref 引用不记密码值. 员工事先用 `security add-generic-password "
            "-a $USER -s eis_password -w '<密码>'` 存到 keychain.\n"
            "  2. **text 直传 (不推荐密码场景)**: text='jiniaA1+' 直接填, 会在 audit 标记 "
            "'credential_field_filled' 但密码已经在 LLM 上下文了.\n\n"
            "**两个字段二选一**: 给了 secret_ref 就忽略 text, 反之亦然. 都没给 → error."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "Playwright selector, 例 'input[name=\"username\"]'",
                },
                "text": {
                    "type": "string",
                    "description": "要填的文字 (明文). 用户名 / 邮箱 / 内容首选这个. 密码场景优先用 secret_ref.",
                },
                "secret_ref": {
                    "type": "string",
                    "description": (
                        "安全源引用, 例 'keychain://eis_password' / 'env://EIS_PASSWORD'. "
                        "tool-bridge 自动拉值, LLM 不会看到真值. 推荐密码场景用这个."
                    ),
                },
                "timeout_seconds": {
                    "type": "number",
                    "default": 10.0,
                    "description": "等元素可写的最长时间. 默认 10s",
                },
            },
            "required": ["selector"],
        },
        "emoji": "⌨️",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_browser_snapshot",
        "description": (
            "拿当前页面的结构化 DOM snapshot (含 visible text + role + selector_hint). 给模型"
            "当「上下文」用 — 想点哪个按钮先 snapshot 看 selector_hint, 直接拿来填 "
            "browser_click(selector=...) / browser_fill(selector=...).\n\n"
            "**双路径**: 优先 Playwright `page.accessibility.snapshot()`; 新版 Playwright "
            "(>=1.50) accessibility 已废弃, 自动 fallback `page.evaluate()` 走 JS 扫 "
            "button/input/a/[role]. 返回里 `snapshot_method` 字段会标明实际走哪条.\n\n"
            "**⚠️ max_elements 用默认 500 别主动减小**. 找不到要点的元素时**加大到 1000**, "
            "不是减小. 减小只会让你少看到关键按钮 (尤其登录 / 提交 这类常被 nav/footer link "
            "挤出 top N). 截断时 `truncated=true` + `hint_for_llm` 字段会提示你怎么改.\n\n"
            "返回字段:\n"
            "  - title: 页面 title\n"
            "  - url: 页面 url (真实 location.href)\n"
            "  - elements: 可见 / 可交互元素列表, 每个含:\n"
            "      role (button/textbox/link...) + name (label/placeholder/innerText) + "
            "depth + selector_hint (#id / tag[name=...] / tag.cls)\n"
            "  - snapshot_method: 'accessibility' / 'dom_evaluate'\n"
            "  - truncated: bool, 超 max_elements 时 true\n"
            "  - hint_for_llm: 截断时的下一步建议 (加大 max_elements 重调)"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "max_elements": {
                    "type": "integer",
                    "default": 500,
                    "description": (
                        "最多返回多少个元素, 防 IPC 撑爆. 默认 500, cap 1000. "
                        "⚠️ 找不到元素时加大不是减小. 首次调用建议不传, 用默认值."
                    ),
                },
            },
            "required": [],
        },
        "emoji": "🔍",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_browser_screenshot",
        "description": (
            "截**浏览器当前 tab** 的图. 走 Playwright `page.screenshot()`, 返 data:image "
            "base64 让模型直接看. 看**验证码 / 按钮位置 / 页面布局 / 弹窗内容** 都走这个.\n\n"
            "⚠️ 区别于 `catfish_screenshot` — 那个走 mac screencapture, 截整个屏幕 "
            "(含其他 app / 跨窗口); 这个只截当前 Chrome tab, 没权限弹窗 / 不需要员工框选.\n\n"
            "**BL-FIX17 (5/8): 自动智能压缩** — viewport / full_page 截图自动 downscale + JPEG, "
            "vision 模型一样能识别但**省 90% size + token + 推理时间**. 默认 ~150-300KB:\n"
            "  - selector 给元素 (例 `#captchaImg`): 不缩 PNG (元素本来就小, 保真重要)\n"
            "  - viewport 默认: max 1280px 边 + JPEG q=80, ~200KB\n"
            "  - full_page: max 1600px 边 + JPEG q=75, ~400KB\n"
            "  - 压完 >800KB: 自动降 q=60 重压. 还不行返 error 让 LLM 改 selector\n"
            "  - compress='none' 强制保留原 PNG (员工显式说'要原图'才传)\n\n"
            "**返回字段加** size_kb_before / size_kb_after / compression_ratio / format, "
            "LLM 看到知道压缩了多少."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": (
                        "可选 — Playwright selector. 给了只截这个元素 (推荐验证码场景, 保真 PNG 不压), "
                        "不给截整个 viewport (会自动 downscale + JPEG)"
                    ),
                },
                "full_page": {
                    "type": "boolean",
                    "default": False,
                    "description": "true=截整个滚动长度 (含 fold 下面), false=只截 viewport",
                },
                "timeout_seconds": {
                    "type": "number",
                    "default": 10.0,
                    "description": "等元素 / 页面加载的最长时间, 默认 10s",
                },
                "compress": {
                    "type": "string",
                    "enum": ["auto", "none"],
                    "default": "auto",
                    "description": (
                        "auto (默认, 推荐): 视情况 downscale + JPEG, 跑得快不卡死. "
                        "none: 不压, 保留原 PNG (员工要看小字 / 高保真证据用, 后果自负 size 4MB+)"
                    ),
                },
            },
            "required": [],
        },
        "emoji": "📸",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_browser_find_by_text",
        "description": (
            "**按文字找元素, 返排序候选 + 元数据**, LLM 看 role/match_type/clickable 挑.\n\n"
            "**BL-FIX44 (5/11) 重写**: 老版返单个 element 容易抓错 (placeholder 撞文字). "
            "新版返**多个候选**, 每个含完整元数据让 LLM 判断, top_recommendation 给最佳猜测.\n\n"
            "**返回结构**:\n"
            "```json\n"
            "{\n"
            "  \"elements\": [  // 按 score 倒序\n"
            "    {\n"
            "      \"selector\": \"role=button[name=\\\"登录\\\"]\",  // 直接喂 click 的 selector\n"
            "      \"tag\": \"button\",         // HTML tag\n"
            "      \"role\": \"button\",        // ARIA role (显式或隐式)\n"
            "      \"text\": \"登 录\",         // 实际匹配到的文字\n"
            "      \"match_type\": \"innerText\",  // innerText/value/aria-label/placeholder/title/alt\n"
            "      \"is_clickable\": true,     // 真可点 vs 普通文本\n"
            "      \"bounds\": {\"x\":450,\"y\":380,\"w\":120,\"h\":40},\n"
            "      \"center\": {\"x\":510,\"y\":400},  // 供 click coordinates 直点\n"
            "      \"in_viewport\": true,\n"
            "      \"score\": 95\n"
            "    }\n"
            "  ],\n"
            "  \"top_recommendation\": <同上, 第 1 个 clickable 候选>,\n"
            "  \"summary\": \"找到 N 个含 'X' 的元素. 推荐: ...\"\n"
            "}\n"
            "```\n\n"
            "**怎么挑**:\n"
            "  1. 看 top_recommendation 是不是 role=button + is_clickable + match_type=innerText\n"
            "     → 是的话直接用 top.selector 或 top.center 坐标 click\n"
            "  2. 不是 → 扫 elements 列表, **优先选** is_clickable=true 且 match_type=innerText 的\n"
            "  3. 都是 placeholder 匹配 (输入框) → 不是按钮, 重传 role='button' 过滤\n\n"
            "**找登录按钮专用模式**: 传 role='button', 候选只剩真按钮, 避开 placeholder 坑.\n"
            "  catfish_browser_find_by_text(text='登录', role='button')\n\n"
            "**找不到** (element_count=0) → 别硬找, 改 catfish_browser_screenshot 看视觉, "
            "再用 catfish_browser_click(coordinates=[x,y]) 直点.\n\n"
            "**文字匹配**: 子串/精确 (exact 参数). 'login' 不匹配 '登录'. 中文给中文."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": (
                        "要找的元素文字 (中文 / 英文 / 数字都行). 例: '登录' / '提交' / "
                        "'下一步' / 'Submit'."
                    ),
                },
                "role": {
                    "type": "string",
                    "description": (
                        "可选, ARIA role 过滤. 'button' / 'link' / 'textbox' / 'checkbox' / "
                        "'menuitem' / 'tab' 等. 找真按钮一定传 'button' 避开 placeholder 坑."
                    ),
                },
                "exact": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "true=完全匹配 (text='登录' 不匹配 '登录用户'); "
                        "false=部分匹配 (默认, 更宽松). 优先 false."
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "default": 10,
                    "description": "最多返回多少候选, 默认 10. 同名按钮多 (例'提交') 加大. 上限 30.",
                },
            },
            "required": ["text"],
        },
        "emoji": "🔎",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_skill_backup",
        "description": (
            "更新 / 删除一个 skill 之前**必须**调这个 tool 做 backup. "
            "把当前 SKILL.md 复制到 ~/.hermes/skills/<ns>/<skill>/.versions/<unix-ts>.md. "
            "员工说 '回退 X skill' 时, 模型可以从 .versions/ 拿最近一版替换. \n\n"
            "✅ 调用时机:\n"
            "  - skill_manage(action=update) 之前\n"
            "  - skill_manage(action=delete) 之前 (即使要删, 也留 .versions/ 历史)\n\n"
            "❌ 不该调的场景:\n"
            "  - skill_manage(action=create) (新建无老版可备)\n"
            "  - 员工跟你聊天没明确要改 skill\n\n"
            "调用后会返回 {ok, backup_path, version_count} 让你确认 backup 真做了, "
            "然后再调 skill_manage update/delete 才合规. "
            "不调直接 update 会被 catfish-policy R10 deny."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": (
                        "skill 全名, 例如 'productivity/catfish-email' 或 "
                        "'productivity/expense-submit'. 用 / 分隔 namespace 和 skill 名."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "为啥要改/删这个 skill — 一句话, 员工会看到, 也写日志",
                },
            },
            "required": ["skill_name", "reason"],
        },
        "emoji": "💾",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_run_skill",
        "description": (
            "调用 catfish 工程审定 skill (含**凝固后的浏览器自动化 skill** 如 "
            "eis-login + **渲染类 skill** 如 weekly-report). 优先于自己写代码 / "
            "自己 step-by-step 调 catfish_browser_* — gateway 在 system prompt "
            "已经把可用 skill 列表注入给你, 看到列表里有匹配的**立即**调.\n\n"
            "✅ 调用场景:\n"
            "  - 员工说'登 EIS' / '上 EIS 看待办' → catfish_run_skill("
            "skill_path='department/eis-login', params={'username': 'chenhb'})\n"
            "  - 员工说'写给领导的请示件' → catfish_run_skill(skill_path="
            "'department/leadership-briefing', params={...})\n"
            "  - 任何 skill 列表覆盖的场景\n\n"
            "❌ 不该调用:\n"
            "  - skill 列表里没有的能力 → 走 execute_code 临时写, **不要** 用 "
            "catfish_browser_* 手工干 skill 该干的事\n\n"
            "**第一次不知道参数?** params={'_help': True} 调一次拿 schema.\n\n"
            "**★★★ skill 失败时的铁律 (BL-MM9-FREEZE-v2 5/12)** ★★★:\n"
            "  如果本 tool 返 ok=false (例 EIS skill goto 冷启动失败), **绝对不要**\n"
            "  自己调 catfish_browser_goto / fill / click 等手工接管 — skill 里的 "
            "selector 是教学时验证过的, 你手工推的 selector 不可靠, 会污染 chrome "
            "状态 + 走偏. **必须**:\n"
            "    1. 把失败原因清楚告诉员工 (skill 名 + error 字段)\n"
            "    2. 问员工: '要不要再试一次 / 重教这个 skill / 我手工接管?'\n"
            "    3. 员工 explicit 说手工 → 才允许调 catfish_browser_*\n"
            "  这是 catfish 凝固 skill 的核心承诺 — skill 失败 ≠ 你接手, skill 失败 "
            "= 报告员工.\n\n"
            "**返回**: {ok, files: [paths], summary, error}. files 自动渲染成 pill."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_path": {
                    "type": "string",
                    "description": (
                        "skill 相对路径, 来自 system prompt 注入的 skill 列表. "
                        "例: 'department/leadership-briefing'. 不带前导 / 后导 /."
                    ),
                },
                "params": {
                    "type": "object",
                    "description": (
                        "skill render 函数的入参. 不知道传什么时, 用 "
                        "{'_help': True} 调一次拿 schema."
                    ),
                },
            },
            "required": ["skill_path", "params"],
        },
        "emoji": "📑",
        "toolset": "catfish_native",
        # 4-30 一度试 B 方案 (hermes 原生) 失败, 立刻撤回. catfish_run_skill 是
        # 模型唯一靠谱的 catfish skill 调用入口, 必须 available=True.
        "available": True,
    },
    {
        "name": "catfish_a2a_ask",
        "description": (
            "Plan D · Catfish Federation — 问另一个员工的鲶鱼一个问题. "
            "五一 sprint Day 4-5 ship.\n\n"
            "✅ 调用场景:\n"
            "  - 员工说 '问下张三老板对项目 X 怎么看' / '问下小李上周做了什么' / "
            "    '让我们看看王五对方案怎么想'\n"
            "  - 你 (鲶鱼) 替员工查另一员工的公开偏好/项目状态\n"
            "  - 注意: 这是**跨员工**信息查询, 不是查公司文档\n\n"
            "❌ 不该调用:\n"
            "  - 员工自己的事 (你直接回答)\n"
            "  - 查文档 / 数据库 (用其他工具)\n"
            "  - 涉及敏感隐私 (B 的 ALLOW.md 默认会拒绝)\n\n"
            "**隐私边界**: B 的鲶鱼会按 B 自己写的 ALLOW.md 决定能不能答.\n"
            "  - 命中 allow → B 回答\n"
            "  - 命中 deny / 没匹配 → 拒绝, 你告知员工\n\n"
            "**audit**: 双方鲶鱼都会写 ~/.catfish/a2a_audit.jsonl, 客户 IT 可审."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to_sub": {
                    "type": "string",
                    "description": (
                        "目标员工的 SSO sub (邮箱形式), 例 'bob@ffcs.cn'. "
                        "你不知道的时候反问员工要."
                    ),
                },
                "question": {
                    "type": "string",
                    "description": (
                        "替员工问 B 的问题, 1 句话, 不超过 500 字. "
                        "尽量具体, 含关键词 (B 的 ALLOW.md 是关键词匹配)."
                    ),
                },
                "purpose": {
                    "type": "string",
                    "description": (
                        "用途分类, 例 '周报' / '汇报' / '咨询' / '协作'. "
                        "B 的 ALLOW.md 可能限定 allow_purpose, 填准了命中率高."
                    ),
                    "default": "",
                },
                "context_hint": {
                    "type": "string",
                    "description": (
                        "解释 A 员工为什么问这个 (1 句话). 帮 B 决定怎么答. "
                        "例: 'alice 要给老板汇报' / 'bob 的同事在做类似项目'."
                    ),
                    "default": "",
                },
            },
            "required": ["to_sub", "question"],
        },
        "emoji": "🤝",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_skill_install",
        "description": (
            "本机安装一个 skill — 两种来源二选一:\n"
            "  (A) 本机目录 source_dir (例 ~/Downloads/x-skill/) — Day 3 MVP, 同事拿目录给员工的场景\n"
            "  (B) 中央 Skills Hub hub_skill (例 'shared/feishu-expense@latest') — Phase 2 (5/5 ship)\n\n"
            "✅ 调用场景:\n"
            "  - 员工说 '装 hub 里的 X skill' / '从 hub 拿 Y' → 用 hub_skill\n"
            "  - 员工说 '把这个 skill 装上' (给本地目录) → 用 source_dir\n\n"
            "❌ 不该调用:\n"
            "  - 员工没明确要求安装\n"
            "  - source_dir 在系统目录 (/etc, /usr 等) — 安全考虑拒绝\n\n"
            "**Hub 模式格式**:\n"
            "  hub_skill: 'namespace/name@version', 例 'shared/feishu-expense@1.0.0'.\n"
            "  version 写 'latest' 拿最新版.\n"
            "  hub_url: 默认 env CATFISH_HUB_URL 或 http://127.0.0.1:9001.\n\n"
            "**安装规则**:\n"
            "  1. SKILL.md 必须 (script.py 可选)\n"
            "  2. SKILL.md frontmatter 的 name 字段 → 决定安装路径 <namespace>/<name>/\n"
            "  3. 同名已存在 → 必须 overwrite=true 才覆盖\n"
            "  4. 安装后自动 dry-run 验证 + audit log + 仪表盘出现\n\n"
            "**返回**: {ok, installed_path, error, source: 'local'|'hub'}.\n"
            "**audit**: ~/.catfish/skill_audit.jsonl event_type=install."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source_dir": {
                    "type": "string",
                    "description": (
                        "(模式 A) 本机源目录绝对路径或 ~ 开头. 必须含 SKILL.md. "
                        "例: '~/Downloads/my-new-skill/' 或 '/tmp/shared-skill/'. "
                        "跟 hub_skill 互斥, 二选一."
                    ),
                },
                "hub_skill": {
                    "type": "string",
                    "description": (
                        "(模式 B) Skills Hub 中央路径, 'namespace/name@version' 格式. "
                        "例 'shared/feishu-expense@latest' 或 'productivity/eis-export@1.0.0'. "
                        "跟 source_dir 互斥, 二选一."
                    ),
                },
                "hub_url": {
                    "type": "string",
                    "description": (
                        "(模式 B 用) Skills Hub server base URL. "
                        "默认 env CATFISH_HUB_URL, 没设默认 http://127.0.0.1:9001."
                    ),
                },
                "namespace": {
                    "type": "string",
                    "description": (
                        "安装到本机的 namespace, 例 'department' / 'personal' / 'shared'. "
                        "默认 'personal' (员工本人装的). "
                        "Hub 模式不写时, 默认走 hub_skill 自带的 namespace."
                    ),
                    "default": "personal",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": (
                        "同名 skill 已存在时是否覆盖. 默认 false. "
                        "覆盖前自动 backup 到 skill-trash."
                    ),
                    "default": False,
                },
            },
            "required": [],
        },
        "emoji": "📦",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_skill_delete",
        "description": (
            "删除一个 catfish 工程审定 skill (整个目录). 五一 sprint Day 2 加.\n\n"
            "✅ 调用场景:\n"
            "  - 员工明确说 '删掉 X skill' / '不再需要 X skill'\n"
            "  - skill 已经废弃 (catfish_run_skill 返回过 deprecated_warning)\n\n"
            "❌ 不该调用:\n"
            "  - 员工只说 '看不到这个 skill 了' (那是其他问题, 不是要删)\n"
            "  - 员工没明确要求删 — 这是不可逆操作, 必须显式确认\n\n"
            "**安全保障**: 删之前自动 backup 到 ~/.catfish/skill-trash/<unix-ts>/, "
            "30 天内可恢复. 真要永久删, 员工 30 天后手动清空 trash.\n\n"
            "**返回**: {ok, deleted_path, backup_path, error}.\n"
            "**audit**: 调用记 ~/.catfish/skill_audit.jsonl event_type=delete, "
            "客户 IT 可审 skill 生命周期.\n\n"
            "**注意**: 删 skill 后, 现有 session 已加载的 module 仍可调 (sys.modules), "
            "但新 session 看不到, 仪表盘自动消失. 想立即生效请重启 Companion."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_path": {
                    "type": "string",
                    "description": (
                        "skill 相对路径, 例 'department/leadership-briefing'. "
                        "跟 catfish_run_skill 用的 skill_path 一致."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "删除原因 — 员工说的话或你判断的, 写 audit log",
                },
                "confirm": {
                    "type": "boolean",
                    "description": (
                        "**必填 true**. 防误删 — 员工没明确说删, "
                        "你不应该自己判断 confirm=true."
                    ),
                },
            },
            "required": ["skill_path", "reason", "confirm"],
        },
        "emoji": "🗑",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-A2.1 (5/8) — 后台任务 (chat 不阻塞 + 员工继续问别的) ──
    {
        "name": "catfish_run_task",
        "description": (
            "★ 启动后台任务, 立即返 task_id, 不阻塞 chat. 员工可继续问别的事.\n\n"
            "✅ 调用时机:\n"
            "  - 员工要写长 docx (>30 段) → 后台跑, 先返 task_id\n"
            "  - 多步流程 (search + read + edit + save) 估计 >10s → 后台跑\n"
            "  - 员工同时问多件事 → 一件后台一件前台\n\n"
            "❌ 不调用:\n"
            "  - 短查询 (查电话 / 算 1+1) — 直接 execute_code, 不需要 task\n"
            "  - 员工等结果的 Q&A (单 step 答完就好)\n\n"
            "kind 枚举:\n"
            "  - 'execute_code': 跑 python/bash. payload={code, lang, timeout_s}\n"
            "  (其他 kind 5/22 后扩)\n\n"
            "label: 给员工看的人类可读描述 (例 '修订《资质管理办法》'). "
            "返 task_id 后, 跟员工说 '我后台在跑 [label] [task_id], 你可以问别的'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["execute_code"],
                    "description": "任务类型枚举",
                },
                "payload": {
                    "type": "object",
                    "description": "任务参数, 跟 kind 对应",
                },
                "label": {
                    "type": "string",
                    "description": "给员工看的描述 (1-100 字)",
                },
            },
            "required": ["kind", "payload"],
        },
        "emoji": "🪄",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_task_status",
        "description": (
            "★ 查后台任务状态. 员工问 '那个修订办法做到哪了?' 时调.\n\n"
            "返字段: status (pending/running/completed/failed/interrupted/not_found), "
            "elapsed_s, label, error, latest_output.\n\n"
            "P3.5.39 (6/18) latest_output: 长 task 跑一半也能拿到中间 stdout/stderr "
            "tail (~4KB), 不再 black box. 跑 print() 看进度 / 看 traceback 部分 / "
            "判断 task 是不是在合理推进都用这个. running 状态下 latest_output 实时更新.\n\n"
            "✅ 别每秒 poll — 员工问的时候才查. 任务完成后桌宠会自动通知 "
            "(BL-A2.3), 你不需要主动 poll."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "task_xxxxxxxx"},
            },
            "required": ["task_id"],
        },
        "emoji": "🔍",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_task_list",
        "description": (
            "★ 列出当前所有后台任务 (running / completed / failed). "
            "Dashboard TasksCard 用这个刷新, LLM 也能调.\n\n"
            "返 {tasks: [{task_id, kind, label, status, elapsed_s, ...}, ...]}.\n\n"
            "调用时机: 员工说 '现在有什么任务在跑' / '后台都做啥呢'."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "emoji": "📋",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_task_result",
        "description": (
            "★ 取后台任务**结果** (含 result / error). 任务必须 status=completed/failed.\n\n"
            "比 catfish_task_status 多返 result 字段. 任务还在 running 时调返 status=running, "
            "result 没有 — 你应该跟员工说 '还在跑, 完成会通知你'.\n\n"
            "调用时机:\n"
            "  - 桌宠通知 '修订办法完了' 后, 你可以调这个拿结果, 转给员工\n"
            "  - 员工催 '好了没' 时调."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
        "emoji": "📦",
        "toolset": "catfish_native",
        "available": True,
    },
    # BL-LONG-RUNNING-V1-PHASE-C (6/1): retry interrupted/failed task
    {
        "name": "catfish_task_retry",
        "description": (
            "★ 重试一个**中断或失败**的后台任务. 拿原 task 的 kind + payload "
            "启一个新 task (新 task_id), 等价于'重跑同一 input'.\n\n"
            "✅ 调用场景:\n"
            "  - 员工看 Dashboard 发现某个 task '中断' (进程重启 / oom 没跑完)\n"
            "  - 员工说 '那个分析的 task 再跑一次'\n"
            "  - 任务 failed (上游 model 挂), 网络恢复后想 retry\n\n"
            "P3.5.33 (6/18) 评估 gate:\n"
            "  - retry_count >= max_retries → 拒 (默认上限 3 次)\n"
            "  - last_error_type=permanent (401 / 404 / payload 错) → 拒\n"
            "  - 其它情况正常 retry, retry_count + 1, parent_task_id 串链路\n\n"
            "P3.5.33 启动自动 retry: tool-bridge 启动时 auto_retry_interrupted_on_startup\n"
            "扫 interrupted task 自动触发 retry (白名单 + 评估 gate). LLM 显式调本 tool\n"
            "只用于: 员工主动要求 / failed 状态非 interrupted / 老 task / 白名单外 kind.\n\n"
            "返 {ok, task_id (新), original_task_id, retry_count, status, label}. "
            "找不到原 task / 评估 gate 拒时返 ok=False + error 说原因."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "原任务 ID, 从 catfish_task_list / 桌宠通知拿.",
                },
            },
            "required": ["task_id"],
        },
        "emoji": "🔁",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-MM9 (5/8) — agent 自动抽 skill ──
    {
        "name": "catfish_propose_skill",
        "description": (
            "把员工反复做的工作流程**提案**成 skill, 等员工确认再装. **不直接装**.\n\n"
            "✅ 调用场景 (3+ 次同 pattern 必调, 跟 BL-MM7 三 evidence 门槛同哲学):\n"
            "  - 员工本周已经第 3 次让你写 '项目立项材料' 用类似结构 → propose 'project-proposal'\n"
            "  - 员工反复粘贴差旅报销单让你算金额 → propose 'travel-expense-calc'\n"
            "  - 员工每周一让你查 audit log 拼周报 → propose 'weekly-report-from-audit'\n\n"
            "❌ 不该调用 (跟 hermes 黑盒自决 区别):\n"
            "  - 员工只做过 1-2 次 → 还不到 pattern, 静默观察\n"
            "  - 红线场景: 健康 / 财务 / 感情 / 政治 / 宗教 — 永远不 propose 这类 skill\n"
            "  - 员工已经 reject 过同类 propose — 别骚扰\n\n"
            "**调用后**: 写 ~/.catfish/skill_proposals.jsonl, append 一条 (员工可看). "
            "**返回给 LLM 的话术**: '已记下提案, 我现在跟员工说: \"我注意到这周你 X 次 Y, "
            "要不我把流程存成 skill 下次直接调? 你说装我就装.\"' 等员工说 yes 再调 catfish_skill_install.\n\n"
            "**audit**: ~/.catfish/skill_proposals.jsonl event_type=propose, accepted/rejected 由后续事件追加.\n\n"
            "**返回**: {ok, proposal_id, total_proposals, summary}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "拟用作 skill name (kebab-case, 简短描述性). "
                        "例: 'project-proposal' / 'travel-expense-calc' / 'weekly-report-from-audit'."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "为什么觉得该提案 — 1-2 句, 含**具体观察证据** "
                        "(例: '本周 5/5/5/6/5/8 三次让我写项目立项材料, 结构相似 (背景/目标/团队/预算/里程碑)'). "
                        "员工看了能直接确认或反驳."
                    ),
                },
                "action_steps": {
                    "type": "string",
                    "description": (
                        "skill 大致做啥的 3-5 步 markdown bullets. "
                        "例: '1. 读员工提供的项目背景\\n2. 拉历史立项材料样本\\n"
                        "3. 按公司模板拼 6 段 (背景/目标/团队/预算/里程碑/风险)\\n"
                        "4. 输出到 ~/.catfish/output/<ts>-立项-<项目>.docx'. "
                        "员工 accept 后 LLM 用这个 outline 调 catfish_skill_install."
                    ),
                },
                "evidence_count": {
                    "type": "integer",
                    "description": (
                        "你观察到员工做这事的次数. 两套阈值 (跟 triggered_by 配套):\n"
                        "  - triggered_by='auto' (你自己观察 propose): **必须 ≥3**, "
                        "不到 3 次不算 pattern, 静默观察.\n"
                        "  - triggered_by='user_request' (员工显式说 '存成 skill'): **≥1 即可**, "
                        "员工说做就做不卡阈值."
                    ),
                    "minimum": 1,
                },
                "triggered_by": {
                    "type": "string",
                    "enum": ["auto", "user_request"],
                    "description": (
                        "BL-MM9-fix (5/9): 区分两种触发场景, 决定 evidence_count 校验严不严.\n"
                        "  - 'auto': 你自己观察员工反复做后主动 propose. 必须 evidence_count ≥3 防骚扰.\n"
                        "  - 'user_request': 员工**明确说**'封装为 skill' / '存成 skill' / '做成 skill', "
                        "你跟着调. evidence_count ≥1 即可.\n"
                        "鸿波 5/9 反馈: '下午我主动让鲶鱼生成 SKILL, 为什么不能生成, 很不合理'. "
                        "员工显式触发不该卡 3 次门槛."
                    ),
                    "default": "auto",
                },
                "triggers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "P3.5.43 (6/20): 触发关键词 list, 3-20 个. hermes 加载 skill 后注入到 "
                        "system prompt, LLM 看到员工说这些词就调 skill. 例: "
                        "['周报', '本周工作', '本周总结', '一周工作', 'weekly report']. "
                        "太少 (<3) 漏触发, 太多 (>20) 占预算 — 严守 3-20."
                    ),
                    "minItems": 3,
                    "maxItems": 20,
                },
                "kind": {
                    "type": "string",
                    "enum": ["procedural", "instructional"],
                    "description": (
                        "P3.5.43 (6/20): skill 类型. 多步操作流程 (录屏类) 选 procedural, "
                        "解释/教学/参考类选 instructional. hermes frontmatter 必填."
                    ),
                    "default": "procedural",
                },
                "skill_namespace": {
                    "type": "string",
                    "enum": ["personal", "department", "public", "creative"],
                    "description": (
                        "P3.5.43 (6/20): skill 装机 namespace. 老 hardcode personal, "
                        "现在让 LLM 按 skill 性质选: 员工本人偏好/工具 → personal, "
                        "整个部门共用 → department, 全公司公开 → public, 设计/文创 → creative."
                    ),
                    "default": "personal",
                },
            },
            "required": ["name", "reason", "action_steps", "evidence_count"],
        },
        "emoji": "💡",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── P3.5.43 (鸿波 6/20) — install_proposal 一键装 hermes-兼容 SKILL ──
    {
        "name": "catfish_install_proposal",
        "description": (
            "把 propose_skill 提案过的 skill **真装**到 hermes (员工 accept 后调). "
            "内部用 proposal jsonl 的 triggers/kind/description/action_steps 字段, "
            "走 skill_format 模板生成 hermes-兼容 SKILL.md + script.py + sync 到 "
            "~/.hermes/skills/<slug>/. 比 catfish_skill_install 自动化 — 不用 LLM "
            "重新拼 SKILL.md.\n\n"
            "✅ 调用场景: 员工说 'yes / 装吧 / 行 / 同意 / 安装' 等 accept 信号后, "
            "立即调本工具传 propose_skill 返回的 proposal_id.\n\n"
            "❌ 不该调:\n"
            "  - 员工没明确 accept (静默 / reject 都不装)\n"
            "  - proposal_id 不存在 (本工具会报错)\n\n"
            "**返**: {ok, skill_dir, hermes_dir, skill_name, namespace, summary}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "proposal_id": {
                    "type": "string",
                    "description": "propose_skill 返回的 proposal_id (例: prop_1781923456_my-skill)",
                },
                "sync_to_hermes": {
                    "type": "boolean",
                    "description": "默认 True. 设 False 仅落 ~/.catfish/skills/ 不同步到 hermes (测试用)",
                    "default": True,
                },
            },
            "required": ["proposal_id"],
        },
        "emoji": "📦",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-MM13 (5/8) — 老 skill 自进化: propose 改进版本 ──
    {
        "name": "catfish_propose_skill_revision",
        "description": (
            "提议**修改一个已存在的 skill** (基于 audit log + BL-MM11 员工反馈观察到的问题). "
            "跟 catfish_propose_skill (抽**新** skill) 区别 — 这条改**老** skill 内容. "
            "**不直接改**, 等员工 accept 才落地, 跟 BL-MM9 一脉相承.\n\n"
            "✅ 调用场景:\n"
            "  - 员工 BL-MM11 给某 skill ≥2 个 👎 + 改动评论 ('太啰嗦' / '少这一步') → propose revision\n"
            "  - skill audit 失败率 ≥30% 持续 5 次 (员工反复重试同 skill) → propose 加 try-catch\n"
            "  - 员工 BL-MM12 综合质量分 < 40 (差) 持续 7 天 → propose 重写\n\n"
            "❌ 不该调用:\n"
            "  - 员工没反馈 / skill 用得少 (<5 次) → 数据不够, 静默\n"
            "  - 红线: 健康 / 财务 / 感情 / 政治 / 宗教 namespace skill 永不 propose 改\n"
            "  - 员工已经 reject 过同 skill 的 revision (24h 内) — 别骚扰\n\n"
            "**调用后**: 写 ~/.catfish/skill_revisions.jsonl, append 一条. 员工 Dashboard "
            "SkillRevisionCard 能看到, 点 ✅ 采纳 → catfish 调 BL-MM3 备份老版 → 写新版到 "
            "skill_path 下. 点 ❌ 拒绝 → 标 dismissed, 24h 内不再 propose.\n\n"
            "**返**: {ok, revision_id, summary}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_path": {
                    "type": "string",
                    "description": (
                        "要改的 skill 路径 (相对 catfish/skills/), kebab-case 命名空间. "
                        "例: 'department/weekly-report' / 'department/leadership-briefing'."
                    ),
                },
                "current_version": {
                    "type": "string",
                    "description": (
                        "当前 skill 版本号 (从 SKILL.md frontmatter 读). 例 '0.3.2'. "
                        "防 LLM 拿到 stale skill 内容做改, 跟 catfish 实际版本不一致."
                    ),
                },
                "proposed_version": {
                    "type": "string",
                    "description": (
                        "提议的新版本号 (SemVer bump). 大改 → minor (0.3.2 → 0.4.0); 修 bug → patch "
                        "(0.3.2 → 0.3.3); 不向后兼容 → major. 必须 > current_version."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "为什么改 — 必含**具体观察证据**: BL-MM11 反馈 / audit 失败 / 质量分. "
                        "例: '员工 5/8 5/9 5/10 三次 👎 + 评论 \"太啰嗦\", 看 audit 4 次 timeout '"
                        "原因没 catch InvalidArgument. 改: 删第 3 段 + 加 try-except.' "
                        "员工看了能直接确认或反驳, ≥30 字."
                    ),
                },
                "diff_summary": {
                    "type": "string",
                    "description": (
                        "改动概览 (3-8 句 markdown bullets, 让员工一眼看明白). "
                        "例: '- SKILL.md: 删第 3 段冗余说明\\n"
                        "- script.py: render_xxx 加 try/except 兜 InvalidArgument\\n"
                        "- 输出: 不再含 \"附件 (供参考)\" 那段员工说没用'. "
                        "完整 patch 在 LLM 后续生成 SKILL.md/script.py 时给, 这里只做 summary."
                    ),
                },
                "evidence_summary": {
                    "type": "string",
                    "description": (
                        "数据依据汇总: feedback 多少条 (👎 N, 评论 M) / audit 失败几次 / "
                        "质量分趋势. 例: 'BL-MM11: 5 个 👎 / 3 个改动评论. audit: 12 次调用 4 次失败 (33%). "
                        "BL-MM12 score: 35 (差) 持续 9 天.'"
                    ),
                },
            },
            "required": [
                "skill_path",
                "current_version",
                "proposed_version",
                "reason",
                "diff_summary",
                "evidence_summary",
            ],
        },
        "emoji": "🔧",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-D2 (5/10) Skills Hub publish ─────────────────────────────
    {
        "name": "catfish_skill_publish",
        "description": (
            "★ 把员工本机的 skill 发布到中央 Skills Hub (全公司共享).\n\n"
            "✅ 调用时机:\n"
            "  - 员工说'把这个 skill 发布到 hub' / '分享给团队'\n"
            "  - 你观察员工把同一 skill 改了 ≥ 3 次稳定后, propose 发布\n"
            "  - 员工 confirm 后才调 (跟 user_profile 同纪律, 不静默自决)\n\n"
            "input:\n"
            "  - skill_path: 本机 skill 目录, 必须含 SKILL.md (例 ~/.hermes/skills/my-skill)\n"
            "  - namespace: hub 上分类 (例 'department' / 'personal' / 'finance')\n"
            "    用员工部门时, 找 catfish_today_summary 的 department 字段\n"
            "  - auto_scrub_pii / auto_scrub_intranet (P3.3.17): 自动脱敏开关\n\n"
            "成功返:\n"
            "  {ok:true, namespace, name, version, published_at, hub_url, scrub_summary?}\n"
            "失败返:\n"
            "  {ok:false, error, scan_phase?, auto_scrub_available?}\n\n"
            "❌ 别在没员工 explicit 确认时调用. 别把含敏感 path / 凭据的 skill 发上去.\n\n"
            "🔁 失败 + auto_scrub_available=true 的处理 (P3.3.17, 6/10):\n"
            "  scan_phase=pii / intranet 命中时, error 给员工看 (含具体撞到啥),\n"
            "  问员工 '要不要让我自动把这些 PII / 内网地址替换成占位, 装上的同事自己填?'.\n"
            "  员工同意 → 再调本工具 with auto_scrub_pii=true (或 auto_scrub_intranet=true).\n"
            "  scrub 只改 hub 上传副本, 员工本机文件不动. SKILL.md 自动加 params: 段.\n"
            "  scan_phase=credentials 永不 auto_scrub — 凭据要员工本机手动改成 keychain:// ref.\n\n"
            "底层: 走 gateway /v1/hub/skills/{namespace} POST multipart, 跟 mcp-registry 同套 OIDC 鉴权."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_path": {
                    "type": "string",
                    "description": "本机 skill 目录路径, 必须含 SKILL.md",
                },
                "namespace": {
                    "type": "string",
                    "description": "hub 上 namespace (例 'department' / 'personal')",
                },
                "auto_scrub_pii": {
                    "type": "boolean",
                    "description": (
                        "P3.3.17 (6/10): 命中 PII (身份证 / 手机号 / 工号 / 银行卡) 时, "
                        "true=自动替换成 {{phone_1}} 等占位 + 注入 SKILL.md frontmatter "
                        "params: 段; false=拒上传 (默认). 第一次 publish 撞到 PII 时, "
                        "先把 error 给员工看 + 问员工同意, 同意后再加这个参数 retry."
                    ),
                    "default": False,
                },
                "auto_scrub_intranet": {
                    "type": "boolean",
                    "description": (
                        "P3.3.17 (6/10): 命中内网 URL (10.x.x.x / 192.168.x.x / *.corp / "
                        "eis.* / oa.* 等) 时, true=自动替换成 {{INTRANET_EIS_1}} 等占位 + "
                        "frontmatter params; false=拒 (默认). 同 auto_scrub_pii, 第一次失败后跟员工确认再 retry."
                    ),
                    "default": False,
                },
            },
            "required": ["skill_path", "namespace"],
        },
        "emoji": "🚀",
        "toolset": "catfish_native",
        "available": True,
    },
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
    # ── BL-Q3-WEBSKILL (5/11) 视觉定位 — 找页面元素位置 ───────────────
    {
        "name": "catfish_browser_locate",
        "description": (
            "★ **视觉找页面元素的位置坐标**. 给自然语言描述 + 截图, vision 模型返\n"
            "{x, y, width, height, center, confidence, reasoning}.\n\n"
            "**为啥要这个**: LLM 看截图大概知道按钮在哪, 但 122b 视觉估坐标常偏 50-100 像素.\n"
            "专用 vision 模型 (catfish-private-vision) 更准. 返回直接喂\n"
            "catfish_browser_click(coordinates=[center.x, center.y]).\n\n"
            "**典型场景**:\n"
            "  - 找登录按钮 (find_by_text 撞 placeholder 时改走视觉)\n"
            "  - 找弹窗的 'X' 关闭 (没文字, 只能视觉)\n"
            "  - 找列表里的某个图标 / 颜色块\n"
            "  - 验证码输入框跟普通输入框混在一起时定位\n\n"
            "**调用模式**:\n"
            "  1. selector 指定区域: 在某元素区域内找 (e.g. modal 内, 表单内)\n"
            "  2. full_page=true: 截全页找 (慢, 但找不在 viewport 的元素时用)\n"
            "  3. 都不传: 截 viewport (默认, 最快)\n"
            "  4. image_b64 直传: 调试用 / 已有图\n\n"
            "**hint 通过 query 自然语言传**: query='页面顶部的蓝色登录按钮' / "
            "query='验证码输入框, 在密码框下方' — 越具体, vision 模型越准.\n\n"
            "**返回**:\n"
            "  {ok: bool, found: bool, x/y/width/height: int, center: {x, y},\n"
            "   confidence: 0-1, reasoning: 'xx 颜色 yy 位置', model, attempts}\n\n"
            "  - found=true + confidence ≥ 0.6 → 直接点 center\n"
            "  - found=true + confidence < 0.6 → 看 reasoning 决定要不要试 / 重新截图\n"
            "  - found=false → vision 没找到, 换 query 描述或 catfish_browser_snapshot 看 DOM\n\n"
            "**跟 catfish_browser_find_by_text 配合**:\n"
            "  - 有文字 → 优先 find_by_text (DOM 精确)\n"
            "  - 没文字 / 文字歧义 (placeholder 撞) → 走 locate (视觉)\n\n"
            "**跟 catfish_recognize_captcha 区别**:\n"
            "  - captcha: 识字符 (OCR), 返字符串\n"
            "  - locate:  定位置 (空间), 返坐标\n"
            "  - 同 vision 模型, 不同 prompt"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "自然语言描述要找的元素. 例: '蓝色登录按钮' / "
                        "'验证码输入框, 在密码框下方' / '右上角的关闭 X'. "
                        "**越具体, 准确率越高**."
                    ),
                },
                "selector": {
                    "type": "string",
                    "description": (
                        "可选, 只截某元素区域内找. 例: 'form.login-form' 截表单内. "
                        "不传则全 viewport (或 full_page)."
                    ),
                },
                "image_b64": {
                    "type": "string",
                    "description": "可选, 直传 base64 PNG (不含 data: prefix). 调试用.",
                },
                "full_page": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "true=截整页 (含 scroll 区域, 慢), false=只截 viewport (默认, 快). "
                        "selector / image_b64 传了则忽略这个."
                    ),
                },
                "max_retry": {
                    "type": "integer",
                    "default": 1,
                    "description": "模型偶发返 garbage 时重试, 默认 1, 最大 3.",
                },
            },
            "required": ["query"],
        },
        "emoji": "🎯",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-Q3-WEBSKILL (5/11) 验证码 OCR ──────────────────────────
    {
        "name": "catfish_recognize_captcha",
        "description": (
            "★ 识别页面上的验证码图. 走 gateway loopback + vision 模型 (catfish-private-vision) OCR. "
            "比 LLM 自己 OCR 准, 不占员工 quota.\n\n"
            "**调用场景**:\n"
            "  - 登录页有验证码 (CAS / EIS / 政府系统常见)\n"
            "  - 表单提交需要验证码 (反 bot)\n"
            "  - skill 脚本需要确定性识别 (跳过 LLM 推理)\n\n"
            "**两种喂图模式 (二选一)**:\n"
            "  1. selector='#captchaImg' (推荐): 工具自己 Playwright 截图, LLM 不需要先 screenshot\n"
            "  2. image_b64='iVBORw...': 传 base64 (无 data: prefix), 你已经有图的场景\n\n"
            "**hint 可选 (强烈推荐传)**: 'numeric_4' / 'alphanumeric_4' / 'numeric_5' / 'numeric_6' / "
            "'alphanumeric_5' / 'alphanumeric_6' / 'chinese' / 任意自然语言. 帮 vision 收紧搜索空间, "
            "也用来算 confidence (长度对不上 confidence 降).\n\n"
            "**返**: {ok, text, confidence: 0-1, model, attempts, raw_response}.\n"
            "  - ok=True + text='2fW2' + confidence=0.85 → 直接 catfish_browser_fill 填进去\n"
            "  - ok=False → 重截 / 换 hint / 让员工手动填\n\n"
            "**重试**: max_retry 控 (默认 1, 最大 3). 模型偶发失败时 retry 一次 + 重新算 confidence."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "验证码图片元素的 CSS selector, 例 '#captchaImg' / 'img.captcha'",
                },
                "image_b64": {
                    "type": "string",
                    "description": "base64 PNG/JPG (不含 data: 前缀). 跟 selector 二选一.",
                },
                "hint": {
                    "type": "string",
                    "description": (
                        "可选, 帮 vision 模型. 'numeric_4' (4 位数字) / 'alphanumeric_4' (4 位字母数字) / "
                        "'numeric_5' / 'numeric_6' / 'alphanumeric_5' / 'alphanumeric_6' / 'chinese' / 自然语言"
                    ),
                },
                "max_retry": {
                    "type": "integer",
                    "default": 1,
                    "description": "模型偶发失败时重试次数, 默认 1, 最大 3.",
                },
            },
        },
        "emoji": "🔢",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-Q3-ARCHIVE (5/11) tool message archive 读回 ──────────────
    {
        "name": "catfish_read_tool_archive",
        "description": (
            "★ 读 gateway 已归档的 tool output 内容 (lossless 全文 in PG, 14 天保留).\n\n"
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
            "三种调用模式:\n"
            "  1. catfish_read_tool_archive(ref='abc12345') — 全文 (max_bytes 上限 8K)\n"
            "  2. catfish_read_tool_archive(ref='abc12345', line_range='40-80') — 按行号\n"
            "  3. catfish_read_tool_archive(ref='abc12345', grep='KeyError') — 关键字 ± 5 行\n\n"
            "底层: 走 gateway POST /api/tool-archives/read, 鉴权同 chat (OIDC).\n"
            "返 {ref, content, total_lines, total_bytes, tool_name, summary}.\n"
            "404 = ref 不存在或 14 天过期; 403 = 不是你的 archive."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": "archive 引用, 16 字 sha256 (从 prompt 里的 archive_ref= 取)",
                },
                "line_range": {
                    "type": "string",
                    "description": "可选, 行号范围 'N-M' 或单行 'N' (1-indexed, e.g. '40-80')",
                },
                "grep": {
                    "type": "string",
                    "description": "可选, 子串关键字, 召回匹配行 ± 5 行上下文 (推荐用)",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "可选, 返回字节上限, 默认 8000, 硬上限 32K",
                },
            },
            "required": ["ref"],
        },
        "emoji": "📂",
        "toolset": "catfish_native",
        "available": True,
    },
    # ════════════════════════════════════════════════════════════
    # BL-MM9-FREEZE-v2 (5/12 鸿波拍板): 教学→凝固→复用闭环 (显式 session 边界)
    # ════════════════════════════════════════════════════════════
    {
        "name": "catfish_teach_start",
        "description": (
            "★★★ **开始一次教学 session** (BL-MM9-FREEZE-v2 5/12).\n\n"
            "员工说'我要教你 X' / '教你做 Y' / '记一下接下来的步骤' / "
            "'凝固成 skill 之前我先教你跑一遍' → **第一件事调本工具**.\n\n"
            "**核心机制**: 没 active teach session 时, 你调任何 "
            "catfish_browser_* / catfish_recognize_captcha / catfish_browser_locate "
            "都**不会被录**. 调本工具后 → 进入教学模式 → 每个业务工具 call 都进"
            "trace → 最终凝固成 skill 的 step.\n\n"
            "✅ 调用场景:\n"
            "  - '我教你登 EIS' → catfish_teach_start(name='eis-login')\n"
            "  - '记一下接下来怎么走 OA 审批' → catfish_teach_start(name='oa-approval')\n"
            "  - 任何'员工指挥你跑一遍, 之后要凝固成 skill'的场景\n\n"
            "❌ 不要调用:\n"
            "  - 员工只是问问题 / 不教学 → 不调\n"
            "  - 你已经在 active session 里 (老 session 会被自动关掉, 但浪费)\n"
            "  - 复用阶段 (调 catfish_run_skill) — 那是用 skill, 不是教 skill\n\n"
            "**教学纪律**: 调完本工具后, 每个 tool call 都进 trace. **不要做无关"
            "探索** (e.g. 'snapshot 看看页面长啥样') — 那会进凝固 skill. "
            "只跑员工 explicit 指挥的步骤. 不确定就先**问员工**, 别自己探.\n\n"
            "**返回**: {ok, session_id, name, started_at_iso, summary}"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "skill 名, 小写字母数字横线 (例 'eis-login'). 凝固时同名."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": "教学目的简介 (1-200 字), 给后续凝固时元数据用.",
                },
            },
            "required": ["name"],
        },
        "emoji": "🎓",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_teach_end",
        "description": (
            "★★ **结束当前教学 session** (BL-MM9-FREEZE-v2 5/12).\n\n"
            "员工说'教完了' / '就这些' / '可以凝固了' / 类似收尾意图 → 立刻调.\n\n"
            "**作用**:\n"
            "  - 归档当前 active.jsonl 到 session_<name>_<ts>.jsonl\n"
            "  - 写 _last_completed.json 让 catfish_freeze_skill 能找到\n"
            "  - 清除 active 状态 — 后续 tool call 不再被录\n\n"
            "✅ 调用时机:\n"
            "  - 员工 explicit 说教完了 / 可以凝固\n"
            "  - 教学的最后一步完成后, 员工没说继续 — 主动问'教完了吗?', "
            "员工确认就调\n\n"
            "❌ 不要调用:\n"
            "  - 没 active session — 调了会返 error\n"
            "  - 教学中途, 员工没 explicit 说结束\n\n"
            "**返回**: {ok, session_id, name, step_count, duration_s, archive_path, summary}"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "结束原因 (e.g. 'done' / 'aborted'), 进归档元信息.",
                },
            },
            "required": [],
        },
        "emoji": "✅",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_freeze_inspect",
        "description": (
            "★ 查 trace 状态. 教学过程中员工想知道'我刚才让鲶鱼做的几步, 系统"
            "都录下来了吗', 调这个看. 返回 trace 文件大小 / 最近窗口内的步骤"
            "数 / 每个 tool 的调用次数. 凝固前先调一次, 确认 trace 长度合理.\n\n"
            "✅ 调用场景:\n"
            "  - 员工说'刚才教的几步录下来了吗?' → catfish_freeze_inspect\n"
            "  - 凝固前 sanity check\n\n"
            "**参数**: since_unix (可选, 默认 1 小时前). \n"
            "**返回**: {ok, trace_file{lines/tools/...}, recent_steps_summary[...]}"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "since_unix": {
                    "type": "number",
                    "description": "起始 unix 时间戳 (秒). 默认 1 小时前.",
                },
            },
            "required": [],
        },
        "emoji": "🔍",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_freeze_skill",
        "description": (
            "★★★ **凝固最近一个完成的 teach session** 成可执行 skill (BL-MM9-FREEZE-v2).\n\n"
            "**前置条件**: 必须先 catfish_teach_start → 教学 → catfish_teach_end "
            "→ 才能 catfish_freeze_skill. 没 session 直接凝固会拒绝.\n\n"
            "✅ 调用时机:\n"
            "  - catfish_teach_end 调完后, 员工说'凝固成 skill'\n"
            "  - 员工 explicit 给了 name + description\n\n"
            "❌ 不该调用:\n"
            "  - active session 还没 end → 拒\n"
            "  - 没有 last_completed session → 拒\n"
            "  - 旧 session trace 有 fail step / 包含 LLM 探索 → 调本工具前\n"
            "    应该让员工**重教一次**, 把干净的 8 步教明白\n\n"
            "**参数**:\n"
            "  - name: 'eis-login' 等. 跟 catfish_teach_start 传的一致就行.\n"
            "  - namespace: 'department' (默认) / 'personal' / 'team'\n"
            "  - description: 1-500 字描述\n"
            "  - target: 'local' (默认, 5/21 加, 落 ~/.catfish/skills/) / 'workspace' (落工程目录, 业务 skill)\n"
            "  - overwrite: 同名 skill 已存在时是否覆盖 (默认 false)\n"
            "  - run_install: 凝固后自动跑 install_to_hermes.sh (5/21 默认 false. 只 target='workspace' 生效)\n\n"
            "**返回**: {ok, name, namespace, target, skill_path, skill_dir, hermes_name, files[], params[], "
            "register_external_dir{...}, install{...}, summary}\n\n"
            "**5/21 方案 1 隐私纪律**:\n"
            "  - 教学产物默认 target='local' 落本机 ~/.catfish/skills/, **永不**自动 publish 中央 Hub.\n"
            "  - 想发布到团队 → 员工显式点 Companion UI 按钮, 走 catfish_skill_publish (跑 3 道扫描: 凭据 / PII / 内网 URL).\n"
            "  - LLM **不要**自己调 catfish_skill_publish 当 freeze 一部分.\n\n"
            "**安全**:\n"
            "  - trace 里 fill 含明文密码 → 拒凝固, 提示员工用 secret_ref 重教\n"
            "  - secret_ref 原样保留在 script.py (不解析成明文)\n"
            "  - captcha 识别结果 hard-code 自动改成实时调用"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "skill 名, 小写字母数字横线 (例 'eis-login'). 字母开头.",
                },
                "namespace": {
                    "type": "string",
                    "enum": ["department", "personal", "team"],
                    "description": "namespace, 默认 department.",
                },
                "description": {
                    "type": "string",
                    "description": "skill 描述, 1-500 字, 进 SKILL.md frontmatter.",
                },
                "target": {
                    "type": "string",
                    "enum": ["local", "workspace"],
                    "description": (
                        "落盘路径 (5/21 加). 'local' (默认): ~/.catfish/skills/, 教学私有, 自动注册到 "
                        "hermes external_dirs, Curator 不动. 'workspace': ~/person_task/catfish/skills/, "
                        "业务 skill 源码工程目录用, 配合 install_to_hermes.sh."
                    ),
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "已存在的 skill 是否覆盖. 默认 false.",
                },
                "run_install": {
                    "type": "boolean",
                    "description": "凝固后自动跑 install_to_hermes.sh. 5/21 默认 false. 仅 target='workspace' 生效.",
                },
                "session_archive_path": {
                    "type": "string",
                    "description": "调试用 — 显式指定某 session archive 文件路径. 一般不传.",
                },
            },
            "required": ["name"],
        },
        "emoji": "🧊",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_freeze_rotate",
        "description": (
            "凝固完一个 skill 之后, 把 active trace 文件归档 (重命名带时间戳), "
            "开始空白的新 trace. 防下次教学跟上次混. 通常在 catfish_freeze_skill "
            "成功后调一次.\n\n"
            "**参数**: reason (可选, e.g. 'post-freeze-eis-login')"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "归档理由 (进归档文件名).",
                },
            },
            "required": [],
        },
        "emoji": "📦",
        "toolset": "catfish_native",
        "available": True,
    },
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
    # ── BL-FED2.6 (5/12 鸿波拍板末) a2a 协助通知 ──
    {
        "name": "catfish_list_a2a_help",
        "description": (
            "★ 看你**通过 Plan D Federation 帮过哪些同事** (反馈环主动审计). "
            "BL-FED2.4 反馈环已经在 ~/.catfish/a2a_notifications.jsonl 累积了你被问过的"
            "每次记录, 这个 tool 是员工主动**查**这个清单的入口.\n\n"
            "✅ 调用场景:\n"
            "  - 员工问 '今天我帮过谁?' → hours_back=24\n"
            "  - 员工问 '最近一周我都被问了哪些事?' → hours_back=168\n"
            "  - 员工问 '小李最近问过我啥?' → from_sub='lijun@ffcs.cn'\n"
            "  - 员工问 '有人问过我资质方面的事吗?' → tag_substr='资质'\n\n"
            "❌ 不调用:\n"
            "  - 员工问'今天我自己干了啥' → 不是 a2a 协助, 走 employee_journal\n\n"
            "返参重点 (展示给员工):\n"
            "  - total: 符合条件的总数\n"
            "  - by_sub: {sub: count} — 谁问得多\n"
            "  - by_purpose: {purpose: count} — 哪个领域被问得多\n"
            "  - items: 最近 N 条详细 (含 ts/question/answer_preview/duration)\n"
            "  - summary: 一句话归纳, 直接念给员工\n\n"
            "🔒 隐私: jsonl 只在员工自己 mac, 中央不存. 这个 tool 也不外发任何数据."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hours_back": {
                    "type": "integer",
                    "description": "过去多少小时 (默认 24). 0/null = 全部.",
                },
                "unseen_only": {
                    "type": "boolean",
                    "description": "只看未读 (Companion 徽章用)",
                },
                "from_sub": {
                    "type": "string",
                    "description": "按问问的同事 SSO sub 过滤",
                },
                "tag_substr": {
                    "type": "string",
                    "description": "按 purpose 子串过滤 (例 '资质' 命中 'expert_consult:资质审核')",
                },
                "max_items": {
                    "type": "integer",
                    "description": "返多少条详细 (默认 50, 上限 200)",
                },
            },
            "required": [],
        },
        "emoji": "📨",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-FIX-TIMEOUT-OUTPUTS (5/13 鸿波"做不出文档") ─────────────────
    {
        "name": "catfish_list_my_outputs",
        "description": (
            "★ 列你 (鲶鱼) 跨 session 写过的所有文件 (~/.catfish/output/), "
            "按时间倒序. 上游 LLM 卡 / 反复幻觉 / 鸿波等不及刷时, **先调这个**"
            "看有没已经写过, 别再 execute_code 重做.\n\n"
            "✅ 调用场景:\n"
            "  - 员工 '我刚才让你做的 xlsx 在哪?' → hours_back=2\n"
            "  - 员工 '上次合并资质那个文件还在吗' (新对话) → hours_back=24 ext_filter=.xlsx\n"
            "  - LLM 自己想确认 '我之前做过这个吗' (避免重做) → 主动调\n"
            "  - 鸿波 '今天我让你写过哪些文档' → hours_back=24\n\n"
            "❌ 不调用:\n"
            "  - 员工自己上传的文件 (那在 ~/.catfish/uploads/, 不是 output)\n"
            "  - 当前 session 内刚写的文件 (你应该记得, 不需要查目录)\n\n"
            "返参:\n"
            "  - count: 文件数\n"
            "  - items: 每条 {path, name, size_human, mtime_iso, ext}\n"
            "  - by_ext: {.xlsx: 3, .docx: 1, .md: 2}\n"
            "  - summary: 一句话归纳, 念给员工知道有哪些可用文件\n\n"
            "🔒 隐私: 只列员工本机 output 目录, 不上行中央."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hours_back": {
                    "type": "integer",
                    "description": "过去多少小时 (默认 24, 0 = 全部时间约 1 年)",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返多少 (默认 20, 上限 100)",
                },
                "ext_filter": {
                    "type": "string",
                    "description": "只返某种类型 (例 '.xlsx' / '.docx' / '.md')",
                },
            },
            "required": [],
        },
        "emoji": "📁",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-REMINDER (5/13 鸿波"macOS 提醒联动") ──────────────────────
    {
        "name": "catfish_create_reminder",
        "description": (
            "★ 在 macOS Reminders.app 创建提醒 (用户管理的真 to-do, iCloud 同步到 iPhone/iPad). "
            "**跟 notify (右上角横幅消息几秒消失) 互补** — Reminder 是用户能勾完成、跨设备的持久 to-do.\n\n"
            "✅ 调用场景:\n"
            "  - 员工 '提醒我明早 9 点交月报' → title='交月报' due_date_iso='2026-05-14T09:00:00'\n"
            "  - 员工 '每周五晚 6 点提醒我备份' → title='备份' due_date_iso='2026-05-17T18:00:00' (Reminders.app 内自己设重复, AppleScript 一次性创建有限制)\n"
            "  - 员工 '帮我记下下周三要给王总汇报' → title='给王总汇报' due_date_iso='...' body='Q2 进度 / 项目风险'\n"
            "  - LLM 自己识别 '这是个待办' → 主动调 (e.g. 看到员工说 '别忘了... ' / '记得...')\n\n"
            "❌ 不调用:\n"
            "  - 一次性弹窗消息 (用 notify, 例如 '验证码已复制')\n"
            "  - 当前会话内提示 (LLM 直接说就行)\n"
            "  - 跨员工/跨人协作 (用 a2a_ask, Reminders 是私人)\n\n"
            "参数:\n"
            "  - title: 提醒标题 (必填, 短)\n"
            "  - body: 备注详情 (可选, 长)\n"
            "  - due_date_iso: ISO 8601 到期时间 (e.g. '2026-05-14T09:00:00'), 可选\n"
            "  - list_name: 写到哪个 list (默认 '提醒事项'). 调 catfish_list_reminder_lists 看可用 list\n"
            "  - priority: 0-9 (0=无, 1-3=高, 4-6=中, 7-9=低), 可选\n\n"
            "返参:\n"
            "  - ok: 成功返 true\n"
            "  - reminder_name: 创建的提醒名 (回报员工时用)\n"
            "  - error: 失败原因 (常见: 权限未给 — 系统设置 → 隐私 → 提醒事项 勾 Catfish Companion)\n\n"
            "🔒 隐私: 100% 本机 + iCloud (用户自己的), catfish 不上行, 不存任何中央."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "提醒标题 (必填, 短描述)",
                },
                "body": {
                    "type": "string",
                    "description": "备注详情 (可选, 长描述)",
                },
                "due_date_iso": {
                    "type": "string",
                    "description": "ISO 8601 到期时间, e.g. '2026-05-14T09:00:00' (本地时区). 可选, 不传就是无截止",
                },
                "list_name": {
                    "type": "string",
                    "description": "写到哪个 list (默认 '提醒事项' 中文系统 / 'Reminders' 英文系统). 不知道传啥就先调 catfish_list_reminder_lists 看可用",
                },
                "priority": {
                    "type": "integer",
                    "description": "优先级 0-9 (0=无, 1-3=高, 4-6=中, 7-9=低)",
                    "minimum": 0,
                    "maximum": 9,
                },
            },
            "required": ["title"],
        },
        "emoji": "⏰",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_list_reminder_lists",
        "description": (
            "列 macOS Reminders.app 所有 list 名 (用户分类如 '工作' / '家庭' / '购物'). "
            "**第一次创建提醒前调** — 看员工有没自己分类的 list, 选合适的写. "
            "默认 list '提醒事项' 总是存在.\n\n"
            "✅ 调用场景:\n"
            "  - LLM 第一次帮员工创建提醒前先看 list (避免乱写)\n"
            "  - 员工说 '加到工作 list' → 先 list 看 '工作' 在不在\n\n"
            "返参: list_names (数组, e.g. ['提醒事项', '工作', '家庭'])"
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "emoji": "📋",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-CALENDAR (5/14 0:30 鸿波"ISO 现场审核会议 LLM 写脚本踩坑") ──
    {
        "name": "catfish_create_calendar_event",
        "description": (
            "★ 在 macOS Calendar.app 创建**时间锚定的事件** (会议 / 现场审核 / 行程, "
            "带 location + 时长). iCloud 同步 iPhone/iPad/Apple Watch.\n\n"
            "**跟 catfish_create_reminder 区别**:\n"
            "  - 有**明确开始结束时间** + 通常带 location → **calendar_event** (这个工具)\n"
            "  - 截止时间但只是提醒 / 没固定时长 → reminder\n"
            "  - 完全没时间 ('记得给王总打电话') → reminder (无 due_date)\n\n"
            "✅ 调用场景:\n"
            "  - '5/18-5/22 上午 8:40 在 409 会议室开 ISO 现场审核会' → 调 5 次\n"
            "  - '明天下午 3 点跟王总评审 Q2 进度, 12 楼 1201' → 一次, 带 location\n"
            "  - '下周一中午 12:30 跟客户吃饭, 苏州工业园区 XX 餐厅' → 一次\n\n"
            "❌ 不要在这里写 osascript Python 脚本拼 AppleScript record — 多行 record "
            "AppleScript 解析器不接受, 会报 syntax error. **直接调本 tool**, 内部已正确处理.\n\n"
            "参数:\n"
            "  - title: 事件标题 (必填, 短描述)\n"
            "  - start_iso: ISO 8601 开始时间 (必填, e.g. '2026-05-18T08:40:00')\n"
            "  - end_iso: ISO 8601 结束时间 (可选, 默认 start + 1h)\n"
            "  - location: 地点 (可选, e.g. '409 会议室' / '12 楼 1201')\n"
            "  - description: 详情备注 (可选, 长描述)\n"
            "  - calendar_name: 写到哪个日历 (默认 '工作'). 调 catfish_list_calendars 看可用\n"
            "  - alarm_minutes_before: ★★ 事件前几分钟弹通知 (默认 [15] 即 15min 前 1 次).\n"
            "    iCloud 同步后 **iPhone 会震动+弹通知**. 不传 alarm 的话, 事件存在但 iPhone 不响,\n"
            "    员工到时间会忘. 传 [15, 1440] = 15min + 1天 前两次提醒. 传 [] 显式不提醒.\n\n"
            "返参:\n"
            "  - ok: True/False\n"
            "  - event_summary, start_iso, end_iso, location, alarm_minutes_before, summary (UI 用)\n"
            "  - 失败时: needs_permission 或 calendar_not_found 字段方便兜底"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "事件标题 (必填, 短描述, e.g. 'ISO 现场审核')",
                },
                "start_iso": {
                    "type": "string",
                    "description": "ISO 8601 开始时间 (必填), e.g. '2026-05-18T08:40:00'",
                },
                "end_iso": {
                    "type": "string",
                    "description": "ISO 8601 结束时间 (可选, 默认 start + 1h)",
                },
                "location": {
                    "type": "string",
                    "description": "地点 (可选), e.g. '409 会议室' / '12 楼 1201' / '苏州工业园区 XX 餐厅'",
                },
                "description": {
                    "type": "string",
                    "description": "详情备注 (可选, 长描述)",
                },
                "calendar_name": {
                    "type": "string",
                    "description": "写到哪个日历 (默认 '工作' 中文系统; 'Work' 英文系统). 不知道传啥就调 catfish_list_calendars 看",
                },
                "alarm_minutes_before": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0, "maximum": 40320},
                    "description": "事件前几分钟弹通知 (默认 [15] 一次). e.g. [15, 60, 1440] = 15min/1h/1天前 三次. 传 [] 显式不提醒. iPhone 上震动+弹通知靠这字段, 不传 = 静默事件",
                },
            },
            "required": ["title", "start_iso"],
        },
        "emoji": "📅",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_list_calendars",
        "description": (
            "列 macOS Calendar.app 所有日历名 (用户分类如 '工作' / '家庭' / '我的日历'). "
            "**第一次创建事件前调** — 看员工有没自己分类的 calendar.\n\n"
            "返参: calendar_names (数组, e.g. ['工作', '家庭', '我的日历'])"
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "emoji": "📅",
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
    # ── BL-SKILLS-RAG-TOOL (5/25 鸿波 "现在做") Progressive Disclosure 折叠区主动捞 ──
    {
        "name": "catfish_search_skills",
        "description": (
            "★★★ 跨 ~/.hermes/skills + ~/.catfish/skills BM25 搜 skill — "
            "**当 system prompt 里 skill catalog 折叠了 N 个**(显示 '还有 N 个 skill, "
            "想用调 catfish_search_skills') **必用这个找**.\n\n"
            "✅ 调用场景:\n"
            "  - system prompt 折叠区显示 'hermes:bundled 还有 168 个 skill' + 员工说 "
            "'帮我做 ECharts 图' → query='ECharts 图表' (上方 catalog 没看到 echarts 类 skill)\n"
            "  - 员工说 '有没有快速生成发票模板的 skill?' → query='发票模板 生成'\n"
            "  - 员工说 '我想找跟 GitHub 同步的工具' → query='GitHub 同步'\n"
            "  - 员工模糊问 '能帮我搞个 X 吗', 你 catalog 里没匹配 → 主动 search\n\n"
            "❌ 不调用:\n"
            "  - catalog 里 inline 显示的 skill (前 ~15 个 BM25 top-K) — 直接 catfish_run_skill\n"
            "  - 已知 skill_path 想要参数 → catfish_run_skill(skill_path='...', params={'_help': True})\n"
            "  - 找历史会话 → catfish_search_sessions\n"
            "  - 找邮件 → catfish_email_search\n\n"
            "返参:\n"
            "  - matches: top-K skill 列表 {skill_path, name, description 摘要, namespace, score}\n"
            "  - count: 命中数\n"
            "  - total_indexed: 本机共扫到多少 skill\n"
            "  - summary: 一句话归纳 + 建议下一步 (e.g. '找到 5 个, 调 catfish_run_skill _help 拿参数')\n"
            "  - latency_ms\n\n"
            "🔒 隐私: 直读员工 mac 本机 ~/.hermes/skills + ~/.catfish/skills, 不走 gateway, "
            "不上行中央, 不跨员工.\n\n"
            "💡 思路 (Anthropic Progressive Disclosure 3 层):\n"
            "  Tier 1 (system prompt catalog) - 你已看见 top-K\n"
            "  Tier 2 (这工具) - 折叠区 BM25 搜 + 拿 description 摘要\n"
            "  Tier 3 (catfish_run_skill _help) - 决定调时拿完整 SKILL.md 参数 schema"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜的关键字 / 自然语言 ('ECharts 图表' / '发票模板' / 'GitHub 同步' 等)",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返多少个 (默认 10, 上限 30). 默认够用, 真没匹配再加大.",
                },
            },
            "required": ["query"],
        },
        "emoji": "🔧",
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
            "**调用后**: 严格文件复制到 ~/.catfish/wiki/raw/sources/, sync_turn 3b 严格\n"
            "后台扫 → LLM 严格抽 entity/concept 到 wiki/entities/ + concepts/ (24h 内).\n\n"
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
