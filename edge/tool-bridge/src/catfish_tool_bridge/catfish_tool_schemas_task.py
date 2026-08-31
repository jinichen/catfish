"""异步任务 / A2A / 提醒 / 日历 —— 12 个 tool 的 JSON Schema。

8/15 从 catfish_tool_schemas.py 拆出: 那个文件是**一整个 3362 行的
列表字面量** (78 个 schema), 越过 800 红线 4 倍。零逻辑、零变量引用,
所以按类别切成 8 份, 由 catfish_tool_schemas.py 拼回去。

加新 tool: schema 加到对应类别文件, impl 加 catfish_tools.py 或子 module,
dispatch 加 catfish_tools._dispatch_native_inner —— **dispatch 不重组**
(这条红线是 5/20 拆分时定的, 见 catfish_tool_schemas.py 文件头)。
"""
from __future__ import annotations

from typing import Any, Dict, List


TASK_TOOLS: List[Dict[str, Any]] = [
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
            "★ 列你 (鲶鱼) 跨 session 写过的所有文件 (~/.catfish/outputs/, 含历史 output/), "
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
        "x_catfish_runtime": {"platforms": ["darwin"]},
        "available": True,
    },
    {
        "name": "catfish_list_reminders",
        "description": (
            "★ 读取用户 macOS Reminders.app 里的真实提醒事项条目，返回标题、清单、"
            "截止时间、完成状态和优先级。用于‘本周待办是什么’、‘列出今天待办’、"
            "‘有哪些逾期待办’、‘列出所有提醒事项’等查询。\n\n"
            "**不要用 Hermes todo 读取 Reminders.app**：Hermes todo 只管理 Agent 当前"
            "会话的执行计划，调用成功也不会读取用户的系统待办。遇到系统提醒查询必须"
            "直接调用本工具。\n\n"
            "scope: today=今天到期，week=本自然周到期，overdue=今天之前未完成，"
            "all=全部；默认 week。默认不含已完成，可用 include_completed=true 查看。"
            "list_name 可限定某个提醒清单，limit 默认 100、最大 500。\n\n"
            "🔒 只读、100% 本机；不修改提醒事项，不上传中央。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["today", "week", "overdue", "all"],
                    "default": "week",
                    "description": "读取范围：today/week/overdue/all，默认 week",
                },
                "include_completed": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否包含已完成提醒，默认 false",
                },
                "list_name": {
                    "type": "string",
                    "description": "只看指定清单，如‘工作’；留空看全部清单",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 100,
                    "description": "最多返回多少条，默认 100",
                },
            },
            "required": [],
        },
        "emoji": "📋",
        "toolset": "catfish_native",
        "x_catfish_runtime": {"platforms": ["darwin"]},
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
        "x_catfish_runtime": {"platforms": ["darwin"]},
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
        "x_catfish_runtime": {"platforms": ["darwin"]},
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
        "x_catfish_runtime": {"platforms": ["darwin"]},
        "available": True,
    },
]
