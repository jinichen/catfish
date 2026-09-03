"""P45 (8/19): 被 defer 的工具被叫到时, 别猜成另一个工具。

# 病

鸿波说「固化 eis-login SKILL」, 小鲶连发六次 `catfish_browser_fill`, 参数却是
`{name, namespace, description, overwrite, target}` —— 那是 `catfish_freeze_skill`
的参数。工具返 `'selector' is a required property`, 模型看不懂, 再发一次, 循环。

看着像模型犯傻。不是。

# 真因: 渐进式披露 + 模糊改名, 两个各自合理的机制撞在一起

hermes 0.20 的 `tool_search` 把 MCP/插件工具收进三个桥
(`tool_search` / `tool_describe` / `tool_call`), 按需检索。catfish 78 个工具全走
MCP 进 hermes, 除了 P43 提升的 11 个, **其余 67 个都被 defer** —— 这是**正确行为**。

而 `agent.valid_tool_names` 是从 `agent.tools`(装配**之后**的可见列表) 派生的
(`tools/mcp_tool.py:6832`), 所以被 defer 的工具不在里面。于是
`conversation_loop.py:5906`:

    if tc.function.name not in agent.valid_tool_names:
        repaired = agent._repair_tool_call(tc.function.name)

命中, 走进 `agent_runtime_helpers.repair_tool_call`。它前面几段精确匹配
(大小写 / 连字符 / CamelCase / `_tool` 后缀) 都是对的, 问题在最后那句兜底:

    matches = get_close_matches(lowered, agent.valid_tool_names, n=1, cutoff=0.7)

**在「可见」列表里模糊匹配一个「被 defer」的名字, 结果一定是另一个工具。**
而 catfish 的工具名共享 28 个字符的前缀 `mcp__catfish_tools__catfish_`, 0.7 这个
阈值在这种名字上形同虚设。8/19 实算 (只在 P43 那 11 个可见工具里比):

    catfish_teach_start   → catfish_search_docs    0.872
    catfish_teach_end     → catfish_search_docs    0.895
    catfish_freeze_skill  → catfish_browser_fill   0.800

**跟当天日志里实际发生的两串一模一样。**

改名是**就地改** `tc.function.name`, 发生在消息落库之前 —— 所以 state.db 里存的
已经是改完的名字, 事后翻记录看到的是"模型自己调错了工具"。

# 为什么这比"少个工具"严重

它不报错、不生效、还**干了别的事**。模型拿到的是一个跟自己意图无关的工具的
schema 错误, 没有任何线索指回"我调的那个其实被 defer 了"。跟 8/19 那天另外几个
故障 (改 ACL 静默无效 / 密码框空转按钮 / cap 白名单 no-op) 是同一个形状:
**永远成功, 正确性无声地丢失**。

# 判据

只拦一种情况: **已注册、但本次被 defer** 的名字。用上游自己的
`tool_search.is_deferrable_tool_name()`, 不另写一份:

    没注册 (entry is None)  → False → 幻觉名字照常走模糊修复 (那本来就该修)
    core 工具               → False → P43 提升过的 11 个照常走 (它们可见)
    已注册的 MCP/插件工具   → True  → 就是它, 不许猜

对这一类, `repair_tool_call` 返 None, 然后错误消息告诉模型走 `tool_call`。
`tool_call` 本来就能调到它 —— 路一直是通的, 只是没人告诉模型往那儿走。

# 为什么不是"把 teach/freeze 也加进 P43"

那是 A 方案, 只救三个。剩下 67 个被 defer 的 catfish 工具, 每一个被叫到时都会
被改成某个可见的邻居。而 P43 的名单从 4 个长到 11 个, 每次都是等员工先撞一次。
这个 patch 一次护住全部, 而且新加工具自动受保护。

# 边界

  · **不复制上游逻辑**。包一层 `repair_tool_call`, 只在它给出结果之后判断要不要
    采信 —— 上游哪天改了修复策略, 这里跟着走, 不会漂。
  · 精确匹配 (大小写 / 分隔符) 仍然放行: 那不是"猜成别的工具", 是同一个名字。
  · 两个 patch 点都是**晚绑定**, 所以 monkeypatch 有效:
      `run_agent.py:4617`  方法内 `from agent.agent_runtime_helpers import ...`
      `conversation_loop.py:5979/6224`  模块级名字, 调用时才查
  · 装在 `~/.hermes/plugins/`(软链到 repo), **在 hermes-agent 树之外** ——
    升级换整棵树不碰它 (见 scripts/upgrade-hermes-v020.sh 的搬运清单)。
    锚点漂了由 `audit_hermes_compat.sh` 在换名之前拦下。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

logger = logging.getLogger("catfish.xcatfish_user.plugin")

#: 原函数, 打补丁时存下来。用于幂等判断 + 包一层调用。
_ORIG_REPAIR = None
_ORIG_INVALID_CONTENT = None
_ORIG_RESOLVE = None


def _is_deferred_but_registered(agent, name: str) -> bool:
    """这个名字是不是「已注册、但本次没发给模型」。

    两个条件都要:
      · 不在 valid_tool_names —— 本次不可见 (调用方已经判过, 这里防独立调用)
      · is_deferrable_tool_name() 为真 —— 已注册且不是 core

    拿不到 tool_search 模块 (hermes 版本没这功能) → 一律返 False, 退回上游行为。
    """
    try:
        valid = getattr(agent, "valid_tool_names", None) or ()
        if name in valid:
            return False
        from tools.tool_search import is_deferrable_tool_name  # noqa: PLC0415
        return bool(is_deferrable_tool_name(name))
    except Exception:  # noqa: BLE001
        return False


def _patch_p45_no_fuzzy_repair_for_deferred() -> None:
    """包一层 repair_tool_call: 被 defer 的名字不许被模糊改成别的工具。"""
    global _ORIG_REPAIR
    try:
        from agent import agent_runtime_helpers as arh  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        logger.warning("P45 skip: import agent_runtime_helpers 失败 (%s)", e)
        return

    orig = getattr(arh, "repair_tool_call", None)
    if not callable(orig):
        logger.warning(
            "P45 skip: agent_runtime_helpers.repair_tool_call 不存在或不可调用 "
            "(实际 %s) —— hermes 可能改了结构, 不硬改",
            type(orig).__name__,
        )
        return
    if getattr(orig, "_catfish_p45", False):
        logger.debug("P45: repair_tool_call 已打过补丁, noop")
        return

    _ORIG_REPAIR = orig

    def repair_tool_call(agent, tool_name: str):  # noqa: ANN001, ANN202
        repaired = orig(agent, tool_name)
        if not repaired or not _is_deferred_but_registered(agent, tool_name):
            return repaired
        # 走到这儿, repaired **必然是另一个工具**, 不可能是"同名的不同写法":
        #   守卫只在 tool_name **精确**命中 registry 且不在 valid_tool_names 时启动,
        #   而上游的几段精确匹配 (小写 / 分隔符 / CamelCase / _tool 后缀) 找的都是
        #   valid_tool_names —— 它不在里面, 那几段必然全落空, 只剩最后的模糊兜底。
        #
        # 第一版这里还有一层 `_norm_for_compare(repaired) == _norm_for_compare(name)`
        # 的"同名放行", 变异测试打它时两条都跑绿 —— 因为那个分支**根本到不了**。
        # 死代码删掉, 免得下一个人以为这里还有一道保护。
        logger.info(
            "P45 拦下一次跨工具改名: %r 被 defer, 上游想改成 %r —— 已拒绝, "
            "改为提示模型走 tool_call",
            tool_name, repaired,
        )
        return None

    repair_tool_call._catfish_p45 = True  # noqa: SLF001
    arh.repair_tool_call = repair_tool_call
    logger.info("P45 ✓ 被 defer 的工具不会再被模糊改名成另一个工具")


def _patch_p45_deferred_tool_error_hint() -> None:
    """把「不存在」的错误消息, 对被 defer 的工具换成「用 tool_call 调」。

    不改这里的话, 模型收到的是
        Tool 'mcp__catfish_tools__catfish_freeze_skill' does not exist.
        Available tools: ...
    —— 而它**存在**, 只是这一轮没直接发给它。照着这句话, 模型只会去列表里另找
    一个, 或者放弃。8/19 那次它就是这么绕了六轮。
    """
    global _ORIG_INVALID_CONTENT
    try:
        from agent import conversation_loop as cl  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        logger.warning("P45 skip: import conversation_loop 失败 (%s)", e)
        return

    orig = getattr(cl, "_invalid_tool_name_error_content", None)
    if not callable(orig):
        logger.warning(
            "P45 skip: conversation_loop._invalid_tool_name_error_content 不存在 "
            "—— hermes 可能改了结构, 不硬改"
        )
        return
    if getattr(orig, "_catfish_p45", False):
        logger.debug("P45: _invalid_tool_name_error_content 已打过补丁, noop")
        return

    _ORIG_INVALID_CONTENT = orig

    def _invalid_tool_name_error_content(name, valid_tool_names):  # noqa: ANN001, ANN202
        try:
            from tools.tool_search import (  # noqa: PLC0415
                TOOL_CALL_NAME,
                is_deferrable_tool_name,
            )
            if (name or "").strip() and is_deferrable_tool_name(name):
                return (
                    f"Tool '{name}' exists but was not sent in this turn's tool "
                    f"list (progressive disclosure). It is still callable — invoke "
                    f"it via `{TOOL_CALL_NAME}`:\n"
                    f'  {TOOL_CALL_NAME}(name="{name}", arguments={{...}})\n'
                    f"Do NOT substitute a different tool from the visible list."
                )
        except Exception:  # noqa: BLE001
            pass
        return orig(name, valid_tool_names)

    _invalid_tool_name_error_content._catfish_p45 = True  # noqa: SLF001
    cl._invalid_tool_name_error_content = _invalid_tool_name_error_content
    logger.info("P45 ✓ 被 defer 的工具报错改成指向 tool_call")


def _normalize_bridge_args(args):  # noqa: ANN001, ANN202
    """修正模型偶发生成的单层嵌套 ``tool_call`` 参数。

    正确形态是 ``{name, arguments}``，但 9/2 的真实请求里出现过
    ``{arguments: {name, arguments}}``。这里只做可证明的一层解包：没有顶层
    name、下一层明确有 name 才修；不猜工具名、不递归，也不覆盖正确参数。
    """
    if not isinstance(args, Mapping):
        return args
    if str(args.get("name") or "").strip():
        return args
    nested = args.get("arguments")
    if not isinstance(nested, Mapping):
        return args
    nested_name = str(nested.get("name") or "").strip()
    if not nested_name:
        return args

    normalized = dict(args)
    normalized["name"] = nested_name
    normalized["arguments"] = nested.get("arguments", {})
    return normalized


def _patch_p48_tool_call_argument_shape() -> None:
    """让 bridge 接受模型多包一层的参数，但继续由上游做全部校验。"""
    global _ORIG_RESOLVE
    try:
        from tools import tool_search as ts  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        logger.warning("P48 skip: import tools.tool_search 失败 (%s)", e)
        return

    orig = getattr(ts, "resolve_underlying_call", None)
    if not callable(orig):
        logger.warning(
            "P48 skip: tools.tool_search.resolve_underlying_call 不存在或不可调用 "
            "(实际 %s) —— hermes 可能改了结构, 不硬改",
            type(orig).__name__,
        )
        return
    if getattr(orig, "_catfish_p48", False):
        logger.debug("P48: resolve_underlying_call 已打过补丁, noop")
        return

    _ORIG_RESOLVE = orig

    def resolve_underlying_call(args):  # noqa: ANN001, ANN202
        normalized = _normalize_bridge_args(args)
        if normalized is not args:
            logger.info("P48 修正一次单层嵌套的 tool_call 参数: name=%r", normalized["name"])
        return orig(normalized)

    resolve_underlying_call._catfish_p48 = True  # noqa: SLF001
    ts.resolve_underlying_call = resolve_underlying_call
    logger.info("P48 ✓ tool_call 单层嵌套参数会在上游校验前自动纠形")


def install() -> None:
    """兼容补丁一起装。任一失败只 warn, 不阻塞 hermes 启动。"""
    _patch_p45_no_fuzzy_repair_for_deferred()
    _patch_p45_deferred_tool_error_hint()
    _patch_p48_tool_call_argument_shape()
