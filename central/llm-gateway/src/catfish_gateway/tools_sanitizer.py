"""客户端发来的 OpenAI tools 数组防御性清洗。

为啥单独一文件:
    历史一次坑 (2026-04-26): Companion 早期版本把 tool-bridge 的 input_schema
    误当成整个 function 字段, 发出去的 tool 缺 name —— Qwen / OpenAI 路径宽容
    不报错, 但 LiteLLM 转 Gemini functionDeclarations 时直接 `KeyError: 'name'`
    把整个聊天 500。

    第二次坑 (2026-05-04 鸿波加 deepseek-v4-flash): DeepSeek 严格校验
    parameters schema, type 是 None 或缺 type 直接 400 BadRequest:
        "Invalid schema for function 'browser_back': schema must be a JSON
         Schema of 'type: \"object\"', got 'type: null'."
    OpenAI / Qwen / Gemini 容忍这种 (默认当 object), DeepSeek 不容忍.
    所以 sanitizer 必须把 parameters.type 缺/None 的统一兜底成 "object".

    第三次坑 (2026-05-08 BL-FIX4 EIS demo): hermes builtin browser_* (browser_back /
    browser_cdp / browser_click / browser_vision / ...) 跟 catfish_browser_* 同时
    暴露给 LLM, namespace 撞. LLM 走 hermes browser_vision (训练分布里这个 tool
    最常见), 但 catfish 这边没配 vision provider → tool 返 "No LLM provider
    configured", LLM 懵掉; 接着 hermes browser_back 等 schema 不规整, 撞 deepseek/
    qwen 严格校验 → 整轮 BadRequest 400. 解法: 看到任何 catfish_browser_* 就把
    hermes builtin browser_* (不含 catfish_ 前缀的) **一律丢**, LLM 只看一套.

    根因当然是客户端 schema 错, 但网关该兜底 —— 一个畸形 tool 不该掀翻整个请求。
    我们丢掉坏的, 留下能用的, 在日志里点名让客户端开发自己看。

OpenAI 标准:
    {"type": "function",
     "function": {"name": "...", "description": "...", "parameters": <schema>}}

最低要求:
    - 顶层 type == "function"
    - function.name 是非空字符串
    - function.parameters 至少能 dict-cast (没有就给个 empty object schema)
    - function.parameters.type 必须是 "object" (DeepSeek 严格校验, 5/4 加)
    - function.parameters.properties 至少是 {} (没 properties 也得有空 dict)
    - 如果有 catfish_browser_*, 同时丢所有不带 catfish_ 前缀的 browser_* (5/8 加)
"""
from __future__ import annotations

import logging
import os
from typing import Any

# 5/22 拆分 (老文件 808 > 800 红线): frozenset 常量 / source profile 表全搬走,
# 这里 re-export 保 import 兼容 — 老 caller `from .tools_sanitizer import _ALWAYS_ON_TOOLS`
# 仍能拿到值. 跟 catfish_tools.py / adapter.py 5/20 拆分用的 touchstone 协议同款.
from .tools_sanitizer_constants import (  # noqa: F401  re-export 保 caller 不变
    ALWAYS_ON_TOOLS as _ALWAYS_ON_TOOLS,
    CATFISH_BROWSER_PREFIX as _CATFISH_BROWSER_PREFIX,
    DEFAULT_MAX_TOOLS as _DEFAULT_MAX_TOOLS,
    ENV_MAX_TOOLS as _ENV_MAX_TOOLS,
    HERMES_BROWSER_PREFIX as _HERMES_BROWSER_PREFIX,
    HIDDEN_FROM_LLM as _HIDDEN_FROM_LLM,
    MCP_CATFISH_PREFIX as _MCP_CATFISH_PREFIX,
    SOURCE_NATIVE_TOOLS as _SOURCE_NATIVE_TOOLS,
    SOURCE_TOOL_PROFILES as _SOURCE_TOOL_PROFILES,
    is_always_on as _is_always_on,
    is_hidden_from_llm as _is_hidden_from_llm,
    pinned_tool_names as _pinned_tool_names,
)

logger = logging.getLogger("catfish.gateway.tools_sanitizer")



def _cap_tools_by_priority(tools: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """按 priority 重排 + cap tools.

    BL-WEB-ALWAYS-ON (5/25 鸿波): **永远** 把 always-on 排到前面, 不只 cap 时.
    动机: hermes 按字母序发 tool, web_search 排倒数 → 模型位置偏置不选 → 退化用
    catfish_browser_* 抓页面 (慢 30 倍). 永远前置让 always-on 一族在第 1-20 位,
    模型 attention 优先看到.

    重排 + cap 策略:
      1. 扫一遍, 分 always_on / other 两堆 (always_on 用 _is_always_on, 同时认
         裸名 `catfish_today_summary` 和 MCP 包装名 `mcp_catfish_tools_*`).
      2. 输出 = always_on + other, **永远** 这个顺序, 无视是否超 cap.
      3. 超 cap (always_on + other > max) 才从 other 末尾砍.
      4. always_on 自己绝不被 cap drop (它们是 LLM agent loop 底座).

    返 (reordered_kept, dropped_names). dropped_names 空 = 没砍 (但可能重排了).
    """
    try:
        max_tools = int(os.environ.get(_ENV_MAX_TOOLS, str(_DEFAULT_MAX_TOOLS)))
    except ValueError:
        max_tools = _DEFAULT_MAX_TOOLS
    max_tools = max(10, max_tools)  # 兜底, 不允许 < 10 (always-on 都装不下)

    # 分堆 — always_on 一类, 其他一类. 都保 caller 给的相对顺序.
    always_on_kept: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    for t in tools:
        if not isinstance(t, dict):
            other.append(t)
            continue
        fn = t.get("function")
        name = fn.get("name") if isinstance(fn, dict) else ""
        if _is_always_on(name):
            always_on_kept.append(t)
        else:
            other.append(t)

    # 算要不要砍 other 尾部 (always_on 永不砍, 哪怕超 cap)
    remaining_slots = max(0, max_tools - len(always_on_kept))
    other_kept = other[:remaining_slots]
    dropped = other[remaining_slots:]

    dropped_names: list[str] = []
    for t in dropped:
        if isinstance(t, dict):
            fn = t.get("function")
            name = fn.get("name") if isinstance(fn, dict) else "<unknown>"
            dropped_names.append(str(name) if name else "<unknown>")

    return always_on_kept + other_kept, dropped_names


def _filter_by_source_profile(
    tools: list[dict[str, Any]],
    source_hint: str,
    pinned: frozenset[str] = frozenset(),
) -> tuple[list[dict[str, Any]], list[str]]:
    """5/22 BL-TOOL-PROFILE: 按 source_hint 把 tool 列表砍到该 source 的白名单.

    规则:
      - source_hint 不在 _SOURCE_TOOL_PROFILES → 不过滤, 返原列表 (员工正常会话
        走这条: companion-chat / unknown / plugin:* 一律不受影响)
      - 在表里:
        * hermes 原生 (归一化后的裸名) → 只保 _SOURCE_NATIVE_TOOLS 白名单里的
        * 员工自装 MCP → 后台 source 不注入；普通会话不在 profile 表里，仍不受影响
        * catfish_* → 只保 profile 显式白名单（always-on 只影响普通会话的 cap）

    ── 8/24 修的两个 bug (BL-ADVISOR-NATIVE-LEAK), 都属「不会失败, 也不会生效」──

    (1) 判据认不出真名。catfish 工具经 MCP wrapper 暴露给 hermes 的注册名是
        `mcp__catfish_tools__catfish_check_compliance`, **裸名从来不出现** (8/24
        查 agent.log: 26/26、10/10 全是包装名)。老判据 `name.startswith("catfish_")`
        对包装名为 False → 全从「非 catfish_ 前缀不动」那条溜过去。也就是说这份
        profile 白名单从 5/22 写下到 8/24, **一次都没真正裁剪过任何 catfish 工具**
        —— 既没按白名单保留, 也没按白名单砍, 是判据压根没对上真实工具名。
        修法: 先 strip `mcp__catfish_tools__` 归一化再判断 (跟 is_always_on /
        is_hidden_from_llm 的做法对齐, 那两个 5/25、6/22 已经各踩过一次同款坑)。

    (2) 原生工具整族绕过。老规则「hermes builtin 不动」的理由写的是"它们跟 catfish
        profile 无关" —— 但 execute_code / write_file / patch / process /
        delegate_task 跟「参谋不代行」这条红线关系极大。8/24 实盘 advisor 就是在
        execute_code 上连打 13 次打到上游 400。
        修法: 原生走 _SOURCE_NATIVE_TOOLS 白名单。

    ⚠ 顺序: 原生分支**必须在 always-on 之前**。execute_code / write_file / patch /
      process / delegate_task 全都在 ALWAYS_ON_TOOLS 里, 放到 always-on 之后等于
      没写 —— 白名单会被 always-on 抢先放行, 测试也照样绿。

    用法:
      kept, dropped = _filter_by_source_profile(cleaned, source_hint)
    """
    if source_hint not in _SOURCE_TOOL_PROFILES:
        return tools, []

    allowed_catfish = _SOURCE_TOOL_PROFILES[source_hint]
    # 缺表项按空集 = 全拒。配套一致性测试保证不会静默走到这个默认值上,
    # 但真漏了时"少个工具"比"多个执行能力"安全。
    allowed_native = _SOURCE_NATIVE_TOOLS.get(source_hint, frozenset())
    kept: list[dict[str, Any]] = []
    dropped: list[str] = []

    for t in tools:
        if not isinstance(t, dict):
            kept.append(t)
            continue
        fn = t.get("function")
        name = fn.get("name") if isinstance(fn, dict) else None
        if not isinstance(name, str):
            kept.append(t)
            continue

        # 归一化: `mcp__catfish_tools__catfish_x` 与裸名 `catfish_x` 是同一个工具
        # 的两种暴露方式, 必须走同一条分支 (bug 1)。
        base = (
            name[len(_MCP_CATFISH_PREFIX):]
            if name.startswith(_MCP_CATFISH_PREFIX)
            else name
        )

        # ── caller 用 tool_choice 点名的工具 → 谁都不许砍 ──
        # 排在所有规则最前面: 砍掉被点名的工具, 这次请求必然废掉 (tool_choice
        # 指向一个不存在的名字), 没有任何情况下这是对的。
        # 8/24 实盘: companion-profile 那一发只带 submit_profile 一个工具, 被
        # 下面的原生分支砍光, 画像识别静默失效。详见 pinned_tool_names()。
        if name in pinned or base in pinned:
            kept.append(t)
            continue

        # ── 员工自装 MCP → 后台 source 不注入 ──
        # 普通 companion-chat 不在 profile 表里，已经在函数入口原样返回；只有
        # advisor/profile 等无人值守调用走到这里，不能让任意外部 MCP 扩大能力面。
        if base.startswith("mcp__"):
            dropped.append(name)
            continue

        # ── hermes 原生 → 查该 source 的原生白名单 (bug 2) ──
        # ⚠ 必须在 always-on 之前, 理由见 docstring。
        if not base.startswith("catfish_"):
            if base in allowed_native:
                kept.append(t)
            else:
                dropped.append(name)
            continue

        # ── catfish_* → 后台 source 只认显式白名单 ──
        # always-on 是普通会话做 tool cap 时的优先级，不是绕过 source 能力边界的
        # 通行证。否则 browser / reminders / email 等都会重新漏进 advisor。
        if base in allowed_catfish:
            kept.append(t)
        else:
            dropped.append(name)

    return kept, dropped


def _has_catfish_browser_tools(tools: list[Any]) -> bool:
    """看 tools 里有没有任何 catfish_browser_* — 有则触发 hermes 去重."""
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function")
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if isinstance(name, str) and name.startswith(_CATFISH_BROWSER_PREFIX):
            return True
    return False


# ── P3.5.72 (6/22 鸿波 catch "继续 architecture 治本") ─────────────────
#
# catfish 平台层 execute_code description 合同. 覆盖 hermes 上游自带 description
# (后者写"single tool call → use normal tool calls" 暗示用 terminal). catfish
# 砍了 terminal, LLM 必须知道 execute_code 是唯一合法执行通道.
#
# 改 tool schema description (LLM 决策硬依据), 不改 hermes 源码 (never fork) 也不
# 改 SOUL.md (避免 prompt 越堆越乱). 这是 catfish 平台层 contract override.
#
# trigger: name == "execute_code" 或 "mcp_catfish_tools_execute_code" 都改.

_EXECUTE_CODE_CATFISH_DESCRIPTION = (
    # P3.5.72.1 (6/22 鸿波 catch "LLM 还在说审批限制") : 砍掉所有 "审批 / 安全
    # / 沙箱 / 限制" 字眼. LLM 看到这些字眼联想到 "我不该轻易调用". 审批是 catfish
    # 平台自动加的, LLM 不需要管. description 只说调用方式 + 例子, LLM 直接 emit
    # tool_call.
    "Execute a Python script. Use for any user request involving: "
    "running shell commands, listing files, checking versions, computing values, "
    "querying state, transforming files, batch processing, or multi-step logic.\n\n"
    "Examples (just emit tool_call(execute_code, code=...)):\n"
    "- user 'ls' → code=\"import os; print('\\n'.join(os.listdir('.')))\"\n"
    "- user 'rm /tmp/x' → code=\"import os; os.remove('/tmp/x'); print('done')\"\n"
    "- user '查 litellm 版本' → code=\"import litellm; print(litellm.__version__)\"\n"
    "- user '算 1+2+...+100' → code=\"print(sum(range(1, 101)))\"\n"
    "- user '看 README' → code=\"print(open('README.md').read())\"\n\n"
    "Behavior:\n"
    "- Always emit the tool_call directly. Don't ask the user to run it themselves.\n"
    "- Don't preface with 'I cannot' / 'safety' / 'restriction' / 'permission'. "
    "The platform handles policy transparently to you — just call.\n"
    "- If sandbox env differs from user's local (e.g. installed package versions), "
    "call first to get sandbox value, then tell user the difference.\n\n"
    "code (str, required): Python source. Print results to stdout.\n"
    "task_id (str, optional): session task id for tracking long tasks.\n"
    "enabled_tools (list, optional): subset of allowed in-sandbox tool names."
)


def _rewrite_execute_code_description(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """P3.5.72: 把 execute_code 的 description 改写成 catfish 平台合同.

    LLM 决策选 tool 主要看 tool schema description, 不是 SOUL.md system prompt.
    hermes 上游 execute_code description 假定有 terminal tool (hermes 默认环境),
    catfish 砍了 terminal 后 LLM 看 hermes description 困惑 → 拒绝 user shell 请求.
    这里平台层覆盖 description, 明确告知 LLM execute_code 是 catfish 唯一通道.

    不动 hermes 源 (never fork). 不动 SOUL.md (不堆 prompt). 改的是 tool schema
    硬合同, 是 LLM 决策的最硬依据.
    """
    rewritten_count = 0
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function")
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if not isinstance(name, str):
            continue
        # 同时认裸名 execute_code 和 MCP 前缀版本
        base_name = name
        if name.startswith(_MCP_CATFISH_PREFIX):
            base_name = name[len(_MCP_CATFISH_PREFIX):]
        if base_name == "execute_code":
            fn["description"] = _EXECUTE_CODE_CATFISH_DESCRIPTION
            rewritten_count += 1

    if rewritten_count > 0:
        logger.info(
            "P3.5.72 execute_code description rewritten ×%d "
            "(catfish 平台合同覆盖 hermes 上游, LLM 看到 execute_code 是唯一执行通道)",
            rewritten_count,
        )
    return tools


def sanitize_tools(
    body: dict[str, Any],
    user: Any = None,
    source_hint: str = "unknown",
) -> dict[str, Any]:
    """原地修 body["tools"] —— 丢畸形条目, 修补能补的字段。

    返回原 body (mutate in-place + return), 调用方习惯链式。
    body 里没 tools / 不是 list / 空数组 都直接返回, 不报错。

    BL-RBAC-DAY4 (5/17): 可选 user 参. 给了就按 user.can_use_tool() 过滤
    白名单, ALWAYS_ON_TOOLS 永远保留 (LLM agent loop 底座). user=None 则
    不做 RBAC 过滤 (兼容老 caller / 内部 loopback / 测试).

    BL-TOOL-PROFILE (5/22 鸿波): source_hint 在 _SOURCE_TOOL_PROFILES 里 → 按
    profile 砍 catfish_* tool. unknown / 未知 source → 不过滤 (现有行为).
    """
    tools = body.get("tools")

    # P3.5.70 diag (6/22): 入口 log 看真实 body["tools"] 完整内容. 上次 first10
    # 全是 browser_*, 看不到 11-31 有没 execute_code. 改 dump 全 list.
    if isinstance(tools, list):
        all_names = []
        for t in tools:
            if isinstance(t, dict):
                fn = t.get("function") or {}
                if isinstance(fn, dict):
                    all_names.append(fn.get("name", "?"))
        logger.info(
            "P3.5.70 sanitize entry: source=%s tools_count=%d "
            "execute_code_present=%s terminal_present=%s "
            "ALL=%s",
            source_hint, len(tools),
            "execute_code" in all_names,
            "terminal" in all_names,
            all_names,
        )
    else:
        logger.info(
            "P3.5.70 sanitize entry: source=%s body['tools']=%r (not list, early return)",
            source_hint, type(tools).__name__ if tools is not None else None,
        )

    if not isinstance(tools, list):
        return body

    cleaned: list[dict[str, Any]] = []
    dropped: list[str] = []
    fixed: list[str] = []

    # BL-FIX4 (5/8): 先扫一遍, 看有没有 catfish_browser_*. 有就触发去重 hermes 一族.
    has_catfish_browsers = _has_catfish_browser_tools(tools)
    deduped_hermes_browser: list[str] = []

    for idx, tool in enumerate(tools):
        if not isinstance(tool, dict):
            dropped.append(f"#{idx}(非 dict)")
            continue

        if tool.get("type") != "function":
            dropped.append(f"#{idx}(type!=function: {tool.get('type')!r})")
            continue

        fn = tool.get("function")
        if not isinstance(fn, dict):
            dropped.append(f"#{idx}(function 缺失或非 dict)")
            continue

        name = fn.get("name")
        if not isinstance(name, str) or not name.strip():
            dropped.append(f"#{idx}(function.name 非法: {name!r})")
            continue

        # BL-MEMORY-CATFISH-REMEMBER-BLACKLIST (5/16 鸿波 A) + P3.5.70 (6/22 鸿波 catch):
        # 永不暴露 catfish_remember / catfish_memory_dedupe / catfish_memory_compress /
        # terminal / read_terminal 给 LLM. 釜底抽薪 — 没选择只能走替代通道
        # (memory → hermes builtin memory; terminal → execute_code 沙箱+审批).
        #
        # P3.5.70 修了老 bug: 老 `name in _HIDDEN_FROM_LLM` 只看裸名, 不处理 MCP 前缀,
        # `mcp_catfish_tools_terminal` 这种带前缀的不被 filter 仍暴露给 LLM. 改用
        # is_hidden_from_llm() helper (跟 is_always_on() 对称 prefix 剥).
        #
        # env override (调试 / dept 信任高需 LLM 直调):
        #   CATFISH_EXPOSE_REMEMBER=1 → 恢复 catfish_remember + memory_dedupe + memory_compress
        #   CATFISH_EXPOSE_TERMINAL=1 → 恢复 terminal + read_terminal
        import os  # noqa: PLC0415
        if _is_hidden_from_llm(name):
            # 两个独立 env override — memory / terminal 各自决策
            base_name = name[len(_MCP_CATFISH_PREFIX):] if name.startswith(_MCP_CATFISH_PREFIX) else name
            is_memory_hidden = base_name in {"catfish_remember", "catfish_memory_dedupe", "catfish_memory_compress"}
            is_terminal_hidden = base_name in {"terminal", "read_terminal"}
            if is_memory_hidden and os.environ.get("CATFISH_EXPOSE_REMEMBER", "0") == "1":
                pass  # env override → 继续 expose, 不丢
            elif is_terminal_hidden and os.environ.get("CATFISH_EXPOSE_TERMINAL", "0") == "1":
                pass  # env override → 继续 expose, 不丢
            else:
                tag = "P3.5.70-LLM-NO-DIRECT-SHELL" if is_terminal_hidden else "BL-MEMORY-CATFISH-REMEMBER-BLACKLIST"
                dropped.append(f"#{idx}(name={name}: {tag}, 强制走替代通道)")
                continue

        # BL-FIX4: 撞 hermes builtin browser_* — 跟 catfish_browser_* 同时存在时丢
        # ("browser_" 开头 + 不带 "catfish_" 前缀)
        if (
            has_catfish_browsers
            and name.startswith(_HERMES_BROWSER_PREFIX)
            and not name.startswith(_CATFISH_BROWSER_PREFIX)
        ):
            deduped_hermes_browser.append(name)
            continue

        # parameters 没设 / 不是 dict → 补默认 empty object schema
        # Gemini 严格要求 parameters 是 object-shaped, 没有的话其 mapper 也会出错
        params = fn.get("parameters")
        if not isinstance(params, dict):
            fn["parameters"] = {"type": "object", "properties": {}}
            fixed.append(f"#{idx}(name={name}: 补默认 parameters)")
        else:
            # parameters 是 dict 但 type 缺 / None → 强制 "object"
            # 5/4 鸿波加 deepseek-v4-flash 时 browser_back 撞过, DeepSeek 严格校验
            params_type = params.get("type")
            if params_type is None or params_type == "":
                params["type"] = "object"
                fixed.append(f"#{idx}(name={name}: parameters.type 缺/None → 'object')")
            elif params_type != "object":
                # 异常情况: 客户端写了非 'object' 的 type (e.g. 'string') — 不动它,
                # 但 log warn, 后端 provider 可能拒. 不丢工具因为还是可能跑通.
                logger.warning(
                    "tool '%s' parameters.type=%r 非 'object', 各 provider 兼容性差",
                    name, params_type,
                )
            # parameters 有 type='object' 但缺 properties → 加空 dict
            # OpenAI 标准里 object 类型 schema 应该有 properties 字段
            if params.get("type") == "object" and "properties" not in params:
                params["properties"] = {}
                fixed.append(f"#{idx}(name={name}: parameters.properties 缺 → {{}})")

        cleaned.append(tool)

    if dropped:
        logger.warning(
            "tools sanitizer dropped %d/%d: %s",
            len(dropped), len(tools), "; ".join(dropped[:5]),
        )
    if fixed:
        logger.info(
            "tools sanitizer fixed %d/%d: %s",
            len(fixed), len(tools), "; ".join(fixed[:5]),
        )
    if deduped_hermes_browser:
        logger.info(
            "BL-FIX4 tools sanitizer deduped %d hermes builtin browser_* "
            "(catfish_browser_* 已暴露, 防 namespace 撞 / browser_vision 失败 / "
            "schema 撞 deepseek-v4): %s",
            len(deduped_hermes_browser),
            ", ".join(sorted(deduped_hermes_browser)[:8]),
        )

    # BL-RBAC-DAY4 (5/17): per-user/dept allowed_tools 白名单过滤.
    # user.effective_allowed_tools 空 = 全允许 (开放默认 / 无 dept 配置).
    # 非空 = 收紧, 只允许列出 tool. ALWAYS_ON_TOOLS 永远保留 (LLM agent loop 底座).
    # sysadmin 永远绕过 (User.can_use_tool 已实现).
    rbac_dropped: list[str] = []
    if user is not None and hasattr(user, "can_use_tool"):
        rbac_kept: list[dict[str, Any]] = []
        for t in cleaned:
            if not isinstance(t, dict):
                rbac_kept.append(t)
                continue
            fn = t.get("function")
            nm = fn.get("name") if isinstance(fn, dict) else None
            if not isinstance(nm, str):
                rbac_kept.append(t)
                continue
            # ALWAYS_ON_TOOLS 兜底: 不论 RBAC 怎么收紧都保留 LLM 底座
            # (用 _is_always_on 同时认裸名 + mcp_catfish_tools_ 前缀, BL-MCP-PREFIX-FIX)
            if _is_always_on(nm):
                rbac_kept.append(t)
                continue
            if user.can_use_tool(nm):
                rbac_kept.append(t)
            else:
                rbac_dropped.append(nm)
        if rbac_dropped:
            logger.info(
                "BL-RBAC-DAY4: drop %d tools per user.allowed_tools "
                "(sub=%s dept=%s effective_allowed_tools=%d): %s",
                len(rbac_dropped),
                getattr(user, "sub", "?"),
                getattr(user, "department", "?"),
                len(getattr(user, "effective_allowed_tools", []) or []),
                ", ".join(rbac_dropped[:10]),
            )
        cleaned = rbac_kept

    # P3.5.161 (7/3 鸿波): 老 _audit_unknown_tools 已删 — hermes v0.18
    # tools/registry.py:395-408 override 默认 REJECT + PermissionError
    # (需 operator 显式 plugins.entries.<pid>.allow_tool_override: true opt-in),
    # 上游本身防住 #26759 tool_override CVE, catfish 侧第二道防线彻底冗余.
    # 详见 CHANGELOG P3.5.161.

    # 5/22 BL-TOOL-PROFILE 鸿波: 按 source_hint 砍 catfish_* 到 profile 白名单.
    # 在 RBAC + cap 之前砍 — 优先级是: 畸形 > RBAC > profile > cap. profile 砍掉
    # 的是 caller 不需要的 (caller 是 advisor 不会用 freeze_*), 不是权限问题.
    profile_kept, profile_dropped = _filter_by_source_profile(
        cleaned, source_hint, _pinned_tool_names(body),
    )
    if profile_dropped:
        logger.info(
            "BL-TOOL-PROFILE: source=%s 砍 %d 个非该 source 能力边界的 tool: %s",
            source_hint,
            len(profile_dropped),
            ", ".join(sorted(profile_dropped)[:10]),
        )
    cleaned = profile_kept

    # BL-TOOL-CAP (5/15 鸿波撞 Qwen 122B 83 tools 空 400): 超 cap 时砍低优先级.
    # 实测 Qwen 122B ≥50 tools 就开始撞空 400 (上游无具体错). 保留 always-on 核心
    # + 剩 slot 按顺序填, 超 cap 的 drop. env CATFISH_MAX_TOOLS 调阈值.
    capped_tools, capped_dropped = _cap_tools_by_priority(cleaned)
    if capped_dropped:
        # 5/25 鸿波: 老格式 "41 tools 超上限 110" 让人误以为是"输入 41 超 cap 110"
        # (其实 41 是 dropped count). 改 "dropped=N cap=K kept=M" 一目了然.
        logger.warning(
            "BL-TOOL-CAP: dropped=%d cap=%d kept=%d (always-on 保留, 砍 other 尾巴): %s",
            len(capped_dropped),
            int(os.environ.get(_ENV_MAX_TOOLS, str(_DEFAULT_MAX_TOOLS))),
            len(capped_tools),
            ", ".join(capped_dropped[:10]),
        )
    cleaned = capped_tools

    # ── P3.5.72 (6/22 鸿波 catch "工作台 LLM 看到 execute_code 还是拒绝调") ──
    # 真因 architecture: hermes 上游 execute_code 的 description 是给 hermes 设计的
    # (含 terminal tool 的环境), 写"single tool call no processing → use normal
    # tool calls instead". hermes 里 normal tool = terminal. 但 catfish 砍了
    # terminal (P3.5.70), LLM 看到 description 想用 terminal 替代但找不到 → 拒绝.
    #
    # 治本: rewrite execute_code description, 让 LLM 看到 catfish 平台合同明确:
    #   - catfish 没暴露 terminal (砍了)
    #   - execute_code 是唯一合法执行通道 (任何 shell/Python/查信息)
    #   - 含审批 + 沙箱保护, 员工点批准就放行
    #
    # 这是 tool schema 硬合同层改 (不是 SOUL.md 软提示, 不是 fork hermes 源码),
    # gateway sanitize 是 LLM 看 tool list 前的最后一关 — 改这里 = 改 LLM 决策硬依据.
    cleaned = _rewrite_execute_code_description(cleaned)

    body["tools"] = cleaned

    # BL-MEMORY-PLUMBING-DIAG (5/16): 暴露 always-on 实际命中. 排"LLM 调了 31 个
    # tool 但 ~/.hermes/memories/ 没动" 真因 — 假设上游 list_tools() 没送  # noqa: BOUNDARY (doc reference)
    # memory_save (hermes registry 没注册), sanitizer 看不到也变不出来.
    # 一次聊天 log 出 always-on 实际命中名字, 三秒看清是模型层还是 plumbing 层.
    #
    # P3.5.166 (7/3 鸿波) skip 条件: cleaned 空 = client 没发 tools list, 走 hermes
    # 内部 tool calling (Companion 5/19 起走 hermes 路径不发 tools, chat.ts:255-260
    # "hermes 内部管 tool calling, 拼好结果返"). 这种场景下 memory tool 在 hermes 侧
    # (catfish-memory plugin ctx.register_tool(name='memory', override=True) 5 kind
    # 路由, catfish_memory.py:1209 已 confirm), sanitizer 无 tools list 可扫,
    # 此 diag 不适用. 触发只是老 gateway 路径 (Companion 走 8999 直连 LiteLLM
    # 前端注入 tools) 的 diag 用途.
    if cleaned and source_hint not in _SOURCE_TOOL_PROFILES:
        seen_always_on: list[str] = []
        for t in cleaned:
            if not isinstance(t, dict):
                continue
            fn = t.get("function")
            nm = fn.get("name") if isinstance(fn, dict) else None
            if isinstance(nm, str) and _is_always_on(nm):
                seen_always_on.append(nm)
        # hermes 0.13 只一个 `memory` 工具, 见到就 OK.
        if "memory" not in seen_always_on:
            logger.warning(
                "BL-MEMORY-PLUMBING-DIAG: always-on 缺 `memory` "
                "(hermes registry 没注册 → LLM 看不到 → 永远写不了 hermes memories). "
                "实际命中 always-on (%d): %s",
                len(seen_always_on), sorted(set(seen_always_on)),
            )

    # BL-FIX5 (5/8): 同步扫消息历史 — assistant.tool_calls 里 name 在 deduped 集
    # 合的剔掉, 对应 tool message 一起丢. 防 BL-FIX4 部署前的旧轮次撞 Qwen Go gRPC
    # adapter 的"assistant 调过的 tool name 必须在 tools 列表里"校验 → 空 reason 400.
    # BL-TOOL-CAP 同样的问题: 砍掉的 tool 在历史里有调用 → 撞校验 → 400. 一起 scrub.
    all_dropped_names: set[str] = (
        set(deduped_hermes_browser) | set(capped_dropped) | set(rbac_dropped)
        | set(profile_dropped)
    )
    if all_dropped_names:
        _scrub_messages_for_dropped_tools(body, all_dropped_names)

    return body


def _scrub_messages_for_dropped_tools(
    body: dict[str, Any], dropped_names: set[str]
) -> None:
    """把 messages 历史里 name 在 dropped_names 的 assistant.tool_calls 剔掉,
    对应的 tool message (匹配 tool_call_id) 也丢. 原地修改 body["messages"].

    Qwen Go gRPC adapter 校验 assistant 的 tool_calls.name 必须在 tools 列表里,
    BL-FIX4 dedupe 之后历史里 BL-FIX4 部署前的轮次会撞这个校验 → 空 reason 400.
    这里把残留扫一遍, 所以 BL-FIX4 + BL-FIX5 是一对的.

    不动假设:
      - assistant.content 非空 (含文字) 时, 即便 tool_calls 全 drop 也保留 message
        (留住 assistant 的解释文字)
      - assistant.content 为空 / None 且 tool_calls 全 drop → 整条 assistant message
        丢, 不留空壳
      - tool message 没 tool_call_id (异常) → 不动
      - 不是 assistant / tool 的 message → 不动
    """
    messages = body.get("messages")
    if not isinstance(messages, list):
        return

    dropped_call_ids: set[str] = set()
    scrubbed_assistants = 0
    dropped_assistants = 0

    # 第一遍: 处理 assistant.tool_calls, 收集要丢的 tool_call_id
    new_messages: list[Any] = []
    for msg in messages:
        if not isinstance(msg, dict):
            new_messages.append(msg)
            continue
        if msg.get("role") != "assistant":
            new_messages.append(msg)
            continue

        tool_calls = msg.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            new_messages.append(msg)
            continue

        # 过滤 — 留下 name 不在 dropped_names 的
        kept_calls: list[Any] = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                kept_calls.append(tc)
                continue
            fn = tc.get("function")
            name = fn.get("name") if isinstance(fn, dict) else None
            if isinstance(name, str) and name in dropped_names:
                # 收集 tool_call_id 给第二遍用
                tcid = tc.get("id")
                if isinstance(tcid, str):
                    dropped_call_ids.add(tcid)
                continue  # 这条 tool_call drop
            kept_calls.append(tc)

        if len(kept_calls) == len(tool_calls):
            # 没一条被 drop, 原样留
            new_messages.append(msg)
            continue

        scrubbed_assistants += 1
        # 有 tool_call 被 drop → 改写 message
        rewritten = dict(msg)
        if kept_calls:
            rewritten["tool_calls"] = kept_calls
            new_messages.append(rewritten)
        else:
            # 全 drop. 看 content 有没有
            content = msg.get("content")
            has_text = isinstance(content, str) and content.strip()
            if has_text:
                # 保留 message 文字部分, 删掉 tool_calls
                rewritten.pop("tool_calls", None)
                new_messages.append(rewritten)
            else:
                # 空 content + 没 tool_calls → 整条丢, 否则留空壳更糟
                dropped_assistants += 1
                continue

    # 第二遍: 把 tool_call_id 在 dropped_call_ids 的 tool message 也丢
    final_messages: list[Any] = []
    dropped_tool_msgs = 0
    for msg in new_messages:
        if not isinstance(msg, dict):
            final_messages.append(msg)
            continue
        if msg.get("role") != "tool":
            final_messages.append(msg)
            continue
        tcid = msg.get("tool_call_id")
        if isinstance(tcid, str) and tcid in dropped_call_ids:
            dropped_tool_msgs += 1
            continue
        final_messages.append(msg)

    body["messages"] = final_messages

    if scrubbed_assistants or dropped_assistants or dropped_tool_msgs:
        logger.info(
            "BL-FIX5 history scrub: 改写 assistant=%d, 丢 assistant=%d, "
            "丢 orphan tool msg=%d (因 BL-FIX4 dedupe 了 %s 等)",
            scrubbed_assistants,
            dropped_assistants,
            dropped_tool_msgs,
            ", ".join(sorted(dropped_names)[:3]),
        )

    # BL-LOOP-C (5/21 鸿波): scrub 完之后给 LLM 显式反馈 — 否则它根本不知道自己
    # 调过的工具被砍了, 下轮又 emit 同名 tool_call (实测 c90300056cee /
    # e49d475cec4a session 30s 反复同 3 个工具 browser_snapshot /
    # confirm_expertise / create_calendar_event). 注 system message 末尾"工具状态
    # 告知", 不动 assistant/tool 历史 shape (防上游 400).
    _inject_scrub_notice(body["messages"], dropped_names)


# ── BL-LOOP-C (5/21): scrub 反馈注入 ────────────────────────────────

#: System message 末尾标记 — 老 notice 入口 strip 时认这个 token, 不重复堆叠.
SCRUB_NOTICE_MARKER = "[catfish-scrub-notice]"


def _inject_scrub_notice(messages: list[Any], dropped_names: set[str]) -> None:
    """在 system message 末尾追加被砍工具反馈, 给 LLM 显式信号.

    打破: BL-FIX4 dedupe / BL-TOOL-CAP / BL-RBAC scrub 后, LLM 看不到自己调过被砍
    工具 (assistant.tool_calls + tool message 都被删), 下轮又 emit 同名 tool_call,
    形成 30s 反复重试的 loop (5/21 鸿波 19:30 日志诊断). 注一条 system notice 后
    LLM 明知工具不可用, 自动避让.

    不动 assistant/tool 历史 shape (那会撞上游 Qwen Go gRPC 校验 "tool_call.name 必须
    在 tools 列表里" 抛 400). 只追 system tail.

    幂等: 每次 sanitize 都会跑, 但 marker 让老 notice 先被截掉, 不堆叠.
    """
    if not dropped_names:
        return
    if not isinstance(messages, list) or not messages:
        return

    notice = (
        f"\n\n{SCRUB_NOTICE_MARKER} ⚠️ 以下工具在本次请求**不可用** (RBAC/Cap/Dedupe 砍掉): "
        f"{', '.join(sorted(dropped_names))}. "
        f"**不要再调这些工具**, 也不要假设它们的输出. "
        f"改用其他可用工具 (e.g. catfish_search) 或直接给用户文字回复."
    )

    # 找首条 system message
    sys_idx = next(
        (
            i for i, m in enumerate(messages)
            if isinstance(m, dict) and m.get("role") == "system"
        ),
        None,
    )

    if sys_idx is not None:
        msg = messages[sys_idx]
        content = msg.get("content")
        if isinstance(content, str):
            # 截掉上一轮 notice (marker 之后全是老 notice, strip 再加新的)
            if SCRUB_NOTICE_MARKER in content:
                content = content.split(SCRUB_NOTICE_MARKER, 1)[0].rstrip()
            messages[sys_idx] = {**msg, "content": content + notice}
        # content 是 multipart list 等非 str — 不动, 简化处理
    # 没 system message → 不主动插入. 真实 chat 一定有 system (Companion/hermes 都注),
    # 没的多半是测试 / 直接 curl, 不污染. 老 test (test_scrub_history_*) 也不破.
