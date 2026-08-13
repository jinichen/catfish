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
#   现在 29 visible + 3 bridge = 32, gateway cap 40 → 还剩 8 个
#   加这 4 个 → 36, 余 4
# 再往上加要先确认 cap 抬得动: 注释里写着 Qwen 122B 实测 50+ tools 撞空 400。
_PROMOTE = (
    "catfish_wiki_search",     # 知识库检索 —— 8/13 员工撞的就是它
    "catfish_search_docs",     # 本地文档检索, 跟上面是一对
    "catfish_today_summary",   # 今日 TODO / 邮件 / 日程汇总
    "catfish_email_search",    # 邮件查询
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
