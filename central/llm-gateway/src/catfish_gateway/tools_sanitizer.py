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

logger = logging.getLogger("catfish.gateway.tools_sanitizer")


# BL-FIX4 (5/8): hermes builtin browser_* 跟 catfish_browser_* namespace 撞, 看到
# catfish 一族就丢 hermes 一族. 这里只列 hermes 已知 builtin (现网客户端实际暴露的),
# 新加的不在表里也无所谓 — 我们只丢 "browser_" 开头且**不带 catfish_ 前缀**的,
# 通过名字 startswith 判断, 不依赖白名单.
_HERMES_BROWSER_PREFIX = "browser_"
_CATFISH_BROWSER_PREFIX = "catfish_browser_"


# BL-TOOL-CAP (5/15 鸿波撞 Qwen 122B 83 tools 空 400): 单 request tool 数量上限.
# Qwen 122B 实测 50+ tools 就开始撞空 400. 默认 cap 50, env 可调.
# 超 cap 时按 priority 保留, 低优先级 drop.
_DEFAULT_MAX_TOOLS = 50
_ENV_MAX_TOOLS = "CATFISH_MAX_TOOLS"

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
_HIDDEN_FROM_LLM: frozenset[str] = frozenset({
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
_ALWAYS_ON_TOOLS: frozenset[str] = frozenset({
    # Catfish 核心 native
    # BL-MEMORY-CATFISH-REMEMBER-BLACKLIST (5/16): catfish_remember 移到 hidden,
    # 不再给 LLM 看到 (强制走 hermes memory).
    # "catfish_remember",  # ← 从 always-on 移除 (现在在 _HIDDEN_FROM_LLM)
    "catfish_search_sessions",
    "catfish_list_my_outputs",
    "catfish_user_profile_get",
    "catfish_user_profile_propose",
    "catfish_user_profile_confirm",
    "catfish_run_skill",
    "search_skills",
    # Hermes 0.13 内置基础 (跨 agent 必须)
    "execute_code",
    "read_file",
    "write_file",
    "edit_file",
    "list_dir",
    "search",
    "grep",
    "clarify",
    "delegate_task",
    # 文件 / shell
    "shell",
    "bash",
    # BL-MEMORY-DIAGNOSIS (5/15 23:50 鸿波本机数据 audit):
    # hermes 原生 memory 工具是跨 session 长期记忆的核心 (~/.hermes/USER.md /
    # memories/), 之前一直被砍 → 日志原话 "砍掉低优先级 ... memory" → USER.md
    # 12 天没动 / memories/ 3 周没动 / project_catfish_facts.md 0 字节空文件.
    # 这是设计缺陷: memory 应该跟 catfish_remember 同优先级, 永不砍.
    # 加白名单后 LLM 每次 chat 都能看到 memory.add/replace/remove, 自然写 USER.md.
    # 5/16 BL-MEMORY-PLUMBING-DIAG 实盘: hermes 0.13 把 4 个旧 memory tool
    # 合一为 `memory` (action=add/replace/remove/search). 不再注册 memory_save
    # / memory_load / memory_search — 老名字留着 always-on hit log 永远报 missing.
    "memory",
})


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
_KNOWN_BUILTIN_TOOLS: frozenset[str] = frozenset({
    # ── catfish 原生 (catfish_tool_bridge.catfish_tools) ──
    "catfish_search_sessions", "catfish_list_my_outputs",
    "catfish_user_profile_get", "catfish_user_profile_propose", "catfish_user_profile_confirm",
    "catfish_run_skill", "search_skills",
    "catfish_remember",  # hidden 但已知 name, 不应被当 unknown
    "catfish_memory_dedupe", "catfish_memory_compress",  # hidden 同
    "catfish_browser_open", "catfish_browser_back", "catfish_browser_click",
    "catfish_browser_screenshot", "catfish_browser_eval", "catfish_browser_navigate",
    "catfish_read_url", "catfish_read_tool_archive",
    # ── hermes 0.13 内置 (catfish-tool-bridge 加载) ──
    "execute_code", "read_file", "write_file", "edit_file", "list_dir",
    "search", "grep", "clarify", "delegate_task", "shell", "bash",
    "memory",  # hermes 0.13 unified
    "todo_tool",  # BL-TODO-BRIDGE-STORE
    "screenshot", "vision",
    "browser_back", "browser_open", "browser_click", "browser_eval",
    "browser_vision", "browser_navigate", "browser_screenshot", "browser_cdp",
    # ── hermes 0.14 新加 (release v2026.5.16) ──
    "x_search",  # #26763 X (Twitter) search
    "video_generate",  # 0.14 unified pluggable
    "computer_use",  # 0.14 cua-driver backend (不再 Anthropic-only)
    "browser_console",  # #23226 180x faster CDP
})


def _audit_unknown_tools(
    body: dict[str, Any],
    user: Any,
    tools: list[dict[str, Any]],
) -> list[str]:
    """BL-RBAC-DAY4-HARDENING: 扫 tool 列表里"陌生"工具, 返 unknown names (audit only).

    陌生定义: 不在 _KNOWN_BUILTIN_TOOLS + 不在 mcp__* / _mcp_ / mcp_ 前缀
    + 不在 user.effective_allowed_tools (dept 显式批的). 落 audit log,
    不 drop — 让 dept admin 在 /admin/access 决定是否加 allowlist.

    防的威胁: hermes 0.14 #26759 tool_override 把 builtin 重命名成 dept-allowed
    名 (e.g. catfish_browser_open → catfish_run_skill). 我们看 name 没法识别原始
    来源, 但 unknown 名出现的频率突然飙高 = 部署里有 plugin tool_override.
    """
    if not tools:
        return []
    allowed_explicit: set[str] = set()
    if user is not None:
        eat = getattr(user, "effective_allowed_tools", None) or []
        allowed_explicit = {str(t) for t in eat}

    unknown: list[str] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function")
        name = fn.get("name") if isinstance(fn, dict) else None
        if not isinstance(name, str) or not name:
            continue
        # 已知 builtin → OK
        if name in _KNOWN_BUILTIN_TOOLS:
            continue
        # MCP server tool (hermes 标准约定 mcp__server__tool) → OK
        if name.startswith("mcp__") or name.startswith("mcp_"):
            continue
        # dept 显式批了 → OK (admin 明知, 故意, 不报)
        if name in allowed_explicit:
            continue
        unknown.append(name)

    if unknown:
        sub = getattr(user, "sub", "?") if user else "?"
        dept = getattr(user, "department", "?") if user else "?"
        logger.warning(
            "BL-RBAC-DAY4-HARDENING: %d unknown tool name(s) seen "
            "(hermes 0.14 tool_override 嫌疑, audit only 不 drop): "
            "user=%s dept=%s unknown=%s",
            len(unknown), sub, dept, ", ".join(sorted(unknown)[:8]),
        )
    return unknown


def _cap_tools_by_priority(tools: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """超 cap 时按 priority 保留 tools.

    保留策略:
      1. 所有 _ALWAYS_ON_TOOLS 中的 tool, 即使总数仍超也保留 (它们是底座)
      2. 剩余 slot 按 tools 原顺序填 (caller 已经按某种 priority 排过, 我们尊重它)
      3. 超出的 drop, 返回 (kept, dropped_names)

    用法:
      kept, dropped = _cap_tools_by_priority(cleaned)
      if dropped: logger.warning(...)

    返 cap 后的 tools + dropped tool 名清单 (给 audit log).
    """
    try:
        max_tools = int(os.environ.get(_ENV_MAX_TOOLS, str(_DEFAULT_MAX_TOOLS)))
    except ValueError:
        max_tools = _DEFAULT_MAX_TOOLS
    max_tools = max(10, max_tools)  # 兜底, 不允许 < 10 (always-on 都装不下)

    if len(tools) <= max_tools:
        return tools, []

    # 先取 always-on (顺序保留)
    always_on_kept: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    for t in tools:
        if not isinstance(t, dict):
            other.append(t)
            continue
        fn = t.get("function")
        name = fn.get("name") if isinstance(fn, dict) else ""
        if isinstance(name, str) and name in _ALWAYS_ON_TOOLS:
            always_on_kept.append(t)
        else:
            other.append(t)

    # 剩 slot = max - always_on. 按原顺序填.
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


def sanitize_tools(body: dict[str, Any], user: Any = None) -> dict[str, Any]:
    """原地修 body["tools"] —— 丢畸形条目, 修补能补的字段。

    返回原 body (mutate in-place + return), 调用方习惯链式。
    body 里没 tools / 不是 list / 空数组 都直接返回, 不报错。

    BL-RBAC-DAY4 (5/17): 可选 user 参. 给了就按 user.can_use_tool() 过滤
    白名单, ALWAYS_ON_TOOLS 永远保留 (LLM agent loop 底座). user=None 则
    不做 RBAC 过滤 (兼容老 caller / 内部 loopback / 测试).
    """
    tools = body.get("tools")
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

        # BL-MEMORY-CATFISH-REMEMBER-BLACKLIST (5/16 鸿波 A): 永不暴露 catfish_remember
        # 给 LLM. 模型层硬不听 SOUL nudge, 这是釜底抽薪 — 没选择只能用 hermes memory.
        # env CATFISH_EXPOSE_REMEMBER=1 操作员可一键还原 (调试用).
        import os  # noqa: PLC0415
        if (
            name in _HIDDEN_FROM_LLM
            and os.environ.get("CATFISH_EXPOSE_REMEMBER", "0") != "1"
        ):
            dropped.append(f"#{idx}(name={name}: BL-MEMORY-CATFISH-REMEMBER-BLACKLIST, 强制走 hermes memory)")
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
            if nm in _ALWAYS_ON_TOOLS:
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

    # BL-RBAC-DAY4-HARDENING (5/17, hermes 0.14 #26759 tool_override 防御):
    # 扫"陌生"tool 名 audit, 不 drop. 防 plugin 把 builtin rename 成 dept-allowed.
    _audit_unknown_tools(body, user, cleaned)

    # BL-TOOL-CAP (5/15 鸿波撞 Qwen 122B 83 tools 空 400): 超 cap 时砍低优先级.
    # 实测 Qwen 122B ≥50 tools 就开始撞空 400 (上游无具体错). 保留 always-on 核心
    # + 剩 slot 按顺序填, 超 cap 的 drop. env CATFISH_MAX_TOOLS 调阈值.
    capped_tools, capped_dropped = _cap_tools_by_priority(cleaned)
    if capped_dropped:
        logger.warning(
            "BL-TOOL-CAP: %d tools 超上限 %d, 砍掉低优先级 (always-on 保留): %s",
            len(capped_dropped),
            int(os.environ.get(_ENV_MAX_TOOLS, str(_DEFAULT_MAX_TOOLS))),
            ", ".join(capped_dropped[:10]),
        )
    cleaned = capped_tools
    body["tools"] = cleaned

    # BL-MEMORY-PLUMBING-DIAG (5/16): 暴露 always-on 实际命中. 排"LLM 调了 31 个
    # tool 但 ~/.hermes/memories/ 没动" 真因 — 假设上游 list_tools() 没送
    # memory_save (hermes registry 没注册), sanitizer 看不到也变不出来.
    # 一次聊天 log 出 always-on 实际命中名字, 三秒看清是模型层还是 plumbing 层.
    seen_always_on: list[str] = []
    for t in cleaned:
        if not isinstance(t, dict):
            continue
        fn = t.get("function")
        nm = fn.get("name") if isinstance(fn, dict) else None
        if isinstance(nm, str) and nm in _ALWAYS_ON_TOOLS:
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
