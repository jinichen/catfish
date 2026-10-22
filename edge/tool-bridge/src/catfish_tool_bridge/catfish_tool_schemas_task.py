"""异步任务 / A2A / 提醒 / 日历 —— 12 个 tool 的 JSON Schema。

8/15 从 catfish_tool_schemas.py 拆出: 那个文件是**一整个 3362 行的
列表字面量** (78 个 schema), 越过 800 红线 4 倍。零逻辑、零变量引用,
所以按类别切成 8 份, 由 catfish_tool_schemas.py 拼回去。

加新 tool: schema 加到对应类别文件, impl 加 catfish_tools.py 或子 module,
dispatch 加 catfish_tools._dispatch_native_inner —— **dispatch 不重组**
(这条红线是 5/20 拆分时定的, 见 catfish_tool_schemas.py 文件头)。

description 每轮对话都发给模型, 写短: 做什么 / 何时用 / 跟哪个邻居工具区分 /
安全规则 / 参数格式。历史和实现细节写在各 tool 上方的 # 注释里。
"""
from __future__ import annotations

from typing import Any, Dict, List


TASK_TOOLS: List[Dict[str, Any]] = [
    # Plan D · Catfish Federation (五一 sprint Day 4-5)。对方鲶鱼按其 ALLOW.md
    # (关键词匹配, 可限定 allow_purpose) 决定答不答; 双方都写 ~/.catfish/a2a_audit.jsonl。
    {
        "name": "catfish_a2a_ask",
        "description": (
            "替员工问另一位同事的鲶鱼一个问题 (跨员工查对方公开的偏好 / 项目状态)。"
            "员工说「问下张三对 X 怎么看」时用。员工自己的事直接答, 查公司文档用其他工具。"
            "对方按自己的隐私规则决定答不答, 被拒就如实告诉员工。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to_sub": {
                    "type": "string",
                    "description": "对方 SSO sub (邮箱, 例 bob@ffcs.cn); 不知道就问员工",
                },
                "question": {
                    "type": "string",
                    "description": "一句话问题 (≤500 字), 具体并含关键词",
                },
                "purpose": {
                    "type": "string",
                    "description": "用途, 例 周报 / 汇报 / 咨询 / 协作",
                    "default": "",
                },
                "context_hint": {
                    "type": "string",
                    "description": "一句话说明为什么问, 帮对方决定怎么答",
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
    # 目前 kind 只有 execute_code, 其他 kind 待扩。
    {
        "name": "catfish_run_task",
        "description": (
            "启动后台执行任务, 立即返回 task_id, 不阻塞对话。用于估计 >10s 的活 (长 docx、多步处理) "
            "或员工同时交代多件事。短查询直接 execute_code。"
            "kind=execute_code 时 payload={code, lang, timeout_s}。"
            "启动后告诉员工「后台在跑 [label], 你可以先问别的」。"
            "这是后台执行任务, 不是待办任务库 (catfish_create_task)。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["execute_code"],
                    "description": "任务类型",
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
    # P3.5.39 (6/18): latest_output = 运行中 stdout/stderr 尾部 (~4KB), 实时更新。
    # BL-A2.3: 任务完成桌宠会自动通知, 不用 poll。
    {
        "name": "catfish_task_status",
        "description": (
            "查后台任务进度: status (pending/running/completed/failed/interrupted/not_found)、耗时、"
            "latest_output (运行中的输出尾部)。员工问「那个做到哪了」时调; 不要轮询, 完成会自动通知。"
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
    # Dashboard TasksCard 也用这个刷新。
    {
        "name": "catfish_task_list",
        "description": (
            "列出所有后台执行任务 (running/completed/failed)。员工问「后台在跑什么」时用。"
            "查待办请用 catfish_list_tasks。"
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "emoji": "📋",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_task_result",
        "description": (
            "取后台任务的结果 (result / error)。任务完成通知后或员工催结果时调; "
            "还在 running 就告诉员工「还在跑, 完成会通知你」。"
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
    # P3.5.33 (6/18) 评估 gate: retry_count >= max_retries (默认 3) 拒;
    # last_error_type=permanent (401/404/payload 错) 拒; 否则 retry_count+1, parent_task_id 串链。
    # 启动时 auto_retry_interrupted_on_startup 会自动重试白名单 kind 的 interrupted task,
    # LLM 显式调只用于员工要求 / failed / 老 task / 白名单外 kind。
    {
        "name": "catfish_task_retry",
        "description": (
            "用原输入重跑一个中断或失败的后台任务, 返回新 task_id。员工说「那个任务再跑一次」时用。"
            "超过重试上限或永久性错误会被拒 (ok=false + error), 把原因告诉员工。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "原任务 ID",
                },
            },
            "required": ["task_id"],
        },
        "emoji": "🔁",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-FED2.6 (5/12) a2a 协助通知 ──
    # 数据来自 BL-FED2.4 反馈环的 ~/.catfish/a2a_notifications.jsonl (只在员工本机)。
    {
        "name": "catfish_list_a2a_help",
        "description": (
            "查员工的鲶鱼被同事通过 catfish_a2a_ask 问过什么 (谁问、问什么、答了什么), 只读本机记录。"
            "员工问「今天我帮过谁」「小李最近问过我啥」时用; 员工自己做了什么走 employee_journal。"
            "返回里的 summary 可直接念给员工。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hours_back": {
                    "type": "integer",
                    "description": "过去多少小时 (默认 24; 0 = 全部)",
                },
                "unseen_only": {
                    "type": "boolean",
                    "description": "只看未读",
                },
                "from_sub": {
                    "type": "string",
                    "description": "按提问同事的 SSO sub 过滤",
                },
                "tag_substr": {
                    "type": "string",
                    "description": "按 purpose 子串过滤 (例 资质)",
                },
                "max_items": {
                    "type": "integer",
                    "description": "详细条数 (默认 50, 上限 200)",
                },
            },
            "required": [],
        },
        "emoji": "📨",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-FIX-TIMEOUT-OUTPUTS (5/13 "做不出文档") ─────────────────
    # 读 ~/.catfish/outputs/ (含历史 output/), 按时间倒序; 只在本机。
    {
        "name": "catfish_list_my_outputs",
        "description": (
            "列出你跨对话生成过的文件 (按时间倒序)。员工问「刚才那个 xlsx 在哪」, "
            "或你想确认之前是否做过时先调, 别再 execute_code 重做。"
            "员工上传的文件不在这里。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hours_back": {
                    "type": "integer",
                    "description": "过去多少小时 (默认 24; 0 = 全部)",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多条数 (默认 20, 上限 100)",
                },
                "ext_filter": {
                    "type": "string",
                    "description": "只返某类型, 例 .xlsx / .docx",
                },
            },
            "required": [],
        },
        "emoji": "📁",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── 本机任务库：任务事实源，Reminders 只是同步提醒 ───────────────
    # 换标题新建会变成两条任务, 早安页当成两件事 —— 所以强调更新必须带 task_id。
    {
        "name": "catfish_create_task",
        "description": (
            "创建或更新员工的待办任务 (本机任务库, 待办的事实来源)。员工交代要做的事、或从邮件/会议里识别出行动时用; "
            "系统提醒事项用 catfish_create_reminder, 有固定时间的会议用 catfish_create_calendar_event, "
            "后台执行用 catfish_run_task。\n"
            "更新/关闭已有任务必须带 task_id (先 catfish_list_tasks 查), 只传要改的字段, 没传的保持原值; "
            "不要换个标题再建一条。删掉一件事用 status=cancelled。"
            "长期任务拆成近期可执行的下一步并给真实截止时间。返回 possible_duplicates 时确认是否同一件事, 是就关掉多余那条。"
            "不要从周报正文生成任务。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "已有任务 ID; 更新/关闭时必传"},
                "title": {"type": "string", "description": "行动标题"},
                "due_date_iso": {"type": "string", "description": "截止时间, 本地 ISO 8601"},
                "body": {"type": "string", "description": "任务详情"},
                "priority": {"type": "integer", "minimum": 0, "maximum": 9, "description": "优先级 0-9"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "cancelled"],
                    "default": "pending",
                    "description": "任务状态",
                },
                "source": {"type": "string", "description": "来源, 如 conversation/email/calendar"},
                "source_id": {"type": "string", "description": "来源中的稳定 ID, 无 task_id 时用于去重"},
                "list_name": {"type": "string", "description": "Reminders 清单名"},
            },
            "required": ["title"],
        },
        "emoji": "✅",
        "toolset": "catfish_native",
        "available": True,
    },
    # macOS 首次读取会导入现有 Reminders; 之后 5 分钟节流。
    {
        "name": "catfish_list_tasks",
        "description": (
            "读取员工的待办任务 (本机任务库, 含 task_id、状态、截止时间、来源)。"
            "员工问「我有什么待办 / 今天要做什么 / 哪些逾期」时用; 更新任务前先用它拿 task_id。"
            "scope: active=全部未完成 (默认), today, week, overdue, all; 默认不含已完成/已取消。"
            "后台执行任务用 catfish_task_list; 要看提醒事项里的原始条目用 catfish_list_reminders。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["active", "today", "week", "overdue", "all"],
                    "default": "active",
                },
                "include_completed": {
                    "type": "boolean",
                    "default": False,
                    "description": "包含已完成/已取消",
                },
                "list_name": {
                    "type": "string",
                    "description": "只看某个清单; 留空看全部",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 100,
                },
                "force_sync": {
                    "type": "boolean",
                    "description": "员工刚在提醒事项里改过时传 true, 立即重新导入",
                },
            },
            "required": [],
        },
        "emoji": "✅",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_sync_tasks_to_reminders",
        "description": (
            "把任务库里未完成的任务同步到系统提醒 (macOS 提醒事项; Windows 到点弹通知)。"
            "可重复执行, 不会重复创建。先用 catfish_create_task 写任务, 再按需同步。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["active", "today", "week", "overdue", "all"],
                    "default": "active",
                    "description": "同步范围, 默认全部未完成",
                },
                "list_name": {"type": "string", "description": "目标提醒清单"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
            },
            "required": [],
        },
        "emoji": "🔁",
        "toolset": "catfish_native",
        "x_catfish_runtime": {"platforms": ["darwin", "windows"]},
        "available": True,
    },
    # ── BL-REMINDER (5/13 "macOS 提醒联动") ──────────────────────
    # 重复提醒: AppleScript 一次性创建有限制, 让用户在 Reminders.app 里自己设重复。
    # 常见失败: 权限未给 (系统设置 → 隐私 → 提醒事项 勾 Catfish Companion)。
    {
        "name": "catfish_create_reminder",
        "description": (
            "员工明确要求「在提醒事项里建一条」时, 直接创建系统提醒 (macOS Reminders.app, iCloud 同步到 iPhone; "
            "Windows 到点弹通知)。普通待办用 catfish_create_task (需要时再 catfish_sync_tasks_to_reminders); "
            "一次性横幅消息用 notify; 有固定起止时间的会议用 catfish_create_calendar_event。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "提醒标题 (短)",
                },
                "body": {
                    "type": "string",
                    "description": "备注详情",
                },
                "due_date_iso": {
                    "type": "string",
                    "description": "到期时间, 本地 ISO 8601, 例 2026-05-14T09:00:00",
                },
                "list_name": {
                    "type": "string",
                    "description": "清单名 (默认 提醒事项); 不确定先 catfish_list_reminder_lists",
                },
                "priority": {
                    "type": "integer",
                    "description": "0=无, 1-3=高, 4-6=中, 7-9=低",
                    "minimum": 0,
                    "maximum": 9,
                },
            },
            "required": ["title"],
        },
        "emoji": "⏰",
        "toolset": "catfish_native",
        "x_catfish_runtime": {"platforms": ["darwin", "windows"]},
        "available": True,
    },
    {
        "name": "catfish_list_reminders",
        "description": (
            "只读查看系统提醒事项 (macOS Reminders.app / Windows Catfish 提醒) 的原始条目。"
            "员工明确问「提醒事项里有什么」时用; 普通待办查询用 catfish_list_tasks。"
            "不要用 Hermes todo 读系统提醒 —— 它只管你当前会话的执行计划。"
            "scope: today / week (默认) / overdue / all。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["today", "week", "overdue", "all"],
                    "default": "week",
                },
                "include_completed": {
                    "type": "boolean",
                    "default": False,
                    "description": "包含已完成",
                },
                "list_name": {
                    "type": "string",
                    "description": "只看某个清单, 如 工作; 留空看全部",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 100,
                },
            },
            "required": [],
        },
        "emoji": "📋",
        "toolset": "catfish_native",
        "x_catfish_runtime": {"platforms": ["darwin", "windows"]},
        "available": True,
    },
    {
        "name": "catfish_list_reminder_lists",
        "description": (
            "列出提醒事项的清单名 (如 工作 / 家庭)。第一次创建提醒、或员工指定「加到 X 清单」前先调, 确认清单存在。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "emoji": "📋",
        "toolset": "catfish_native",
        "x_catfish_runtime": {"platforms": ["darwin", "windows"]},
        "available": True,
    },
    # ── BL-CALENDAR (5/14 "ISO 现场审核会议 LLM 写脚本踩坑") ──
    # 起因: LLM 用 execute_code 写 osascript 拼多行 AppleScript record, 解析器报 syntax error。
    # Windows: 记进 Catfish 日历 + 生成 .ics。失败返回 needs_permission / calendar_not_found。
    {
        "name": "catfish_create_calendar_event",
        "description": (
            "在系统日历创建有明确起止时间的事件 (会议 / 审核 / 行程, 可带地点), "
            "macOS 经 iCloud 同步到 iPhone。只有截止时间或没有时间的事用 catfish_create_reminder; "
            "多天的会每天调一次。直接调本工具, 不要自己写 osascript 脚本。"
            "时间用本地 ISO 8601, 如 2026-05-18T08:40:00。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "事件标题",
                },
                "start_iso": {
                    "type": "string",
                    "description": "开始时间, 本地 ISO 8601",
                },
                "end_iso": {
                    "type": "string",
                    "description": "结束时间, 默认开始后 1 小时",
                },
                "location": {
                    "type": "string",
                    "description": "地点, 例 409 会议室",
                },
                "description": {
                    "type": "string",
                    "description": "详情备注",
                },
                "calendar_name": {
                    "type": "string",
                    "description": "日历名 (默认 工作); 不确定先 catfish_list_calendars",
                },
                "alarm_minutes_before": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0, "maximum": 40320},
                    "description": "提前几分钟提醒, 默认 [15]; [15, 1440]=两次; []=不提醒",
                },
            },
            "required": ["title", "start_iso"],
        },
        "emoji": "📅",
        "toolset": "catfish_native",
        "x_catfish_runtime": {"platforms": ["darwin", "windows"]},
        "available": True,
    },
    {
        "name": "catfish_list_calendars",
        "description": (
            "列出系统日历名 (如 工作 / 家庭)。第一次创建日历事件前先调, 选合适的日历。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "emoji": "📅",
        "toolset": "catfish_native",
        "x_catfish_runtime": {"platforms": ["darwin", "windows"]},
        "available": True,
    },
]
