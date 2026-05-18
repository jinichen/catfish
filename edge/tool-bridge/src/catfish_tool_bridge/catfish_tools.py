"""Catfish 原生 tools —— 不来自 hermes registry, 是 catfish 自己加的。

为什么需要:
    Hermes 的 session_search / memory_recall 是面向 LLM 自己"翻历史"用的, 但
    员工聊天里问"今天小鲶学了啥"时, LLM 调 session_search 要么搜不到要么
    答非所问 (今晚截图里就是这样)。我们暴露一个明确的 catfish_today_summary
    工具, 让 LLM 一眼知道该调它。

数据源:
    1. ~/.hermes/USER.md + memories/*.md  → 今天有更新的 memory
    2. ~/.hermes/skills/<ns>/<name>/SKILL.md  → 今天 mtime 落在今天的
    3. ~/.hermes/state.db sessions  → 今天启动的会话 + token 总量
    4. ~/.hermes/state.db messages  → 今天的 tool 调用次数

跟 companion-app/src-tauri/src/commands/learning.rs 是同一份逻辑的 Python
镜像 —— 故意不走 IPC 调 Companion, 因为 tool-bridge 起来时 Companion 不一
定开着 (CLI 也在用 tool-bridge)。两边各自直读 ~/.hermes 是最 robust 的。
"""
from __future__ import annotations

import base64
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# 工具 schema —— 给 LLM 看的描述
# ============================================================

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
            "★ 重新扫描员工历史文档目录, 重建文书风格指纹.\n\n"
            "默认扫: ~/Documents/work/, ~/.catfish/output/.\n"
            "支持: .md / .txt / .docx (其他类型跳过).\n"
            "约束: 跳过 < 200 字 / > 5MB / 隐藏文件; 最多扫 500 个文件.\n"
            "时间衰减: 30 天内权重 1.0, 90 天 0.5, 180 天 0.25, 更老 0.1.\n\n"
            "✅ 调用时机:\n"
            "  - 员工说 '更新一下你对我写作风格的认识'\n"
            "  - 员工写完一份新汇报后, 主动 refresh (10-20 个文档变化时)\n"
            "  - 第一次启动 (员工 onboarding 时)\n\n"
            "❌ 频率: 不要每次写文档前都 refresh — 文档没变前指纹一样, 浪费 IO.\n"
            "    一周一次或员工显式要求时再调."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source_dirs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选, 自定义扫描目录列表; 默认 ['~/Documents/work', '~/.catfish/output']",
                },
            },
            "required": [],
        },
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
            "在 Chrome 当前 tab 打开一个 URL. **走 Playwright 后端** (不再是直 CDP), "
            "Playwright 内部包了 auto-waiting + retry, 比直 CDP 稳得多. 复用 Companion 起的"
            "隔离 Chrome 已登录态 (connect_over_cdp). \n\n"
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
        },
        "emoji": "🌐",
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
            "返字段: status (pending/running/completed/failed/not_found), "
            "elapsed_s, label, error.\n\n"
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
            },
            "required": ["name", "reason", "action_steps", "evidence_count"],
        },
        "emoji": "💡",
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
            "    用员工部门时, 找 catfish_today_summary 的 department 字段\n\n"
            "成功返:\n"
            "  {ok:true, namespace, name, version, published_at, hub_url}\n"
            "失败返:\n"
            "  {ok:false, error}\n\n"
            "❌ 别在没员工 explicit 确认时调用. 别把含敏感 path / 凭据的 skill 发上去 — "
            "publish 前要 grep 是否含 password / api_key / token 字眼.\n\n"
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
            },
            "required": ["skill_path", "namespace"],
        },
        "emoji": "🚀",
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
            "  - overwrite: 同名 skill 已存在时是否覆盖 (默认 false)\n"
            "  - run_install: 凝固后自动跑 install_to_hermes.sh (默认 true)\n\n"
            "**返回**: {ok, name, namespace, skill_path, hermes_name, files[], params[], install{...}, summary}\n\n"
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
                "overwrite": {
                    "type": "boolean",
                    "description": "已存在的 skill 是否覆盖. 默认 false.",
                },
                "run_install": {
                    "type": "boolean",
                    "description": "凝固后自动跑 install_to_hermes.sh 同步. 默认 true.",
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
]


# ============================================================
# 实现
# ============================================================

def _home() -> Path:
    return Path(os.environ.get("HOME") or os.environ.get("USERPROFILE") or ".")


def _hermes_dir() -> Path:
    return _home() / ".hermes"


def _today_start_unix() -> float:
    """本地时区今天 00:00:00 的 unix 秒。"""
    today = datetime.now().date()
    return datetime.combine(today, dtime.min).timestamp()


def _is_today(mtime: float) -> bool:
    return mtime >= _today_start_unix()


def _unix_to_iso(secs: float) -> str:
    try:
        return datetime.fromtimestamp(secs).isoformat()
    except Exception:
        return ""


def _collect_memories() -> List[Dict[str, Any]]:
    """读 ~/.hermes/USER.md + memories/*.md, 按 mtime 降序返回。"""
    out: List[Dict[str, Any]] = []
    hermes = _hermes_dir()

    user_md = hermes / "USER.md"
    if user_md.exists():
        try:
            st = user_md.stat()
            out.append({
                "name": "USER",
                "size": st.st_size,
                "modified_at": _unix_to_iso(st.st_mtime),
                "modified_today": _is_today(st.st_mtime),
            })
        except OSError:
            pass

    mem_dir = hermes / "memories"
    if mem_dir.is_dir():
        for entry in mem_dir.iterdir():
            if entry.suffix != ".md" or not entry.is_file():
                continue
            try:
                st = entry.stat()
            except OSError:
                continue
            out.append({
                "name": f"memories/{entry.stem}",
                "size": st.st_size,
                "modified_at": _unix_to_iso(st.st_mtime),
                "modified_today": _is_today(st.st_mtime),
            })

    out.sort(key=lambda m: m["modified_at"], reverse=True)
    return out


def _extract_description(text: str) -> str:
    """从 SKILL.md frontmatter 抽 description 字段。"""
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return ""
    after = stripped[3:].lstrip("\n")
    end = after.find("\n---")
    if end < 0:
        return ""
    for line in after[:end].splitlines():
        if line.startswith("description:"):
            value = line[len("description:"):].strip().strip('"').strip("'")
            if value:
                return value
    return ""


def _collect_new_skills() -> List[Dict[str, Any]]:
    """~/.hermes/skills/<ns>/<name>/SKILL.md mtime 落在今天的算"今天新增"。"""
    out: List[Dict[str, Any]] = []
    skills_dir = _hermes_dir() / "skills"
    if not skills_dir.is_dir():
        return out

    for ns_dir in skills_dir.iterdir():
        if not ns_dir.is_dir() or ns_dir.name.startswith("."):
            continue
        for skill_dir in ns_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            manifest = skill_dir / "SKILL.md"
            if not manifest.exists():
                continue
            try:
                st = manifest.stat()
            except OSError:
                continue
            if not _is_today(st.st_mtime):
                continue
            try:
                desc = _extract_description(manifest.read_text(encoding="utf-8"))
            except OSError:
                desc = ""
            out.append({
                "full_name": f"{ns_dir.name}/{skill_dir.name}",
                "description": desc or "(无 description)",
                "modified_at": _unix_to_iso(st.st_mtime),
            })

    out.sort(key=lambda s: s["modified_at"], reverse=True)
    return out


def _collect_db_stats() -> Dict[str, int]:
    """state.db: 今天的 sessions / token / tool_calls 数。"""
    db_path = _hermes_dir() / "state.db"
    fallback = {"sessions_today": 0, "tool_calls_today": 0, "total_tokens_today": 0}
    if not db_path.exists():
        return fallback

    today_start = _today_start_unix()
    try:
        # read-only 打开,免得污染 hermes 自己的 WAL
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return fallback

    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS n,
                COALESCE(SUM(
                    COALESCE(input_tokens, 0) +
                    COALESCE(output_tokens, 0) +
                    COALESCE(cache_read_tokens, 0) +
                    COALESCE(cache_write_tokens, 0) +
                    COALESCE(reasoning_tokens, 0)
                ), 0) AS tok
            FROM sessions
            WHERE started_at >= ?
            """,
            (today_start,),
        ).fetchone()
        sessions = int(row[0] or 0)
        tokens = int(row[1] or 0)

        tool_row = conn.execute(
            """
            SELECT COUNT(*) FROM messages
            WHERE timestamp >= ?
              AND tool_calls IS NOT NULL
              AND tool_calls != ''
            """,
            (today_start,),
        ).fetchone()
        tool_calls = int(tool_row[0] or 0)
    except sqlite3.Error:
        return fallback
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {
        "sessions_today": sessions,
        "tool_calls_today": tool_calls,
        "total_tokens_today": tokens,
    }


# ============================================================
# 软技能维度 (#46 沟通能力进步追踪)
# ============================================================

#: 沟通方法论关键词清单 (跟 Rust learning.rs 对齐)
KNOWN_METHODOLOGIES = [
    "STAR", "SBI", "NVC", "非暴力沟通", "金字塔", "Pyramid",
    "SPIN", "DESC", "Disagree and commit",
]


def _week_start_unix(weeks_ago: int) -> float:
    """给定"几周前"的本周一 00:00 unix 秒。"""
    today = datetime.now().date()
    days_since_monday = today.weekday()  # Monday=0
    this_monday = today - timedelta(days=days_since_monday)
    target_monday = this_monday - timedelta(weeks=weeks_ago)
    midnight = datetime.combine(target_monday, dtime.min)
    return midnight.timestamp()


def _collect_soft_skill_stats() -> Dict[str, Any]:
    """演练 / 邮件起草 / 方法论暴露 跨周指标。

    跟 Rust learning.rs 的 collect_soft_skill_stats 镜像。
    """
    fallback = {
        "coaching_sessions_today": 0,
        "coaching_sessions_this_week": 0,
        "coaching_sessions_prev_week": 0,
        "emails_drafted_today": 0,
        "methodologies_this_week": [],
    }
    db_path = _hermes_dir() / "state.db"
    if not db_path.exists():
        return fallback

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return fallback

    today_start = _today_start_unix()
    this_week_start = _week_start_unix(0)
    prev_week_start = _week_start_unix(1)

    try:
        # 演练: assistant content 含"做对了" + "改进点"
        coaching_today = int(conn.execute(
            """
            SELECT COUNT(DISTINCT session_id) FROM messages
            WHERE timestamp >= ?
              AND role = 'assistant'
              AND content LIKE '%做对了%'
              AND content LIKE '%改进点%'
            """,
            (today_start,),
        ).fetchone()[0] or 0)

        coaching_this_week = int(conn.execute(
            """
            SELECT COUNT(DISTINCT session_id) FROM messages
            WHERE timestamp >= ?
              AND role = 'assistant'
              AND content LIKE '%做对了%'
              AND content LIKE '%改进点%'
            """,
            (this_week_start,),
        ).fetchone()[0] or 0)

        coaching_prev_week = int(conn.execute(
            """
            SELECT COUNT(DISTINCT session_id) FROM messages
            WHERE timestamp >= ? AND timestamp < ?
              AND role = 'assistant'
              AND content LIKE '%做对了%'
              AND content LIKE '%改进点%'
            """,
            (prev_week_start, this_week_start),
        ).fetchone()[0] or 0)

        # 邮件起草: tool_calls 含 catfish-email
        emails_drafted = int(conn.execute(
            """
            SELECT COUNT(*) FROM messages
            WHERE timestamp >= ?
              AND tool_calls LIKE '%catfish-email%'
            """,
            (today_start,),
        ).fetchone()[0] or 0)

        # 本周接触的方法论
        rows = conn.execute(
            """
            SELECT content FROM messages
            WHERE timestamp >= ?
              AND role = 'assistant'
              AND content IS NOT NULL
              AND length(content) > 50
            LIMIT 2000
            """,
            (this_week_start,),
        ).fetchall()
        hits = set()
        for (content,) in rows:
            if not content:
                continue
            for term in KNOWN_METHODOLOGIES:
                if term in content:
                    hits.add(term)
        # 按 KNOWN 顺序输出 (UI 稳定)
        methodologies = [m for m in KNOWN_METHODOLOGIES if m in hits]

    except sqlite3.Error:
        return fallback
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {
        "coaching_sessions_today": coaching_today,
        "coaching_sessions_this_week": coaching_this_week,
        "coaching_sessions_prev_week": coaching_prev_week,
        "emails_drafted_today": emails_drafted,
        "methodologies_this_week": methodologies,
    }


def _build_summary(
    memories_today: int,
    skills_today: int,
    sessions: int,
    tool_calls: int,
    coaching_today: int = 0,
    emails_drafted: int = 0,
) -> str:
    if (
        memories_today == 0
        and skills_today == 0
        and sessions == 0
        and coaching_today == 0
        and emails_drafted == 0
    ):
        return "今天还没动静——跟小鲶聊聊它就开始学了。"
    parts: List[str] = []
    if sessions > 0:
        parts.append(f"{sessions} 次对话")
    if tool_calls > 0:
        parts.append(f"调用 {tool_calls} 次工具")
    if coaching_today > 0:
        parts.append(f"演练 {coaching_today} 次")
    if emails_drafted > 0:
        parts.append(f"起草 {emails_drafted} 封邮件")
    if memories_today > 0:
        parts.append(f"更新 {memories_today} 条 memory")
    if skills_today > 0:
        parts.append(f"新增 {skills_today} 个 skill")
    return f"今天小鲶 {'、'.join(parts)}。"


def _collect_audit_stats_today() -> Dict[str, Any]:
    """从 ~/.hermes/.catfish_audit.jsonl 拿今天的 tool 调用统计.

    跟 db_stats 的 tool_calls_today 不同 — db_stats 来自 hermes state.db (cli/companion
    sessions), audit.jsonl 来自 tool-bridge dispatch (含 catfish native tool 调用).
    audit 视角是"tool-bridge 服务的所有调用", 更准.
    """
    fallback = {"tool_invocations_today": 0, "tool_failures_today": 0}
    try:
        from . import audit  # 避免顶层 import 循环
    except ImportError:
        return fallback

    today_iso = datetime.now().date().isoformat()  # 例 "2026-04-28"
    try:
        events = audit.read_events(since_iso=today_iso, limit=10000)
    except Exception:  # noqa: BLE001
        return fallback

    failures = sum(1 for e in events if not e.get("ok", True))
    return {
        "tool_invocations_today": len(events),
        "tool_failures_today": failures,
    }


def _collect_unused_skills(days: int = 30) -> List[Dict[str, Any]]:
    """找 ~/.hermes/skills/<ns>/<name>/SKILL.md mtime 超过 N 天前的 skill.

    判定原则: SKILL.md 文件 mtime 是 last touch (创建 / update / 员工手动改). N 天没动
    + 没出现在 audit 里 = 候选 unused. 给员工建议删 (走 catfish_skill_backup +
    skill_manage delete 流程, 详见 docs/SKILL-LIFECYCLE.md 阶段 5).

    catfish-* skill 排除掉 (软链管理, R6 也禁止删).
    """
    out: List[Dict[str, Any]] = []
    skills_dir = _hermes_dir() / "skills"
    if not skills_dir.is_dir():
        return out

    cutoff_unix = time.time() - days * 86400

    for ns_dir in skills_dir.iterdir():
        if not ns_dir.is_dir() or ns_dir.name.startswith("."):
            continue
        for skill_dir in ns_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            # catfish-* skill 通过 install.sh 软链, 不算"员工 unused"
            if skill_dir.is_symlink():
                continue
            manifest = skill_dir / "SKILL.md"
            if not manifest.exists():
                continue
            try:
                mtime = manifest.stat().st_mtime
            except OSError:
                continue
            if mtime > cutoff_unix:
                continue
            out.append({
                "full_name": f"{ns_dir.name}/{skill_dir.name}",
                "last_modified": _unix_to_iso(mtime),
                "days_since_modified": int((time.time() - mtime) / 86400),
            })

    out.sort(key=lambda s: s["days_since_modified"], reverse=True)
    return out


def collect_today_summary() -> Dict[str, Any]:
    """返回与 Tauri learning_today_stats 对齐的字段(camelCase 风格), 给 LLM 看。"""
    memories = _collect_memories()
    memories_today = sum(1 for m in memories if m["modified_today"])

    new_skills = _collect_new_skills()
    skills_today = len(new_skills)

    db_stats = _collect_db_stats()
    sessions = db_stats["sessions_today"]
    tool_calls = db_stats["tool_calls_today"]
    tokens = db_stats["total_tokens_today"]

    soft = _collect_soft_skill_stats()

    # BL-C15: audit-based 字段 (tool 调用监控)
    audit_today = _collect_audit_stats_today()

    # BL-C15/C16: 30 天 SKILL.md mtime 没动的 skill, 候选 unused (给员工删/留建议)
    unused_skills = _collect_unused_skills(days=30)

    summary = _build_summary(
        memories_today, skills_today, sessions, tool_calls,
        soft["coaching_sessions_today"], soft["emails_drafted_today"],
    )

    return {
        "summary": summary,
        "date": datetime.now().date().isoformat(),
        "sessions_today": sessions,
        "tool_calls_today": tool_calls,
        "total_tokens_today": tokens,
        "memories_updated_today": memories_today,
        "memories": [m for m in memories if m["modified_today"]],
        "new_skills_count": skills_today,
        "new_skills": new_skills,
        # ==== 软技能维度 (#46) ====
        "coaching_sessions_today": soft["coaching_sessions_today"],
        "coaching_sessions_this_week": soft["coaching_sessions_this_week"],
        "coaching_sessions_prev_week": soft["coaching_sessions_prev_week"],
        "emails_drafted_today": soft["emails_drafted_today"],
        "methodologies_this_week": soft["methodologies_this_week"],
        # ==== Skill lifecycle 阶段 4 (BL-C15/C16, audit-based) ====
        "tool_invocations_today": audit_today["tool_invocations_today"],
        "tool_failures_today": audit_today["tool_failures_today"],
        "skill_unused_30d": unused_skills,
        "generated_at": _unix_to_iso(time.time()),
    }


# ============================================================
# 截图 (catfish_screenshot)
# ============================================================
#
# 设计要点:
#   1. 默认 interactive mode → 员工框选, 隐私优先
#   2. 同时返回 base64 PNG + 临时文件路径, 调用方可二选一
#   3. screencapture 退码 0 即使员工 ESC 取消 — 用 "文件不存在或为空" 判取消
#   4. Win 没有原生交互式截图工具 → 退到 fullscreen 并在结果里说明
#   5. 不主动 cleanup 临时文件 — 让员工自查 /tmp/catfish-shot-*.png

# 锁住单次截图最大字节, 防员工框了一个 27" 5K 屏幕一截 50MB 卡死 socket
# (asyncio readline 默认 limit=64KB, 我们在 server.py 把它提到 16MB)
_MAX_SCREENSHOT_BYTES = 12 * 1024 * 1024  # 12MB raw PNG, base64 后 ~16MB


def _screencapture_macos(mode: str, out_path: Path) -> Tuple[bool, Optional[str]]:
    """macOS 用系统自带 screencapture. 返回 (是否成功, 错误信息)。

    flags:
      -x          静音(无快门声)
      -i          交互模式: 框选区域 (按 ESC 取消)
      -W          配合 -i, 让员工点选窗口

    (active_window 模式已废弃 — 它需要 osascript "tell application System Events"
     拿前台窗口 ID, 触发 macOS Automation 权限弹窗, 而 hermes venv 的 python3.11
     没 Apple 代码签名, macOS 不持久化授权, 反复弹. 删掉了.)
    """
    if not shutil.which("screencapture"):
        return False, "找不到 screencapture (非 macOS 或系统残缺)"

    cmd = ["screencapture", "-x"]
    if mode == "interactive":
        cmd.append("-i")
    elif mode == "fullscreen":
        pass  # 默认就是全屏
    elif mode == "window":
        cmd.extend(["-i", "-W"])
    else:
        return False, f"未知 mode: {mode}"
    cmd.append(str(out_path))

    try:
        # interactive 模式员工可能磨蹭很久, 给宽裕超时
        # fullscreen 是即拍即出, 几秒就回
        timeout = 10 if mode == "fullscreen" else 120
        subprocess.run(cmd, timeout=timeout, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return False, "screencapture 超时"
    except OSError as e:
        return False, f"启动 screencapture 失败: {e}"

    # screencapture 即使员工按 ESC 也退码 0, 用文件状态判定
    if not out_path.exists() or out_path.stat().st_size == 0:
        # 清掉空文件免得垃圾
        try:
            out_path.unlink(missing_ok=True)
        except OSError:
            pass
        if mode in ("interactive", "window"):
            return False, "员工取消了截图(或选区为空)"
        return False, "截图失败 (文件不存在或为空)"
    return True, None


def _screencapture_windows(mode: str, out_path: Path) -> Tuple[bool, Optional[str]]:
    """Windows 用 PIL.ImageGrab.grab() — 只能全屏, interactive/window 不支持。"""
    try:
        from PIL import ImageGrab  # type: ignore
    except ImportError:
        return False, "Windows 截图需要 Pillow: pip install Pillow"

    try:
        # PIL 不区分 mode, 都拍全屏。caller 已经把 interactive/window 改成 fullscreen
        img = ImageGrab.grab()
        img.save(str(out_path), "PNG")
    except Exception as e:
        return False, f"截图失败: {e}"
    return True, None


def capture_screenshot(args: Dict[str, Any]) -> Dict[str, Any]:
    """catfish_screenshot tool 主入口。"""
    mode = (args.get("mode") or "fullscreen").lower()
    reason = (args.get("reason") or "").strip()

    if not reason:
        return {
            "type": "error",
            "error": "reason 必填 — 一句话说明为啥要截屏, 员工会看到",
        }
    if mode not in {"interactive", "fullscreen", "window"}:
        return {
            "type": "error",
            "error": (
                f"未知 mode: {mode}. 只接受 "
                "fullscreen / interactive / window. "
                "(active_window 模式已废弃, 用 fullscreen)"
            ),
        }

    # 临时文件 — /tmp/catfish-shot-<unix>.png. 不删, 给员工 / debug 用
    ts = int(time.time())
    out_path = Path(tempfile.gettempdir()) / f"catfish-shot-{ts}.png"

    sysname = platform.system()
    fallback_note: Optional[str] = None
    if sysname == "Darwin":
        ok, err = _screencapture_macos(mode, out_path)
    elif sysname == "Windows":
        # Win 没有原生交互式 / 窗口选择 / active window — 全退到 fullscreen
        if mode in {"interactive", "window"}:
            fallback_note = (
                f"Windows 没有原生 {mode} 截图, 自动退到 fullscreen. "
                "敏感窗口请提前关掉再让 catfish 拍."
            )
            mode = "fullscreen"
        ok, err = _screencapture_windows(mode, out_path)
    else:
        return {
            "type": "error",
            "error": f"不支持的平台: {sysname} (目前只支持 macOS / Windows)",
        }

    if not ok:
        return {"type": "error", "error": err or "截图失败"}

    size = out_path.stat().st_size
    if size > _MAX_SCREENSHOT_BYTES:
        # 文件还是留着让员工自己处理, 但不返回 base64 (会撑爆 socket)
        return {
            "type": "error",
            "error": (
                f"截图太大 ({size // (1024*1024)} MB > "
                f"{_MAX_SCREENSHOT_BYTES // (1024*1024)} MB 上限). "
                f"用 mode=interactive 框选小一点的区域. 文件: {out_path}"
            ),
        }

    # 5/8 BL-FIX3: 恢复返 base64 data_uri (跟 5/7 之前行为一致, LLM 训练时学的就是这样).
    # 上游 Qwen 不接受 role=tool 含 multimodal 的问题, 由 gateway 层 multimodal_tool_unwrap
    # 解决 — gateway 检测到 role=tool 的 content 含 data_uri, 自动**拆出 image** 重组成
    # 紧接着的 role=user multipart message (OpenAI 标准, 上游 Qwen 接受).
    # 这样 LLM 行为完全不变 (它仍然"调 screenshot → 下一轮看 image_url"), 上游也吃.
    size = out_path.stat().st_size
    raw = out_path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    result: Dict[str, Any] = {
        "type": "image",
        "format": "png",
        "encoding": "base64",
        "data": b64,
        "data_uri": f"data:image/png;base64,{b64}",
        "path": str(out_path),
        "size_bytes": size,
        "captured_at": _unix_to_iso(time.time()),
        "mode": mode,
        "reason": reason,
        "summary": f"截图完成 ({mode}, {size // 1024} KB), 路径: {out_path}",
    }
    if fallback_note:
        result["platform_note"] = fallback_note
    return result


# ============================================================
# 浏览器全栈 (catfish_browser_*) — 走 Playwright connect_over_cdp
# ============================================================
#
# 历史:
#   v1 (2026-04-27): hermes browser_navigate 直 CDP, 在 Companion 隔离 Chrome 上
#      ✓ 调用成功但页面没真换, 模型幻觉 "已打开". 自己写直 CDP 绕开 — catfish_browser_goto.
#   v2 (2026-04-28): 直 CDP 撞 Chrome 138+ --remote-allow-origins 限制, 没自动等待 / iframe
#      处理代码量大, 失败率仍高.
#   v3 (2026-04-28, 当前): 全栈换 Playwright connect_over_cdp(http://127.0.0.1:9222)
#      复用员工已登录 Chrome, 但 API 用 Playwright 的稳健版 (auto-waiting / retry / iframe).
#      失败率从 ~30% → ~5%.
#
# 设计要点:
#   - 不装 chromium binary (Playwright 默认会装 ~150MB), 用 connect_over_cdp 复用员工 Chrome
#   - 每次操作开新 Playwright instance + connect → 操作 → close. 性能够用 (人在等)
#   - 全 sync API (sync_playwright), 因为 tool_bridge 工具调用是 await asyncio.to_thread

import os as _os


def _chrome_base() -> str:
    """从 hermes config 或环境变量拿 chrome 调试端口 base url."""
    return _os.environ.get("CATFISH_CHROME_BASE", "http://127.0.0.1:9222")


# BL-FIX10 (5/8): Playwright sync API 卡死时 ignore 自带 timeout 参数, 拖死整个
# tool-bridge daemon (单线程). 用 concurrent.futures ThreadPoolExecutor + 硬
# timeout 兜底, 卡了直接返 error 给 LLM, daemon 继续服务别的请求.
#
# 副作用: 卡死的线程没法真杀 (Python 没有"杀线程"原语), 会泄漏直到下次 daemon 重启.
# 接受这个代价 — Companion watchdog 5s 检 tool-bridge 死活, 累计太多线程时
# Tauri restart_tool_bridge 命令一刀切. 以后真要根治得改 multiprocessing pool.
import concurrent.futures as _futures  # noqa: E402

_BROWSER_HARD_TIMEOUT_SEC = 30.0


def _run_with_hard_timeout(fn: Any, args: Dict[str, Any], hard_timeout_sec: float = _BROWSER_HARD_TIMEOUT_SEC) -> Dict[str, Any]:
    """在线程池里跑 fn(args), 硬超时直接返 error. 不真杀线程 (Python 限制).

    用法 — 在 browser_* 入口套一层:
        def browser_xxx(args):
            return _run_with_hard_timeout(_browser_xxx_impl, args)

    BL-FIX11 (5/8): 不用 ``with ThreadPoolExecutor()`` —— 那个的 ``__exit__``
    默认 ``shutdown(wait=True)`` 会**等卡死线程结束才返回**, timeout 等于没用.
    改成裸 executor + finally ``shutdown(wait=False)`` 让卡死线程后台跑去 (mac
    重启 GC), daemon 立刻继续服务别的请求.
    """
    pool = _futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="catfish_pw")
    try:
        future = pool.submit(fn, args)
        try:
            return future.result(timeout=hard_timeout_sec)
        except _futures.TimeoutError:
            # 硬超时 — 返 error, 线程仍在跑 (没法真杀), 让 daemon 继续服务别的请求
            tool_name = getattr(fn, "__name__", "browser_tool")
            return {
                "type": "error",
                "error": (
                    f"{tool_name} 硬超时 ({hard_timeout_sec}s) — Playwright 卡住没响应. "
                    f"page 状态可能不稳定 (navigation 中 / iframe 重载 / Chrome 没响应). "
                    f"建议: catfish_browser_snapshot 看页面当前结构, 或者改 selector "
                    f"用 'text=...' / 'role=...' 文字匹配, 或者 Companion 控制台重启 Chrome."
                ),
            }
    finally:
        # BL-FIX11: wait=False 关键, 不等卡死线程结束 — 否则 shutdown 自己卡, daemon 死
        pool.shutdown(wait=False)


def _connect_playwright_browser(playwright):
    """connect_over_cdp 复用 Companion 起的 Chrome.

    Returns:
        (browser, context, page) — context 是第一个 BrowserContext, page 是第一个 page.
        失败抛 RuntimeError, 上层 catch 转 friendly error.
    """
    chrome_base = _chrome_base()
    try:
        browser = playwright.chromium.connect_over_cdp(chrome_base)
    except Exception as e:
        raise RuntimeError(
            f"连不上 Chrome CDP {chrome_base}: {e}. "
            "Chrome 没起? Companion 控制台点'启动 Catfish Chrome'."
        ) from e

    contexts = browser.contexts
    if not contexts:
        # 极少见 — Chrome 没任何 context (新启动), 创建一个
        context = browser.new_context()
    else:
        context = contexts[0]

    pages = context.pages
    if not pages:
        page = context.new_page()
    else:
        page = pages[0]  # 第一个 page (about:blank 或员工正在用的 tab)

    return browser, context, page


def _import_playwright():
    """lazy import playwright, 失败友好提示装."""
    try:
        from playwright.sync_api import sync_playwright  # type: ignore  # noqa: PLC0415
        return sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "缺 playwright 包. 装一下 (在 hermes venv): "
            "HTTPS_PROXY= HTTP_PROXY= ~/.hermes/hermes-agent/venv/bin/pip install "
            "--proxy '' playwright"
        ) from e


# ── BL-HERMES013-2 (5/11): cloud-metadata SSRF deny ──────────────────
#
# 借鉴 Hermes 0.13 `Browser — enforce cloud-metadata SSRF floor in hybrid routing`.
# 防 LLM 被 prompt injection 引导去访问云厂商 metadata 服务 (AWS IMDS / GCP /
# Azure / 阿里云 / ECS) 泄露 IAM credentials.
#
# **不拦内网 IP**: catfish 主客户央企内网 (10.10.40.102 EIS / 192.168 / 172.16),
# 这些是日常正常 URL, 不该拦. 只拦真正的 metadata 端点 + link-local 段.
_SSRF_DENY_HOSTS = frozenset({
    # AWS IMDSv1/v2, GCP, Azure metadata (link-local 段)
    "169.254.169.254",
    # ECS task metadata
    "169.254.170.2",
    # GCP metadata (DNS 名)
    "metadata.google.internal",
    "metadata",
    # 阿里云 metadata
    "100.100.100.200",
    # AWS IPv6 IMDS
    "fd00:ec2::254",
})

# link-local 段整段拦 (IPv4 169.254.0.0/16, IPv6 fe80::/10).
# 内网常用 169.254 是 link-local autoconf, 不该有正经服务.
_SSRF_DENY_IPV4_PREFIXES = ("169.254.",)
_SSRF_DENY_IPV6_PREFIXES = ("fe80::", "fd00:ec2:")


def _check_ssrf_safe(url: str) -> str | None:
    """检测 url 是否撞 cloud-metadata SSRF deny list.

    返 None = 安全可访问, 返 str = 拒绝原因.

    Args:
      url: 完整 URL (http:// / https:// 前缀)
    """
    if not url:
        return None
    try:
        from urllib.parse import urlparse  # noqa: PLC0415
        host = urlparse(url).hostname
        if not host:
            return None
        h = host.lower()
        # 名字精确匹配
        if h in _SSRF_DENY_HOSTS:
            return (
                f"SSRF deny: '{h}' 是云厂商 metadata 端点 (IAM credentials 泄露风险). "
                "catfish 默认拦截. 如果是误判 (内网巧合同名), 跟 IT 报."
            )
        # IPv4 link-local
        for prefix in _SSRF_DENY_IPV4_PREFIXES:
            if h.startswith(prefix):
                return (
                    f"SSRF deny: '{h}' 在 169.254.0.0/16 link-local 段 "
                    "(AWS/GCP/Azure metadata 标准位置). catfish 默认拦截."
                )
        # IPv6 link-local
        for prefix in _SSRF_DENY_IPV6_PREFIXES:
            if h.startswith(prefix):
                return (
                    f"SSRF deny: '{h}' 在 IPv6 link-local 段. catfish 默认拦截."
                )
        return None
    except Exception:  # noqa: BLE001
        # 解析失败不阻塞业务, 兜底放过 (上层有 timeout / network err 各种兜底)
        return None


# Chrome 网络层 net_error 关键字 (区别于 404/500 这种 server 层 error).
# 撞这些 = 根本连不上服务器 → 适合走 https→http fallback.
_NET_LAYER_ERRORS = (
    "ERR_CONNECTION_REFUSED",
    "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_CLOSED",
    "ERR_SSL_PROTOCOL_ERROR",
    "ERR_CERT_",
    "ERR_TIMED_OUT",
)


def browser_goto(args: Dict[str, Any]) -> Dict[str, Any]:
    """硬 timeout 兜底 wrapper, 调 _impl. 防 Playwright 卡死锁住整个 daemon."""
    return _run_with_hard_timeout(_browser_goto_impl, args)


def _browser_goto_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    """走 Playwright `page.goto()`. connect_over_cdp 复用员工已登录 Chrome.

    https→http 自动 fallback (踩过坑 2026-04-28):
      模型默认补 https, 但中国电信内网很多老系统 (.ffcs.cn / .10086.cn) 只监听 80.
      https 过去直接 ERR_CONNECTION_REFUSED. 这里检测到网络层 error + url 是 https
      时, 自动用同一个 page 切 http 重试 1 次. 成功就加 fallback_hint 让模型记住.
      双保险: SOUL.md 也有"内网默认 http" 纪律, 这是工程层兜底.
    """
    url = (args.get("url") or "").strip()
    if not url:
        return {"type": "error", "error": "url 必填"}
    # BL-HERMES013-2 (5/11): SSRF deny — 拦云 metadata 端点防 IAM 泄露
    ssrf_err = _check_ssrf_safe(url)
    if ssrf_err:
        return {"type": "error", "error": ssrf_err}
    wait_until = (args.get("wait_until") or "load").lower()
    if wait_until not in {"load", "domcontentloaded", "networkidle"}:
        wait_until = "load"
    timeout_ms = int(float(args.get("timeout_seconds") or 30.0) * 1000)
    timeout_ms = max(1000, min(timeout_ms, 120_000))

    try:
        sync_playwright = _import_playwright()
    except RuntimeError as e:
        return {"type": "error", "error": str(e)}

    def _try_goto(page, target_url: str) -> Dict[str, Any]:
        """单次 goto 尝试, 包装成 result dict (不抛)."""
        try:
            response = page.goto(target_url, wait_until=wait_until, timeout=timeout_ms)
            actual_title = page.title()
            actual_url = page.url
            http_status = response.status if response else None
            matched = target_url in actual_url or actual_url.startswith(target_url[:20])
            return {
                "type": "ok",
                "navigated_to": target_url,
                "actual_title": actual_title,
                "actual_url": actual_url,
                "http_status": http_status,
                "summary": (
                    f"已 navigate 到 {target_url}. 真实 title='{actual_title}', "
                    f"url='{actual_url}', http={http_status}. "
                    f"({'✓ 加载成功' if matched else '⚠ url 跟请求不一致, 可能重定向'})"
                ),
            }
        except Exception as e:
            return {"type": "error", "error": f"playwright goto 异常: {type(e).__name__}: {e}"}

    try:
        with sync_playwright() as p:
            try:
                browser, context, page = _connect_playwright_browser(p)
            except RuntimeError as e:
                return {"type": "error", "error": str(e)}

            result = _try_goto(page, url)

            # https → http fallback (网络层撞墙 + url 是 https 才触发)
            if (
                result.get("type") == "error"
                and url.lower().startswith("https://")
                and any(err in result.get("error", "") for err in _NET_LAYER_ERRORS)
            ):
                fallback_url = "http://" + url[len("https://"):]
                fb_result = _try_goto(page, fallback_url)
                if fb_result.get("type") == "ok":
                    fb_result["fallback_hint"] = (
                        f"⚠ {url} (https) 不通 ({result['error'][:80]}…), 自动 fallback "
                        f"到 {fallback_url} (http) 成功. 内网老系统常见 "
                        f"(.ffcs.cn / .10086.cn / .chinatelecom.cn 等). "
                        f"以后**直接用 http://**, 不要补 https://."
                    )
                    fb_result["summary"] = "[https→http fallback] " + fb_result["summary"]
                    return fb_result
                # fallback 也失败 → 增强原始 error 给员工更多线索
                result["error"] = (
                    f"{result['error']}\n"
                    f"注: 已自动尝试 http fallback ({fallback_url}) 也失败 "
                    f"({fb_result.get('error', '')[:80]}). 可能员工不在公司内网, 或服务器临时挂了."
                )
            return result
            # 不关 browser (员工日常 Chrome) / 不关 page (后续 tool 复用)
    except Exception as e:
        return {"type": "error", "error": f"playwright goto 异常: {type(e).__name__}: {e}"}


def browser_click(args: Dict[str, Any]) -> Dict[str, Any]:
    """硬 timeout 兜底 wrapper, 调 _impl. 防 Playwright 卡死锁住整个 daemon."""
    return _run_with_hard_timeout(_browser_click_impl, args)


def _browser_click_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    """走 Playwright `page.click()`. auto-waiting 等元素出现 + visible + clickable.

    BL-FIX44 (5/11) 加 coordinates 路径: LLM 截图看到位置直接传 [x,y], 不依赖 selector.
    走 page.mouse.click(x, y), 完全绕开 selector 歧义.

    Args:
      selector: Playwright selector (优先, 老路径不变)
      coordinates: [x, y] 整数像素坐标. 跟 selector 二选一.
                   LLM 拿截图看到按钮位置时用这个 — 没有 selector 歧义.
      timeout_seconds: selector 等待超时, coordinates 模式不用 (鼠标点立即触发).
    """
    selector = (args.get("selector") or "").strip()
    coordinates = args.get("coordinates")
    timeout_ms = int(float(args.get("timeout_seconds") or 30.0) * 1000)
    timeout_ms = max(1000, min(timeout_ms, 120_000))

    # 校验: 二选一
    if not selector and not coordinates:
        return {
            "type": "error",
            "error": "selector 跟 coordinates 至少传一个. "
                     "看到截图直接传 coordinates=[x,y]; 有可靠 selector 传 selector.",
        }

    # coordinates 校验
    coord_xy = None
    if coordinates:
        try:
            if isinstance(coordinates, dict):
                cx, cy = int(coordinates.get("x")), int(coordinates.get("y"))
            elif isinstance(coordinates, (list, tuple)) and len(coordinates) == 2:
                cx, cy = int(coordinates[0]), int(coordinates[1])
            else:
                raise ValueError("不是 [x,y] 列表或 {x,y} 字典")
            if cx < 0 or cy < 0 or cx > 10000 or cy > 10000:
                return {
                    "type": "error",
                    "error": f"坐标超合理范围 ({cx},{cy}). 应该是页面 pixel 坐标, 0-3000 量级.",
                }
            coord_xy = (cx, cy)
        except Exception as e:  # noqa: BLE001
            return {
                "type": "error",
                "error": f"coordinates 格式错: {type(e).__name__}: {e}. "
                         "应该是 [x, y] 像素整数, 例 [450, 380].",
            }

    try:
        sync_playwright = _import_playwright()
    except RuntimeError as e:
        return {"type": "error", "error": str(e)}

    try:
        with sync_playwright() as p:
            try:
                browser, context, page = _connect_playwright_browser(p)
            except RuntimeError as e:
                return {"type": "error", "error": str(e)}

            # 优先级: selector 传了 → 走 selector (老路径); 没传 → 走 coordinates
            try:
                if selector:
                    page.click(selector, timeout=timeout_ms)
                    page.wait_for_load_state("domcontentloaded", timeout=5000)
                    return {
                        "type": "ok",
                        "mode": "selector",
                        "selector": selector,
                        "current_url": page.url,
                        "current_title": page.title(),
                        "summary": f"✓ 点击 '{selector}' 成功. 当前页面: {page.title()}",
                    }
                else:
                    # coordinates 模式: page.mouse.click(x, y)
                    cx, cy = coord_xy
                    page.mouse.click(cx, cy)
                    page.wait_for_load_state("domcontentloaded", timeout=5000)
                    return {
                        "type": "ok",
                        "mode": "coordinates",
                        "coordinates": [cx, cy],
                        "current_url": page.url,
                        "current_title": page.title(),
                        "summary": (
                            f"✓ 点击坐标 ({cx},{cy}) 成功. 当前页面: {page.title()}. "
                            "注: 坐标点击没 auto-waiting, 如果页面没反应可能是点空了 — "
                            "重新截图确认位置."
                        ),
                    }
            except Exception as e:
                err_str = str(e)
                if "Timeout" in err_str or "timeout" in err_str:
                    return {
                        "type": "error",
                        "error": (
                            f"等不到元素 '{selector}' 可点击 (超时 {timeout_ms}ms). "
                            "selector 写错? 元素被 modal 遮住? 先 catfish_browser_snapshot 看 DOM, "
                            "或者 screenshot 看视觉 + 用 coordinates 直点."
                        ),
                    }
                return {"type": "error", "error": f"click 失败: {type(e).__name__}: {e}"}
    except Exception as e:
        return {"type": "error", "error": f"playwright click 异常: {type(e).__name__}: {e}"}


def browser_fill(args: Dict[str, Any]) -> Dict[str, Any]:
    """硬 timeout 兜底 wrapper, 调 _impl. 防 Playwright 卡死锁住整个 daemon."""
    return _run_with_hard_timeout(_browser_fill_impl, args)


def _browser_fill_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    """走 Playwright `page.fill()`. 自动清空原值再填.

    历史:
      v1 (2026-04-28 早): 拒填 password 字段 → 实测员工需要登录场景, 拒了核心废.
      v2 (2026-04-28 中): 允许填 + 加 security_audit 标记 → 但密码仍在 LLM 上下文.
      v3 (2026-04-28 当前): 加 secret_ref 字段, 密码从 keychain / env 拉, **永不进 LLM 上下文**.
        text 字段保留 (用户名 / 邮箱 / 内容用), secret_ref 跟 text 二选一.
    """
    selector = (args.get("selector") or "").strip()
    text = args.get("text")
    secret_ref = (args.get("secret_ref") or "").strip()

    if not selector:
        return {"type": "error", "error": "selector 必填"}

    # secret_ref 跟 text 二选一. 都没给 → error. 都给 → 优先 secret_ref + warning.
    if not secret_ref and (text is None or text == ""):
        return {
            "type": "error",
            "error": "必须给 'text' 或 'secret_ref' 之一. 密码场景用 secret_ref",
        }

    timeout_ms = int(float(args.get("timeout_seconds") or 10.0) * 1000)
    timeout_ms = max(1000, min(timeout_ms, 60_000))

    # 检测密码 / 凭据字段
    selector_lower = selector.lower()
    is_credential_field = (
        "password" in selector_lower
        or "pwd" in selector_lower
        or "passwd" in selector_lower
    )

    # 解析 secret_ref (如果有), 拿到真实密码值
    actual_text: str
    used_secret_ref = False
    if secret_ref:
        try:
            from . import secret_resolver  # noqa: PLC0415
            actual_text = secret_resolver.resolve_secret(secret_ref)
            used_secret_ref = True
        except Exception as e:  # SecretResolveError 或其他
            return {
                "type": "error",
                "error": f"secret_ref 解析失败: {e}",
            }
    else:
        actual_text = str(text)
        # 检测员工是不是把 secret_ref 写错位置 (写到 text 字段了)
        try:
            from . import secret_resolver  # noqa: PLC0415
            if secret_resolver.is_secret_ref(actual_text):
                return {
                    "type": "error",
                    "error": (
                        f"text='{actual_text[:30]}...' 看起来是 secret_ref. "
                        "应该传到 secret_ref 字段, 不是 text 字段."
                    ),
                }
        except ImportError:
            pass

    try:
        sync_playwright = _import_playwright()
    except RuntimeError as e:
        return {"type": "error", "error": str(e)}

    try:
        with sync_playwright() as p:
            try:
                browser, context, page = _connect_playwright_browser(p)
            except RuntimeError as e:
                return {"type": "error", "error": str(e)}

            try:
                page.fill(selector, actual_text, timeout=timeout_ms)
                result: Dict[str, Any] = {
                    "type": "ok",
                    "selector": selector,
                    "filled_chars": len(actual_text),
                    "summary": f"✓ 在 '{selector}' 填了 {len(actual_text)} 个字符",
                }
                # 标 audit:
                #   - 用了 secret_ref → "credential_via_secret_ref" (好的实践)
                #   - 直接 text + 是密码字段 → "credential_field_filled" (不好的实践, 提醒)
                if used_secret_ref:
                    result["security_audit"] = "credential_via_secret_ref"
                    result["secret_ref_used"] = secret_ref  # 记 ref 不记值
                    result["summary"] += f" (从 {secret_ref} 拉值, 密码不进 LLM 上下文)"
                elif is_credential_field:
                    result["security_audit"] = "credential_field_filled"
                    result["security_note"] = (
                        "selector 看起来是密码 / 凭据字段, 但 text 是明文 (已经在 LLM 上下文了). "
                        "下次推荐用 secret_ref='keychain://<name>' 或 'env://<NAME>' "
                        "让密码从安全源拉, 不进 LLM."
                    )
                return result
            except Exception as e:
                err_str = str(e)
                if "Timeout" in err_str or "timeout" in err_str:
                    return {
                        "type": "error",
                        "error": (
                            f"等不到 '{selector}' 可写 (超时 {timeout_ms}ms). "
                            "selector 错? 输入框被 disabled? 用 catfish_browser_snapshot 看一下"
                        ),
                    }
                return {"type": "error", "error": f"fill 失败: {type(e).__name__}: {e}"}
    except Exception as e:
        return {"type": "error", "error": f"playwright fill 异常: {type(e).__name__}: {e}"}


def browser_snapshot(args: Dict[str, Any]) -> Dict[str, Any]:
    """硬 timeout 兜底 wrapper, 调 _impl. 防 Playwright 卡死锁住整个 daemon."""
    return _run_with_hard_timeout(_browser_snapshot_impl, args)


def _browser_snapshot_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    """拿当前页面结构化 DOM. 优先 Playwright accessibility, 失败 fallback 到 DOM evaluate.

    BL-FIX3 (5/8): EIS 登录 demo 撞 ``page.accessibility`` 在新版 Playwright 上 None,
    LLM 看到 ``AttributeError`` 直接放弃, 跟员工说"工具坏了". 修法 — 双路径:
      1. 先试 ``page.accessibility.snapshot()`` (老版 Playwright, 数据最干净)
      2. 失败 (None / AttributeError / 抛异常) → fallback ``page.evaluate()`` 走 JS
         扫 button/input/a/[role] 拿可见可交互元素列表, 跟 a11y 输出格式兼容

    返回里多个 ``snapshot_method`` 字段标明走哪条路径, 方便 audit / debug.

    BL-FIX9 (5/8): default 200 → 500, cap 500 → 1000. 鸿波 5/8 点的真因 — LLM 偷
    懒主动选 max_elements=50, CAS 登录页 nav / footer link 把登录 button 挤出 50,
    LLM 看不到只能截图找 → 撞 Playwright sync 卡死. 默认大点 + truncated 时返
    hint_for_llm 引导加大不是减小, 这条链上无解.
    """
    max_elements = int(args.get("max_elements") or 500)
    max_elements = max(10, min(max_elements, 1000))

    try:
        sync_playwright = _import_playwright()
    except RuntimeError as e:
        return {"type": "error", "error": str(e)}

    try:
        with sync_playwright() as p:
            try:
                browser, context, page = _connect_playwright_browser(p)
            except RuntimeError as e:
                return {"type": "error", "error": str(e)}

            # 先拿 title / url — 这俩失败说明 page 本身坏了, 直接退出
            try:
                title = page.title()
                url = page.url
            except Exception as e:
                return {
                    "type": "error",
                    "error": (
                        f"读 page.title/url 失败 (page 不可用): "
                        f"{type(e).__name__}: {e}. "
                        f"Chrome 标签页是不是被员工关了? Companion 重启 Chrome 再试."
                    ),
                }

            elements: List[Dict[str, Any]] = []
            snapshot_method = "unknown"
            a11y_error: Optional[str] = None

            # 路径 1: accessibility tree (老版 Playwright, 输出最干净)
            try:
                a11y_module = getattr(page, "accessibility", None)
                if a11y_module is not None:
                    a11y = a11y_module.snapshot()
                    if a11y:
                        _flatten_a11y(a11y, elements, max_count=max_elements)
                        if elements:
                            snapshot_method = "accessibility"
                else:
                    a11y_error = "page.accessibility 属性不存在 (Playwright >=1.50 已移除)"
            except Exception as e:
                a11y_error = f"{type(e).__name__}: {e}"
                import logging as _logging  # noqa: PLC0415
                _logging.getLogger("catfish.tool_bridge").warning(
                    "BL-FIX3 accessibility.snapshot 失败 → fallback DOM evaluate: %s",
                    a11y_error,
                )

            # 路径 2: DOM evaluate fallback (新版 Playwright 走这, 也是 a11y 拿不到时兜底)
            if not elements:
                try:
                    elements = _evaluate_dom_snapshot(page, max_count=max_elements)
                    snapshot_method = "dom_evaluate"
                except Exception as e:
                    return {
                        "type": "error",
                        "error": (
                            f"snapshot 失败 — accessibility ({a11y_error}) 和 "
                            f"DOM evaluate ({type(e).__name__}: {e}) 都不可用. "
                            f"页面 title={title!r} url={url!r}"
                        ),
                    }

            truncated = len(elements) >= max_elements
            summary = (
                f"页面 '{title}' ({url}) 有 {len(elements)} 个可见元素 "
                f"[via {snapshot_method}]"
            )
            if truncated:
                summary += f" — 截断到 {max_elements}, 加大 max_elements 看全部"

            result: Dict[str, Any] = {
                "type": "ok",
                "title": title,
                "url": url,
                "elements": elements[:max_elements],
                "element_count": len(elements),
                "truncated": truncated,
                "snapshot_method": snapshot_method,
                "summary": summary,
            }
            if truncated:
                # BL-FIX9: 显式给 LLM 下一步建议, 防它偷懒减小或者去截图找
                next_max = min(max_elements * 2, 1000)
                result["hint_for_llm"] = (
                    f"⚠️ elements 被截断了 (实际 >={max_elements}). 找不到要点"
                    f"的按钮 / link 时, **重调本工具加大 max_elements 到 {next_max}** "
                    f"(不是减小, 不是去截图). 如果 max_elements 已经到 1000, 改用 "
                    f"selector='text=登录' 这种文字匹配直接 click, 不用先看 element."
                )
            if a11y_error and snapshot_method == "dom_evaluate":
                result["accessibility_fallback_reason"] = a11y_error
            return result
    except Exception as e:
        return {"type": "error", "error": f"playwright snapshot 异常: {type(e).__name__}: {e}"}


# DOM-based fallback. ``page.accessibility.snapshot()`` 在新版 Playwright (>=1.50)
# 已废弃 / 返 None, 这条路走 ``page.evaluate(JS)`` 直接扫 DOM 拿可见可交互元素.
# 输出 schema 跟 _flatten_a11y 兼容: {role, name, depth} + 多个 selector_hint
# 给模型抓 selector 用.
_DOM_SNAPSHOT_JS = r"""
(maxCount) => {
    const out = [];
    function visible(el) {
        const rect = el.getBoundingClientRect();
        if (rect.width < 1 || rect.height < 1) return false;
        const cs = getComputedStyle(el);
        return cs.display !== 'none' && cs.visibility !== 'hidden' && cs.opacity !== '0';
    }
    function role(el) {
        const r = el.getAttribute('role');
        if (r) return r;
        const tag = el.tagName.toUpperCase();
        if (tag === 'A') return 'link';
        if (tag === 'BUTTON') return 'button';
        if (tag === 'INPUT') {
            const t = (el.getAttribute('type') || 'text').toLowerCase();
            if (t === 'checkbox') return 'checkbox';
            if (t === 'radio') return 'radio';
            if (t === 'submit' || t === 'button') return 'button';
            if (t === 'password') return 'textbox';
            return 'textbox';
        }
        if (tag === 'TEXTAREA') return 'textbox';
        if (tag === 'SELECT') return 'combobox';
        if (tag === 'IMG') return 'img';
        if (tag === 'FORM') return 'form';
        if (tag === 'LABEL') return 'label';
        if (/^H[1-6]$/.test(tag)) return 'heading';
        return tag.toLowerCase();
    }
    function name(el) {
        const candidates = [
            el.getAttribute('aria-label'),
            el.getAttribute('placeholder'),
            el.getAttribute('name'),
            el.getAttribute('title'),
            el.getAttribute('alt'),
            (el.innerText || '').trim(),
            el.getAttribute('value'),
        ];
        for (const c of candidates) {
            if (c) return String(c).trim().slice(0, 100);
        }
        return '';
    }
    function selectorHint(el) {
        if (el.id) return '#' + el.id;
        const nm = el.getAttribute('name');
        if (nm) return el.tagName.toLowerCase() + '[name="' + nm + '"]';
        const cls = (el.className || '').toString().split(/\s+/).filter(Boolean).slice(0, 2).join('.');
        if (cls) return el.tagName.toLowerCase() + '.' + cls;
        return el.tagName.toLowerCase();
    }
    function depthOf(el) {
        let d = 0;
        let cur = el;
        while (cur.parentElement) { d += 1; cur = cur.parentElement; }
        return d;
    }
    const all = document.querySelectorAll(
        'button, a, input, textarea, select, [role], h1, h2, h3, h4, h5, h6, label, form, img'
    );
    for (const el of all) {
        if (out.length >= maxCount) break;
        if (!visible(el)) continue;
        const r = role(el);
        const n = name(el);
        // 没 name 的 generic / div / span 跳过, 跟 a11y 行为一致
        const interesting = ['button', 'link', 'textbox', 'checkbox', 'radio',
                             'combobox', 'menuitem', 'tab', 'heading', 'img', 'form'];
        if (!n && interesting.indexOf(r) === -1) continue;
        out.push({
            role: r,
            name: n,
            depth: depthOf(el),
            selector_hint: selectorHint(el),
        });
    }
    return out;
}
"""


def _evaluate_dom_snapshot(page: Any, max_count: int = 200) -> List[Dict[str, Any]]:
    """跑 JS 拿可见可交互元素列表. 跟 _flatten_a11y 输出格式兼容 + 多 selector_hint."""
    raw = page.evaluate(_DOM_SNAPSHOT_JS, max_count) or []
    # 防 JS 端塞了脏 / 非 dict
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append({
            "role": str(item.get("role", "")),
            "name": str(item.get("name", ""))[:100],
            "depth": int(item.get("depth", 0)),
            "selector_hint": str(item.get("selector_hint", ""))[:200],
        })
    return out


def _flatten_a11y(
    node: Optional[Dict[str, Any]],
    out: List[Dict[str, Any]],
    max_count: int = 200,
    depth: int = 0,
) -> None:
    """把 accessibility tree 递归平铺成 element 列表. 超 max_count 立刻停."""
    if not node or len(out) >= max_count:
        return
    role = node.get("role", "")
    name = node.get("name", "")
    # 只收 "有意义" 的元素 (有 name 或可交互 role)
    interesting_roles = {
        "button", "link", "textbox", "checkbox", "radio", "combobox",
        "menuitem", "tab", "heading", "img", "img-text", "form",
    }
    if name or role in interesting_roles:
        out.append({
            "role": role,
            "name": name[:100] if name else "",
            "depth": depth,
        })

    for child in node.get("children", []) or []:
        if len(out) >= max_count:
            break
        _flatten_a11y(child, out, max_count=max_count, depth=depth + 1)


def browser_screenshot(args: Dict[str, Any]) -> Dict[str, Any]:
    """硬 timeout 兜底 wrapper, 调 _impl. 防 Playwright 卡死锁住整个 daemon."""
    return _run_with_hard_timeout(_browser_screenshot_impl, args)


# BL-FIX17 (5/8): 截图智能压缩参数
_SCREENSHOT_TARGET_KB = 800           # 硬 cap, 超过自动降 quality 重压
_SCREENSHOT_VIEWPORT_MAX_PX = 1280    # viewport max 边
_SCREENSHOT_FULLPAGE_MAX_PX = 1600    # full_page max 边
_SCREENSHOT_VIEWPORT_QUALITY = 80     # JPEG quality (viewport)
_SCREENSHOT_FULLPAGE_QUALITY = 75     # JPEG quality (full_page)
_SCREENSHOT_FALLBACK_QUALITY = 60     # 重压 quality


def _compress_screenshot(
    png_bytes: bytes,
    *,
    is_element: bool,
    full_page: bool,
) -> tuple[bytes, str, Dict[str, Any]]:
    """智能压缩截图. 返 (压完 bytes, format 'png'/'jpeg', meta).

    BL-FIX17 (5/8): 4MB 截图卡死 LLM (IPC + context + vision 推理三连卡).
    自动 downscale + JPEG, vision 一样能识别但省 90% size.

    策略:
      - is_element=True (有 selector): 不压 PNG (元素本来就小, 保真重要)
      - viewport: max 1280px + JPEG q=80
      - full_page: max 1600px + JPEG q=75
      - 压完仍 >800KB → 降 q=60 重压
      - 仍 >800KB → 抛 ValueError, 让 caller 报 error 给 LLM 改策略
    """
    size_before = len(png_bytes)

    # 元素截图: 不压, 直接返
    if is_element:
        return png_bytes, "png", {
            "size_kb_before": round(size_before / 1024, 1),
            "size_kb_after": round(size_before / 1024, 1),
            "compression_ratio": 1.0,
            "downscaled": False,
            "compress_strategy": "element_keep_png",
        }

    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        # PIL 没装 (极少见, hermes venv 一般有), fallback 不压返原图
        return png_bytes, "png", {
            "size_kb_before": round(size_before / 1024, 1),
            "size_kb_after": round(size_before / 1024, 1),
            "compression_ratio": 1.0,
            "downscaled": False,
            "compress_strategy": "no_pil_fallback",
            "warning": "PIL 没装, 没压. pip install Pillow.",
        }

    import io  # noqa: PLC0415
    img = Image.open(io.BytesIO(png_bytes))
    orig_w, orig_h = img.size

    max_px = _SCREENSHOT_FULLPAGE_MAX_PX if full_page else _SCREENSHOT_VIEWPORT_MAX_PX
    quality = _SCREENSHOT_FULLPAGE_QUALITY if full_page else _SCREENSHOT_VIEWPORT_QUALITY

    # downscale (保比例, 长边到 max_px)
    long_edge = max(orig_w, orig_h)
    downscaled = False
    if long_edge > max_px:
        scale = max_px / long_edge
        new_w = round(orig_w * scale)
        new_h = round(orig_h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        downscaled = True

    # 转 RGB (JPEG 不支持 alpha)
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1] if img.mode != "P" else None)
        img = bg

    # 编 JPEG
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    out_bytes = buf.getvalue()

    # 压完仍 >800KB → 降 quality 重压
    if len(out_bytes) > _SCREENSHOT_TARGET_KB * 1024:
        buf2 = io.BytesIO()
        img.save(buf2, format="JPEG", quality=_SCREENSHOT_FALLBACK_QUALITY, optimize=True)
        out_bytes = buf2.getvalue()
        quality = _SCREENSHOT_FALLBACK_QUALITY

    size_after = len(out_bytes)
    if size_after > _SCREENSHOT_TARGET_KB * 1024:
        # 第二次还不行 — 让 caller 报 error
        raise ValueError(
            f"截图压缩后仍 {size_after // 1024} KB > {_SCREENSHOT_TARGET_KB} KB. "
            f"原图 {orig_w}x{orig_h} {size_before // 1024} KB. "
            f"建议: 加 selector 截特定元素 (元素截图不压保真), "
            f"或 full_page=false 只截 viewport."
        )

    return out_bytes, "jpeg", {
        "size_kb_before": round(size_before / 1024, 1),
        "size_kb_after": round(size_after / 1024, 1),
        "compression_ratio": round(size_before / max(size_after, 1), 2),
        "downscaled": downscaled,
        "downscaled_to": f"{img.size[0]}x{img.size[1]}" if downscaled else None,
        "orig_size": f"{orig_w}x{orig_h}",
        "jpeg_quality": quality,
        "compress_strategy": "viewport_jpeg" if not full_page else "fullpage_jpeg",
    }


def _browser_screenshot_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    """截浏览器当前 tab 的图. 走 Playwright `page.screenshot()`, 返 data:image base64.

    BL-FIX7 (5/8): 之前 hermes builtin browser_screenshot 被 BL-FIX4 dedupe 一刀切
    丢了, LLM 想看浏览器内容只剩 catfish_screenshot (mac screencapture), 不对路.
    这条直接拿 Playwright 的 page.screenshot, 跟 browser_goto / fill / click 同
    一个 connect_over_cdp 链路, **不需要 mac 截屏权限**.

    BL-FIX17 (5/8): 自动智能压缩 — viewport / full_page 自动 downscale + JPEG,
    省 90% size + token + 推理时间. element 截图保 PNG 不压.

    LLM 拿到 data_uri 之后, 经 gateway BL-FIX2 multimodal_tool_unwrap 重组到 user
    multipart, 上游 Qwen 主力直接看图回答.
    """
    selector = (args.get("selector") or "").strip()
    full_page = bool(args.get("full_page", False))
    timeout_ms = int(float(args.get("timeout_seconds") or 10.0) * 1000)
    timeout_ms = max(1000, min(timeout_ms, 60_000))
    compress_mode = (args.get("compress") or "auto").strip().lower()

    try:
        sync_playwright = _import_playwright()
    except RuntimeError as e:
        return {"type": "error", "error": str(e)}

    try:
        with sync_playwright() as p:
            try:
                browser, context, page = _connect_playwright_browser(p)
            except RuntimeError as e:
                return {"type": "error", "error": str(e)}

            try:
                title = page.title()
                url = page.url
            except Exception as e:
                return {
                    "type": "error",
                    "error": (
                        f"读 page.title/url 失败 (page 不可用): "
                        f"{type(e).__name__}: {e}. "
                        f"Chrome 标签页是不是被员工关了?"
                    ),
                }

            try:
                if selector:
                    locator = page.locator(selector)
                    locator.wait_for(state="visible", timeout=timeout_ms)
                    png_bytes = locator.screenshot(timeout=timeout_ms)
                    capture_kind = f"element[{selector}]"
                else:
                    png_bytes = page.screenshot(
                        full_page=full_page, timeout=timeout_ms
                    )
                    capture_kind = "full_page" if full_page else "viewport"
            except Exception as e:
                return {
                    "type": "error",
                    "error": (
                        f"截图失败: {type(e).__name__}: {e}. "
                        f"selector={selector!r} full_page={full_page}"
                    ),
                }

            # BL-FIX17: 智能压缩
            if compress_mode == "none":
                # 员工显式要原图
                final_bytes = png_bytes
                final_format = "png"
                compress_meta = {
                    "size_kb_before": round(len(png_bytes) / 1024, 1),
                    "size_kb_after": round(len(png_bytes) / 1024, 1),
                    "compression_ratio": 1.0,
                    "compress_strategy": "none_explicit",
                }
            else:
                # auto (默认): 智能压缩
                try:
                    final_bytes, final_format, compress_meta = _compress_screenshot(
                        png_bytes,
                        is_element=bool(selector),
                        full_page=full_page,
                    )
                except ValueError as e:
                    return {
                        "type": "error",
                        "error": str(e),
                    }

            size = len(final_bytes)
            # 12MB hard cap (防 IPC 撑爆 — 跟 catfish_screenshot 一致)
            if size > _MAX_SCREENSHOT_BYTES:
                return {
                    "type": "error",
                    "error": (
                        f"截图太大 ({size // (1024*1024)} MB > "
                        f"{_MAX_SCREENSHOT_BYTES // (1024*1024)} MB 上限). "
                        f"加 selector 截单个元素, 或 compress='auto'"
                    ),
                }

            b64 = base64.b64encode(final_bytes).decode("ascii")
            mime = "image/jpeg" if final_format == "jpeg" else "image/png"
            return {
                "type": "image",
                "format": final_format,
                "encoding": "base64",
                "data": b64,
                "data_uri": f"data:{mime};base64,{b64}",
                "size_bytes": size,
                "captured_at": _unix_to_iso(time.time()),
                "capture": capture_kind,
                "title": title,
                "url": url,
                # BL-FIX17: 压缩 meta
                "size_kb_before": compress_meta.get("size_kb_before"),
                "size_kb_after": compress_meta.get("size_kb_after"),
                "compression_ratio": compress_meta.get("compression_ratio"),
                "downscaled": compress_meta.get("downscaled", False),
                "downscaled_to": compress_meta.get("downscaled_to"),
                "orig_size": compress_meta.get("orig_size"),
                "compress_strategy": compress_meta.get("compress_strategy"),
                "summary": (
                    f"浏览器截图完成 ({capture_kind}, "
                    f"{compress_meta.get('size_kb_after', size // 1024)} KB "
                    f"{final_format.upper()}, "
                    f"压缩 {compress_meta.get('compression_ratio', 1)}x), "
                    f"页面: {title} ({url})"
                ),
            }
    except Exception as e:
        return {"type": "error", "error": f"playwright browser_screenshot 异常: {type(e).__name__}: {e}"}


# ============================================================
# BL-FIX16 (5/8) — catfish_browser_find_by_text: 文字直接定位元素
# ============================================================
#
# CAS / 央企老页面常用非标准登录按钮 (`<a class="login-btn">登录</a>` / `<div onclick>` /
# `<input type="image">`), snapshot 里 DOM evaluate / accessibility tree 都找不到.
# 但**用户眼里就是个登录按钮**, 文字是 '登录'. 这条工具按文字找, 不挑 tag.
#
# 实现走 Playwright `page.get_by_text()` (内置部分匹配 + 优先 visible 元素), 失败
# fallback page.evaluate JS 全 DOM 扫. 返多个候选 (selector_hint + tag + bbox), LLM
# 拿第 1 个直接 click.

def browser_find_by_text(args: Dict[str, Any]) -> Dict[str, Any]:
    """硬 timeout 兜底 wrapper, 调 _impl. 防 Playwright 卡死锁住整个 daemon."""
    return _run_with_hard_timeout(_browser_find_by_text_impl, args)


_FIND_BY_TEXT_JS = r"""
(params) => {
    // BL-FIX44 (5/11) 重写: 返**全维度候选元数据 + 综合排序**, 让 LLM 自己判断挑哪个.
    // 之前版本只返 selector_hint, 撞 placeholder/label 同字符就翻车 (鸿波 EIS 登录场景).
    //
    // 新返字段:
    //   - selector: Playwright 最稳的 selector (优先 #id, 再 [name], 再 role/text 组合)
    //   - tag, role (ARIA), match_type (innerText/placeholder/aria-label/value/title/alt)
    //   - text (匹配到的文字), is_clickable (有 click handler 或 interactive role/tag)
    //   - bounds {x,y,w,h}, center {x,y} (供 catfish_browser_click coordinates 直点)
    //   - score (综合排序权重, 透明可解释)
    const wantedText = params.text;
    const exact = !!params.exact;
    const maxCount = params.maxCount || 10;
    const wantedRole = (params.role || '').toLowerCase();  // 'button' / 'link' / null

    function visible(el) {
        const rect = el.getBoundingClientRect();
        if (rect.width < 1 || rect.height < 1) return false;
        const cs = getComputedStyle(el);
        return cs.display !== 'none' && cs.visibility !== 'hidden' && cs.opacity !== '0';
    }

    // 返 [text, match_type] — 哪个属性匹配的, 优先级 innerText > value > aria-label > placeholder > title > alt
    //
    // BL-FIX44-fix (5/12): 鸿波 EIS 实测 — 登录按钮真实文字是 '登 录' (中间空格),
    // text='登录' 子串匹配失败. 改 normalize whitespace 比较 — 两侧 strip + 内部
    // \s+ 折成空 (中文场景两字之间空格通常无意义), 再 substring 比.
    function _norm(s) {
        return (s || '').replace(/\s+/g, '').toLowerCase();
    }
    function matchedText(el, wanted, exact) {
        const wantedNorm = _norm(wanted);
        const tries = [
            ['innerText', (el.innerText || '').trim()],
            ['value', (el.value || el.getAttribute('value') || '').trim()],
            ['aria-label', (el.getAttribute('aria-label') || '').trim()],
            ['placeholder', (el.getAttribute('placeholder') || '').trim()],
            ['title', (el.getAttribute('title') || '').trim()],
            ['alt', (el.getAttribute('alt') || '').trim()],
        ];
        for (const [mt, t] of tries) {
            if (!t) continue;
            // 先试原始匹配 (保兼容); 没中再 norm-whitespace 匹配 (修 '登 录' 类按钮)
            const m1 = exact ? (t === wanted) : t.includes(wanted);
            if (m1) return [t, mt];
            const tNorm = _norm(t);
            const m2 = exact ? (tNorm === wantedNorm) : tNorm.includes(wantedNorm);
            if (m2) return [t, mt];
        }
        return [null, null];
    }

    // 显式 ARIA role 或隐式 (button/a/input[submit]/...).
    function getRole(el) {
        const explicit = el.getAttribute('role');
        if (explicit) return explicit.toLowerCase();
        const tag = el.tagName.toUpperCase();
        if (tag === 'BUTTON') return 'button';
        if (tag === 'A' && el.hasAttribute('href')) return 'link';
        if (tag === 'INPUT') {
            const t = (el.type || 'text').toLowerCase();
            if (t === 'submit' || t === 'button' || t === 'reset' || t === 'image') return 'button';
            if (t === 'checkbox') return 'checkbox';
            if (t === 'radio') return 'radio';
            return 'textbox';
        }
        if (tag === 'TEXTAREA') return 'textbox';
        if (tag === 'SELECT') return 'combobox';
        return '';
    }

    // 是否真可点击 — 有原生 interactive 行为或显式 click handler.
    function isClickable(el) {
        const tag = el.tagName.toUpperCase();
        if (['BUTTON', 'A', 'INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) return true;
        if (el.hasAttribute('onclick')) return true;
        const r = (el.getAttribute('role') || '').toLowerCase();
        if (['button', 'link', 'menuitem', 'tab'].includes(r)) return true;
        // cursor:pointer 也是 click 信号
        try {
            if (getComputedStyle(el).cursor === 'pointer') return true;
        } catch (e) {}
        return false;
    }

    // 构造最稳的 Playwright selector. 注意: 不用 'text=' (歧义), 优先 #id / [name] / role-name.
    function bestSelector(el, matchType, matchedTextVal) {
        if (el.id) return '#' + CSS.escape(el.id);
        const nm = el.getAttribute('name');
        if (nm) return el.tagName.toLowerCase() + '[name=' + JSON.stringify(nm) + ']';
        // role + name (Playwright 1.27+ 支持 'role=button[name="登录"]')
        const role = getRole(el);
        if (role && matchType === 'innerText' && matchedTextVal && matchedTextVal.length <= 50) {
            return 'role=' + role + '[name=' + JSON.stringify(matchedTextVal) + ']';
        }
        // tag + class 兜底
        const cls = (el.className || '').toString().split(/\s+/).filter(Boolean).slice(0, 2).join('.');
        if (cls) return el.tagName.toLowerCase() + '.' + cls;
        return el.tagName.toLowerCase();
    }

    function inViewport(rect) {
        return rect.top < window.innerHeight && rect.bottom > 0 &&
               rect.left < window.innerWidth && rect.right > 0;
    }

    // 扫所有元素 (限制只看可见 + 文字匹配的)
    const all = document.querySelectorAll('*');
    const out = [];
    for (const el of all) {
        if (out.length >= 200) break;  // 硬上限, 防大页面爆
        if (!visible(el)) continue;
        const [matched, matchType] = matchedText(el, wantedText, exact);
        if (!matched) continue;

        const rect = el.getBoundingClientRect();
        const role = getRole(el);
        const clickable = isClickable(el);

        // 综合排序: 透明可解释
        let score = 0;
        // 1. 用户显式指定 role → 同 role 大加分, 不同 -10 排到末尾 (但仍返, 不丢)
        if (wantedRole) {
            if (role === wantedRole) score += 50;
            else score -= 20;
        }
        // 2. clickable > 不可点
        if (clickable) score += 30;
        // 3. match_type 优先级 (innerText 最强, alt 最弱)
        const mtBonus = {
            'innerText': 20, 'value': 15, 'aria-label': 12,
            'placeholder': 3, 'title': 2, 'alt': 1,
        };
        score += mtBonus[matchType] || 0;
        // 4. exact match + 10
        if (matched === wantedText) score += 10;
        // 5. 元素大小: 登录按钮通常 ≥ 100×40, log scale
        const area = Math.max(1, rect.width * rect.height);
        score += Math.min(15, Math.log2(area) | 0);
        // 6. 在 viewport 内 +5
        if (inViewport(rect)) score += 5;
        // 7. 文字越长越可能是误匹配 (placeholder 长描述 vs 按钮短文字)
        if (matched.length > 20) score -= 5;
        if (matched.length > 50) score -= 10;

        out.push({
            selector: bestSelector(el, matchType, matched),
            tag: el.tagName.toLowerCase(),
            role: role || null,
            text: matched.slice(0, 100),
            match_type: matchType,
            is_clickable: clickable,
            bounds: {
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                w: Math.round(rect.width),
                h: Math.round(rect.height),
            },
            center: {
                x: Math.round(rect.x + rect.width / 2),
                y: Math.round(rect.y + rect.height / 2),
            },
            in_viewport: inViewport(rect),
            score: score,
        });
    }

    // score 倒序
    out.sort((a, b) => b.score - a.score);
    return out.slice(0, maxCount);
}
"""


def _browser_find_by_text_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    """文字直接定位元素. 返**排序候选 + 元数据**, LLM 看 role/match_type/clickable 挑.

    BL-FIX44 (5/11) 重写: 单元素 → 候选列表 + 元数据. 修鸿波 EIS 实测 —
    find_by_text 抓到密码框 placeholder 含 '登录' 翻车. 老版 selector_hint='text=登录'
    天然歧义, 新版返 role/match_type/is_clickable, LLM 自己判断.

    Args:
      text: 要找的文字 (必填)
      exact: 完全匹配 (默认 False, 子串匹配)
      role: ARIA role 过滤 ('button' / 'link' / 'textbox' / ...). 显式指定时
            同 role 大加分, 不同 -20. 不传则不过滤.
      max_results: 返回数量 (默认 10)

    BL-FIX16 (5/8) 历史: CAS 登录非标准, find_by_text 不挑 tag 找文字. L44 保留.
    """
    text = (args.get("text") or "").strip()
    if not text:
        return {"type": "error", "error": "text 必填 (要找的元素文字)"}
    exact = bool(args.get("exact", False))
    max_results = int(args.get("max_results") or 10)
    max_results = max(1, min(max_results, 30))
    role_filter = (args.get("role") or "").strip().lower()  # BL-FIX44 新加

    try:
        sync_playwright = _import_playwright()
    except RuntimeError as e:
        return {"type": "error", "error": str(e)}

    try:
        with sync_playwright() as p:
            try:
                browser, context, page = _connect_playwright_browser(p)
            except RuntimeError as e:
                return {"type": "error", "error": str(e)}

            try:
                title = page.title()
                url = page.url
            except Exception as e:
                return {
                    "type": "error",
                    "error": (
                        f"读 page.title/url 失败 (page 不可用): "
                        f"{type(e).__name__}: {e}"
                    ),
                }

            # 走 page.evaluate JS 全扫 (比 page.get_by_text 兼容性更好, 回退一招)
            try:
                raw = page.evaluate(
                    _FIND_BY_TEXT_JS,
                    {
                        "text": text,
                        "exact": exact,
                        "maxCount": max_results,
                        "role": role_filter,
                    },
                )
            except Exception as e:
                return {
                    "type": "error",
                    "error": (
                        f"page.evaluate 失败: {type(e).__name__}: {e}. "
                        f"页面 title={title!r}"
                    ),
                }

            elements: List[Dict[str, Any]] = []
            if isinstance(raw, list):
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    elements.append({
                        "selector": str(item.get("selector", ""))[:200],
                        "tag": str(item.get("tag", "")),
                        "role": item.get("role"),
                        "text": str(item.get("text", ""))[:100],
                        "match_type": str(item.get("match_type", "")),
                        "is_clickable": bool(item.get("is_clickable", False)),
                        "bounds": item.get("bounds") or {},
                        "center": item.get("center") or {},  # 供 coordinates click 用
                        "in_viewport": bool(item.get("in_viewport", False)),
                        "score": int(item.get("score", 0)),
                    })

            # 顶部推荐: score 最高 + clickable (如果有 clickable 的话)
            top = None
            if elements:
                clickables = [e for e in elements if e["is_clickable"]]
                top = clickables[0] if clickables else elements[0]

            # 构造给 LLM 的 summary — 解释 top 是怎么挑出来的
            summary_parts: list[str] = []
            if not elements:
                summary_parts.append(
                    f"页面 {title!r} 上没找到含 '{text}' 的元素."
                )
                if role_filter:
                    summary_parts.append(
                        f"过滤 role='{role_filter}' 可能太严, 去掉再试一次, "
                        "或者直接 catfish_browser_screenshot 看一眼页面真实结构."
                    )
                else:
                    summary_parts.append(
                        "试 exact=false / 改文字, 或 catfish_browser_screenshot 让员工看一眼."
                    )
            else:
                summary_parts.append(
                    f"找到 {len(elements)} 个含 '{text}' 的元素."
                )
                if top:
                    mt_zh = {
                        "innerText": "正文",
                        "value": "value 属性",
                        "aria-label": "aria-label",
                        "placeholder": "placeholder",
                        "title": "title",
                        "alt": "alt",
                    }.get(top.get("match_type", ""), top.get("match_type", ""))
                    summary_parts.append(
                        f"推荐: tag={top['tag']} role={top['role']} "
                        f"match={mt_zh} clickable={top['is_clickable']} "
                        f"size={top['bounds'].get('w')}x{top['bounds'].get('h')}."
                    )
                    summary_parts.append(
                        f"如果这是要的, 直接 catfish_browser_click(selector={top['selector']!r}) "
                        f"或 catfish_browser_click(coordinates=[{top['center'].get('x')}, "
                        f"{top['center'].get('y')}])."
                    )
                    # 检查 top 是不是 placeholder 匹配, 提醒可能不是真按钮
                    if top.get("match_type") == "placeholder":
                        summary_parts.append(
                            "⚠ top 候选匹配的是 placeholder (输入框提示文字), 不是真按钮. "
                            "想找按钮请传 role='button' 重试, 或看下面候选挑 clickable+role=button 的."
                        )

            return {
                "type": "ok",
                "title": title,
                "url": url,
                "search_text": text,
                "exact": exact,
                "role_filter": role_filter or None,
                "elements": elements,
                "element_count": len(elements),
                "top_recommendation": top,
                "summary": "\n".join(summary_parts),
            }
    except Exception as e:
        return {"type": "error", "error": f"playwright find_by_text 异常: {type(e).__name__}: {e}"}


# ============================================================
# Skill backup (catfish_skill_backup)
# ============================================================
#
# 配套 catfish-policy R10 + SOUL "Skill 生成纪律"扩展 + docs/SKILL-LIFECYCLE.md.
# 防御 skill 退化: skill_manage(action=update/delete) 之前必须先调本 tool 备份,
# 老版会留在 ~/.hermes/skills/<ns>/<skill>/.versions/<unix-ts>.md, 员工说"回退"
# 时模型从这里拿最近一版替换.

import shutil as _shutil  # noqa: E402  (renamed alias to avoid shadowing)


def skill_backup(args: Dict[str, Any]) -> Dict[str, Any]:
    """把当前 skill 的 SKILL.md 复制到 .versions/<unix-ts>.md."""
    skill_name = (args.get("skill_name") or "").strip()
    reason = (args.get("reason") or "").strip()

    if not skill_name:
        return {
            "type": "error",
            "error": "skill_name 必填, 格式 'namespace/skill_name', 例如 'productivity/catfish-email'",
        }
    if not reason:
        return {
            "type": "error",
            "error": "reason 必填, 一句话说明为啥要改/删这个 skill",
        }
    if "/" not in skill_name:
        return {
            "type": "error",
            "error": (
                f"skill_name 格式错: '{skill_name}'. 必须是 'namespace/skill', "
                "例如 'productivity/expense-submit'"
            ),
        }

    skills_root = _hermes_dir() / "skills"
    skill_dir = skills_root / skill_name
    skill_md = skill_dir / "SKILL.md"

    # skill_dir 可能是软链 (catfish 自家 skill 走 install.sh 软链回源代码),
    # 这种 skill 不能让 LLM 改, R6 已防, 但这里也加一道
    if skill_dir.is_symlink():
        return {
            "type": "error",
            "error": (
                f"skill '{skill_name}' 是软链 (大概率是 catfish 自家 skill, "
                "由 install.sh 管理), LLM 不能改. 想改让员工跑 install.sh 重装"
            ),
        }

    if not skill_md.exists():
        return {
            "type": "error",
            "error": (
                f"找不到 {skill_md}. skill '{skill_name}' 可能不存在, "
                "或者 namespace/name 拼错了. 用 skill_view / skill_list 确认下"
            ),
        }

    # backup 到 .versions/<unix-ts>.md
    versions_dir = skill_dir / ".versions"
    versions_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    backup_path = versions_dir / f"{ts}.md"

    try:
        _shutil.copy2(skill_md, backup_path)
    except OSError as e:
        return {"type": "error", "error": f"backup 失败: {e}"}

    # 看下 .versions/ 现在有几版, 给个 UI hint
    try:
        version_count = sum(
            1 for p in versions_dir.iterdir() if p.is_file() and p.suffix == ".md"
        )
    except OSError:
        version_count = 1

    return {
        "type": "ok",
        "skill_name": skill_name,
        "backup_path": str(backup_path),
        "version_count": version_count,
        "reason": reason,
        "summary": (
            f"已 backup '{skill_name}' 到 {backup_path}. 现在 .versions/ 有 "
            f"{version_count} 个历史版本. 现在可以安全调 skill_manage update/delete."
        ),
    }


# ============================================================
# session_facts — 跟 LLM attention 失焦 hot-fix 配套, 工程级兜底
# ============================================================
#
# 设计 (2026-04-28 鸿波 demo 后加):
#   SOUL.md 复述模式靠模型自觉, 不一定每次都 quote 关键事实. session_facts
#   是工程级兜底: 模型听到员工硬事实时调 catfish_remember, 写到一个 JSON 文件;
#   gateway 每次 chat 请求, 自动把这个文件内容拼到 system prompt 末尾.
#   不依赖 attention, 永远在最近 token.
#
# 跟 memory_save 的区别:
#   memory_save → 跨 session 永久 (写 ~/.hermes/memories/*.md)
#   catfish_remember → 当前 session 内的硬事实 (写 ~/.catfish/session_facts.json)
#                      session 结束员工 rm 文件即可清空
#
# 边界:
#   - key 1-100 字符, value 1-1000 字符 (防滥用)
#   - 同 key **保留版本** (BL-MM2 五一 sprint 5/5 晚): 不再 silent overwrite,
#     新值 push 到 revision list, 保留 prev_value, 显示 "已更新 N 次".
#     SOUL BL-MM1 要求模型 quote 旧值, 这里给到工程支持: gateway inject 时
#     带上 "上次值: X", 模型就算自觉性差也能看到.
#   - 单文件全局 (Phase 1 单用户单进程; SSO 上来后加 user_id 区分)
#   - 文件不存在 = 没有 facts, gateway inject 跳过
#
# Schema (v2, 2026-05-05):
#   {
#     "key1": [
#       {"value": "v1", "ts": 1714867200.0, "prev_value": null},
#       {"value": "v2", "ts": 1714867260.0, "prev_value": "v1"},
#     ],
#     ...
#   }
#   list 顺序: [0] 最早, [-1] 最新 (current). value = revisions[-1]["value"].
#
# 向后兼容:
#   旧 schema {"key": "value"} (string) 会被 _read_session_facts 自动迁移到
#   单 revision list 形态, 写回时落新 schema. 员工不需要手动迁.

SESSION_FACTS_PATH = Path.home() / ".catfish" / "session_facts.json"
_FACTS_KEY_MAX_LEN = 100
_FACTS_VALUE_MAX_LEN = 1000
_FACTS_MAX_ENTRIES = 50  # 防内存爆: 超过 50 个 key 拒绝再加
_FACTS_MAX_REVISIONS_PER_KEY = 5  # BL-MM2: 同 key 最多保留 5 个历史版本, 老的截掉

# 类型 alias
Revision = Dict[str, Any]  # {"value": str, "ts": float, "prev_value": str | None}
FactsMap = Dict[str, List[Revision]]


def _normalize_revision(r: Any) -> Optional[Revision]:
    """把磁盘上一条 revision 规整成合法形态. 不合法返 None."""
    if not isinstance(r, dict):
        return None
    val = r.get("value")
    if not isinstance(val, str):
        return None
    val = val[:_FACTS_VALUE_MAX_LEN]
    ts = r.get("ts")
    if not isinstance(ts, (int, float)):
        ts = 0.0
    prev = r.get("prev_value")
    if prev is not None and not isinstance(prev, str):
        prev = None
    if isinstance(prev, str):
        prev = prev[:_FACTS_VALUE_MAX_LEN]
    return {"value": val, "ts": float(ts), "prev_value": prev}


def _read_session_facts() -> FactsMap:
    """读 session_facts.json, 规整成 v2 schema (revision list).

    兼容:
      - 文件不存在 / JSON 损坏 → 返空 dict
      - 旧 schema {"key": "value"} → 自动迁移到单 revision list
      - 新 schema {"key": [{...}, ...]} → 校验每条 revision

    不会写盘 (read-only). 真正落盘是下次 _write_session_facts 时.
    """
    if not SESSION_FACTS_PATH.exists():
        return {}
    try:
        with open(SESSION_FACTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}

    out: FactsMap = {}
    for k, v in data.items():
        if not isinstance(k, str):
            continue
        k = k[:_FACTS_KEY_MAX_LEN]
        if isinstance(v, str):
            # 旧 schema: 单 string. 包成单 revision (ts=0 表示未知).
            out[k] = [{"value": v[:_FACTS_VALUE_MAX_LEN], "ts": 0.0, "prev_value": None}]
        elif isinstance(v, list):
            revs: List[Revision] = []
            for r in v:
                norm = _normalize_revision(r)
                if norm is not None:
                    revs.append(norm)
            if revs:
                # 截到最近 N 个 (防文件被乱塞)
                if len(revs) > _FACTS_MAX_REVISIONS_PER_KEY:
                    revs = revs[-_FACTS_MAX_REVISIONS_PER_KEY:]
                out[k] = revs
        # 其他类型 (int/dict/None) 跳过
    return out


def _write_session_facts(facts: FactsMap) -> None:
    """写回 session_facts.json. 失败抛, 让 caller 处理 (返回 error)."""
    SESSION_FACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SESSION_FACTS_PATH, "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False, indent=2)


def _current_value(revisions: List[Revision]) -> Optional[str]:
    """从 revision list 取当前值. 空 list → None."""
    if not revisions:
        return None
    return revisions[-1].get("value")


def remember_fact(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: 记一个 session 内的硬事实 (BL-MM2: 版本化, 不 silent overwrite).

    - 新 key → push 第一条 revision (prev_value=None)
    - 旧 key + 同值 → no-op, 不算更新, 不 push 新 revision (防重复 tool call 灌脏数据)
    - 旧 key + 新值 → push 新 revision (prev_value=旧 current_value),
                      revision list 超 _FACTS_MAX_REVISIONS_PER_KEY 时截掉最早的
    """
    key = (args.get("key") or "").strip()
    value = (args.get("value") or "").strip()
    if not key:
        return {"type": "error", "error": "key 必填"}
    if not value:
        return {"type": "error", "error": "value 必填"}
    if len(key) > _FACTS_KEY_MAX_LEN:
        return {"type": "error", "error": f"key 太长 (>{_FACTS_KEY_MAX_LEN} 字符)"}
    if len(value) > _FACTS_VALUE_MAX_LEN:
        return {"type": "error", "error": f"value 太长 (>{_FACTS_VALUE_MAX_LEN} 字符)"}

    facts = _read_session_facts()
    existing = facts.get(key)
    is_existing = existing is not None and len(existing) > 0

    if not is_existing and len(facts) >= _FACTS_MAX_ENTRIES:
        return {
            "type": "error",
            "error": (
                f"session_facts 已满 ({_FACTS_MAX_ENTRIES} 条上限). "
                "员工 rm ~/.catfish/session_facts.json 清空, 或员工 explicit "
                "告诉你哪些可以删."
            ),
        }

    prev_value: Optional[str] = _current_value(existing) if is_existing else None

    # 同值再调一次 = no-op, 不污染 revision history
    if is_existing and prev_value == value:
        revs_existing = existing or []
        return {
            "type": "ok",
            "key": key,
            "value_preview": value[:100] + ("…" if len(value) > 100 else ""),
            "total_facts": len(facts),
            "overwrite": False,
            "no_change": True,
            "previous_value": None,  # 同值, 没有"上次值"概念
            "revision_count": len(revs_existing),
            "summary": (
                f"'{key}' 已是这个值, 不重复记. "
                f"当前 {len(facts)} 条 session_facts."
            ),
        }

    new_rev: Revision = {
        "value": value,
        "ts": time.time(),
        "prev_value": prev_value,
    }
    if is_existing:
        revs = list(existing or [])
        revs.append(new_rev)
        # 截到最近 N 个
        if len(revs) > _FACTS_MAX_REVISIONS_PER_KEY:
            revs = revs[-_FACTS_MAX_REVISIONS_PER_KEY:]
        facts[key] = revs
    else:
        facts[key] = [new_rev]

    try:
        _write_session_facts(facts)
    except Exception as e:
        return {"type": "error", "error": f"写 session_facts 失败: {e}"}

    revision_count = len(facts[key])
    if is_existing:
        verb = "更新"
        # 给模型显式 prev_value, 配合 SOUL BL-MM1 quote 旧值纪律
        prev_preview = (prev_value[:80] + "…") if prev_value and len(prev_value) > 80 else (prev_value or "")
        summary = (
            f"更新了 '{key}' (第 {revision_count} 版). 上次值: {prev_preview!r}. "
            f"按 BL-MM1 纪律, 你回员工时**必须**主动 quote 旧值 (\"我之前记的是 X, 现在改成 Y\"), "
            f"不要装作从来没记过."
        )
    else:
        verb = "记住"
        summary = (
            f"记住了 '{key}' (首次). "
            f"当前 {len(facts)} 条 session_facts. "
            f"gateway 会在每次 chat 自动 inject 到 system prompt 末尾."
        )

    return {
        "type": "ok",
        "key": key,
        "value_preview": value[:100] + ("…" if len(value) > 100 else ""),
        "total_facts": len(facts),
        "overwrite": is_existing,
        "previous_value": prev_value,
        "revision_count": revision_count,
        "summary": summary,
    }


# ============================================================
# BL-MM9 (5/8) — catfish_propose_skill: agent 自动抽 skill (员工 confirm 门槛)
# ============================================================
#
# 跟 hermes "creates skills from experience" 对标但加员工 confirm 门槛 — 跟
# BL-MM7 user_profile 三 evidence + lock 同哲学.
#
# 流程:
#   1. LLM chat 中观察到员工反复做某事 (≥3 次同 pattern)
#   2. LLM 调 catfish_propose_skill(name, reason, action_steps, evidence_count)
#   3. 工具写一行 JSON 到 ~/.catfish/skill_proposals.jsonl, 状态 status=proposed
#   4. LLM 跟员工说 '我注意到你 N 次 X, 要不存成 skill?'
#   5a. 员工 yes → LLM 调 catfish_skill_install (带上 reason / action_steps)
#       同时再调 catfish_propose_skill 把 status 改 accepted (传同 name)
#   5b. 员工 no → LLM 调 catfish_propose_skill 把 status 改 rejected
#       (员工 reject 过同 name 后, LLM 别再 propose, 写 SOUL 纪律)
#
# 限流防骚扰:
#   - 同 name 同 status 已 ≤24h 内 propose 过 → 拒绝再 propose
#   - 同 session 累计 propose ≥ 3 → 提醒 LLM 节制
#
# 红线:
#   - 健康 / 财务 / 感情 / 政治 / 宗教 namespace skill 严禁 propose (跟 BL-MM7 红线一致)
#   - SOUL § 红线字段 章节会用 prompt 明确告诉 LLM

SKILL_PROPOSALS_PATH = Path.home() / ".catfish" / "skill_proposals.jsonl"
_PROPOSAL_REDLINE_KEYWORDS = (
    "health", "medical", "diagnos", "drug",  # 健康
    "salary", "loan", "debt", "finance",     # 财务
    "love", "dating", "marriage", "divorce", # 感情
    "politic", "election", "govern",         # 政治
    "religion", "buddh", "christ", "muslim", # 宗教
    "健康", "病", "诊", "药",
    "工资", "贷款", "债", "理财",
    "恋爱", "结婚", "离婚",
    "政治", "选举",
    "宗教", "基督", "佛", "伊斯兰",
)
_PROPOSAL_RECENT_HOURS = 24       # 同 name 24h 内不重复 propose
_PROPOSAL_PER_SESSION_LIMIT = 5   # 单 session 最多 propose 5 个 skill (防骚扰)


def _read_proposals_history() -> list[Dict[str, Any]]:
    """读 ~/.catfish/skill_proposals.jsonl, 返 list of dict (jsonl, 一行一条)."""
    if not SKILL_PROPOSALS_PATH.exists():
        return []
    out: list[Dict[str, Any]] = []
    try:
        with open(SKILL_PROPOSALS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def _append_proposal_event(event: Dict[str, Any]) -> None:
    """append 一条 event 到 ~/.catfish/skill_proposals.jsonl (atomic 不重要, 多 LLM 不并发写)."""
    SKILL_PROPOSALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SKILL_PROPOSALS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _is_redline_skill_name(name: str, reason: str) -> bool:
    """检查 skill name + reason 是否触红线 (健康/财务/感情/政治/宗教)."""
    text = (name + " " + reason).lower()
    return any(kw in text for kw in _PROPOSAL_REDLINE_KEYWORDS)


def propose_skill(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: BL-MM9 (5/8) — LLM 提案一个 skill 给员工确认.

    校验:
      - name / reason / action_steps 都必填
      - triggered_by='auto' → evidence_count ≥3 (BL-MM9 防骚扰)
      - triggered_by='user_request' → evidence_count ≥1 (员工显式触发不卡)
      - name kebab-case (避免奇怪字符)
      - 红线字段拒绝
      - 同 name 24h 内已 propose 过 → 拒绝
      - 单 session 累计 ≥ 5 → 拒绝 (防骚扰)

    BL-MM9-fix (5/9): 鸿波 '下午我主动让鲶鱼生成 SKILL, 为什么不能生成, 很不合理' —
    加 triggered_by 区分两种场景, user_request 跳 3 次门槛.
    """
    name = (args.get("name") or "").strip()
    reason = (args.get("reason") or "").strip()
    action_steps = (args.get("action_steps") or "").strip()
    triggered_by = (args.get("triggered_by") or "auto").strip().lower()
    if triggered_by not in ("auto", "user_request"):
        triggered_by = "auto"  # 不识别的值兜底当 auto (严格模式)
    try:
        evidence_count = int(args.get("evidence_count") or 0)
    except (TypeError, ValueError):
        evidence_count = 0

    # validation
    if not name:
        return {"type": "error", "error": "name 必填"}
    if not re.match(r"^[a-z0-9][a-z0-9-]{1,49}$", name):
        return {
            "type": "error",
            "error": (
                "name 必须 kebab-case, 1-50 字符, 字母数字开头 (例: 'project-proposal'). "
                f"收到: {name!r}"
            ),
        }
    if not reason or len(reason) < 10:
        return {"type": "error", "error": "reason 必填且 ≥ 10 字 (含具体观察证据)"}
    if not action_steps or len(action_steps) < 20:
        return {"type": "error", "error": "action_steps 必填且 ≥ 20 字 (3-5 步说明 skill 干啥)"}
    # 阈值校验 — 两套, 看 triggered_by
    min_evidence = 3 if triggered_by == "auto" else 1
    if evidence_count < min_evidence:
        if triggered_by == "auto":
            err = (
                f"evidence_count={evidence_count} < 3 (auto 模式). "
                "BL-MM9 哲学: 你自己观察员工 ≥3 次同 pattern 才该 propose, 1-2 次静默观察. "
                "如果是员工**明确说**'存成 skill', triggered_by 改 'user_request' 即可放行."
            )
        else:
            err = f"evidence_count={evidence_count} < 1 — 至少 1 次实际操作"
        return {"type": "error", "error": err}

    # 红线检查
    if _is_redline_skill_name(name, reason):
        return {
            "type": "error",
            "error": (
                "skill 名 / 理由触红线 (健康/财务/感情/政治/宗教). "
                "鲶鱼不 propose 这类 skill — SOUL § 红线字段 已禁."
            ),
        }

    # 限流: 同 name 24h 内已 propose
    history = _read_proposals_history()
    now_ts = time.time()
    cutoff = now_ts - _PROPOSAL_RECENT_HOURS * 3600
    recent_same_name = [
        e for e in history
        if e.get("name") == name
        and e.get("ts", 0) >= cutoff
        and e.get("event_type") == "proposed"
        and e.get("status") == "proposed"  # 还没被员工 accept/reject
    ]
    if recent_same_name:
        return {
            "type": "error",
            "error": (
                f"已经在 24h 内 propose 过 '{name}' (proposal_id={recent_same_name[-1].get('proposal_id')}), "
                "等员工 accept/reject 后再 propose, 别骚扰."
            ),
        }

    # 限流: 单 session 累计 (用最近 1 小时近似 session)
    one_hour_ago = now_ts - 3600
    recent_in_session = [
        e for e in history
        if e.get("ts", 0) >= one_hour_ago
        and e.get("event_type") == "proposed"
    ]
    if len(recent_in_session) >= _PROPOSAL_PER_SESSION_LIMIT:
        return {
            "type": "error",
            "error": (
                f"最近 1 小时已 propose {len(recent_in_session)} 个 skill (上限 {_PROPOSAL_PER_SESSION_LIMIT}). "
                "员工还没 confirm 之前别再 propose, 让员工先选."
            ),
        }

    # 写 jsonl
    proposal_id = f"prop_{int(now_ts)}_{name}"
    # BL-MM9-fix (5/9): 字段对齐 learning.rs 期待的 schema (namespace/name 拆开)
    # + 加 description 字段给 Dashboard 渲染 + triggered_by 留 audit. 'name' 字段
    # 留旧值兼容老 propose_skill caller (但 learning.rs 现在认 skill_name).
    event = {
        "event_type": "proposed",  # learning.rs 看 'propose' 也兼容下
        "proposal_id": proposal_id,
        "skill_namespace": "personal",   # propose_skill 默认放 personal/, accept 后落 ~/.hermes/skills/personal/
        "skill_name": name,
        "name": name,                     # 旧字段兼容
        "description": reason,            # learning.rs Dashboard 显示用
        "reason": reason,
        "action_steps": action_steps,
        "evidence_count": evidence_count,
        "triggered_by": triggered_by,     # 'auto' | 'user_request' 留 audit
        "status": "proposed",
        "ts": now_ts,
        "ts_iso": _unix_to_iso(now_ts),
    }
    try:
        _append_proposal_event(event)
    except OSError as e:
        return {"type": "error", "error": f"写 skill_proposals.jsonl 失败: {e}"}

    total_proposed = sum(1 for e in history if e.get("event_type") == "proposed") + 1
    return {
        "type": "ok",
        "proposal_id": proposal_id,
        "name": name,
        "total_proposals": total_proposed,
        "summary": (
            f"已记下提案 '{name}' (基于 {evidence_count} 次员工行为). "
            f"现在跟员工说: '我注意到你最近 {evidence_count} 次 {reason[:50]}, "
            f"要不我把这个流程存成 skill, 下次你说一句就触发? 你说装我就装.' "
            f"等员工说 yes 再调 catfish_skill_install. 员工 reject 时再调本工具传 status='rejected' 关单."
        ),
    }


# ============================================================
# BL-MM13 (5/8) — propose_skill_revision: agent 自进化老 skill
# ============================================================
#
# 跟 BL-MM9 propose_skill 一脉相承, 但操作对象不同:
#   - MM9: 抽**新** skill (员工反复做 → 提议存)
#   - MM13: 改**老** skill 内容 (员工反馈 / audit 失败 → 提议改)
#
# 流程:
#   1. LLM 看 BL-MM11 feedback + audit + BL-MM12 quality_score 找问题 skill
#   2. LLM 调 catfish_propose_skill_revision(skill_path, current_v, proposed_v,
#      reason, diff_summary, evidence_summary)
#   3. 工具校验 + 写 ~/.catfish/skill_revisions.jsonl, status=proposed
#   4. Dashboard SkillRevisionCard (BL-MM14) 列出, 员工 click 采纳/拒绝
#   5. 采纳 → Tauri 调 BL-MM3 备份老版 + 写新版到 skill_path
#   6. 拒绝 → 标 dismissed, 24h 内不重 propose 同 skill
#
# 限流防骚扰 (跟 MM9 一致):
#   - 同 skill_path 24h 内 已 proposed 过 → 拒
#   - 单 session ≥ 3 个 revision propose → 拒 (一次别改太多)
#
# 红线 (跟 MM7/MM9 一致):
#   - 健康 / 财务 / 感情 / 政治 / 宗教 namespace skill 严禁 propose 改

SKILL_REVISIONS_PATH = Path.home() / ".catfish" / "skill_revisions.jsonl"
_REVISION_RECENT_HOURS = 24       # 同 skill_path 24h 内不重复 propose
_REVISION_PER_SESSION_LIMIT = 3   # 单 session 最多 propose 3 个 revision


def _read_revisions_history() -> list[Dict[str, Any]]:
    """读 ~/.catfish/skill_revisions.jsonl, 返 list of dict."""
    if not SKILL_REVISIONS_PATH.exists():
        return []
    out: list[Dict[str, Any]] = []
    try:
        with open(SKILL_REVISIONS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def _append_revision_event(event: Dict[str, Any]) -> None:
    """append 一条 event 到 ~/.catfish/skill_revisions.jsonl."""
    SKILL_REVISIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SKILL_REVISIONS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _semver_tuple(v: str) -> Optional[tuple[int, int, int]]:
    m = _SEMVER_RE.match(v.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def propose_skill_revision(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: BL-MM13 (5/8) — LLM 提议改进一个已存在的 skill.

    校验:
      - skill_path / current_version / proposed_version / reason / diff_summary /
        evidence_summary 都必填
      - skill_path kebab-case 含 '/' 命名空间 (`department/weekly-report` 形式)
      - SemVer 严格 (current 和 proposed 都 X.Y.Z)
      - proposed_version > current_version
      - reason ≥ 30 字, diff_summary ≥ 30 字, evidence_summary ≥ 20 字
      - 红线 namespace 拒
      - 同 skill_path 24h 内已 propose → 拒
      - 单 session ≥ 3 → 拒
    """
    skill_path = (args.get("skill_path") or "").strip()
    current_v = (args.get("current_version") or "").strip()
    proposed_v = (args.get("proposed_version") or "").strip()
    reason = (args.get("reason") or "").strip()
    diff_summary = (args.get("diff_summary") or "").strip()
    evidence_summary = (args.get("evidence_summary") or "").strip()

    # validation: 必填
    if not skill_path:
        return {"type": "error", "error": "skill_path 必填"}
    if not re.match(r"^[a-z0-9][a-z0-9-]*(/[a-z0-9][a-z0-9-]*)+$", skill_path):
        return {
            "type": "error",
            "error": (
                "skill_path 必须 kebab-case 含 '/' (例 'department/weekly-report'). "
                f"收到: {skill_path!r}"
            ),
        }

    # SemVer
    cur_t = _semver_tuple(current_v)
    prop_t = _semver_tuple(proposed_v)
    if cur_t is None:
        return {"type": "error", "error": f"current_version 不是 SemVer X.Y.Z: {current_v!r}"}
    if prop_t is None:
        return {"type": "error", "error": f"proposed_version 不是 SemVer X.Y.Z: {proposed_v!r}"}
    if prop_t <= cur_t:
        return {
            "type": "error",
            "error": (
                f"proposed_version {proposed_v} 必须 > current_version {current_v}. "
                "改动要 bump 版本号才能让员工区分新旧."
            ),
        }

    if len(reason) < 30:
        return {"type": "error", "error": "reason ≥ 30 字 (含具体观察 / feedback / audit 数据)"}
    if len(diff_summary) < 30:
        return {"type": "error", "error": "diff_summary ≥ 30 字 (3-8 句 markdown bullets)"}
    if len(evidence_summary) < 20:
        return {
            "type": "error",
            "error": "evidence_summary ≥ 20 字 (BL-MM11 / audit / BL-MM12 数据汇总)",
        }

    # 红线 — 复用 MM9 _is_redline_skill_name 检查 (路径 + reason)
    if _is_redline_skill_name(skill_path, reason):
        return {
            "type": "error",
            "error": (
                "skill_path / 理由触红线 (健康/财务/感情/政治/宗教). "
                "鲶鱼不 propose 改这类 skill — SOUL § 红线字段 已禁."
            ),
        }

    # 限流: 同 skill_path 24h 内已 propose
    history = _read_revisions_history()
    now_ts = time.time()
    cutoff = now_ts - _REVISION_RECENT_HOURS * 3600
    recent_same = [
        e for e in history
        if e.get("skill_path") == skill_path
        and e.get("ts", 0) >= cutoff
        and e.get("event_type") == "proposed"
        and e.get("status") == "proposed"
    ]
    if recent_same:
        return {
            "type": "error",
            "error": (
                f"已经在 24h 内 propose 过 '{skill_path}' 的改进 "
                f"(revision_id={recent_same[-1].get('revision_id')}), "
                "等员工 accept/reject 后再 propose 新一轮, 别骚扰."
            ),
        }

    # 限流: 单 session 累计 (近 1 小时)
    one_hour_ago = now_ts - 3600
    recent_in_session = [
        e for e in history
        if e.get("ts", 0) >= one_hour_ago
        and e.get("event_type") == "proposed"
    ]
    if len(recent_in_session) >= _REVISION_PER_SESSION_LIMIT:
        return {
            "type": "error",
            "error": (
                f"最近 1 小时已 propose {len(recent_in_session)} 个 revision "
                f"(上限 {_REVISION_PER_SESSION_LIMIT}). 一次别改太多, 让员工先消化."
            ),
        }

    # 写 jsonl
    revision_id = (
        f"rev_{int(now_ts)}_{skill_path.replace('/', '-')}_{proposed_v}"
    )
    event = {
        "event_type": "proposed",
        "revision_id": revision_id,
        "skill_path": skill_path,
        "current_version": current_v,
        "proposed_version": proposed_v,
        "reason": reason,
        "diff_summary": diff_summary,
        "evidence_summary": evidence_summary,
        "status": "proposed",
        "ts": now_ts,
        "ts_iso": _unix_to_iso(now_ts),
    }
    try:
        _append_revision_event(event)
    except OSError as e:
        return {"type": "error", "error": f"写 skill_revisions.jsonl 失败: {e}"}

    total = sum(1 for e in history if e.get("event_type") == "proposed") + 1
    return {
        "type": "ok",
        "revision_id": revision_id,
        "skill_path": skill_path,
        "current_version": current_v,
        "proposed_version": proposed_v,
        "total_revisions": total,
        "summary": (
            f"已记下改进提议: '{skill_path}' v{current_v} → v{proposed_v}. "
            f"现在跟员工说: '这个 skill 最近 {evidence_summary[:80]}. "
            f"我建议改 {diff_summary[:80]}. 你看 Dashboard 决定采不采纳.' "
            f"员工 accept 走 catfish_skill_install + BL-MM3 自动备份老版."
        ),
    }


# ============================================================
# catfish_run_skill —— 调用 catfish/skills/ 下工程审定 skill
# ============================================================
#
# 设计:
#   - skill_path 必须在白名单 (扫 catfish_skills_root 得来)
#   - 不允许任意路径, 防止越界 import
#   - 加载 script.py, 找 render_* 函数, 用反射调用
#   - 捕获返回值里的文件路径, 拼到 'files' 字段


def _catfish_skills_root() -> Optional[Path]:
    """复用 gateway 的 skills 发现逻辑, 但 tool-bridge 独立运行不能 import gateway.

    优先级:
      1. CATFISH_SKILLS_DIR env
      2. 从本文件向上找
    """
    env_dir = os.environ.get("CATFISH_SKILLS_DIR")
    if env_dir:
        p = Path(env_dir).expanduser()
        if p.is_dir():
            return p

    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "skills"
        if cand.is_dir() and (cand / "department").is_dir():
            return cand
    return None


def _read_skill_metadata(skill_md: Path) -> Dict[str, Any]:
    """读 SKILL.md frontmatter 拿 version/deprecated/deprecated_reason.

    五一 sprint Day 2: Skill 全生命周期 4 步基础.
    tool-bridge 独立 venv 不能 import gateway 的 skills_loader, 这里写 mini 版本.

    返回 {version, deprecated, deprecated_reason}, 没 frontmatter 走默认值.
    """
    default = {"version": "0.1.0", "deprecated": False, "deprecated_reason": ""}
    if not skill_md.exists():
        return default
    try:
        text = skill_md.read_text(encoding="utf-8")
    except Exception:
        return default

    # 找 --- ... --- frontmatter
    if not text.startswith("---"):
        return default
    end_idx = text.find("\n---", 3)
    if end_idx < 0:
        return default
    fm_text = text[3:end_idx].strip()

    # mini yaml 解析: 不引 yaml 依赖, 只支持 key: value 单行
    # SKILL.md 复杂字段 (description |- multiline) 这里跳过, 只关心 version/deprecated 单行
    result = dict(default)
    for line in fm_text.split("\n"):
        line = line.strip()
        if ":" not in line or line.startswith("#"):
            continue
        if line.startswith(" ") or line.startswith("\t"):
            continue  # 缩进行 (description 子内容) 跳过
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip("'\"")
        if key == "version" and val:
            result["version"] = val
        elif key == "deprecated":
            result["deprecated"] = val.lower() in ("true", "yes", "1")
        elif key == "deprecated_reason" and val:
            result["deprecated_reason"] = val
    return result


def _skill_audit_path() -> Path:
    """skill 调用审计 jsonl 路径. ~/.catfish/skill_audit.jsonl, 一行一个事件."""
    return Path.home() / ".catfish" / "skill_audit.jsonl"


def _write_skill_audit(event: Dict[str, Any]) -> None:
    """append 一行 JSON 到 ~/.catfish/skill_audit.jsonl. 失败静默, 不阻塞主流程."""
    try:
        path = _skill_audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # 不存原始 params (可能含密码 / PII), 只存关键 metadata
        line = json.dumps(event, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        # audit 写失败不阻塞用户操作
        try:
            import logging  # noqa: PLC0415
            logging.getLogger("catfish.tool_bridge").warning(
                "skill_audit 写失败: %s", e
            )
        except Exception:
            pass


def _load_skill_module(script_py: Path):
    """动态 import 一个 skill 的 script.py.

    技术坑:
      `from __future__ import annotations` + `@dataclass` 在 importlib 显式加载
      时, dataclass 装饰器会去 `sys.modules.get(cls.__module__)` 查模块的
      `__dict__`. 如果我们没把 module 提前 put 进 sys.modules, 这个 lookup 返回
      None, 抛 AttributeError("'NoneType' object has no attribute '__dict__'").

    解法: 先 sys.modules[name] = module, 再 exec_module. 失败时清理.

    用唯一 name 防 skill 之间冲突 (script.py 在不同 skill 都叫 script.py).
    """
    import importlib.util
    import sys

    mod_name = f"catfish_skill_{script_py.parent.name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(mod_name, script_py)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {script_py}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        # 失败清掉, 不留半截 module 污染 sys.modules
        sys.modules.pop(mod_name, None)
        raise
    return module


def _find_render_function(module) -> Optional[Tuple[str, Any]]:
    """在 skill module 里找 render_* 函数. 返回 (name, fn) 或 None."""
    for attr in dir(module):
        if attr.startswith("render_") and callable(getattr(module, attr)):
            return attr, getattr(module, attr)
    return None


def _function_signature_help(fn) -> Dict[str, Any]:
    """给 LLM 看的 function param schema —— 从 inspect.signature 推导."""
    import inspect

    sig = inspect.signature(fn)
    params: Dict[str, Any] = {}
    required: List[str] = []
    for name, p in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        info: Dict[str, Any] = {}
        if p.annotation is not inspect.Parameter.empty:
            info["type"] = str(p.annotation)
        if p.default is inspect.Parameter.empty:
            required.append(name)
        else:
            info["default"] = repr(p.default)
        params[name] = info
    return {
        "function": fn.__name__,
        "params": params,
        "required": required,
        "doc": (fn.__doc__ or "").strip()[:1000],
    }


def _extract_file_paths(result: Any) -> List[str]:
    """从 skill render 返回值里挖文件路径 (.docx / .xlsx / .pptx 等).

    支持几种返回形态:
      - {"docx": "/path/to/x.docx", ...}
      - {"files": [...]}
      - 字符串路径
    """
    paths: List[str] = []
    if isinstance(result, str):
        if "/" in result and "." in result:
            paths.append(result)
    elif isinstance(result, dict):
        for k, v in result.items():
            if isinstance(v, str) and "/" in v and "." in v:
                paths.append(v)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, str) and "/" in item:
                        paths.append(item)
    elif isinstance(result, list):
        for item in result:
            if isinstance(item, str) and "/" in item:
                paths.append(item)
    # 去重保持顺序
    seen = set()
    deduped = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    return deduped


def run_skill(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: 调 catfish 工程审定 skill.

    args:
      skill_path: 相对路径, 例 'department/leadership-briefing'
      params: render 函数入参 dict; {'_help': true} 返回 schema

    返回 dict.
    """
    skill_path = (args.get("skill_path") or "").strip().strip("/")
    params = args.get("params") or {}

    if not skill_path:
        return {
            "ok": False,
            "error": "skill_path 必填",
            "files": [],
            "summary": "",
        }

    # 路径安全检查
    if ".." in skill_path.split("/"):
        return {
            "ok": False,
            "error": "skill_path 不允许 '..' 越界",
            "files": [],
            "summary": "",
        }

    root = _catfish_skills_root()
    if root is None:
        return {
            "ok": False,
            "error": (
                "找不到 catfish skills 目录. 设 CATFISH_SKILLS_DIR env "
                "或检查部署路径."
            ),
            "files": [],
            "summary": "",
        }

    skill_dir = root / skill_path
    if not skill_dir.is_dir():
        return {
            "ok": False,
            "error": f"skill 不存在: {skill_path}",
            "files": [],
            "summary": "",
        }
    script_py = skill_dir / "script.py"
    if not script_py.exists():
        return {
            "ok": False,
            "error": (
                f"{skill_path}/script.py 不存在. catfish_run_skill 只调凝固"
                f"好的 skill (有 script.py 的). 没凝固的工作流, 先让员工"
                f"教学一遍, 再用 catfish_freeze_skill 自动生成."
            ),
            "files": [],
            "summary": "",
        }

    # 加载 + 找 render_*
    try:
        module = _load_skill_module(script_py)
    except Exception as e:
        return {
            "ok": False,
            "error": f"加载 {script_py} 失败: {e!r}",
            "files": [],
            "summary": "",
        }

    pair = _find_render_function(module)
    if pair is None:
        return {
            "ok": False,
            "error": (
                f"{skill_path}/script.py 没有 render_* 函数 — skill 没正确暴露入口"
            ),
            "files": [],
            "summary": "",
        }
    fn_name, fn = pair

    # _help 模式: 不真跑, 返回参数 schema
    if params.get("_help"):
        help_info = _function_signature_help(fn)
        return {
            "ok": True,
            "help": help_info,
            "files": [],
            "summary": (
                f"{skill_path} 入口: {fn_name}({', '.join(help_info['required'])}). "
                f"完整 schema 在 help 字段; 详细规范看 {skill_dir / 'SKILL.md'}."
            ),
        }

    # 五一 sprint Day 2: 读 skill metadata (version/deprecated)
    skill_md = skill_dir / "SKILL.md"
    metadata = _read_skill_metadata(skill_md)
    deprecated_warning = None
    if metadata["deprecated"]:
        reason = metadata["deprecated_reason"] or "(未填原因)"
        deprecated_warning = (
            f"⚠️ skill '{skill_path}' 已下线 (deprecated). 原因: {reason}. "
            "本次仍执行但建议换用其他 skill."
        )

    # 真调 — 计时 + audit
    started_at = time.time()
    audit_event: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "skill_path": skill_path,
        "skill_version": metadata["version"],
        "deprecated": metadata["deprecated"],
        "param_keys": sorted([k for k in params.keys() if not k.startswith("_")]),
        # 注意: 不存 params 原始值 (可能含 PII), 只记 key 列表
    }
    try:
        result = fn(**params)
    except TypeError as e:
        audit_event.update({
            "ok": False,
            "error_type": "TypeError",
            "error_msg": str(e)[:500],
            "duration_ms": int((time.time() - started_at) * 1000),
        })
        _write_skill_audit(audit_event)
        return {
            "ok": False,
            "error": (
                f"{fn_name} 参数不匹配: {e}. 用 params={{'_help': True}} 看 schema."
            ),
            "files": [],
            "summary": "",
        }
    except Exception as e:
        audit_event.update({
            "ok": False,
            "error_type": type(e).__name__,
            "error_msg": str(e)[:500],
            "duration_ms": int((time.time() - started_at) * 1000),
        })
        _write_skill_audit(audit_event)
        return {
            "ok": False,
            "error": f"{fn_name} 执行失败: {e!r}",
            "files": [],
            "summary": "",
        }

    duration_ms = int((time.time() - started_at) * 1000)
    files = _extract_file_paths(result)

    # 写 audit (成功)
    audit_event.update({
        "ok": True,
        "duration_ms": duration_ms,
        "file_count": len(files),
        "files": files[:10],  # 限制 10 个 path 防 audit 过大
    })
    _write_skill_audit(audit_event)

    response: Dict[str, Any] = {
        "ok": True,
        "result": result,
        "files": files,
        "summary": (
            f"已通过 {skill_path} (v{metadata['version']}) 生成 {len(files)} 个文件: " +
            (", ".join(files) if files else "(无文件输出, result 见 result 字段)")
        ),
    }
    if deprecated_warning:
        response["deprecated_warning"] = deprecated_warning
    return response


# ============================================================
# catfish_a2a_ask — Plan D Catfish Federation (五一 sprint Day 4-5)
# ============================================================


def a2a_ask(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: 问另一个员工的鲶鱼一个问题.

    HTTP POST 到 gateway 的 /a2a/internal/ask, gateway 内部做 lookup + sign + 调 B.
    """
    to_sub = (args.get("to_sub") or "").strip()
    question = (args.get("question") or "").strip()
    purpose = (args.get("purpose") or "").strip()
    context_hint = (args.get("context_hint") or "").strip()

    if not to_sub or not question:
        return {"ok": False, "error": "to_sub / question 必填"}

    # 当前员工 sub. 单机 mock 通过 env CATFISH_USER_SUB.
    from_sub = os.environ.get("CATFISH_USER_SUB", "").strip()
    if not from_sub:
        return {
            "ok": False,
            "error": "CATFISH_USER_SUB env 未设, 单机 mock 必须设 (生产从 SSO 拿)",
        }

    gateway_url = os.environ.get(
        "CATFISH_GATEWAY_URL",
        "http://127.0.0.1:8999",
    ).rstrip("/")

    body = {
        "from_sub": from_sub,
        "to_sub": to_sub,
        "question": question,
        "purpose": purpose,
        "context_hint": context_hint,
    }

    try:
        import urllib.request  # noqa: PLC0415

        req = urllib.request.Request(
            f"{gateway_url}/a2a/internal/ask",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {
            "ok": False,
            "error": f"调 gateway /a2a/internal/ask 失败: {e}",
        }

    if not data.get("ok"):
        err_type = data.get("error_type", "")
        err_msg = data.get("error", "")
        if err_type == "denied":
            return {
                "ok": False,
                "error": f"{to_sub} 的鲶鱼按 ALLOW.md 拒绝了这个问题: {err_msg}. "
                         "请换个角度问, 或问员工有没有授权这类信息.",
            }
        return {
            "ok": False,
            "error": f"A2A 调用 ({err_type}): {err_msg}",
        }

    return {
        "ok": True,
        "answer": data.get("answer", ""),
        "chunks_count": data.get("chunks_count", 0),
        "summary": (
            f"已通过 Plan D Federation 拿到 {to_sub} 的回答 "
            f"({data.get('chunks_count', 0)} 个 chunk). 详见 answer 字段."
        ),
    }


# ============================================================
# catfish_skill_install — Skills Hub MVP 本机版 (五一 sprint Day 3)
# ============================================================


def _list_existing_skills() -> List[Dict[str, Any]]:
    """枚举所有已装 skill, 返 [{path, name, description}].

    给 dedup 检查 (BL-C13) 用. path 形如 "department/leadership-briefing".
    """
    root = _catfish_skills_root()
    if root is None or not root.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        for ns_dir in root.iterdir():
            if not ns_dir.is_dir() or ns_dir.name.startswith("."):
                continue
            for skill_dir in ns_dir.iterdir():
                if not skill_dir.is_dir() or skill_dir.name.startswith("."):
                    continue
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    continue
                # mini parse: name + description 第一行
                name = skill_dir.name
                description_first_line = ""
                try:
                    text = skill_md.read_text(encoding="utf-8")
                    if text.startswith("---"):
                        end = text.find("\n---", 3)
                        if end > 0:
                            for line in text[3:end].strip().split("\n"):
                                ls = line.strip()
                                if ls.startswith("name:"):
                                    name = ls.partition(":")[2].strip().strip("'\"")
                                if ls.startswith("description:"):
                                    description_first_line = ls.partition(":")[2].strip().strip("|").strip()
                                    if not description_first_line:
                                        # description: |- 多行, 找下一非空行
                                        idx = text[3:end].split("\n").index(line)
                                        rest = text[3:end].split("\n")[idx + 1:]
                                        for rl in rest:
                                            if rl.strip() and not rl.strip().startswith("#"):
                                                description_first_line = rl.strip()
                                                break
                                    break
                except Exception:
                    pass
                out.append({
                    "path": f"{ns_dir.name}/{skill_dir.name}",
                    "name": name,
                    "description": description_first_line[:300],
                })
    except Exception as e:
        logger.warning("_list_existing_skills 失败: %s", e)
    return out


def _check_skill_dedup(new_name: str, new_description: str) -> List[Dict[str, Any]]:
    """BL-C13 重复检查 — 找跟新 skill 名/描述高度相似的已装 skill.

    简单 heuristic (够 demo 用):
    - name 完全相同 → 命中
    - description 前 50 字相同 → 命中
    - name 含彼此 (e.g. 'weekly-report' vs 'weekly-report-v2') → 命中

    返 [{path, name, similarity_reason}], 空 = 无重复.
    Phase 2 升级用 embedding 语义相似度.
    """
    if not new_name and not new_description:
        return []
    new_name_lower = (new_name or "").lower().strip()
    new_desc_short = (new_description or "")[:50].strip()

    hits: List[Dict[str, Any]] = []
    for existing in _list_existing_skills():
        ex_name = existing["name"].lower()
        ex_desc = existing["description"][:50]

        reason = ""
        if new_name_lower and ex_name and new_name_lower == ex_name:
            reason = f"name 完全相同 ({new_name})"
        elif (
            new_name_lower and ex_name
            and len(new_name_lower) >= 4 and len(ex_name) >= 4
            and (new_name_lower in ex_name or ex_name in new_name_lower)
        ):
            reason = f"name 互含 ({new_name} ↔ {existing['name']})"
        elif new_desc_short and ex_desc and new_desc_short == ex_desc:
            reason = "description 前 50 字相同"

        if reason:
            hits.append({
                "path": existing["path"],
                "name": existing["name"],
                "similarity_reason": reason,
            })
    return hits


def _dry_run_skill(skill_dir: Path) -> Dict[str, Any]:
    """BL-C12 dry-run 验证 — 试图 import skill 的 script.py 检查基础健康.

    检查:
    1. script.py 存在
    2. 能 import (语法 OK + 顶层依赖能 resolve)
    3. 至少有一个 render_xxx / run / main / 入口函数 (常见命名)

    返 {"ok": True} 或 {"ok": False, "error": "...", "stage": "..."}.
    不真跑 render — render 需 docx 等重依赖, 而且要参数, MVP 不验.
    """
    script_path = skill_dir / "script.py"
    if not script_path.exists():
        # 不是所有 skill 都有 script.py (有些 skill 可能纯 prompt 模板)
        # 没 script.py 视为无侵入 skill, dry-run 通过
        return {"ok": True, "note": "no script.py, skipping import check"}

    # 用 importlib.spec_from_file_location 加载, 模块名加 prefix 防撞
    import importlib.util
    spec_name = f"_dryrun_{skill_dir.parent.name}_{skill_dir.name}".replace("-", "_")
    try:
        spec = importlib.util.spec_from_file_location(spec_name, script_path)
        if spec is None or spec.loader is None:
            return {
                "ok": False,
                "error": f"无法加载 {script_path}",
                "stage": "spec_from_file_location",
            }
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as e:
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "stage": "import_module",
        }

    # 找入口函数: render_xxx / run / main / execute
    entry_names = [n for n in dir(module) if not n.startswith("_") and callable(getattr(module, n, None))]
    entry_funcs = [
        n for n in entry_names
        if n.startswith("render_") or n in ("run", "main", "execute", "render")
    ]
    if not entry_funcs:
        return {
            "ok": False,
            "error": f"没找到入口函数 (render_xxx / run / main / execute), 只有 {entry_names[:5]}",
            "stage": "entry_function",
        }

    return {
        "ok": True,
        "entry_functions": entry_funcs[:3],
        "note": f"导入 OK, 找到入口 {entry_funcs[0]}",
    }


def _install_from_hub(
    hub_skill: str,
    hub_url: str,
) -> Dict[str, Any]:
    """从 Skills Hub server 拉 skill 到 ~/.catfish/skill-staging/<uuid>/.

    成功返 {ok: True, staging_dir, hub_namespace, hub_name, hub_version}.
    失败返 {ok: False, error}.

    流程:
      1. parse 'ns/name@version' → ns / name / version (version='latest' 默认)
      2. GET {hub_url}/skills/{ns}/{name}/{version} 拿元信息 + 文件列表
      3. mkdir staging dir
      4. 对每个 file, GET {hub_url}/.../files/{path} 写到 staging
      5. 返 staging_dir, caller 走原 install 流程 (dedup + dry-run + 复制)
    """
    import json as _json  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415
    import urllib.error  # noqa: PLC0415
    import uuid as _uuid  # noqa: PLC0415

    # parse 'ns/name@version'
    if "/" not in hub_skill:
        return {"ok": False, "error": f"hub_skill 格式错: 期望 'ns/name@version', 拿到 {hub_skill!r}"}
    ns_part, _, after_slash = hub_skill.partition("/")
    if "@" in after_slash:
        name_part, _, version_part = after_slash.partition("@")
    else:
        name_part = after_slash
        version_part = "latest"
    ns_part = ns_part.strip()
    name_part = name_part.strip()
    version_part = version_part.strip() or "latest"
    if not ns_part or not name_part:
        return {"ok": False, "error": f"hub_skill 缺 namespace 或 name: {hub_skill!r}"}

    hub_url = hub_url.rstrip("/")

    # 1. 拿元信息
    meta_url = f"{hub_url}/skills/{ns_part}/{name_part}/{version_part}"
    try:
        with urllib.request.urlopen(meta_url, timeout=10) as resp:
            meta = _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {
                "ok": False,
                "error": f"hub 里找不到 {hub_skill}. URL: {meta_url}",
            }
        return {"ok": False, "error": f"hub GET 元信息失败 ({e.code}): {meta_url}"}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {
            "ok": False,
            "error": (
                f"hub 不可达 ({type(e).__name__}: {e}). 检查 {hub_url} 是否启动 "
                "(docker compose ps skills-hub) 或网络."
            ),
        }
    except Exception as e:
        return {"ok": False, "error": f"hub 元信息解析失败: {type(e).__name__}: {e}"}

    files_list = meta.get("files") or []
    if not files_list:
        return {"ok": False, "error": f"hub {hub_skill} 元信息里没 files 列表"}

    # 5/6 安全 P0 G2: 供应链防护. hub 元信息可附 files_sha256 字典:
    #   { "SKILL.md": "abc123...", "compute.py": "def456..." }
    # 客户端拉完每个文件 sha256, 跟 meta 对比, 不匹配 → rmtree + 拒装.
    # meta 没 files_sha256 → 当未签名处理: 严格模式 (CATFISH_HUB_REQUIRE_HASH=1) 拒装,
    # 默认模式只记 audit warning + 返回 unsigned=true.
    files_sha256: dict = meta.get("files_sha256") or {}
    require_hash_env = (os.environ.get("CATFISH_HUB_REQUIRE_HASH") or "").strip() == "1"
    if require_hash_env and not files_sha256:
        return {
            "ok": False,
            "error": (
                f"hub {hub_skill} 元信息没提供 files_sha256, "
                "CATFISH_HUB_REQUIRE_HASH=1 严格模式下拒装. "
                "联系 hub 维护者发布签名版本, 或临时取消 CATFISH_HUB_REQUIRE_HASH."
            ),
        }
    unsigned = not files_sha256

    import hashlib as _hashlib  # noqa: PLC0415

    # 2. 创 staging 目录
    staging_root = Path.home() / ".catfish" / "skill-staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = staging_root / _uuid.uuid4().hex
    staging.mkdir(parents=True, exist_ok=True)

    # 3. 逐个下载文件 + sha256 校验
    real_version = meta.get("version") or version_part
    for file_path in files_list:
        if not isinstance(file_path, str) or ".." in file_path or file_path.startswith("/"):
            # 防 zip-slip 类路径越界
            continue
        file_url = (
            f"{hub_url}/skills/{ns_part}/{name_part}/{real_version}/files/{file_path}"
        )
        try:
            with urllib.request.urlopen(file_url, timeout=20) as resp:
                content = resp.read()
        except Exception as e:
            # 清 staging 防部分文件残留
            import shutil as _sh  # noqa: PLC0415
            _sh.rmtree(staging, ignore_errors=True)
            return {
                "ok": False,
                "error": f"hub 下载 {file_path} 失败 ({type(e).__name__}: {e})",
            }
        # sha256 校验 (有 expected hash 才校, 没 expected 走 unsigned 流程)
        expected = files_sha256.get(file_path)
        if expected:
            actual = _hashlib.sha256(content).hexdigest()
            if actual.lower() != str(expected).lower():
                import shutil as _sh  # noqa: PLC0415
                _sh.rmtree(staging, ignore_errors=True)
                return {
                    "ok": False,
                    "error": (
                        f"hub {hub_skill} 文件 {file_path} sha256 不匹配. "
                        f"预期 {expected}, 实际 {actual}. "
                        f"中间人攻击或 hub 被篡改, 拒装."
                    ),
                }
        target = staging / file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    # 4. 验证关键文件
    if not (staging / "SKILL.md").exists():
        import shutil as _sh  # noqa: PLC0415
        _sh.rmtree(staging, ignore_errors=True)
        return {
            "ok": False,
            "error": f"hub 拿到的 {hub_skill} 缺 SKILL.md (元信息里 files={files_list})",
        }

    return {
        "ok": True,
        "staging_dir": str(staging),
        "hub_namespace": ns_part,
        "hub_name": name_part,
        "hub_version": real_version,
        "unsigned": unsigned,  # 5/6 G2: 没 sha256 校验时为 True
    }


def skill_install(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: 本机安装 skill — 两种来源:
      (A) source_dir 本机目录 (Day 3 MVP)
      (B) hub_skill 'ns/name@version' (5/5 ship, BL-D1 Skills Hub 第 1 件)

    args:
        source_dir: 模式 A 的本机源目录, 跟 hub_skill 互斥
        hub_skill: 模式 B 的 hub 路径
        hub_url: 模式 B 的 hub server base URL, 默认 env CATFISH_HUB_URL or http://127.0.0.1:9001
        namespace: 安装到本机的 namespace, 默认 'personal'
        overwrite: 默认 False
    """
    source = (args.get("source_dir") or "").strip()
    hub_skill = (args.get("hub_skill") or "").strip()
    namespace = (args.get("namespace") or "personal").strip()
    overwrite = bool(args.get("overwrite", False))

    # 模式互斥
    if source and hub_skill:
        return {
            "ok": False,
            "error": "source_dir 跟 hub_skill 互斥, 二选一",
        }
    if not source and not hub_skill:
        return {
            "ok": False,
            "error": "必须传 source_dir (本机目录) 或 hub_skill (hub 路径) 之一",
        }

    install_source: str  # 'local' | 'hub'
    hub_meta: Dict[str, Any] = {}

    # 模式 B: hub URL — 先拉到 staging 目录, 当 source_dir 用
    if hub_skill:
        hub_url_raw = (args.get("hub_url") or "").strip()
        if not hub_url_raw:
            hub_url_raw = os.environ.get("CATFISH_HUB_URL") or "http://127.0.0.1:9001"
        hub_result = _install_from_hub(hub_skill, hub_url_raw)
        if not hub_result.get("ok"):
            return hub_result  # 错误透传
        source = hub_result["staging_dir"]
        install_source = "hub"
        hub_meta = {
            "hub_skill": hub_skill,
            "hub_url": hub_url_raw,
            "hub_namespace": hub_result["hub_namespace"],
            "hub_name": hub_result["hub_name"],
            "hub_version": hub_result["hub_version"],
        }
        # hub 自带 namespace 时, 如果员工没 explicit 传 namespace, 用 hub 的
        if "namespace" not in args or not args.get("namespace"):
            namespace = hub_result["hub_namespace"]
    else:
        install_source = "local"

    # 展开 ~
    source_path = Path(source).expanduser().resolve()

    # 安全检查: 不允许从系统目录装.
    # 注意: macOS 上 /etc 是 /private/etc 的 symlink, resolve 后变 /private/etc,
    # 所以 blocklist 同时含 / 和 /private/ 两套.
    _system_prefixes = ["/etc", "/usr", "/bin", "/sbin", "/System", "/Library/System"]
    blocked_prefixes = _system_prefixes + [f"/private{p}" for p in _system_prefixes]
    str_source = str(source_path)
    if any(str_source.startswith(p) for p in blocked_prefixes):
        return {
            "ok": False,
            "error": f"安全考虑: 不允许从系统目录安装 ({source_path})",
        }

    if not source_path.is_dir():
        return {"ok": False, "error": f"source_dir 不存在或不是目录: {source_path}"}

    # 必须含 SKILL.md
    skill_md = source_path / "SKILL.md"
    if not skill_md.exists():
        return {
            "ok": False,
            "error": f"{source_path}/SKILL.md 不存在 — 不是合法 skill 目录",
        }

    # 解析 SKILL.md 拿 name (用于决定安装目标路径)
    metadata = _read_skill_metadata(skill_md)
    # _read_skill_metadata 不返 name, 这里 mini parse 一下
    skill_name = ""
    try:
        text = skill_md.read_text(encoding="utf-8")
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end > 0:
                for line in text[3:end].strip().split("\n"):
                    line = line.strip()
                    if line.startswith("name:") and not line.startswith(" "):
                        _, _, val = line.partition(":")
                        skill_name = val.strip().strip("'\"")
                        break
    except Exception as e:
        return {"ok": False, "error": f"读 SKILL.md 失败: {e}"}

    if not skill_name:
        return {
            "ok": False,
            "error": "SKILL.md frontmatter 缺 name 字段, 无法决定安装路径",
        }

    # ── BL-C13 dedup 检查 (五一 sprint 5/2 收尾) ──────────────
    # overwrite=True 跳过 dedup (员工显式说要覆盖). force_install=True 也跳过 (LLM 明确知重了还要装).
    if not overwrite and not args.get("force_install"):
        # 取 SKILL.md 第一行 description
        new_desc = ""
        try:
            text = skill_md.read_text(encoding="utf-8")
            if text.startswith("---"):
                end = text.find("\n---", 3)
                if end > 0:
                    for raw_line in text[3:end].strip().split("\n"):
                        ls = raw_line.strip()
                        if ls.startswith("description:"):
                            v = ls.partition(":")[2].strip().strip("|").strip()
                            if v:
                                new_desc = v
                            break
        except Exception:
            pass

        dups = _check_skill_dedup(skill_name, new_desc)
        if dups:
            audit_event_dedup = {
                "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "event_type": "install_dedup_blocked",
                "skill_path": f"{namespace}/{skill_name}",
                "duplicates": dups,
            }
            _write_skill_audit(audit_event_dedup)
            return {
                "ok": False,
                "error": (
                    f"检测到 {len(dups)} 个高度相似 skill: "
                    + ", ".join(f"{d['path']} ({d['similarity_reason']})" for d in dups)
                    + ". 想强制装传 force_install=true; 想覆盖具体某个传 overwrite=true."
                ),
                "duplicates": dups,
            }

    # namespace 安全 (不允许 .. / 跨目录)
    if ".." in namespace or "/" in namespace:
        return {"ok": False, "error": f"namespace 不允许 '..' 或 '/' ({namespace})"}

    # 目标路径: catfish/skills/<namespace>/<skill_name>/
    root = _catfish_skills_root()
    if root is None:
        return {"ok": False, "error": "找不到 catfish skills 目录"}

    target_dir = root / namespace / skill_name
    audit_event: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event_type": "install",
        "skill_path": f"{namespace}/{skill_name}",
        "skill_version": metadata["version"],
        "source_dir": str(source_path),
        "overwrite": overwrite,
    }

    # 同名已存在?
    if target_dir.exists():
        if not overwrite:
            audit_event.update({
                "ok": False,
                "error_msg": "skill 已存在, overwrite=false",
            })
            _write_skill_audit(audit_event)
            return {
                "ok": False,
                "error": (
                    f"skill {namespace}/{skill_name} 已存在. "
                    "想覆盖请传 overwrite=true (会先 backup 到 skill-trash)."
                ),
            }
        # overwrite: 先 backup
        ts = int(time.time())
        trash_root = Path.home() / ".catfish" / "skill-trash"
        trash_root.mkdir(parents=True, exist_ok=True)
        backup_dir = trash_root / f"{ts}-{skill_name}-replaced"
        try:
            shutil.move(str(target_dir), str(backup_dir))
            audit_event["backup_path"] = str(backup_dir)
        except Exception as e:
            audit_event.update({
                "ok": False,
                "error_msg": f"backup 失败: {e}",
            })
            _write_skill_audit(audit_event)
            return {"ok": False, "error": f"backup 旧 skill 失败: {e}"}

    # 复制
    try:
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(source_path), str(target_dir))
    except Exception as e:
        audit_event.update({
            "ok": False,
            "error_msg": f"复制失败: {e}",
        })
        _write_skill_audit(audit_event)
        return {"ok": False, "error": f"复制 skill 失败: {e}"}

    # ── BL-C12 dry-run 验证 (五一 sprint 5/2 收尾) ──────────────
    # 复制完立即试 import script.py + 找入口函数, 失败 rollback (删 target_dir).
    # 保护 LLM 装坏 skill 后整个 catfish 链路炸. skip_dry_run=true 跳过 (老 skill / 不带 script).
    if not args.get("skip_dry_run"):
        dry = _dry_run_skill(target_dir)
        if not dry.get("ok"):
            # rollback: 删 target_dir
            try:
                shutil.rmtree(target_dir)
            except Exception as rm_e:
                logger.warning("dry-run 失败后 rollback 删目录失败: %s", rm_e)
            audit_event.update({
                "ok": False,
                "error_msg": f"dry-run 失败 ({dry.get('stage')}): {dry.get('error')}",
                "rolled_back": True,
            })
            _write_skill_audit(audit_event)
            return {
                "ok": False,
                "error": (
                    f"skill 装上后 dry-run 验证失败 (stage={dry.get('stage')}): "
                    f"{dry.get('error')}. 已 rollback 删目录, 不影响其他 skill."
                ),
                "dry_run": dry,
            }
        audit_event["dry_run"] = dry

    audit_event.update({
        "ok": True,
        "installed_path": str(target_dir),
        "install_source": install_source,  # 'local' | 'hub'
        **({"hub_meta": hub_meta} if hub_meta else {}),
    })
    _write_skill_audit(audit_event)

    # 5/5 BL-D1 第 1 件: hub 模式下 staging 目录用完清掉, 防 ~/.catfish/skill-staging/ 堆积
    if install_source == "hub" and source_path.parent.name == "skill-staging":
        try:
            import shutil as _sh  # noqa: PLC0415
            _sh.rmtree(source_path, ignore_errors=True)
        except Exception:
            pass

    dry_note = audit_event.get("dry_run", {}).get("note", "")
    source_label = (
        f"hub {hub_meta.get('hub_skill')}" if install_source == "hub" else str(source_path)
    )
    return {
        "ok": True,
        "installed_path": f"{namespace}/{skill_name}",
        "source": install_source,
        **({"hub_meta": hub_meta} if hub_meta else {}),
        "summary": (
            f"已安装 skill {namespace}/{skill_name} (v{metadata['version']}) "
            f"从 {source_label}. dry-run 通过 ({dry_note}). "
            f"仪表盘下次刷新会出现, gateway 重新扫到后 LLM 也能调."
            + (f" 旧版备份: {audit_event.get('backup_path')}" if overwrite else "")
        ),
        "dry_run": audit_event.get("dry_run", {}),
    }


# ============================================================
# BL-MEMORY-DEDUPE-COMPRESS (5/17 凌晨, P2 #1+#2 lite 版)
# catfish_memory_dedupe / catfish_memory_compress — 给 LLM 主动调的整理工具
# 走 jieba (catfish 已装) + heuristic, 不调 hermes LCM / 不用 embedding.
# 真版本留白天清醒做.
# ============================================================


_HERMES_ENTRY_DELIM = "\n§\n"


def _read_hermes_memory_entries(target: str) -> List[str]:
    """读 hermes 0.13 ~/.hermes/memories/<USER|MEMORY>.md, 按 § 分隔解析."""
    if target not in ("user", "memory"):
        return []
    filename = "USER.md" if target == "user" else "MEMORY.md"
    path = Path.home() / ".hermes" / "memories" / filename
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return []
    if not content.strip():
        return []
    return [s.strip() for s in content.split(_HERMES_ENTRY_DELIM) if s.strip()]


def _jieba_tokens(text: str) -> set:
    """jieba 分词 → 去停用词 → set. 用于 Jaccard."""
    try:
        import jieba  # noqa: PLC0415
    except ImportError:
        # jieba 没装 → fallback: 字符级 unigram
        return set(text)
    # 简易停用词 (中文常见 + 标点)
    stopwords = {
        "的", "了", "是", "在", "我", "你", "他", "她", "我们", "你们",
        "和", "跟", "也", "都", "就", "这", "那", "有", "没", "不",
        ",", "。", "?", "!", "、", " ", "\n", "(", ")", "—",
    }
    tokens = set(jieba.lcut(text))
    return {t for t in tokens if t.strip() and t not in stopwords}


def _jaccard_similarity(a: set, b: set) -> float:
    """Jaccard 相似度 |a ∩ b| / |a ∪ b|."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union > 0 else 0.0


def memory_dedupe(args: Dict[str, Any]) -> Dict[str, Any]:
    """扫 hermes USER.md / MEMORY.md, 找 Jaccard 相似度 ≥ threshold 的 entry 对.

    返建议给 LLM, 不真改盘 (员工 explicit consent 才动, 通过 memory tool).
    """
    target = args.get("target", "both")
    threshold = float(args.get("threshold", 0.6))

    targets_to_scan = ["user", "memory"] if target == "both" else [target]
    all_suggestions: List[Dict[str, Any]] = []

    for t in targets_to_scan:
        entries = _read_hermes_memory_entries(t)
        if len(entries) < 2:
            continue
        # 每对 entry 比 Jaccard
        token_cache = {e: _jieba_tokens(e) for e in entries}
        seen_pairs = set()
        for i, e1 in enumerate(entries):
            for j, e2 in enumerate(entries):
                if i >= j:  # 不重复对
                    continue
                pair_key = (e1[:30], e2[:30])
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                sim = _jaccard_similarity(token_cache[e1], token_cache[e2])
                if sim >= threshold:
                    # 选长的 entry 作为合并基础 (信息更全)
                    longer, shorter = (e1, e2) if len(e1) >= len(e2) else (e2, e1)
                    all_suggestions.append({
                        "target": t,
                        "entries": [e1, e2],
                        "similarity": round(sim, 2),
                        "suggested_keep": longer,
                        "suggested_remove": shorter,
                        "suggested_merge": (
                            f"{longer} (合并: '{shorter}')" if longer != shorter else longer
                        ),
                    })

    return {
        "ok": True,
        "scanned_targets": targets_to_scan,
        "threshold": threshold,
        "suggestions_count": len(all_suggestions),
        "suggestions": all_suggestions,
        "next_step_for_llm": (
            "把 suggestions 列给员工 review, 员工 yes 才调 "
            "memory(action='remove', old_text=suggested_remove) + "
            "memory(action='replace', old_text=suggested_keep, content=suggested_merge). "
            "员工 no → 不动."
        ),
    }


def memory_compress(args: Dict[str, Any]) -> Dict[str, Any]:
    """看 USER.md / MEMORY.md 使用率, 提议把最老 N 条合并成摘要.

    不真改盘, 只返建议. 员工 yes 才动.
    """
    target = args.get("target")
    oldest_n = int(args.get("oldest_n", 5))
    if target not in ("user", "memory"):
        return {
            "ok": False,
            "error": f"target 必须是 'user' 或 'memory', 收到: {target!r}",
        }

    char_limit = 1375 if target == "user" else 2200  # hermes 默认
    entries = _read_hermes_memory_entries(target)
    total_chars = sum(len(e) for e in entries)
    usage_pct = (total_chars / char_limit * 100) if char_limit > 0 else 0

    if usage_pct < 80:
        return {
            "ok": True,
            "target": target,
            "usage_pct": round(usage_pct, 1),
            "char_limit": char_limit,
            "total_chars": total_chars,
            "entries_count": len(entries),
            "action_needed": False,
            "message": (
                f"{target} 当前用 {usage_pct:.1f}% ({total_chars}/{char_limit} chars), "
                f"< 80%, 不需要压缩. 等 entries 多了再叫我."
            ),
        }

    # > 80% — 选最老 N 条 (entries 头部是老的, hermes 按 add 顺序写)
    oldest = entries[: min(oldest_n, len(entries))]
    oldest_chars = sum(len(e) for e in oldest)

    return {
        "ok": True,
        "target": target,
        "usage_pct": round(usage_pct, 1),
        "char_limit": char_limit,
        "total_chars": total_chars,
        "entries_count": len(entries),
        "action_needed": True,
        "oldest_n": len(oldest),
        "oldest_entries": oldest,
        "oldest_chars": oldest_chars,
        "would_save_chars_if_summary_under": int(oldest_chars * 0.4),
        "next_step_for_llm": (
            f"建议把 {target} 这 {len(oldest)} 条最老 entry (共 {oldest_chars} chars) "
            f"合并成一条摘要 entry (目标 < {int(oldest_chars * 0.4)} chars). "
            "你写好摘要后给员工 review: '我想把这 N 条压成 1 条摘要 X, 老的就删了'. "
            "员工 yes → 调 memory(action='remove') 删 N 条, 再 "
            "memory(action='add', content=摘要) 写新. 员工 no → 不动."
        ),
    }


# ============================================================
# catfish_skill_delete — 安全删除 skill (五一 sprint Day 2)
# ============================================================


def skill_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: 删除 catfish 工程审定 skill 整个目录.

    设计:
    - 必须 confirm=True (防误删)
    - 删除前先复制到 ~/.catfish/skill-trash/<unix-ts>-<basename>/ (30 天内可恢复)
    - audit jsonl 记 event_type=delete

    args:
        skill_path: 'department/leadership-briefing'
        reason: 删除原因 (写 audit)
        confirm: True (硬要求, 防误删)
    """
    skill_path = (args.get("skill_path") or "").strip().strip("/")
    reason = (args.get("reason") or "").strip()
    confirm = bool(args.get("confirm", False))

    if not skill_path:
        return {"ok": False, "error": "skill_path 必填"}
    if ".." in skill_path.split("/"):
        return {"ok": False, "error": "skill_path 不允许 '..' 越界"}
    if not reason:
        return {"ok": False, "error": "reason 必填 (写 audit log)"}
    if not confirm:
        return {
            "ok": False,
            "error": (
                "confirm 必须 true. 这是不可逆操作 (虽然 30 天内可从 trash 恢复). "
                "员工没明确说删, 不要自己判断 confirm=true."
            ),
        }

    root = _catfish_skills_root()
    if root is None:
        return {"ok": False, "error": "找不到 catfish skills 目录"}

    skill_dir = root / skill_path
    if not skill_dir.is_dir():
        return {"ok": False, "error": f"skill 不存在: {skill_path}"}

    # 备份到 ~/.catfish/skill-trash/<unix-ts>-<basename>/
    ts = int(time.time())
    basename = skill_dir.name
    trash_root = Path.home() / ".catfish" / "skill-trash"
    trash_root.mkdir(parents=True, exist_ok=True)
    backup_dir = trash_root / f"{ts}-{basename}"

    audit_event: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event_type": "delete",
        "skill_path": skill_path,
        "reason": reason[:500],
    }

    try:
        shutil.move(str(skill_dir), str(backup_dir))
        # 读 metadata 记到 audit (虽然 skill 已经移走, 但 backup_dir 里 SKILL.md 还在)
        metadata = _read_skill_metadata(backup_dir / "SKILL.md")
        audit_event.update({
            "ok": True,
            "skill_version": metadata["version"],
            "deprecated": metadata["deprecated"],
            "backup_path": str(backup_dir),
        })
        _write_skill_audit(audit_event)

        return {
            "ok": True,
            "deleted_path": skill_path,
            "backup_path": str(backup_dir),
            "summary": (
                f"已删除 skill {skill_path} (备份在 {backup_dir}, 30 天内可恢复). "
                f"重启 Companion 后仪表盘也会移除. 原因: {reason}"
            ),
        }
    except Exception as e:
        audit_event.update({
            "ok": False,
            "error_type": type(e).__name__,
            "error_msg": str(e)[:500],
        })
        _write_skill_audit(audit_event)
        return {
            "ok": False,
            "error": f"删除 {skill_path} 失败: {e!r}",
        }


# ============================================================
# dispatch 入口
# ============================================================

NATIVE_TOOL_NAMES = {t["name"] for t in CATFISH_NATIVE_TOOLS}


def is_native(name: str) -> bool:
    return name in NATIVE_TOOL_NAMES


def dispatch_native(name: str, args: Dict[str, Any]) -> Any:
    # BL-MM9-FREEZE (5/12): 业务流程 tool 自动 trace.
    # 拦截白名单内 tool 调用前后记录到 ~/.catfish/traces/active.jsonl,
    # 供 catfish_freeze_skill 凝固为 script.py + SKILL.md.
    from . import trace_recorder  # noqa: PLC0415

    if trace_recorder.is_recorded(name):
        # BL-MM9-FREEZE-bugfix (5/12): 嵌套深度判定. 教学路径走最外层 dispatch
        # (员工教 LLM, LLM 调 catfish_browser_* → wrapper depth=1 → 录).
        # 复用路径走 catfish_run_skill → script.py 内部用 dispatch_native 调
        # catfish_browser_* → wrapper depth>=2 → **不录** (script 行为不该污染
        # 教学 trace, 否则下次 freeze 撞混).
        with trace_recorder.record_depth_guard():
            should_record = trace_recorder.is_outermost()
            _t0 = time.time()
            _err: Exception | None = None
            try:
                result = _dispatch_native_inner(name, args)
                _ok = bool(result.get("ok", True)) if isinstance(result, dict) else True
                return result
            except Exception as e:
                _err = e
                _ok = False
                raise
            finally:
                if should_record:
                    _dur = int((time.time() - _t0) * 1000)
                    trace_recorder.record(
                        tool_name=name,
                        args=args or {},
                        result=(
                            locals().get("result")
                            if _err is None
                            else {"ok": False, "error": repr(_err)}
                        ),
                        ok=_ok,
                        duration_ms=_dur,
                    )
    else:
        return _dispatch_native_inner(name, args)


def _dispatch_native_inner(name: str, args: Dict[str, Any]) -> Any:
    """原 dispatch_native body — 包了 trace wrapper 之后从这里调."""
    if name == "catfish_remember":
        return remember_fact(args)
    if name == "catfish_propose_skill":
        return propose_skill(args)
    if name == "catfish_propose_skill_revision":
        return propose_skill_revision(args)
    if name == "catfish_today_summary":
        return collect_today_summary()
    if name == "catfish_screenshot":
        return capture_screenshot(args)
    if name == "catfish_browser_goto":
        return browser_goto(args)
    if name == "catfish_browser_click":
        return browser_click(args)
    if name == "catfish_browser_fill":
        return browser_fill(args)
    if name == "catfish_browser_snapshot":
        return browser_snapshot(args)
    if name == "catfish_browser_screenshot":
        return browser_screenshot(args)
    if name == "catfish_browser_find_by_text":
        return browser_find_by_text(args)
    if name == "catfish_skill_backup":
        return skill_backup(args)
    if name == "catfish_run_skill":
        return run_skill(args)
    if name == "catfish_skill_install":
        return skill_install(args)
    if name == "catfish_skill_delete":
        return skill_delete(args)
    if name == "catfish_a2a_ask":
        return a2a_ask(args)
    # BL-MEMORY-DEDUPE-COMPRESS (5/17 凌晨)
    if name == "catfish_memory_dedupe":
        return memory_dedupe(args)
    if name == "catfish_memory_compress":
        return memory_compress(args)
    # 5/6 BL-MM7 user profile (长期画像, 跨 session)
    if name == "catfish_user_profile_get":
        from . import user_profile  # noqa: PLC0415
        return user_profile.user_profile_get(args)
    if name == "catfish_user_profile_propose":
        from . import user_profile  # noqa: PLC0415
        return user_profile.user_profile_propose(args)
    if name == "catfish_user_profile_confirm":
        from . import user_profile  # noqa: PLC0415
        return user_profile.user_profile_confirm(args)
    if name == "catfish_user_profile_clear":
        from . import user_profile  # noqa: PLC0415
        return user_profile.user_profile_clear(args)
    # 5/6 BL-MM8 文书风格 fingerprint
    if name == "catfish_style_fingerprint_get":
        from . import style_fingerprint  # noqa: PLC0415
        return style_fingerprint.style_fingerprint_get(args)
    if name == "catfish_style_fingerprint_refresh":
        from . import style_fingerprint  # noqa: PLC0415
        return style_fingerprint.style_fingerprint_refresh(args)
    if name == "catfish_style_fingerprint_clear":
        from . import style_fingerprint  # noqa: PLC0415
        return style_fingerprint.style_fingerprint_clear(args)
    # 5/8 BL-A2.1: 后台任务 (chat 不阻塞)
    if name == "catfish_run_task":
        from . import task_manager  # noqa: PLC0415
        return task_manager.submit_typed_task(
            kind=args.get("kind") or "execute_code",
            payload=args.get("payload") or {},
            label=args.get("label") or "",
        )
    if name == "catfish_task_status":
        from . import task_manager  # noqa: PLC0415
        return task_manager.manager().status_dict(args.get("task_id") or "")
    if name == "catfish_task_list":
        from . import task_manager  # noqa: PLC0415
        return {"tasks": task_manager.manager().list_active()}
    if name == "catfish_task_result":
        from . import task_manager  # noqa: PLC0415
        return task_manager.manager().result_dict(args.get("task_id") or "")
    # BL-D2 (5/10) Skills Hub publish
    if name == "catfish_skill_publish":
        from . import skill_publish  # noqa: PLC0415
        return skill_publish.skill_publish(args)
    # BL-Q3-ARCHIVE (5/11) tool message archive 读回
    if name == "catfish_read_tool_archive":
        from . import read_tool_archive  # noqa: PLC0415
        return read_tool_archive.read_tool_archive(args)
    # BL-Q3-WEBSKILL (5/11) 验证码 OCR
    if name == "catfish_recognize_captcha":
        from . import recognize_captcha  # noqa: PLC0415
        return recognize_captcha.recognize_captcha(args)
    # BL-Q3-WEBSKILL (5/11) 视觉定位元素
    if name == "catfish_browser_locate":
        from . import browser_locate  # noqa: PLC0415
        return browser_locate.locate(args)
    # BL-MM9-FREEZE-v2 (5/12 鸿波拍板) 教学→凝固→复用闭环 (显式 session 边界)
    if name == "catfish_teach_start":
        from . import skill_freeze  # noqa: PLC0415
        return skill_freeze.teach_start(args)
    if name == "catfish_teach_end":
        from . import skill_freeze  # noqa: PLC0415
        return skill_freeze.teach_end(args)
    if name == "catfish_freeze_inspect":
        from . import skill_freeze  # noqa: PLC0415
        return skill_freeze.freeze_inspect(args)
    if name == "catfish_freeze_skill":
        from . import skill_freeze  # noqa: PLC0415
        return skill_freeze.freeze_skill(args)
    if name == "catfish_freeze_rotate":
        from . import skill_freeze  # noqa: PLC0415
        return skill_freeze.freeze_rotate(args)
    # BL-FED2.1 (5/12 鸿波拍板) 专长从 employee_journal 自动抽
    if name == "catfish_extract_expertise":
        from . import expertise  # noqa: PLC0415
        return expertise.tool_extract_expertise(
            args,
            llm_call_fn=_expertise_llm_call,
            journal_text=_load_employee_journal(),
        )
    if name == "catfish_list_expertise":
        from . import expertise  # noqa: PLC0415
        return expertise.tool_list_expertise(args)
    if name == "catfish_confirm_expertise":
        from . import expertise  # noqa: PLC0415
        return expertise.tool_confirm_expertise(args)
    # BL-FED2.3 (5/12 鸿波拍板) 跨员工路由
    if name == "catfish_expert_consult":
        from . import expert_consult  # noqa: PLC0415
        return expert_consult.tool_expert_consult(args)
    # BL-FED2.6 (5/12) a2a 协助通知主动审计
    if name == "catfish_list_a2a_help":
        from . import a2a_notifications  # noqa: PLC0415
        return a2a_notifications.tool_list_a2a_help(args)
    # BL-FIX-SESSION-SEARCH (5/13 鸿波"历史会话搜不到")
    if name == "catfish_search_sessions":
        from . import sessions_search  # noqa: PLC0415
        return sessions_search.tool_search_sessions(args)
    # BL-EMAIL-SEARCH-TOOL (5/18 鸿波"对话里检索没搜到邮件")
    if name == "catfish_email_search":
        from . import email_search  # noqa: PLC0415
        return email_search.tool_email_search(args)
    # BL-FIX-TIMEOUT-OUTPUTS (5/13 鸿波"做不出文档")
    if name == "catfish_list_my_outputs":
        from . import recent_outputs  # noqa: PLC0415
        return recent_outputs.tool_list_my_outputs(args)
    # BL-REMINDER (5/13 鸿波"macOS 提醒联动")
    if name == "catfish_create_reminder":
        from . import reminders  # noqa: PLC0415
        return reminders.tool_create_reminder(args)
    if name == "catfish_list_reminder_lists":
        from . import reminders  # noqa: PLC0415
        return reminders.tool_list_reminder_lists(args)
    # BL-CALENDAR (5/14 0:30 鸿波"ISO 会议 LLM 写脚本踩坑")
    if name == "catfish_create_calendar_event":
        from . import calendar_events  # noqa: PLC0415
        return calendar_events.tool_create_calendar_event(args)
    if name == "catfish_list_calendars":
        from . import calendar_events  # noqa: PLC0415
        return calendar_events.tool_list_calendars(args)
    raise ValueError(f"unknown native tool: {name}")


# ─────────────────────────────────────────────────────────────
# BL-FED2.1 helpers — journal 加载 + LLM 调用
# ─────────────────────────────────────────────────────────────

def _load_employee_journal() -> str:
    """读 employee_journal.

    BL-FED2.4 (5/12) 修 path bug: 真路径 ~/.catfish/employee_journal.md (跟
    gateway employee_journal.py / session_summarizer / proactive.py 对齐).
    BL-FED2.1 第一版误写成 ~/.hermes/memories/employee_journal.md —
    用 hermes USER.md 的命名约定错搬过来.

    fallback: 老 path ~/.hermes/employee_journal.md (proactive.py 也有同款兼容).

    没有则返空串 (调用方会返 ok=False + '没东西可抽').
    """
    catfish_path = _home() / ".catfish" / "employee_journal.md"
    fallback_path = _home() / ".hermes" / "employee_journal.md"
    journal_path = catfish_path if catfish_path.exists() else fallback_path
    if not journal_path.exists():
        return ""
    try:
        return journal_path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""


def _expertise_llm_call(prompt: str) -> str:
    """走 gateway loopback POST /v1/chat/completions, model=catfish-private-main.

    复用 browser_locate 同一套 GATEWAY_URL + id_token 模式. 不抛异常 — 失败返
    空串, 让 expertise.extract_from_journal 走"返非 JSON"分支自然降级.
    """
    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        return ""
    try:
        from .browser_locate import GATEWAY_URL, _read_id_token  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return ""
    token = _read_id_token()
    if not token:
        return ""
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{GATEWAY_URL}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "catfish-private-main",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 2000,
                    "stream": False,
                },
            )
        if resp.status_code != 200:
            return ""
        data = resp.json()
        return (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            or ""
        )
    except Exception:  # noqa: BLE001
        return ""
