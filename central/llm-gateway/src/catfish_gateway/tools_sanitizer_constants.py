"""tools_sanitizer 的常量集 — 从 tools_sanitizer.py 抽出 (5/22, 老文件 808 > 800 红线).

按 touchstone 拆分协议:
  - 这里只放 frozenset / dict / 标量配置 + 注释 (没有运行时逻辑)
  - 老 tools_sanitizer.py 顶上 re-export, 老 caller 不变
  - 改名单时改这里, 改逻辑时改 tools_sanitizer.py

加新常量该不该来这里:
  - 是 frozenset / dict 大列表? → 这里
  - 是 helper 函数 / 主入口? → tools_sanitizer.py
"""
from __future__ import annotations


# BL-FIX4 (5/8): hermes builtin browser_* 跟 catfish_browser_* namespace 撞, 看到
# catfish 一族就丢 hermes 一族. 这里只列 hermes 已知 builtin (现网客户端实际暴露的),
# 新加的不在表里也无所谓 — 我们只丢 "browser_" 开头且**不带 catfish_ 前缀**的,
# 通过名字 startswith 判断, 不依赖白名单.
HERMES_BROWSER_PREFIX = "browser_"
CATFISH_BROWSER_PREFIX = "catfish_browser_"


# BL-TOOL-CAP (5/15 鸿波撞 Qwen 122B 83 tools 空 400): 单 request tool 数量上限.
# Qwen 122B 实测 50+ tools 就开始撞空 400. 默认 cap 35 (5/22 鸿波降, 配合 source profile)
# 5/22 鸿波: H20 4 卡上 advisor prompt 40K, KV cache 打满单实例并发只 3. 砍 tool 数
# = 砍 prompt schema = 减 KV. always-on (~20) + profile (~10) + buffer (5) = 35.
# env CATFISH_MAX_TOOLS 调.
DEFAULT_MAX_TOOLS = 35
ENV_MAX_TOOLS = "CATFISH_MAX_TOOLS"


# BL-TOOL-PROFILE (5/22 鸿波): 按 X-Catfish-Source / ?catfish_source= 砍 tool 列表.
# caller 已经在标 source (companion-advisor / companion-profile / etc), gateway 这层
# 按 source 选白名单, 不属于该 profile 的 catfish_* tool 砍掉. always-on / hermes
# builtin / unknown source 不动 (保现有行为).
#
# 值 = 该 source 额外允许的 catfish_* tool 名 (always-on 自动加).
# 不在表里的 source → 不过滤 (现有行为, 走 cap).
SOURCE_TOOL_PROFILES: dict[str, frozenset[str]] = {
    # 早安智能参谋 (briefing_advisor.ts) — 只要主菜决策相关
    "companion-advisor": frozenset({
        "catfish_draft_email_reply",
        "catfish_draft_meeting_brief",
        "catfish_compose_followup_list",
        "catfish_check_compliance",
        "catfish_political_sensitivity_scan",
        "catfish_recall_decision_history",
        "catfish_forget_about",  # 5/22 新加, 员工说"老李是测试数据"用
        "catfish_today_summary",
    }),
    # 画像识别 (profile.ts) — 只要 style + user_profile
    "companion-profile": frozenset({
        "catfish_style_fingerprint_get",
        "catfish_style_fingerprint_refresh",
        "catfish_email_search",  # profile 从邮件抽风格
    }),
    # 老版 briefing card (Phase 6, 跟 advisor 类似)
    "companion-briefing-card": frozenset({
        "catfish_today_summary",
        "catfish_recall_decision_history",
    }),
    # 邮件分类调度
    "companion-email-scheduler": frozenset({
        "catfish_email_search",
    }),
}


#: BL-MEMORY-CATFISH-REMEMBER-BLACKLIST (5/16 鸿波 A 真切) — 永不暴露给 LLM 的工具.
#:
#: 5/16 实盘 Nemotron 49B 在 catfish_remember vs hermes memory 之间反复, 选了
#: 错的 (session-only 的 catfish_remember 当跨 session 用). SOUL nudge V1/V2/V3
#: 都没让模型听话. 鸿波拍板: 干脆从 LLM 工具列表彻底移除 catfish_remember, 让
#: LLM 没选择, 强制走 hermes memory.
#:
#: catfish_remember tool 本身**保留**实现 (供员工 /remember 显式命令触发, 或
#: gateway 内部 BL-MM1/MM2 流程). 只是不再 expose 给上游 LLM.
#:
#: 想恢复 expose (操作员调试): env CATFISH_EXPOSE_REMEMBER=1.
HIDDEN_FROM_LLM: frozenset[str] = frozenset({
    "catfish_remember",
    # BL-MEMORY-DEDUPE-COMPRESS-REVIEW (5/17 04:55 凌晨, #61): 鸿波 5 次抓我
    # over-engineer 后拍板砍这 2 工具暴露. 越界 hermes 责任 (压缩归 hermes
    # catfish-autocompress + ContextCompressor), entries 2 年才撞 limit 不该
    # 凌晨写. 留代码作 git 历史, LLM 看不到不会调.
    # 想恢复 (真撞 limit 时): env CATFISH_EXPOSE_MEMORY_INTEGRITY=1 (未实现, 真要时
    # 改 sanitizer if 加 env check).
    "catfish_memory_dedupe",
    "catfish_memory_compress",
})


#: Tier 1 — always-on 核心工具, 任何任务都该有, 永不 drop.
#: 这些是 LLM agent loop 的最底座 (执行代码 / 读文件 / 写文件 / 记忆 / 跨问 / 切片 /
#: 求澄清 / 派任务). 砍了 LLM 干不了基本事.
ALWAYS_ON_TOOLS: frozenset[str] = frozenset({
    # ── Catfish 核心 native ──
    # BL-MEMORY-CATFISH-REMEMBER-BLACKLIST (5/16): catfish_remember 移到 hidden.
    "catfish_search_sessions",
    "catfish_list_my_outputs",
    "catfish_user_profile_get",
    "catfish_user_profile_propose",
    "catfish_user_profile_confirm",
    "catfish_run_skill",
    "search_skills",
    # 5/23 鸿波: 早上撞 LLM 说"读取 TODO 列表" 但没真调工具 = catfish_today_summary
    # 被 BL-TOOL-CAP 砍了. 这俩是员工高频场景 (看今日 TODO/邮件), 必须 always-on
    # 防 cap 误砍.
    "catfish_today_summary",   # 今日活动 (TODO / 邮件 / 日程 / chat 汇总)
    "catfish_email_search",    # 邮件查询 (chat 常用)
    # BL-LLM-PLAN-WITHOUT-ACT (5/19): 内网 qwen 见到周报 / PPT 等关键词必须能立即
    # 找到对应 skill 并触发, 不能因 BL-TOOL-CAP 被砍. skill discovery + invocation
    # 这一族永不 drop. (catfish_run_skill 已在表里, 这里补 hermes 0.14 的 skill_*.)
    "skill_view",        # 看单个 skill 详情 (LLM 决定是否调用前)
    "skills_list",       # 列所有 skill (无 search_skills 时兜底)
    # BL-WEB-ALWAYS-ON (5/25 鸿波 web_search demo): hermes 按字母序发 tool, web_*
    # 排尾, 模型位置偏置 → 选 catfish_browser_* 抓页面而不是直接 web_search → 慢
    # 30 倍 + 贵 30 倍 token. 加进 always-on 让 _cap_tools_by_priority 永远前置.
    # 配合 BL-EDGE-TOOL-KEY 中央派发 Tavily key (5/24 DEPLOYMENT §15), 让模型真
    # emit web_search(query=...) tool_call.
    "web_search",        # hermes 0.14 内置, 走配置的 backend (我们选 tavily)
    "web_extract",       # 同上 (single backend mode)
    "web_crawl",         # 同上
    # ── Hermes 0.14 内置基础 (5/17 客户机实测对齐 hermes 0.14 tool name) ──
    # BL-HERMES-014-UPGRADE (5/17): hermes 0.14 改了一批 tool name, 老的
    # shell/bash/edit_file/list_dir/search/grep/todo_tool/screenshot **不再注册** —
    # always-on hit log 一直报 missing. 改用 0.14 真名:
    "execute_code",      # 跑代码
    "read_file",         # 读文件
    "write_file",        # 写文件
    "patch",             # hermes 0.14 取代 edit_file (统一 patch-style 编辑)
    "search_files",      # hermes 0.14 取代 search/grep/list_dir (统一搜索)
    "terminal",          # hermes 0.14 取代 shell/bash
    "process",           # hermes 0.14 新加 (process 管理)
    "clarify",           # 跨问 / 求澄清
    "delegate_task",     # 派任务
    "todo",              # hermes 0.14 重命名 todo_tool → todo (catfish-tool-bridge
                         # adapter.py 仍 `from tools.todo_tool import TodoStore`
                         # module 路径未变, 只是 tool name 改了)
    "memory",            # hermes 0.13/0.14 unified memory (action=add/replace/remove/search)
})


# BL-MCP-PREFIX-FIX (5/25 鸿波): catfish-tool-bridge MCP wrapper 把 tool 名加
# `mcp_catfish_tools_` 前缀 暴露给 hermes. always-on 白名单只列裸名 — 进 sanitizer
# 后 `mcp_catfish_tools_catfish_today_summary` 这种 MCP 包装版本就匹配不上, 被
# 当成普通 tool 处理 → 容易被 cap 砍 / 不前置. 用这个前缀 strip 一下再比.
MCP_CATFISH_PREFIX = "mcp_catfish_tools_"


def is_always_on(name: str) -> bool:
    """Always-on 判定 — 同时认裸名和 MCP 包装名.

    例: `catfish_today_summary` 和 `mcp_catfish_tools_catfish_today_summary`
        都返 True (都映射到同一个工具的两种暴露方式).

    用法: 全部 `name in ALWAYS_ON_TOOLS` 检查都改用这个, 防 BL-MCP-PREFIX-FIX 复发.
    """
    if not isinstance(name, str):
        return False
    if name in ALWAYS_ON_TOOLS:
        return True
    if name.startswith(MCP_CATFISH_PREFIX):
        return name[len(MCP_CATFISH_PREFIX):] in ALWAYS_ON_TOOLS
    return False


#: BL-RBAC-DAY4-HARDENING (5/17, hermes 0.14 #26759 tool_override 威胁模型):
#:
#: 已知 hermes / catfish builtin tool 白名单. 用于 detect "陌生" tool 名
#: (plugin tool_override rename builtin 成 dept-allowed 名的攻击)。
#: 不在此白名单 + 不在 mcp__* 前缀 + 不在 dept allowed_tools → audit WARN.
#:
#: 不 drop, 因为:
#:   1. 客户自家 plugin 命名千差万别, drop 会误杀
#:   2. RBAC allowed_tools 已经在 sanitize 里实施了, 这层只看异常模式
#:   3. drop 决策留给 dept admin 在 catfish-web /admin/access 配 allowed_tools
#:
#: 维护策略: hermes major 升级时 (e.g. 0.14 → 0.15) 跟 release notes 同步, 漏
#: 一个工具只是误报多一条 audit 行, 不影响功能.
KNOWN_BUILTIN_TOOLS: frozenset[str] = frozenset({
    # ── catfish 原生 (catfish_tool_bridge/catfish_tools.py CATFISH_NATIVE_TOOLS) ──
    # 5/17 客户机实测 log 漏报 37 个, grep edge/tool-bridge/src 拉真实 47 个全名:
    # catfish 用户身份 / skill / a2a / memory / browser_* / freeze / expert /
    # reminder / calendar / task / style_fingerprint / today_summary / teach
    "catfish_a2a_ask", "catfish_list_a2a_help",
    "catfish_browser_click", "catfish_browser_fill", "catfish_browser_find_by_text",
    "catfish_browser_goto", "catfish_browser_locate", "catfish_browser_screenshot",
    "catfish_browser_snapshot",
    "catfish_confirm_expertise", "catfish_expert_consult", "catfish_extract_expertise",
    "catfish_list_expertise",
    "catfish_create_calendar_event", "catfish_list_calendars",
    "catfish_create_reminder", "catfish_list_reminder_lists",
    "catfish_freeze_inspect", "catfish_freeze_rotate", "catfish_freeze_skill",
    "catfish_list_my_outputs", "catfish_memory_compress", "catfish_memory_dedupe",
    "catfish_propose_skill", "catfish_propose_skill_revision",
    "catfish_read_tool_archive", "catfish_recognize_captcha", "catfish_remember",
    "catfish_run_skill", "catfish_run_task", "catfish_screenshot",
    "catfish_search_sessions", "catfish_skill_backup", "catfish_skill_delete",
    "catfish_skill_install", "catfish_skill_publish",
    "catfish_style_fingerprint_clear", "catfish_style_fingerprint_get",
    "catfish_style_fingerprint_refresh",
    "catfish_task_list", "catfish_task_result", "catfish_task_status",
    "catfish_teach_end", "catfish_teach_start", "catfish_today_summary",
    "catfish_user_profile_clear", "catfish_user_profile_confirm",
    "catfish_user_profile_get", "catfish_user_profile_propose",
    # 5/21 Phase 7 advisor (鸿波): 7 个新 tool, 补 audit 白名单免每次 warn
    "catfish_draft_email_reply", "catfish_draft_meeting_brief",
    "catfish_compose_followup_list",
    "catfish_check_compliance", "catfish_political_sensitivity_scan",
    "catfish_recall_decision_history",
    # 5/22 鸿波: catfish_forget_about 跨源记忆清理
    "catfish_forget_about",
    # 顺手放进 search_skills (catfish ALWAYS_ON 不在 catfish_native, 但用)
    "search_skills",
    # ── hermes 0.14 真实 71 tool name (5/17 客户机实测拉的, hermes-agent
    # registry.get_all_tool_names() 真实输出, 不是 release notes 推测) ──
    # browser (12)
    "browser_back", "browser_cdp", "browser_click", "browser_console",
    "browser_dialog", "browser_get_images", "browser_navigate", "browser_press",
    "browser_scroll", "browser_snapshot", "browser_type", "browser_vision",
    # core agent (10)
    "clarify", "delegate_task", "execute_code",
    "patch",         # 0.14 取代 edit_file
    "process",       # 0.14 新加
    "read_file", "write_file",
    "search_files",  # 0.14 取代 search/grep/list_dir
    "terminal",      # 0.14 取代 shell/bash
    "memory",
    # task / cron / kanban (11)
    "todo", "cronjob",
    "kanban_block", "kanban_comment", "kanban_complete", "kanban_create",
    "kanban_heartbeat", "kanban_link", "kanban_list", "kanban_show", "kanban_unblock",
    # skills (3)
    "skill_manage", "skill_view", "skills_list",
    # vision / video / image (4)
    "image_generate", "video_analyze", "video_generate", "vision_analyze",
    # web / search (4)
    "session_search", "web_extract", "web_search", "x_search",
    # messaging (3)
    "send_message", "discord", "discord_admin",
    # feishu (5)
    "feishu_doc_read", "feishu_drive_add_comment", "feishu_drive_list_comment_replies",
    "feishu_drive_list_comments", "feishu_drive_reply_comment",
    # home assistant (4)
    "ha_call_service", "ha_get_state", "ha_list_entities", "ha_list_services",
    # spotify (8)
    "spotify_albums", "spotify_devices", "spotify_library", "spotify_playback",
    "spotify_playlists", "spotify_queue", "spotify_search",
    # yuanbao (5)
    "yb_query_group_info", "yb_query_group_members",
    "yb_search_sticker", "yb_send_dm", "yb_send_sticker",
    # misc (3)
    "computer_use",   # 0.14 cua-driver, 非 Anthropic
    "text_to_speech",
    "mixture_of_agents",
})
