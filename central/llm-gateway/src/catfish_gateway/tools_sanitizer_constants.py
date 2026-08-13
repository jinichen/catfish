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
DEFAULT_MAX_TOOLS = 40
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
    "catfish_email_search",    # 邮件查询 (chat 常用)
    # 8/13 鸿波撞「你去知识库里面核对福富资质」→ 小鲶答"无法连接到知识库检索工具"。
    # 跟上面 5/23 那两条同一个病: 高频工具没进 always-on, 被 cap 误砍。
    # 配套 P43 (plugin_core_tools.py) 把它们提升为 hermes 核心, 两处都要改 ——
    # 只改一处的话: 只改这里 → 仍被 tool_search defer, 根本到不了 gateway;
    #               只改 P43 → 到了 gateway 但不在 always-on, 名额紧时被砍。
    "catfish_wiki_search",     # 知识库检索
    "catfish_search_docs",     # 本地文档检索
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
