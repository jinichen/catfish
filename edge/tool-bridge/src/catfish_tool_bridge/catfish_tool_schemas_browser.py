"""浏览器操作 + 截图 + 验证码 —— 11 个 tool 的 JSON Schema。

8/15 从 catfish_tool_schemas.py 拆出: 那个文件是**一整个 3362 行的
列表字面量** (78 个 schema), 越过 800 红线 4 倍。零逻辑、零变量引用,
所以按类别切成 8 份, 由 catfish_tool_schemas.py 拼回去。

加新 tool: schema 加到对应类别文件, impl 加 catfish_tools.py 或子 module,
dispatch 加 catfish_tools._dispatch_native_inner —— **dispatch 不重组**
(这条红线是 5/20 拆分时定的, 见 catfish_tool_schemas.py 文件头)。
"""
from __future__ import annotations

from typing import Any, Dict, List


BROWSER_TOOLS: List[Dict[str, Any]] = [
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
            "✅ **浏览器导航永远用这个**, 不要用 hermes browser_navigate (那个走 agent-browser "
            "外部 CLI, 等 'load' 等不到就干等超时, 没有降级). \n\n"
            "wait_until 选项: 'load' (默认, 等所有资源加载完) / 'domcontentloaded' (只等 DOM) / "
            "'networkidle' (等 500ms 无网络活动, 适合 SPA). \n\n"
            "**超时会自动降级**: 用默认 'load' 撞超时时, 本工具自动改 'domcontentloaded' 重试一次 "
            "—— 门户站 (搜狐 / 新浪这类) 挂着大量第三方广告统计, 那些请求在受限网络里一直挂着, "
            "'load' 事件永远不触发, 但 DOM 其实早就好了. 降级成功的返回带 degraded_wait_until "
            "字段, 意思是'页面能操作, 但部分资源可能没到位' —— 后续要截图 / 取全文时留意.\n\n"
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
            "**填密码一律 secret_for_site=true** — 按当前页站点自动取本机存的密码, 你不用知道任何 "
            "ref, 密码永不进 LLM 上下文. 这个站点还没存过 → 返 needs_credential, 员工就地存一次, "
            "你再调一次同样的就行.\n"
            "secret_ref='keychain://…' 是老写法, 只有已冻结的 skill 还在用; 新教学别用 —— "
            "那串会被焊进 script.py, 员工改密码就失联.\n"
            "text 是明文, 只给用户名 / 邮箱 / 普通内容用."
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
                "secret_for_site": {
                    "type": "boolean",
                    "description": (
                        "填密码用这个. 按当前页 hostname 找本机存的密码, 不用给 ref. "
                        "没存过返 needs_credential (不是坏了, 是要员工存一次)."
                    ),
                },
                "secret_ref": {
                    "type": "string",
                    "description": (
                        "老写法, 显式引用, 例 'keychain://eis_password' / 'env://EIS_PASSWORD'. "
                        "只给已冻结的 skill 用, 新教学用 secret_for_site."
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
                        "可选. ⚠ **不确定就别传** —— 传错比不传糟得多。\n"
                        "hint 会原样进 prompt ('提示: numeric_4'), 模型会照着它读: "
                        "说是数字, 它就把 S 读成 5、B 读成 8, 而且返回的置信度**照样很高** "
                        "(0.85), 于是一个错答案会被当成对的填进去。不传 hint 反而是最高分。\n"
                        "8/19 实撞: EIS 验证码是 '5KBz' / 'cZf3' / 'W6UV' (字母数字混排), "
                        "模型顺手传了 numeric_4 —— 这个值当时排在本行示例的第一个。\n"
                        "只有**亲眼确认过这个站点的字符集**才传: "
                        "'alphanumeric_4' (4 位字母数字, 最常见) / 'alphanumeric_5' / 'alphanumeric_6' / "
                        "'numeric_4' (纯数字, 少见) / 'numeric_5' / 'numeric_6' / 'chinese' / 自然语言"
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
]
