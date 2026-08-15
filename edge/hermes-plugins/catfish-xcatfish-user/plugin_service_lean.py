"""P44 (8/15): 后台分类调用不背 agent 上下文 —— 一次评级 42K token 换 185 token。

# 病

8/15 百炼 token-plan 周配额 07:54 刚重置, 09:17 就空了。查 PG gateway_audit:

    来源                        模型              次数   输入token   输出token  每次平均输入
    companion-email-scheduler   qwen-flash         433   18,235,291     79,997      42,114
    companion-email-scheduler   deepseek-flash     152    6,382,393      8,908      41,989

83 分钟约 3000 万 token, 邮件评级占 2470 万 (83%)。**输入输出比 230:1**。

而 Companion 那侧发出去的请求体只有一个 system prompt 加一列邮件标题,
`max_tokens: 64`, 要的就是 `["急","中","低"]` 这么一个数组
(services/email_llm.rs:105)。42K 是 **hermes 8642 这一跳**加的:

  · 38 个工具的 JSON schema (api_server.py:2759 `_get_platform_tools`)
  · catfish-memory 的 prefetch —— distilled_facts + journal 尾部 + wiki
  · hermes 自己的系统提示词

实测 `cache_read_tokens=31,872 / 41,989`, 即约 3.2 万是这些稳定前缀。
给一个邮件紧急度分类器喂员工的长期记忆和 38 个工具, 每 30 秒一次。

# 为什么挡在这两个点

`_create_agent` 已经被 P5/P6/P11 wrap 过, 但 `enabled_toolsets` 是在它**函数体
内部**算出来再塞进 `agent_kwargs` 的 (api_server.py:2759), 从外面 wrap 改不到。
好在那行用的 `_get_platform_tools` 是**函数体内 import**
(api_server.py:2535 `from hermes_cli.tools_config import _get_platform_tools`),
每次调用都重新从模块取属性 —— patch 模块属性就生效, 不用碰 `_create_agent`。

记忆那半走 `MemoryManager.prefetch_all` (memory_manager.py:525), 它由
turn_context 调, 仍在请求上下文里, `CV_CF_SOURCE` 读得到 —— 跟 P42 挡
`sync_all` 是同一个道理 (P42 挡的是**写**, 这里挡的是**读**)。

# 判据: 白名单两项, **不是** P42 那条"有 source 就跳"

P42 用的是"source 非空 = 后台调用"。那条判据对**记忆写入**是对的, 但不能
照搬到这里 —— 鸿波 8/15 明确要求"不能影响到聊天、早安、知识库的使用",
而这三样里有两样是带 source 的:

    companion-briefing-card / companion-advisor / companion-advisor-transform
        → 早安。它要 LLM 结合当天情况写建议, 记忆和工具都可能用得上。
    companion-wiki-suggest
        → 知识库。
    companion-email-draft / companion-profile
        → 没查证过它们要不要工具, 不在没查证的情况下动。

所以这里用**显式白名单**, 只放两个已经查证过"就是个分类器、不需要工具也
不需要记忆"的来源:

    companion-email-scheduler  —— email_llm.rs:105 要的是 ["急","中","低"]
    companion-phishing-scan    —— phishing_llm.rs 要的是钓鱼判定

**失败方向**: 将来新增一个后台分类器而忘了加进白名单, 它只是继续贵,
不会坏。反过来 (聊天/早安/知识库被误伤) 需要有人主动把它们的 source 加进
这张表 —— 是个显式动作, 不会自己发生。跟 P42 一样, 宁可漏优化不可误伤。

# 为什么 enabled_toolsets=[] 是安全的

`platform_toolsets.api_server: []` 本来就是 hermes 支持的合法配置
(tools_config.py:2238 `explicitly_configured` 分支), 走到 AIAgent 的就是同一个
空列表。也就是说这条路径 hermes 自己就支持, 不是我们造出来的新状态。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("catfish.xcatfish_user.plugin")


#: 只有这两个来源会被"瘦身"。见文件头「判据」—— 这里是白名单不是黑名单,
#: 加一项之前必须先确认那个调用**确实不需要工具、也不需要记忆**。
LEAN_SOURCES = frozenset({
    "companion-email-scheduler",
    "companion-phishing-scan",
})

#: 只对 api_server 平台生效。hermes 还有 cli / telegram / discord 等平台走
#: 同一个 _get_platform_tools, 不能连它们一起掐。
_LEAN_PLATFORM = "api_server"


def _is_lean_call(cv_cf_source) -> bool:
    """当前请求是不是白名单里的后台分类调用。

    CV 读失败一律返 False —— 失败就走原路 (贵但正确), 不赌。
    """
    try:
        return (cv_cf_source.get() or "").strip() in LEAN_SOURCES
    except Exception:  # noqa: BLE001  CV 不该抛, 抛了也不能拖垮请求
        return False


def _patch_p44_service_call_lean(cv_cf_source) -> None:
    """两处 wrap: 工具 schema + 记忆读取。

    Args:
        cv_cf_source: plugin.py 的 `CV_CF_SOURCE` ContextVar。传进来而不是
            import —— 跟 P42 同理, 免得跟 plugin.py 形成循环依赖。
    """
    _patch_toolsets(cv_cf_source)
    _patch_memory_prefetch(cv_cf_source)


def _patch_toolsets(cv_cf_source) -> None:
    """api_server 平台 + 白名单来源 → 不给工具。

    patch 的是 `hermes_cli.tools_config._get_platform_tools` 这个**模块属性**,
    因为 api_server.py:2535 是函数体内 import, 每次调用重新取。
    """
    # 用 import_module 而不是 `from hermes_cli import tools_config` ——
    # 后者要求父包是真 module 对象才认得子模块, import_module 直接查
    # sys.modules 的全名。真实环境两者等价, 但前者能被测试注入。
    try:
        import importlib
        tools_config = importlib.import_module("hermes_cli.tools_config")
    except Exception as e:  # noqa: BLE001
        logger.warning("P44 工具闸: import tools_config 失败, 跳过: %s", e)
        return

    orig = getattr(tools_config, "_get_platform_tools", None)
    if orig is None:
        logger.warning("P44 工具闸: 没有 _get_platform_tools, hermes 版本变了?")
        return
    if getattr(orig, "_catfish_p44_patched", False):
        return  # 幂等

    def patched(config, platform, *args, **kwargs):
        # platform 在两个调用点都是位置参 (api_server.py:2759 / :3103),
        # 但仍按 kwargs 兜一手, 免得将来 hermes 改成关键字传。
        _plat = platform if platform is not None else kwargs.get("platform")
        if _plat == _LEAN_PLATFORM and _is_lean_call(cv_cf_source):
            logger.info(
                "P44: source=%s 是后台分类调用, 本次不注入工具 schema "
                "(原本 %d 个 toolset)",
                (cv_cf_source.get() or "").strip(),
                len(orig(config, platform, *args, **kwargs) or ()),
            )
            return set()
        return orig(config, platform, *args, **kwargs)

    patched._catfish_p44_patched = True  # type: ignore[attr-defined]
    patched._catfish_p44_orig = orig     # type: ignore[attr-defined]
    tools_config._get_platform_tools = patched
    logger.info("P44: 工具闸已装 (白名单 %s)", sorted(LEAN_SOURCES))


def _patch_memory_prefetch(cv_cf_source) -> None:
    """白名单来源 → 不读记忆。

    `prefetch_all` 返 "" 是它自己就有的返回值 (memory_manager.py:533 空 query
    分支), 上层 `build_memory_context_block("")` 返 "" 不加任何块 —— 不是我们
    造出来的新状态。

    `queue_prefetch_all` 是预热用的, 一并跳掉, 否则后台线程照样去读一遍。
    """
    try:
        import importlib
        MemoryManager = importlib.import_module(
            "agent.memory_manager"
        ).MemoryManager
    except Exception as e:  # noqa: BLE001
        logger.warning("P44 记忆闸: import MemoryManager 失败, 跳过: %s", e)
        return

    _orig_prefetch = getattr(MemoryManager, "prefetch_all", None)
    if _orig_prefetch is not None and not getattr(
        _orig_prefetch, "_catfish_p44_patched", False
    ):
        def patched_prefetch(self, query, *args, **kwargs):
            if _is_lean_call(cv_cf_source):
                logger.info(
                    "P44: source=%s 是后台分类调用, 本次不读记忆",
                    (cv_cf_source.get() or "").strip(),
                )
                return ""
            return _orig_prefetch(self, query, *args, **kwargs)

        patched_prefetch._catfish_p44_patched = True   # type: ignore[attr-defined]
        patched_prefetch._catfish_p44_orig = _orig_prefetch  # type: ignore[attr-defined]
        MemoryManager.prefetch_all = patched_prefetch

    _orig_queue = getattr(MemoryManager, "queue_prefetch_all", None)
    if _orig_queue is not None and not getattr(
        _orig_queue, "_catfish_p44_patched", False
    ):
        def patched_queue(self, query, *args, **kwargs):
            if _is_lean_call(cv_cf_source):
                return None
            return _orig_queue(self, query, *args, **kwargs)

        patched_queue._catfish_p44_patched = True     # type: ignore[attr-defined]
        patched_queue._catfish_p44_orig = _orig_queue  # type: ignore[attr-defined]
        MemoryManager.queue_prefetch_all = patched_queue

    logger.info("P44: 记忆闸已装 (白名单 %s)", sorted(LEAN_SOURCES))
