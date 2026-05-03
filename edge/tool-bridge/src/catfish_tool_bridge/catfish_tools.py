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
            "★ 记住员工告诉你的硬事实 — **本会话内**自动注入到上下文 (短期记忆). "
            "比依赖你自觉复述更稳, 鲶鱼内置存储, 不外泄.\n\n"
            "★ **跨 session 永久记忆也用这个工具** (优先级 P0): "
            "如果 memory_save 不可用, **务必**用 catfish_remember 替代, 不要跟员工说 "
            "'memory tool 不可用' / '存到 ~/.hermes/...' 等暴露内部的话. "
            "对员工说 '记下了, 鲶鱼本机存储' 即可, 不解释.\n\n"
            "✅ 调用场景:\n"
            "  - 员工说 'EIS 用 http 不是 https' → catfish_remember(key='eis_url', value='http://eis.ffcs.cn')\n"
            "  - 员工说 '我密码 ref 是 keychain://eis_password' → catfish_remember(key='eis_password_ref', value='keychain://eis_password')\n"
            "  - 员工纠正你 'tool-bridge 死了不是我的请求错' → catfish_remember(key='conn_refused_means', value='tool-bridge 死了, watchdog 5s 内会重启, 不要重做我的请求')\n"
            "  - 员工说 '我打招呼习惯用早' → catfish_remember(key='greeting_style', value='喜欢用早开场, 偏好简洁直接')\n\n"
            "❌ 不该调用:\n"
            "  - 员工的情绪/客套 ('好烦' / '辛苦') → 不是事实, 别记\n"
            "  - 你自己推测的 → 必须是员工**明确**说的硬事实\n\n"
            "key: 短 snake_case (例 'eis_url' / 'login_flow_step3'), 1-100 字符\n"
            "value: 事实内容, 1-1000 字符\n\n"
            "覆盖语义: 同 key 再调一次会覆盖. session 结束 session_facts.json 自动清."
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
            "拍员工屏幕一张图, 返回 base64 PNG. 给视觉模型 (Qwen3-VL / Gemini "
            "vision / Qwen-Flash 多模态) 看员工 GUI 上的内容. \n\n"
            "✅ 调用场景:\n"
            "  - 员工说「这个报错是什么意思」「我屏幕上 X 是什么」\n"
            "  - 员工说「截屏看下」「你看一下我这边」\n"
            "  - GUI 调试: 看一个软件按钮在哪 / 一个对话框在说什么\n"
            "  - 表格/图片识别: 员工用 Excel/Numbers 时不想复制粘贴\n\n"
            "❌ 不该调用:\n"
            "  - 看网页内容 → 用 catfish-browser-task 跟 Chrome 直接交互, "
            "不要绕去截图\n"
            "  - 看本地文件 → 用 read_file\n"
            "  - 员工没明确要求看屏幕但你「想看一下」——不要主动截\n\n"
            "🔒 默认 mode=fullscreen: 拍员工主屏当前内容. 0 权限 0 打扰. "
            "浏览器场景 Chrome 一般占主屏, 拍下来给视觉模型看就够; "
            "桌面应用类似 (员工正在用的窗口就是前台主屏内容). \n"
            "其他模式: interactive=员工框选区域 (员工要精确选一小块时), "
            "window=员工点选窗口 (交互式). \n\n"
            "调用前要在 reason 字段一句话说明为啥要截图, 这句话会写入日志, "
            "员工也会看到."
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
            "点击页面上一个元素. 走 Playwright `page.click()` 内置 auto-waiting: 等元素"
            "出现 + visible + enabled + 不被遮挡, 默认 30s 内自动 retry. 比 hermes "
            "browser_click 失败率低一个数量级. \n\n"
            "selector 用 CSS / text / role 三种语法之一: \n"
            "  - CSS: 'button#submit' / 'input[name=\"username\"]'\n"
            "  - text: 'text=登录' (匹配按钮文字)\n"
            "  - role: 'role=button[name=\"提交\"]' (无障碍语义, 最稳)\n\n"
            "**优先 role**, 其次 text, 最后 CSS. role 不依赖样式 / DOM 结构, 页面改版"
            "也不容易挂. 实在拿不到 role / text 才退到 CSS."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "Playwright selector. 优先 role= / text=, fallback CSS",
                },
                "timeout_seconds": {
                    "type": "number",
                    "default": 30.0,
                    "description": "等元素可点击的最长时间, 默认 30s",
                },
            },
            "required": ["selector"],
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
            "拿当前页面的结构化 DOM snapshot (含 visible text + role + ref). 给模型当"
            "「上下文」用 — 想点哪个按钮先 snapshot 看 ref. 走 Playwright `page.accessibility.snapshot()`"
            ", 是 accessibility tree 不是 raw HTML, 模型友好.\n\n"
            "返回字段:\n"
            "  - title: 页面 title\n"
            "  - url: 页面 url (真实 location.href)\n"
            "  - elements: 可见 / 可交互元素列表 (含 role / name / ref / text 摘要)\n"
            "  - 页面太大时 elements 会被截断到 200 个, 提示员工 scroll / 缩小范围"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "max_elements": {
                    "type": "integer",
                    "default": 200,
                    "description": "最多返回多少个元素, 防 IPC 撑爆. 默认 200",
                },
            },
            "required": [],
        },
        "emoji": "🔍",
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
            "调用 catfish 工程审定 skill 生成合规文档 (公文 / 汇报 / 请示 / 模板 等). "
            "**优先于自己写 Python 脚本** — gateway 在 system prompt 已经把可用 "
            "skill 列表注入给你, 看到列表里有匹配的就调这个工具, 不要绕道.\n\n"
            "✅ 调用场景:\n"
            "  - 员工说'写一份给领导的请示件' → catfish_run_skill(skill_path="
            "'department/leadership-briefing', params={...})\n"
            "  - 员工说'生成 X 月工作汇报材料' → 同上\n"
            "  - 任何 catfish skill 列表覆盖的合规场景\n\n"
            "❌ 不该调用:\n"
            "  - skill 列表里没有的能力 → 老老实实走 execute_code 临时写代码\n"
            "  - 不属于工程审定的 skill (~/.hermes/skills/ 那些自学 skill 不在这里调, "
            "走 hermes-skill 入口)\n\n"
            "**第一次不知道某 skill 的参数?** params={'_help': True} 调一次, "
            "返回该 skill 的输入 schema 给你看. 然后第二次正式传完整 params.\n\n"
            "**返回**: {ok: bool, files: [paths], summary: str, error: str | null}. "
            "files 字段里的路径会被 Companion 自动渲染成可点击 pill 给员工."
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
            "本机安装一个 skill — 从一个目录复制到 catfish/skills/<namespace>/<skill-name>/. "
            "Skills Hub MVP (五一 sprint Day 3, 跨实例 share 是 Phase 3 BL-D1).\n\n"
            "✅ 调用场景:\n"
            "  - 员工说 '把这个 skill 装上' / '安装 X skill' (员工提供目录路径)\n"
            "  - 员工拿了同事的 skill 目录, 想装到自己 catfish\n\n"
            "❌ 不该调用:\n"
            "  - 员工没明确要求安装\n"
            "  - source_dir 在系统目录 (/etc, /usr 等) — 安全考虑拒绝\n\n"
            "**安装规则**:\n"
            "  1. source_dir 必须含 SKILL.md (必须), script.py (可选, 没 script 也行就只 LLM 看 spec)\n"
            "  2. SKILL.md frontmatter 的 name 字段 → 决定安装到 <namespace>/<name>/\n"
            "  3. 同名 skill 已存在 → 必须 overwrite=true 才覆盖, 否则拒绝\n"
            "  4. 安装后自动 audit log + 仪表盘自动出现 (skills_loader 下次扫描就看到)\n\n"
            "**返回**: {ok, installed_path, error}.\n"
            "**audit**: ~/.catfish/skill_audit.jsonl event_type=install."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source_dir": {
                    "type": "string",
                    "description": (
                        "源目录绝对路径或 ~ 开头. 必须含 SKILL.md. "
                        "例: '~/Downloads/my-new-skill/' 或 '/tmp/shared-skill/'"
                    ),
                },
                "namespace": {
                    "type": "string",
                    "description": (
                        "安装到的 namespace, 例 'department' / 'personal' / 'shared'. "
                        "默认 'personal' (员工本人安装的). "
                        "工程审定 skill 装 'department' (鸿波 / 工程团队)."
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
            "required": ["source_dir"],
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
    """走 Playwright `page.click()`. auto-waiting 等元素出现 + visible + clickable."""
    selector = (args.get("selector") or "").strip()
    if not selector:
        return {"type": "error", "error": "selector 必填"}
    timeout_ms = int(float(args.get("timeout_seconds") or 30.0) * 1000)
    timeout_ms = max(1000, min(timeout_ms, 120_000))

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
                page.click(selector, timeout=timeout_ms)
                # 点击后页面可能跳, 等一下 + 拿新 url + title
                page.wait_for_load_state("domcontentloaded", timeout=5000)
                return {
                    "type": "ok",
                    "selector": selector,
                    "current_url": page.url,
                    "current_title": page.title(),
                    "summary": f"✓ 点击 '{selector}' 成功. 当前页面: {page.title()}",
                }
            except Exception as e:
                # Playwright 的 timeout / element not found 都是常见错, friendly 化
                err_str = str(e)
                if "Timeout" in err_str or "timeout" in err_str:
                    return {
                        "type": "error",
                        "error": (
                            f"等不到元素 '{selector}' 可点击 (超时 {timeout_ms}ms). "
                            "selector 写错? 元素被 modal 遮住? 先 catfish_browser_snapshot 看 DOM"
                        ),
                    }
                return {"type": "error", "error": f"click 失败: {type(e).__name__}: {e}"}
    except Exception as e:
        return {"type": "error", "error": f"playwright click 异常: {type(e).__name__}: {e}"}


def browser_fill(args: Dict[str, Any]) -> Dict[str, Any]:
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
    """走 Playwright `page.accessibility.snapshot()` 拿结构化 DOM."""
    max_elements = int(args.get("max_elements") or 200)
    max_elements = max(10, min(max_elements, 500))

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
                # accessibility snapshot 给模型用比 raw HTML 友好得多
                a11y = page.accessibility.snapshot()

                # 把 a11y tree 平铺成 element 列表 (限制深度防爆)
                elements: List[Dict[str, Any]] = []
                _flatten_a11y(a11y, elements, max_count=max_elements)

                truncated = len(elements) >= max_elements
                return {
                    "type": "ok",
                    "title": title,
                    "url": url,
                    "elements": elements[:max_elements],
                    "element_count": len(elements),
                    "truncated": truncated,
                    "summary": (
                        f"页面 '{title}' ({url}) 有 {len(elements)} 个可见元素"
                        + (" — 截断到 200, 想看更多 scroll 后再 snapshot" if truncated else "")
                    ),
                }
            except Exception as e:
                return {"type": "error", "error": f"snapshot 失败: {type(e).__name__}: {e}"}
    except Exception as e:
        return {"type": "error", "error": f"playwright snapshot 异常: {type(e).__name__}: {e}"}


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
#   - 同 key 覆盖 (员工说 "EIS 改 https 了" 重存即可)
#   - 单文件全局 (Phase 1 单用户单进程; SSO 上来后加 user_id 区分)
#   - 文件不存在 = 没有 facts, gateway inject 跳过

SESSION_FACTS_PATH = Path.home() / ".catfish" / "session_facts.json"
_FACTS_KEY_MAX_LEN = 100
_FACTS_VALUE_MAX_LEN = 1000
_FACTS_MAX_ENTRIES = 50  # 防内存爆: 超过 50 个 key 拒绝再加


def _read_session_facts() -> Dict[str, str]:
    """读 session_facts.json. 文件不存在 / 损坏 → 空 dict."""
    if not SESSION_FACTS_PATH.exists():
        return {}
    try:
        import json
        with open(SESSION_FACTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        # 只保留 str -> str (防文件被乱写)
        return {
            str(k)[:_FACTS_KEY_MAX_LEN]: str(v)[:_FACTS_VALUE_MAX_LEN]
            for k, v in data.items()
            if isinstance(k, str) and isinstance(v, str)
        }
    except Exception:
        return {}


def _write_session_facts(facts: Dict[str, str]) -> None:
    """写回 session_facts.json. 失败抛, 让 caller 处理 (返回 error)."""
    import json
    SESSION_FACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SESSION_FACTS_PATH, "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False, indent=2)


def remember_fact(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: 记一个 session 内的硬事实."""
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
    if key not in facts and len(facts) >= _FACTS_MAX_ENTRIES:
        return {
            "type": "error",
            "error": (
                f"session_facts 已满 ({_FACTS_MAX_ENTRIES} 条上限). "
                "员工 rm ~/.catfish/session_facts.json 清空, 或员工 explicit "
                "告诉你哪些可以删."
            ),
        }
    is_overwrite = key in facts
    facts[key] = value
    try:
        _write_session_facts(facts)
    except Exception as e:
        return {"type": "error", "error": f"写 session_facts 失败: {e}"}

    return {
        "type": "ok",
        "key": key,
        "value_preview": value[:100] + ("…" if len(value) > 100 else ""),
        "total_facts": len(facts),
        "overwrite": is_overwrite,
        "summary": (
            f"{'更新' if is_overwrite else '记住'}了 '{key}'. "
            f"当前 {len(facts)} 条 session_facts. "
            f"gateway 会在每次 chat 自动 inject 到 system prompt 末尾."
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
            "error": f"{skill_path}/script.py 不存在, 该 skill 没暴露代码入口",
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


def skill_install(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: 本机安装 skill — 从 source_dir 复制到 catfish/skills/<namespace>/<name>/.

    跨实例 share 是 Phase 3 BL-D1 (中央 Skills Hub), 这里只做本机版.

    args:
        source_dir: 源目录, 绝对路径或 ~/...
        namespace: 默认 'personal'
        overwrite: 默认 False
    """
    source = (args.get("source_dir") or "").strip()
    namespace = (args.get("namespace") or "personal").strip()
    overwrite = bool(args.get("overwrite", False))

    if not source:
        return {"ok": False, "error": "source_dir 必填"}

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
    })
    _write_skill_audit(audit_event)

    dry_note = audit_event.get("dry_run", {}).get("note", "")
    return {
        "ok": True,
        "installed_path": f"{namespace}/{skill_name}",
        "summary": (
            f"已安装 skill {namespace}/{skill_name} (v{metadata['version']}) "
            f"从 {source_path}. dry-run 通过 ({dry_note}). "
            f"仪表盘下次刷新会出现, gateway 重新扫到后 LLM 也能调."
            + (f" 旧版备份: {audit_event.get('backup_path')}" if overwrite else "")
        ),
        "dry_run": audit_event.get("dry_run", {}),
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
    if name == "catfish_remember":
        return remember_fact(args)
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
    raise ValueError(f"unknown native tool: {name}")
