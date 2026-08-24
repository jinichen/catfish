"""P43 (8/13): 让几个高频 catfish 工具不被 hermes 的渐进式披露藏起来。

# 病

员工在工作台说「你去知识库里面核对福富资质」, 小鲶回:

    我目前无法连接到可用的知识库检索工具, 因此不能直接核实…

这句话仓库里搜不到 —— 是模型自己说的, 因为它**确实没看到**那些工具。

拿 gateway 的 request shape dump 对过 (`~/Library/Logs/catfish/shape-dumps/`),
实际下发给模型的是 `n_tools: 32`, 其中 **catfish 工具 0 个**:

    29 个 hermes 核心工具 (browser_* 12 个、read_file、memory、web_search…)
    +  3 个 bridge (tool_search / tool_describe / tool_call)

# 真因: hermes 0.20 的 progressive tool disclosure

`tools/tool_search.py` 文件头第一句:

    When enabled, MCP and non-core plugin tools are replaced in the
    model-visible tools array by three bridge tools — tool_search,
    tool_describe, tool_call — and surfaced on demand.
    **Core Hermes tools never defer.**

判据在 `is_deferrable_tool_name()`:

    if name in _core_tool_names():        # toolsets._HERMES_CORE_TOOLS
        return False                      # 核心永不 defer
    if entry.toolset.startswith("mcp-"):
        return True                       # MCP 一律 defer

catfish 的 77 个工具全部走 MCP server `catfish-tools` 进 hermes, 于是**无一例外
全被 defer**。而激活条件是「只要存在任何一个 deferrable 工具就激活」(Tier 1),
所以这条路一直是开着的。

模型要拿到知识库工具, 得先自己调 `tool_search` 去搜。它没调 —— 直接说没有。

# 为什么改这里而不是别处

- 关掉 tool_search (`tool_search.enabled: off`): 77 个工具全涌回来, 直接撞
  gateway 的 `DEFAULT_MAX_TOOLS`, 换一个更难查的问题。
- 靠 SOUL.md 教模型先调 tool_search: 提示词兜底, 不可靠。
- `_HERMES_CORE_TOOLS` 是**唯一**的「永不 defer」名单, 而且 `_core_tool_names()`
  没有缓存装饰器、`from toolsets import` 写在函数体内 —— 每次调用重读模块属性,
  所以启动时 patch 一次, 之后每个请求都生效。

# 两个必须守住的实现约束

## 1. 只能重新绑定, 不能 extend

`toolsets.py` 里 `_HERMES_CORE_TOOLS` 是个普通 list, 而它在 464-487 行还被
5 个 toolset 定义引用:

    "hermes-cli":      {"tools": _HERMES_CORE_TOOLS, ...}   ← 持同一对象
    "hermes-cron":     {"tools": _HERMES_CORE_TOOLS, ...}
    "hermes-telegram": {"tools": _HERMES_CORE_TOOLS, ...}
    "hermes-discord":  {"tools": _HERMES_CORE_TOOLS + [...]}  ← 求值时已复制

`.extend()` 会原地改那个 list, 把 catfish 的工具混进 hermes 的 CLI / cron /
Telegram toolset —— 那几个跟 Companion 不是一条路, 而且 discord 因为用了 `+`
不会跟着变, 结果还不一致。

重新绑定模块属性只影响 `_core_tool_names()` (它每次重新 import), 那 5 个
toolset 仍持有旧 list 对象, 不受影响。**这是有意的。**

## 2. 名字必须是 MCP 全名, 不是裸名

hermes 给 MCP 工具的注册名是 `mcp__<server>__<tool>` (`tools/mcp_tool.py`
`MCP_TOOL_NAME_PREFIX = "mcp__"`, **双下划线**), server 名经
`sanitize_mcp_name_component` 把 `-` 换成 `_`。

config.yaml 里 server 叫 `catfish-tools`, 所以真名是:

    mcp__catfish_tools__catfish_wiki_search

`classify_tools` 读的是 tool_defs 里的 `function.name`, 而那来自
`registry.get_definitions()` → `entry.name` → 注册名。全链路没有任何地方剥前缀,
所以这里写裸名等于白写。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("catfish.xcatfish_user.plugin")

# MCP server 名 (见 ~/.hermes/config.yaml 的 mcp_servers)。
# 改这里之前先确认 config 里的 server 名 —— 两边对不上 patch 就是空转。
_MCP_SERVER = "catfish_tools"          # config 里是 catfish-tools, `-` 被 sanitize 成 `_`
_MCP_PREFIX = f"mcp__{_MCP_SERVER}__"  # hermes 用双下划线分隔, 见模块 docstring

# 提升为「核心」的 catfish 工具 —— 每加一个都占掉 gateway 的一个 tool 名额,
# 所以只放员工高频、且「模型不会想到先去 tool_search 搜」的那几个。
#
# 名额账 (按 shape dump 实测):
#   29 visible + 3 bridge = 32, gateway cap 40
#   加这 5 个 → 37, 余 3
# 再往上加要先确认 cap 抬得动: 注释里写着 Qwen 122B 实测 50+ tools 撞空 400。
#
# ⚠ 加名单的判据是两条, 8/13 当天补的第二条:
#   1. 员工高频, 且模型不会想到"先去 tool_search 搜一下"
#   2. **被提升工具的输出会指向它** —— 见下面 catfish_read_tool_archive
#
# 只满足"同类路由提示"的不算 (比如 wiki_search 的描述里写着"想找附件用
# catfish_attachments_search")。那种模型可以自己调 tool_search 找, 不是断链。
_PROMOTE = (
    "catfish_wiki_search",     # 知识库检索 —— 8/13 员工撞的就是它
    "catfish_search_docs",     # 本地文档检索, 跟上面是一对
    "catfish_today_summary",   # 今日 TODO / 邮件 / 日程汇总
    # 8/24: 用户系统待办的唯一只读入口。没有它时 Qwen 把“查询 Reminders”
    # 连续写进 Hermes 会话规划 todo 12 次；这个入口不能再藏到 tool_search 后面。
    "catfish_list_reminders",
    "catfish_email_search",    # 邮件查询
    # ── 8/13 补: 上面 4 个的输出可能变成 [已归档], 那时必须能读回来 ──
    #
    # tool-bridge 的 adapter 对**任何**超 4KB 的 tool result 做归档 (
    # `_maybe_archive_oversized_result`), 把 result 换成
    # `[已归档: archive_ref=xxx] 摘要 + 头尾预览`, 并指望 LLM 调本工具拿全文。
    #
    # 但本工具当时不在提升名单里 → 仍被 tool_search defer → 模型看不见 →
    # 提示语指向一个不存在的工具。8/13 实测: 员工问"去知识库核对福富资质",
    # 小鲶两次说"搜索返回被归档截断了", 然后放弃归档路径, 改用 execute_code
    # 一份份手工读 wiki 文件和 xlsx —— 结果对, 但多烧了十几轮 tool call。
    #
    # 教训: 提升一个入口工具时, 要连它**输出可能指向的工具**一起提升,
    # 否则就是把人放进一条断头路。
    "catfish_read_tool_archive",
    # ── 8/17 补: 浏览器一族 ──────────────────────────────────────────
    #
    # 这 6 个进来不是为了加东西, 是为了**让 gateway 那条去重能重新生效**。
    #
    # BL-FIX4 (5/8) 定的规矩: tools 数组里出现 catfish_browser_* 就丢掉 hermes
    # 自带的 12 个 browser_* (tools_sanitizer.py:309 判据, :365 执行)。8/13
    # tool_search 上线后, 9 个 catfish_browser_* 全被 defer, 数组里一个都没有,
    # 于是那条判据恒为 False —— 去重**静默失效了四天**, 两族并存。
    #
    # 后果不只是费 token: tool-bridge 上那些内网适配补丁 (FIX3 的 a11y →
    # JS evaluate 双路径、FIX9 的 max_elements 500) 打的都是 catfish 这一族,
    # hermes 原生那 12 个没吃到。也就是说跑内网系统用的是没打补丁的那族。
    #
    # 提升之后数组里就有了 → 判据自然为 True → 12 个 hermes browser 被丢。
    # 不用改 gateway 的判据 (我一开始以为要改, 看了执行顺序才发现不用):
    # 丢弃在 :365, cap 在 :474, 丢在前。
    #
    # 选这 6 个的依据:
    #   前 5 个 = CHANGELOG 3535-3540 那条内网实盘链 (10.10.111.53:8776 登录
    #             → 抓用户列表) 真正用到的动作
    #   recognize_captcha = SOUL.md 写着"验证码**必走**"; 它出现在登录流程中段,
    #             那里最不该多一次 tool_search 往返
    #
    # 没提升的留在 defer: catfish_browser_locate (1,022 tok, find_by_text 失败
    # 后的视觉兜底, 本身是异常路径) / screenshot / evaluate / console。
    #
    # token 账 (tiktoken 实测): -6,872 (hermes 12 个) + 3,961 (这 6 个)
    #                         = 净省 ~2,900 /轮
    "catfish_browser_goto",
    "catfish_browser_snapshot",
    "catfish_browser_click",
    "catfish_browser_fill",
    "catfish_browser_find_by_text",
    "catfish_recognize_captcha",
    # ── 8/24 补: advisor 的 4 个业务工具 ─────────────────────────────
    #
    # 这是上面那条"断头路"教训的第三种形态, 而且更直接: 不是**工具输出**
    # 指向一个看不见的工具, 是 **prompt 直接点名**一个看不见的工具。
    #
    # briefing_advisor_prompts.ts 的 SYSTEM_PROMPT「工作步骤」第 3 条写死:
    #     涉及邮件回复 → catfish_draft_email_reply
    #     涉及会议汇报 → catfish_draft_meeting_brief
    #     涉及催办     → catfish_compose_followup_list
    #     涉及决策     → catfish_recall_decision_history
    # 而这 4 个当时全在 defer 名单里, 一个都不出现在模型收到的 tools 数组中,
    # 且整份 prompt 没有一个字提到"得先 tool_search 找出来"。
    #
    # 8/24 实盘 (鸿波「千问为什么一直出错」) 两个模型两种反应:
    #   DeepSeek 自己摸索出 tool_search → tool_describe → 调用 (8/15、8/21
    #            agent.log 有完整链路), 今天这次连它也没走完;
    #   Qwen     不自发探索, 找不到被点名的工具 → 判定「用户发送了系统提示
    #            内容, 无具体任务请求」→ 回一句"收到, 待命", tool_calls=0。
    #
    # 判据落在名单第 1 条「模型不会想到先去 tool_search 搜一下」上 —— Qwen
    # 那一发是这条判据的直接实证。靠 prompt 教模型走两步是"prompt 约定",
    # 提升成可见是"能力边界", 后者不挑模型。
    #
    # cap 账: .env CATFISH_MAX_TOOLS=110, 当前实测最大一发 44 个 (companion-chat),
    # +4 = 48, 远在线内; BL-TOOL-CAP 历史从未触发。
    "catfish_draft_email_reply",
    "catfish_draft_meeting_brief",
    "catfish_compose_followup_list",
    "catfish_recall_decision_history",
    # 同一份 SYSTEM_PROMPT 里还有两个被 `→` 点名的, 一起提升 —— 漏掉它们
    # 就是同一个病换个工具复发。这两个是 8/15 日志里 DeepSeek 调用次数最多的
    # (check_compliance 4 次、political_sensitivity_scan 2 次), 每次都得先
    # tool_search 一轮。央国企场景里合规和政治敏感是每条主菜都要过的关,
    # 让它们每次多绕一轮 tool_search 不合算。
    "catfish_check_compliance",
    "catfish_political_sensitivity_scan",
)

#: 提升后的完整注册名, 给单测和 gateway 侧对齐用。
PROMOTED_TOOL_NAMES = tuple(f"{_MCP_PREFIX}{n}" for n in _PROMOTE)


def _patch_p43_promote_catfish_core_tools() -> None:
    """把 _PROMOTE 里的工具加进 toolsets._HERMES_CORE_TOOLS (重新绑定)。"""
    try:
        import toolsets  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        # toolsets.py 顶部只 import typing, 不碰 registry / model_tools,
        # 所以不会撞 6/1 那个 circular import (见 plugin.py 的
        # BL-PLUGIN-HERMES-015-LAZY-INSTALL 长注释)。真挂了是别的原因。
        logger.warning("P43 skip: import toolsets 失败 (%s)", e)
        return

    current = getattr(toolsets, "_HERMES_CORE_TOOLS", None)
    if not isinstance(current, list):
        logger.warning(
            "P43 skip: toolsets._HERMES_CORE_TOOLS 不是 list (实际 %s) —— "
            "hermes 可能改了结构, 不硬改",
            type(current).__name__,
        )
        return

    added = [n for n in PROMOTED_TOOL_NAMES if n not in current]
    if not added:
        logger.debug("P43: 目标工具已在 _HERMES_CORE_TOOLS 里, noop")
        return

    # ⚠ 重新绑定, **不是** current.extend(added) —— 见模块 docstring 约束 1。
    toolsets._HERMES_CORE_TOOLS = list(current) + added

    logger.info(
        "P43 ✓ %d 个 catfish 工具提升为 hermes 核心 (不再被 tool_search defer): %s",
        len(added),
        ", ".join(n[len(_MCP_PREFIX):] for n in added),
    )
