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
# 8/13: 35 → 40。tool_search 上线后模型实收只有 32 个 (shape dump 实测),
# 这个 cap 其实一直没触发过 —— 它是为 tool_search 之前那个「165 个全涌进来」
# 的世界定的。P43 把 4 个 catfish 工具提升为核心后是 36, 留 4 个余量。
# 上沿仍远离实测红线 (Qwen 122B 50+ 撞空 400), KV 代价约 +2K token。
# 8/31: Reminders 读写都提升为核心后需要 41 个；写入口不可再被 cap 静默砍掉，
# 否则模型会把会话规划误报成系统提醒创建成功。
DEFAULT_MAX_TOOLS = 41
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


# BL-ADVISOR-NATIVE-LEAK (8/24 鸿波「千问为什么一直出错」)
#
# 上面那份 SOURCE_TOOL_PROFILES 只管 catfish_* —— hermes 原生工具 (execute_code /
# write_file / patch / process / delegate_task ...) 整族绕过。判据是「catfish_*
# 工具」, 要治的真事是「这条 source 该有哪些**能力**」, 判据比真事窄。
#
# 后果不是"多几个工具"。8/24 实盘: advisor 拿到 execute_code, 在上面连打 13 次
# (10:34:00→10:34:25, 约 2s 一发), prompt_tokens 44237→44997 而 completion 恒为
# 69 —— 原地打转。上游百炼返 400 "Repetitive tool calls detected", 员工那头看到的
# 就是「千问一直出错」。
#
# 更要紧的是它撞 CATFISH-ADVISOR-DESIGN.md:55 红线「任何级别都不代行」。参谋只
# 出建议不动手, 却握着执行代码 / 写文件 / 派任务的能力; 且 advisor 是**后台无人
# 值守**调用, 没有员工盯着它在跑什么。这跟 catfish_email_create_draft 那边一样,
# 要落成能力边界而不是 prompt 约定 —— 模型手上根本没这个工具, 想调也调不到。
#
# 值 = 该 source 允许的 **hermes 原生 (裸名) ** 工具白名单。
#
# 为什么是白名单不是黑名单: 黑名单要求"每次 hermes 新增原生工具都记得来加一
# 行", 漏一次就静默泄漏 —— 那正是本 bug 的复发温床。白名单默认全拒, 漏加的
# 后果是"少个工具"(看得见), 不是"多个执行能力"(看不见)。
#
# ⚠ SOURCE_TOOL_PROFILES 的每个 key 必须在这里显式出现, 哪怕是空集; 由
# test_source_native_tools.py::test_两表key必须一一对应 钉死。漏加会当场红,
# 而不是静默按空集全砍 (误伤) 或静默放行 (漏)。
#: hermes 0.20 的三个「渐进式披露」桥。**砍了会直接废掉一条 source。**
#:
#: 0.20 起 MCP/插件工具不再全量塞进 tools 数组, 而是收进这三个桥按需检索
#: (tool_search 找 → tool_describe 看 schema → tool_call 调)。catfish 78 个工具
#: 里, 除 P43 提升为核心的 11 个 (plugin_core_tools.py:_PROMOTE) 外, **其余 67 个
#: 全部被 defer** —— 也就是说它们压根不出现在 gateway 收到的 tools 数组里, 只能
#: 走桥。
#:
#: 具体到 advisor: 它 profile 白名单里那 8 个业务工具, 只有 catfish_today_summary
#: 在 P43 名单内, 另外 7 个 (check_compliance / draft_email_reply / ...) 全靠桥。
#: 桥砍掉 = advisor 一个业务工具都够不着, 变成空手参谋。
#:
#: 给桥安不安全? 安全, 且是**双保险**:
#:   gateway 这层 —— execute_code 等已从 tools 数组砍掉, 模型看不见;
#:   hermes 那层 —— tool_search.py:1044 `if not is_deferrable_tool_name(name)`
#:                  直接拒绝, core 工具 (execute_code / write_file / ...) 永不
#:                  deferrable, 所以 tool_call 根本调不动它们。
#: 桥能到达的集合 = 被 defer 的工具, 恰好就是 catfish 业务工具那一族。
DEFERRED_TOOL_BRIDGES: frozenset[str] = frozenset({
    "tool_search",
    "tool_describe",
    "tool_call",
})


SOURCE_NATIVE_TOOLS: dict[str, frozenset[str]] = {
    # 早安智能参谋的输入已经由 Companion 确定性预取；SYSTEM_PROMPT 点名的业务
    # 工具也全部由 Catfish 插件提升为直接可见。这里不再给 bridge / web / browser
    # 等偏航入口：参谋只分析本轮数据，不反问、不联网、不执行外部动作。
    "companion-advisor": frozenset(),
    # 下面三条都是**后台无人值守**批处理 —— 没有员工在屏幕前, 给执行 / 写入类工具
    # 的风险比 advisor 还高, 所以除了够得着自己业务工具所必需的桥, 一个都不给。
    #
    # 谁要桥, 看该 source 的 catfish 白名单里有没有 P43 名单外的工具:
    "companion-profile": DEFERRED_TOOL_BRIDGES,        # style_fingerprint_* 被 defer
    "companion-briefing-card": DEFERRED_TOOL_BRIDGES,  # recall_decision_history 被 defer
    # 它白名单里只有 catfish_email_search, 在 P43 名单内 = 直接可见, 不需要桥。
    "companion-email-scheduler": frozenset(),
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
    # ── P3.5.70 (6/22 鸿波 catch "工作台拒调 execute_code 真因") ──
    # terminal/read_terminal = hermes 自带直接跑 shell / 读 shell 历史. catfish
    # 安全设计 = LLM 直调 shell 唯一通道 execute_code (强制沙箱 + 弹审批). 暴露
    # terminal 给 LLM 撞红线设计: LLM 看到 tool list 含 terminal, SOUL.md 教 shell
    # 红线 → LLM 困惑拒绝 → 给员工说 "你自己跑". 历史 5/17 hermes 0.14 升级时把
    # terminal 加 ALWAYS_ON 是失误 (那时 execute_code 沙箱审批架构未落地).
    # skill 内部 dispatch terminal 不经过 gateway sanitize_tools (走 tool-bridge 直
    # dispatch), 不受影响 — 治本不误伤合法 skill 路径.
    # 想恢复 (某 dept 信任高需 LLM 直调): env CATFISH_EXPOSE_TERMINAL=1.
    "terminal",
    "read_terminal",
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
    "catfish_list_reminders",  # macOS Reminders.app 真实待办读取（不是 Hermes todo）
    "catfish_create_reminder",  # macOS Reminders.app 唯一用户待办写入口
    "catfish_email_search",    # 邮件查询 (chat 常用)
    # 8/28: 邮件页交接带 email_id; Qwen 需要在当前终端直接看到读取链路,
    # 否则会在 tool_search / execute_code 间空转。实现仍来自该终端自己的
    # Tool Bridge, 这里不是中央工具目录。
    "catfish_email_read",      # 读取指定邮件全文
    "catfish_email_attachment",  # 查看指定邮件附件
    # 8/13 鸿波撞「你去知识库里面核对福富资质」→ 小鲶答"无法连接到知识库检索工具"。
    # 跟上面 5/23 那两条同一个病: 高频工具没进 always-on, 被 cap 误砍。
    # 配套 P43 (plugin_core_tools.py) 把它们提升为 hermes 核心, 两处都要改 ——
    # 只改一处的话: 只改这里 → 仍被 tool_search defer, 根本到不了 gateway;
    #               只改 P43 → 到了 gateway 但不在 always-on, 名额紧时被砍。
    "catfish_wiki_search",     # 知识库检索
    "catfish_search_docs",     # 本地文档检索
    # 8/13 补: 上面这些工具的输出超 4KB 会被 tool-bridge 换成
    # `[已归档: archive_ref=xxx]`, 提示语让 LLM 调本工具拿全文。它不在名单里的
    # 后果不是"少个工具", 是**把搜索结果变成一条断头路** —— 实测小鲶两次说
    # "搜索返回被归档截断了", 然后放弃归档路径改用 execute_code 手工读文件。
    # 跟 P43 的 _PROMOTE 必须同步 (test_p43_core_tools.py 有跨仓一致性测试)。
    "catfish_read_tool_archive",
    # 8/17: 浏览器一族跟着 P43 一起进来 —— 两处必须同时改, 否则
    #   只改 P43     → 到得了 gateway, 但名额紧时被 cap 砍
    #   只改这里     → 仍被 tool_search defer, 压根到不了 gateway
    # 两种都表现为"改了没效果" (test_p43_core_tools 那条测试就是钉这个的)。
    #
    # 它们进来会让 BL-FIX4 (:309 判据) 重新为真, 从而丢掉 hermes 自带的 12 个
    # browser_* —— 那条去重 8/13 tool_search 上线后静默失效了四天。
    "catfish_browser_goto",
    "catfish_browser_snapshot",
    "catfish_browser_click",
    "catfish_browser_fill",
    "catfish_browser_find_by_text",
    "catfish_recognize_captcha",
    # 8/24: advisor 的 4 个业务工具跟着 P43 一起进来 —— 同样是两处必须一起改
    # (理由见上面 8/17 那段)。
    #
    # 病根: briefing_advisor_prompts.ts 的 SYSTEM_PROMPT 点名要求调这 4 个,
    # 但它们全被 tool_search defer, 一个都不在模型收到的 tools 数组里, 而
    # 整份 prompt 没提过"要先 tool_search 找"。DeepSeek 能自己摸索出两步流程
    # (8/15、8/21 实证), Qwen 不能 —— 8/24 那一发 tool_calls=0, 回了句
    # 「用户发送了系统提示内容, 无具体任务请求」。
    #
    # 靠 prompt 教模型走两步是 prompt 约定, 提升成可见是能力边界, 后者不挑模型。
    "catfish_draft_email_reply",
    "catfish_draft_meeting_brief",
    "catfish_compose_followup_list",
    "catfish_recall_decision_history",
    # 同一份 prompt 里另外两个被 `→` 点名的 (合规 / 政治敏感), 同理。
    "catfish_check_compliance",
    "catfish_political_sensitivity_scan",
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
    # P3.5.70 (6/22 鸿波 catch): "terminal" 移到 HIDDEN_FROM_LLM. catfish 安全
    # 设计 LLM 直调 shell 唯一通道 = execute_code (沙箱+审批). terminal 暴露
    # 给 LLM 撞红线设计, 历史 5/17 加进 ALWAYS_ON 是 hermes 0.14 升级时失误
    # (那时 execute_code 沙箱+审批架构未落地). skill 内部仍可用 terminal (走
    # tool-bridge dispatch, 不经过 gateway sanitize_tools 这关).
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
# 8/13: 原值 "mcp_catfish_tools_" (单下划线) —— **从写下来就没匹配过**。
#
# hermes 给 MCP 工具的注册名是 `mcp__<server>__<tool>` (tools/mcp_tool.py
# `MCP_TOOL_NAME_PREFIX = "mcp__"`, 双下划线), server 名 `catfish-tools` 经
# sanitize_mcp_name_component 变 `catfish_tools`。真名长这样:
#
#     mcp__catfish_tools__catfish_wiki_search
#
# 单下划线版本连第一段 `mcp_` vs `mcp__` 都对不上, is_always_on() 对所有 MCP
# 包装名恒返 False。而这个常量 5/25 加进来**就是为了修**「always-on 白名单只列
# 裸名 → MCP 包装名匹配不上 → 容易被 cap 砍」—— 修 bug 的代码自己没生效。
#
# hermes 侧那段注释说明了原因: 它某版把单下划线改成双, 为对齐 Claude Code /
# Codex 的约定, "removing the single->double rewrite that path previously had
# to perform"。上游改了, 这边没跟。
MCP_CATFISH_PREFIX = "mcp__catfish_tools__"


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


def named_tool_choice_names(body: dict) -> frozenset[str]:
    """解析 caller 用 `tool_choice` 点名的工具名，不在这里做授权。

    # 为什么需要这个 (8/24 实盘, 我自己砍出来的)

    Companion 有一批 `catfish_direct=1` 的调用: 不走 agent loop, 只带**一个**
    结构化输出工具 + 强制 tool_choice, 用来把模型输出压成 JSON。
        companion-profile           → submit_profile
        companion-advisor-transform → submit_advisor_result
        companion-wiki-suggest      → ...

    这些名字既不是 hermes 原生也不是 catfish_*, 老代码靠「非 catfish_ 前缀 →
    不动」放行。8/24 收紧 source profile 后这条路堵上了, companion-profile
    那一发的**唯一**工具 submit_profile 当场被砍光 —— tool_choice 指着一个
    不存在的工具, 画像识别静默失效。

    # 判据

    返回值只是协议解析结果。是否允许由 tool_choice_policy 按 source 合同校验；
    不能把“调用方点名”直接当成越过 source/RBAC 的授权。

    tool_choice 为 "auto"/"none"/"required" 或缺省时返空集 (没有点名)。
    """
    tc = body.get("tool_choice")
    if not isinstance(tc, dict):
        return frozenset()
    fn = tc.get("function")
    if not isinstance(fn, dict):
        return frozenset()
    name = fn.get("name")
    return frozenset({name}) if isinstance(name, str) and name else frozenset()


# 兼容旧测试/调用方；授权逻辑必须使用 tool_choice_policy。
pinned_tool_names = named_tool_choice_names


def is_hidden_from_llm(name: str) -> bool:
    """Hidden-from-LLM 判定 — 同时认裸名和 MCP 包装名 (P3.5.70 6/22 鸿波 catch).

    跟 is_always_on() 对称. 历史 bug: tools_sanitizer.py 老 `name in
    _HIDDEN_FROM_LLM` 检查只看裸名, 不处理 MCP 前缀 — `mcp_catfish_tools_terminal`
    不会被 filter, 仍暴露给 LLM. 这个 helper 修了.

    例: `terminal` 和 `mcp_catfish_tools_terminal` 都返 True.

    用法: tools_sanitizer.py 老 `name in _HIDDEN_FROM_LLM` 检查改用 is_hidden_from_llm().
    """
    if not isinstance(name, str):
        return False
    if name in HIDDEN_FROM_LLM:
        return True
    if name.startswith(MCP_CATFISH_PREFIX):
        return name[len(MCP_CATFISH_PREFIX):] in HIDDEN_FROM_LLM
    return False


# P3.5.161 (7/3 鸿波): KNOWN_BUILTIN_TOOLS + _audit_unknown_tools 全删.
#
# 老角色 (5/17 加): 防 hermes 0.14 #26759 tool_override CVE — plugin 悄悄
# rename built-in tool 成 dept-allowed 名绕 RBAC. catfish 侧加第二道防线
# audit-only warn (不 drop) 追踪嫌疑.
#
# 现在过时: hermes v0.18 tools/registry.py:395-408 已本身防御 override —
# plugin 试图 override → 默认 raise PermissionError REJECT, 需 operator
# 显式 plugins.entries.<pid>.allow_tool_override: true opt-in 才允许.
# catfish 侧第二道防线冗余, warn 已从"防攻击"蜕变成"hermes 升级 tool 追踪"
# 维护负担 (每次 hermes 升级都要补白名单).
#
# 严格 audit 完 8 层依赖后删: 唯一 consumer 是 _audit_unknown_tools 本身,
# _audit_unknown_tools 返值 discard, 无 downstream metric / dashboard.
# X-Catfish-Source header 追踪 (BL-RBAC-DAY4-HARDENING 族 B) 独立防 #23194
# ctx.llm bypass, **保留不删** (metrics.py / app.py / output_transforms.py).
#
# 详见 CHANGELOG P3.5.161.
