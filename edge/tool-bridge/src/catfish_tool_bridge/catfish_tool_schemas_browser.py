"""浏览器操作 + 截图 + 验证码 —— 11 个 tool 的 JSON Schema。

8/15 从 catfish_tool_schemas.py 拆出: 那个文件是**一整个 3362 行的
列表字面量** (78 个 schema), 越过 800 红线 4 倍。零逻辑、零变量引用,
所以按类别切成 8 份, 由 catfish_tool_schemas.py 拼回去。

加新 tool: schema 加到对应类别文件, impl 加 catfish_tools.py 或子 module,
dispatch 加 catfish_tools._dispatch_native_inner —— **dispatch 不重组**
(这条红线是 5/20 拆分时定的, 见 catfish_tool_schemas.py 文件头)。

description 每轮请求都发给模型, 要短: 只写做什么 / 何时用 / 何时别用 / 参数格式 /
安全规则。历史和实现细节写成 `#` 注释放在对应 dict 上方。
"""
from __future__ import annotations

from typing import Any, Dict, List


BROWSER_TOOLS: List[Dict[str, Any]] = [
    # 走 mac screencapture, 需要屏幕录制权限; 返回 data_uri, gateway 把它重组成下一轮
    # user 的 multipart image。active_window 模式已废弃: 走 osascript 'tell application
    # System Events' 会反复弹 macOS Automation 权限框。
    {
        "name": "catfish_screenshot",
        "description": (
            "截员工整个 Mac 屏幕, 返回图片供你看。仅用于浏览器以外的内容: 别的 app 的报错 / "
            "对话框, 或跨窗口一起看。看网页 (验证码 / 按钮 / 弹窗) 用 catfish_browser_screenshot; "
            "看本地文件用 read_file。员工没要求看屏幕时不要主动截。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["fullscreen", "interactive", "window"],
                    "default": "fullscreen",
                    "description": "fullscreen=主屏全屏(默认); interactive=员工框选; window=员工点选窗口",
                },
                "reason": {
                    "type": "string",
                    "description": "为什么要截图, 一句话, 员工会看到",
                },
            },
            "required": ["reason"],
        },
        "emoji": "📸",
        "toolset": "catfish_native",
        "available": True,
    },
    # 走 Playwright page.goto() (connect_over_cdp 复用 Companion 起的 Chrome)。
    # 默认 'load' 超时会自动降级 'domcontentloaded' 重试一次 (门户站第三方广告请求
    # 在受限网络里一直挂着, load 永不触发), 降级成功返回带 degraded_wait_until。
    # hermes browser_navigate 走外部 CLI, 等不到 load 会干等超时, 所以导航统一用这个。
    # BL-TOOLBRIDGE-CONSOLE-TOOL (5/27): 模型传 expression=... 想跑 JS 曾导致死循环,
    # additionalProperties:false 让未知字段在 schema 层就被拒。
    {
        "name": "catfish_browser_goto",
        "description": (
            "仅用于 URL 导航: 在 Chrome 当前 tab 打开一个 URL, 返回真实 title + url 供核对。"
            "浏览器导航一律用它, 不用 browser_navigate。不能跑 JS / 读 DOM (那用 "
            "catfish_browser_evaluate)。下载文件用 catfish_browser_download。"
            "load 超时会自动降级重试, 返回带 degraded_wait_until 表示部分资源可能没加载完。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "完整 URL, 含 https://"},
                "wait_until": {
                    "type": "string",
                    "enum": ["load", "domcontentloaded", "networkidle"],
                    "default": "load",
                    "description": "SPA 用 networkidle, 普通页面 load",
                },
                "timeout_seconds": {
                    "type": "number",
                    "default": 30.0,
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        "emoji": "🌐",
        "toolset": "catfish_native",
        "available": True,
    },
    # BL-TOOLBRIDGE-CONSOLE-TOOL (5/27): JS eval 工具, 起源见
    # catfish_tools_browser.py::browser_evaluate。双名: evaluate (规范) +
    # console (alias)。dispatch 两个都路同一 impl。走 Playwright page.evaluate()。
    {
        "name": "catfish_browser_evaluate",
        "description": (
            "在 Chrome 当前页跑一段 JavaScript 表达式并返回结果 (等同 DevTools Console)。"
            "读 DOM / 查元素 / 抽列表文本都用它, 例 document.title、"
            "Array.from(document.querySelectorAll('.item')).map(x=>x.innerText)。"
            "返回值须可 JSON 序列化, DOM 节点会变 null, 过大会截断。不要用 goto 跑 JS。"
            "银行 / 政务等敏感页先确认再跑。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "JS 表达式或 IIFE, 不能是裸语句 (如 let x = 1)",
                },
                "timeout_seconds": {
                    "type": "number",
                    "default": 10.0,
                    "description": "默认 10, 上限 30",
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
            "catfish_browser_evaluate 的别名, 参数和行为完全相同。优先用 catfish_browser_evaluate。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "JS 表达式, 同 catfish_browser_evaluate",
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
    # BL-FIX44 (5/11): 加 coordinates 模式 (Playwright mouse.click)。实测 text= selector
    # 会撞 placeholder 点错, 所以不推荐 text=。selector 模式走 page.click 自带 auto-waiting。
    {
        "name": "catfish_browser_click",
        "description": (
            "点击页面元素, selector 与 coordinates 二选一:\n"
            "- coordinates=[x, y]: 视口像素 (catfish_browser_screenshot 图中坐标, 或 find_by_text / "
            "locate 返回的 center), 完全绕开 selector 歧义; 不自动等待。\n"
            "- selector: 自动等元素可点。优先 role=button[name=\"提交\"], 其次 CSS, 避免 text= "
            "(易误中 placeholder)。\n"
            "有文字先用 catfish_browser_find_by_text; 不确定页面状态先截图。下载文件用 "
            "catfish_browser_download。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "Playwright selector (role= / CSS), 与 coordinates 二选一",
                },
                "coordinates": {
                    "type": "array",
                    "description": "[x, y] 视口像素坐标, 与 selector 二选一, 例 [450, 380]",
                    "items": {"type": "integer"},
                    "minItems": 2,
                    "maxItems": 2,
                },
                "timeout_seconds": {
                    "type": "number",
                    "default": 30.0,
                    "description": "仅 selector 模式: 等元素可点的秒数",
                },
            },
        },
        "emoji": "🖱",
        "toolset": "catfish_native",
        "available": True,
    },
    # secret_ref='keychain://…' 是老写法, 只给已冻结的 skill 用: 那串会焊进 script.py,
    # 员工改密码就失联, 所以新教学用 secret_for_site。走 Playwright page.fill()。
    {
        "name": "catfish_browser_fill",
        "description": (
            "往输入框 (input / textarea / contenteditable) 填文字, 自动清空原值, 无需先 click。"
            "text 是明文, 只填用户名 / 邮箱 / 普通内容。填密码一律 secret_for_site=true, "
            "密码不进对话; 返回 needs_credential 表示该站还没存密码, 请员工存一次后重调。"
            "绝不把密码写进 text。"
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
                    "description": "明文内容, 不能放密码",
                },
                "secret_for_site": {
                    "type": "boolean",
                    "description": "填密码用: 按当前站点取本机存的密码",
                },
                "secret_ref": {
                    "type": "string",
                    "description": "旧写法 (keychain://… / env://…), 仅已冻结 skill 用",
                },
                "timeout_seconds": {
                    "type": "number",
                    "default": 10.0,
                },
            },
            "required": ["selector"],
        },
        "emoji": "⌨️",
        "toolset": "catfish_native",
        "available": True,
    },
    # 优先 Playwright accessibility.snapshot(); >=1.50 已废弃, 自动 fallback 到
    # page.evaluate 扫 button/input/a/[role], 返回 snapshot_method 标明走哪条。
    # 减小 max_elements 会让登录 / 提交按钮被 nav/footer link 挤出 top N。
    {
        "name": "catfish_browser_snapshot",
        "description": (
            "取当前页可见 / 可交互元素列表 (role + name + selector_hint), 用来找 "
            "catfish_browser_click / catfish_browser_fill 要用的 selector。"
            "返回 title、url、elements、truncated。找不到元素或 truncated=true 时把 "
            "max_elements 加大到 1000, 不要减小。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "max_elements": {
                    "type": "integer",
                    "default": 500,
                    "description": "默认 500, 上限 1000; 找不到元素时加大, 别减小",
                },
            },
            "required": [],
        },
        "emoji": "🔍",
        "toolset": "catfish_native",
        "available": True,
    },
    # BL-FIX17 (5/8): 自动压缩。viewport: max 1280px + JPEG q80; full_page: max 1600px +
    # JPEG q75; >800KB 降 q60 重压, 还不行返 error。selector 截元素保留 PNG 不压。
    # 返回带 size_kb_before / size_kb_after / compression_ratio / format。
    {
        "name": "catfish_browser_screenshot",
        "description": (
            "截浏览器当前 tab 的图供你看: 验证码、按钮位置、页面布局、弹窗。无需权限。"
            "截整个 Mac 屏幕 / 其他 app 用 catfish_screenshot。图中像素坐标即 "
            "catfish_browser_click(coordinates) 的坐标系。默认自动压缩; 给 selector 只截该元素且保留原图。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "只截该元素 (如验证码图), 不压缩; 不传截视口",
                },
                "full_page": {
                    "type": "boolean",
                    "default": False,
                    "description": "true=截整个滚动长度, false=只截视口",
                },
                "timeout_seconds": {
                    "type": "number",
                    "default": 10.0,
                },
                "compress": {
                    "type": "string",
                    "enum": ["auto", "none"],
                    "default": "auto",
                    "description": "auto 推荐; none 保留原 PNG (很大), 员工要原图时才用",
                },
            },
            "required": [],
        },
        "emoji": "📸",
        "toolset": "catfish_native",
        "available": True,
    },
    # BL-FIX44 (5/11) 重写: 老版只返单个 element, 常抓到同文字的 placeholder;
    # 新版返多个带元数据的候选 + top_recommendation (第 1 个 clickable 候选)。
    {
        "name": "catfish_browser_find_by_text",
        "description": (
            "按可见文字在 DOM 里找元素, 返回按 score 排序的候选 elements 和 top_recommendation。"
            "有文字的按钮 / 链接首选它; 无文字 (图标、关闭 X) 用 catfish_browser_locate。\n"
            "候选含 selector、center{x,y}、role、match_type (innerText / placeholder / aria-label 等)、"
            "is_clickable。优先选 is_clickable=true 且 match_type=innerText 的, 把其 selector 或 "
            "center 交给 catfish_browser_click; 只命中 placeholder 时传 role='button' 重查 "
            "(找按钮建议直接传)。\n"
            "element_count=0 → 改用 catfish_browser_screenshot 看图后按坐标点。"
            "匹配不跨语言: 'login' 不匹配 '登录', 中文页面给中文。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "要找的元素文字, 例 '登录' / '下一步'",
                },
                "role": {
                    "type": "string",
                    "description": "ARIA role 过滤, 如 button / link / textbox; 找按钮传 button",
                },
                "exact": {
                    "type": "boolean",
                    "default": False,
                    "description": "true=完全匹配; false=子串匹配 (默认, 优先)",
                },
                "max_results": {
                    "type": "integer",
                    "default": 10,
                    "description": "默认 10, 上限 30; 同名元素多时加大",
                },
            },
            "required": ["text"],
        },
        "emoji": "🔎",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-Q3-WEBSKILL (5/11) 视觉定位 ───────────────────────────────
    # 主模型看截图估坐标常偏 50-100px, 专用 vision 模型 (catfish-private-vision) 更准。
    # 与 catfish_recognize_captcha 同一个 vision 模型, 不同 prompt。
    {
        "name": "catfish_browser_locate",
        "description": (
            "用视觉模型按自然语言描述定位页面元素, 返回 found、center{x,y}、confidence。"
            "用于无文字元素 (关闭 X、图标) 或文字歧义时; 有文字优先 catfish_browser_find_by_text; "
            "识别验证码字符用 catfish_recognize_captcha。confidence≥0.6 → 把 center 交给 "
            "catfish_browser_click(coordinates); found=false → 换描述重试。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "越具体越准, 例 '密码框下方的验证码输入框'",
                },
                "selector": {
                    "type": "string",
                    "description": "只在该元素区域内找, 例 'form.login-form'",
                },
                "image_b64": {
                    "type": "string",
                    "description": "直传 base64 PNG (不含 data: 前缀), 调试用",
                },
                "full_page": {
                    "type": "boolean",
                    "default": False,
                    "description": "true=截整页 (慢); 传了 selector / image_b64 时忽略",
                },
                "max_retry": {
                    "type": "integer",
                    "default": 1,
                    "description": "默认 1, 最大 3",
                },
            },
            "required": ["query"],
        },
        "emoji": "🎯",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-Q3-WEBSKILL (5/11) 验证码 OCR ──────────────────────────
    # 走 gateway loopback + catfish-private-vision。
    # hint 坑 (8/19): hint 原样进 prompt, 传错 (如字母数字混排的验证码传 numeric_4) 时模型
    # 会把 S 读成 5、B 读成 8, 且 confidence 仍很高 (0.85), 错答案被当成对的填进去。
    {
        "name": "catfish_recognize_captcha",
        "description": (
            "识别页面上的验证码图片字符 (OCR), 返回 ok、text、confidence。登录 / 提交表单遇到"
            "图形验证码时用, 拿到 text 后用 catfish_browser_fill 填入。selector 与 image_b64 二选一 "
            "(推荐 selector, 无需先截图)。ok=false → 重截 / 重试, 仍失败则请员工手动填。"
                    ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "验证码图片的 CSS selector, 例 '#captchaImg'",
                },
                "image_b64": {
                    "type": "string",
                    "description": "base64 PNG/JPG (不含 data: 前缀), 与 selector 二选一",
                },
                "hint": {
                    "type": "string",
                    "description": "字符集提示, 如 alphanumeric_4 / numeric_4; 没亲眼确认就别传, 传错会高置信度读错",
                },
                "max_retry": {
                    "type": "integer",
                    "default": 1,
                    "description": "默认 1, 最大 3",
                },
            },
        },
        "emoji": "🔢",
        "toolset": "catfish_native",
        "available": True,
    },
    # 10/17 加。教学时用它, 凝固出的 skill 会把下载文件路径交给后续步骤 / 工作流。
    {
        "name": "catfish_browser_download",
        "description": (
            "点击下载按钮 / 链接并等文件下完, 返回 {path, filename, size} (存到 "
            "~/.catfish/downloads/<日期>/)。下载报表 / 附件 / 导出时用它, 不要用 "
            "catfish_browser_click (拿不到文件) 或 goto。selector 与 coordinates 二选一, 指向真正触发下载"
            "的元素; 若先弹窗, 先 click 打开弹窗再对弹窗里的下载链接用它。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "下载按钮 / 链接的 selector"},
                "coordinates": {"type": "array", "items": {"type": "integer"}, "description": "[x, y] 视口像素坐标"},
                "timeout_seconds": {"type": "number", "description": "等下载完成的最长秒数, 默认 60"},
            },
        },
    },
]
