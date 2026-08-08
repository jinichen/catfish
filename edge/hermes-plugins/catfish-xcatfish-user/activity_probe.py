"""hermes agent 进度快照的只读出口 (P44, 8/9).

# ⚠ 8/9 第二版: 第一版取数锚点选错了, 早安页始终探不到

第一版照 `gateway/run.py:_session_activity_for_stall` 读 `runner._running_agents`。
在鸿波本机实测: advisor 正在跑, 端点仍然返 `no_running_turn`。

查下去发现 **`_running_agents` 是"平台会话"的注册表** (Telegram / 微信 / 飞书
那类长驻会话), 而 Companion 走的是 OpenAI 兼容的 `/v1/chat/completions`:

    api_server._create_agent(...)  →  只是 `return agent`, 不往任何注册表登记
    api_server._handle_runs        →  填 self._active_run_agents (那是 /v1/runs)
    api_server._handle_chat_completions → agent 只活在局部变量里

也就是说 **chat_completions 路径的 agent 压根没有可查的落点**。照抄 hermes
自己的取法在这里不成立 —— 它那段代码服务的是另一条链路。

# 第二版的锚点: `APIServerAdapter._run_agent`

三条 HTTP 路径 (chat_completions / responses / runs) 全汇到这一个方法, 而且
它自带一个现成的钩子 (docstring 原话):

    If *agent_ref* is a one-element list, the AIAgent instance is stored at
    ``agent_ref[0]`` before ``run_conversation`` begins.

流式路径已经在用它 (为了能从另一个线程调 agent.interrupt())。我们包一层,
传一个**会通知的 list**: agent 被放进去时登记, `_run_agent` 返回时注销。
调用方自己传了 agent_ref 的话原样写穿, 不破坏中断功能。


# 本模块只管一件事

把 hermes **已经算好的** activity 快照原样透出来, 给 Companion 看:

    GET /api/catfish/agent-activity

# 什么不归这里管 —— 这条边界是 hermes 自己划的

`agent/session_activity.py` 开头第 4 行把话说死了:

    Observation-only: timestamp + bounded description/provenance.
    **Notification, timeout, kill, and retry policy stay in their own
    components.**

所以本模块不做超时判断、不 kill、不重试、不自己算进度。超时策略留在 Companion
(`briefing_advisor.ts` 的 `CLIENT_TIMEOUT_MS`) —— 按 hermes 这个划分, 那本来就是
**消费方自己的职责**, 不是"跟 hermes 抢进度源"。8/9 一度打算把它砍掉, 是误读。

同样的纪律参考 hermes `gateway/session_stall.py`: 它消费同一份契约, 但只拥有
"通知一次"这一条策略, 并在文件头列清楚哪些东西归别的模块。

# 为什么非得加这个出口

`get_activity_summary()` 是 `AIAgent` 上的**进程内** Python 方法
(`run_agent.py:3993`)。hermes 自己 20+ 处消费者 —— `gateway/run.py`、
`tools/delegate_tool.py`、`cron/scheduler.py` —— 全部直接持有 agent 对象。
**没有任何 HTTP / IPC 出口。**

而 Companion 是独立进程, 走 HTTP 8642, 且 advisor 那几个请求都是
`stream: false`。请求发出到最终响应之间**零信息**: 用户看到转圈然后超时, 而
hermes 那边其实知道跑到第几轮 API 调用、正在用哪个工具、上次动是多久之前。

SessionDB 那条路走不通: `~/.hermes/state.db` 的 sessions 表虽然有
`last_activity_*` 三列, 但 8/9 实测 384 行有数据的记录里 description 全是空串、
provenance 全是 unknown, 且心跳间隔硬编码 >=60s
(`SESSION_ACTIVITY_HEARTBEAT_MIN_INTERVAL_SECONDS`, 注释写明是刻意压低写压力的
"observation-only projection")。拿得到"上次动的时间", 拿不到"在干什么"。

# 取数路径: 逐行照抄 hermes 自己的写法

`gateway/run.py:_session_activity_for_stall` 是 hermes 标准取法, 本模块照它:

    agent = (getattr(runner, "_running_agents", None) or {}).get(session_key)
    agent is None / is _AGENT_PENDING_SENTINEL      → 跳过
    not hasattr(agent, "get_activity_summary")      → 跳过
    get_activity_summary() 抛异常                    → 跳过

**不加工、不改名、不补字段、不填默认值** —— 上游返什么就透什么。hermes 哪天改了
形状, 该在 Companion 的 contract test 上红出来, 不是在这里被悄悄兼容掉。
(这条是 5/27 定下的 fail-loud 原则: 静默错位比启动失败难查得多。)

# 为什么返全部, 不按 session_key 查

session_key 是 `f"api-{sha256(system_prompt + chr(10) + first_user_message)[:16]}"`
(`gateway/platforms/api_server.py:1231`)。Companion 要自己算, 就得把这个 seed
拼法复刻一份焊进客户端 —— hermes 改 seed 我们就静默错位, 而且错的方式是"永远
查不到", 最难查。

员工机上同时跑的 turn 通常 0-1 个。全返回既躲开这个耦合, 也让端点保持"只观察"。
调用方要认哪一个, 自己按 `session_key` 挑。
"""
from __future__ import annotations

import logging
import threading
import uuid
from typing import Any

logger = logging.getLogger("catfish.xcatfish_user.activity_probe")

# ── api_server 在途 turn 的登记表 (第二版加) ──────────────────
#
# key 是我们自己发的一次性 id, 不是 session_key —— 我们只需要"现在有哪些 agent
# 在跑", 不需要跟 hermes 的 session 命名对齐 (对齐反而是耦合, 见下面 collect
# 那段关于 session_key 的说明)。
_ACTIVE_LOCK = threading.Lock()
_ACTIVE_API_AGENTS: dict[str, Any] = {}


def _register_active(key: str, agent: Any) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_API_AGENTS[key] = agent


def _unregister_active(key: str) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_API_AGENTS.pop(key, None)


def _snapshot_active() -> list[tuple[str, Any]]:
    with _ACTIVE_LOCK:
        return list(_ACTIVE_API_AGENTS.items())


class _NotifyingAgentRef(list):
    """`_run_agent(agent_ref=...)` 用的 one-element list, 被赋值时通知我们。

    hermes 的契约是"把 AIAgent 放进 agent_ref[0]"。我们传这个子类进去:
      · 照常记住 agent (调用方可能要拿它 interrupt)
      · **写穿**回调用方原本传的那个 list —— 流式路径靠它中断, 不能破
      · 顺手登记到 _ACTIVE_API_AGENTS
    """

    def __init__(self, inner: list | None, key: str) -> None:
        super().__init__([None])
        self._inner = inner
        self._key = key

    def __setitem__(self, index, value):  # noqa: D105
        super().__setitem__(index, value)
        try:
            if self._inner is not None:
                self._inner[index] = value
        except Exception as e:  # noqa: BLE001 — 写穿失败不该连累主流程
            logger.debug("agent_ref 写穿失败: %s", e)
        try:
            if index == 0 and value is not None:
                _register_active(self._key, value)
        except Exception as e:  # noqa: BLE001
            logger.debug("登记在途 agent 失败: %s", e)

#: 端点路径。改这里要同时改 Companion 的 `src/lib/agentActivity.ts`。
ROUTE_PATH = "/api/catfish/agent-activity"

#: 拿不到快照时的原因码 —— **封闭词表**, 不回显异常原文。
#:
#: 跟中央端 `error_class.py` 同一条道理: 调用方要的是"下一步干什么", 不是
#: 上游那串东西。而且这些值会进 Companion 的 UI, 是我们自己写的常量才安全。
REASON_NO_RUNNER = "no_runner"          # gateway runner 还没起来 / 已回收
REASON_NO_RUNNING_TURN = "no_running_turn"   # 当前没有正在跑的 turn
REASON_PROBE_FAILED = "probe_failed"    # 取数本身出错 (已记日志)
REASON_AUTH_UNAVAILABLE = "auth_unavailable"  # 鉴权组件没就位 → fail closed

#: 调用方 (agentActivity.ts) 按这张表判"是不是该继续轮询"。
#: no_running_turn 是正常态 (还没开始 / 已结束), 其余都是异常态。
ALL_REASONS = (
    REASON_NO_RUNNER,
    REASON_NO_RUNNING_TURN,
    REASON_PROBE_FAILED,
    REASON_AUTH_UNAVAILABLE,
)


def _gateway_runner() -> Any | None:
    """拿 GatewayRunner 实例; 拿不到返 None。

    `gateway/run.py:3323` 定义 `_gateway_runner_ref` 为 weakref, 默认值是
    `lambda: None`, 真实例在 `__init__` 里 (5778) 赋上。所以调用它永远安全,
    没起来时自然返 None。
    """
    try:
        from gateway import run as _run  # noqa: PLC0415 (hermes 进程内才有)
        ref = getattr(_run, "_gateway_runner_ref", None)
        return ref() if callable(ref) else None
    except Exception as e:  # noqa: BLE001
        logger.debug("拿 gateway runner 失败: %s", e)
        return None


def _running_turns(runner: Any) -> list[tuple[str, Any]]:
    """(session_key, agent) 列表; 含 pending sentinel, 由调用方过滤。

    优先用 hermes 的 `_running_agent_items()` (run.py:5734 —— 它是
    consolidation 之后的正路), 没有再退回 `_running_agents` dict view
    (legacy_dict_property, 老版本兼容)。
    """
    items = getattr(runner, "_running_agent_items", None)
    if callable(items):
        try:
            return list(items())
        except Exception as e:  # noqa: BLE001
            logger.debug("_running_agent_items() 失败, 退回 dict view: %s", e)
    try:
        return list((getattr(runner, "_running_agents", None) or {}).items())
    except Exception as e:  # noqa: BLE001
        logger.debug("_running_agents dict view 也失败: %s", e)
        return []


def _is_real_agent(agent: Any) -> bool:
    """排掉 None 和 pending sentinel (run.py:2346 `object()`)。

    sentinel 是"session 抢到槽位但 agent 还没建好"的占位。它没有
    get_activity_summary, 下面 hasattr 也会挡住 —— 这里显式判一次是为了让
    "还没开始跑"和"跑起来了但探不到"在日志上分得开。
    """
    if agent is None:
        return False
    try:
        from gateway.run import _AGENT_PENDING_SENTINEL  # noqa: PLC0415
        if agent is _AGENT_PENDING_SENTINEL:
            return False
    except Exception as e:  # noqa: BLE001
        # 拿不到 sentinel 不致命 —— 下面 hasattr 一样能挡住它 (sentinel 是裸
        # object(), 没有 get_activity_summary)。记一条 debug 是为了让
        # "hermes 换了 sentinel 的位置" 这件事留痕, 而不是静默走兜底。
        logger.debug("拿不到 _AGENT_PENDING_SENTINEL, 只靠 hasattr 兜底: %s", e)
    return hasattr(agent, "get_activity_summary")


#: `agent_ref` 在 `_run_agent` 里是 self 之后的**第 9 个**位置参数:
#:   user_message, conversation_history, ephemeral_system_prompt, session_id,
#:   stream_delta_callback, tool_progress_callback, tool_start_callback,
#:   tool_complete_callback, agent_ref
#: 所以 `len(args) >= 9` 就说明调用方已经位置传了它, 我们不能再塞 kwargs
#: (会撞 "got multiple values for argument")。
#: 观察到的 6 个调用点全用关键字传, 这条只是防万一。
#: (8/9: 第一版写成 `<= 9` 差一位, 被 test_agent_ref_位置传时不动它 抓出来。)
_AGENT_REF_POSITION = 9


def patch_run_agent_registry() -> bool:
    """包 `APIServerAdapter._run_agent`, 把在途 agent 登记起来。幂等。

    为什么必须打这个 patch: chat_completions 路径的 agent 只活在局部变量里,
    没有任何注册表可查 (见模块顶部第二版说明)。

    **只加登记, 不改行为** —— 原函数原样调, 参数原样透, 返回原样返, finally
    里注销。异常路径也注销 (不然一次失败的 turn 会永远挂在表里, 显示成"一直
    在跑")。
    """
    try:
        from gateway.platforms.api_server import APIServerAdapter  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        logger.warning("P44: 拿不到 APIServerAdapter, 进度登记未安装: %s", e)
        return False

    orig = getattr(APIServerAdapter, "_run_agent", None)
    if orig is None:
        logger.warning("P44: APIServerAdapter._run_agent 不存在 (hermes 改了?), 跳过")
        return False
    if getattr(orig, "_catfish_p44", False):
        return True  # 已经包过 (plugin 重载 / 多个 Application)

    async def patched(self, *args, **kwargs):
        key = uuid.uuid4().hex
        wrapped = False
        # agent_ref 位置传的话不动它 —— 宁可这一次探不到, 也不要改错参数
        if len(args) < _AGENT_REF_POSITION:
            kwargs["agent_ref"] = _NotifyingAgentRef(kwargs.get("agent_ref"), key)
            wrapped = True
        try:
            return await orig(self, *args, **kwargs)
        finally:
            if wrapped:
                _unregister_active(key)

    patched._catfish_p44 = True
    APIServerAdapter._run_agent = patched
    logger.info("P44 _run_agent 进度登记已安装 ✓")
    return True


def collect_activity() -> dict[str, Any]:
    """当前所有正在跑的 turn 的 activity 快照。**永不抛。**

    返回形状 (Companion 的 agentActivity.ts 按这个解):

        {"available": true,  "turns": [{"session_key": "...", ...snapshot}]}
        {"available": false, "reason": "<REASON_*>", "turns": []}

    snapshot 是 `AIAgent.get_activity_summary()` 的**原样输出**, 我们一个字段
    都不动。当前 hermes v0.20 会给:
        last_activity_at / last_activity_description / last_activity_provenance
        seconds_since_activity / current_tool / api_call_count
        max_iterations / budget_used / budget_max
        (外加 last_activity_ts / last_activity_desc / description / provenance 别名)
    """
    try:
        # 两个来源, 缺一不可:
        #   api_server 在途 turn  ← Companion 走的就是这条 (chat_completions)
        #   平台会话 _running_agents ← Telegram / 微信那类, 顺带也报
        sources: list[tuple[str, Any]] = list(_snapshot_active())

        runner = _gateway_runner()
        if runner is not None:
            sources += _running_turns(runner)
        elif not sources:
            # 两个都没有才算"runner 没起来"; 只要 api_server 有在途 turn,
            # runner 拿不到也不影响回答问题
            return {"available": False, "reason": REASON_NO_RUNNER, "turns": []}

        turns: list[dict[str, Any]] = []
        seen: set[int] = set()
        for session_key, agent in sources:
            if not _is_real_agent(agent):
                continue
            # 同一个 agent 可能两边都在 (理论上不会, 但去重成本极低)
            if id(agent) in seen:
                continue
            seen.add(id(agent))
            try:
                summary = agent.get_activity_summary()
            except Exception as e:  # noqa: BLE001
                logger.debug("session %s 取快照失败, 跳过: %s", session_key, e)
                continue
            if not isinstance(summary, dict):
                logger.warning(
                    "get_activity_summary 返的不是 dict 而是 %s —— "
                    "hermes 契约变了? 跳过这条",
                    type(summary).__name__,
                )
                continue
            turns.append({"session_key": session_key, **summary})

        if not turns:
            return {"available": False, "reason": REASON_NO_RUNNING_TURN, "turns": []}
        return {"available": True, "turns": turns}
    except Exception:  # noqa: BLE001
        logger.exception("collect_activity 出错")
        return {"available": False, "reason": REASON_PROBE_FAILED, "turns": []}


def register_routes(router: Any) -> bool:
    """在 aiohttp router 上挂只读端点。返回是否挂上了。

    调用时机很讲究: 必须在 `Application.__init__` 里 (router 还没 freeze),
    跟 P18 / P26 同一个 fence。connect() 之后再挂会撞
    "Cannot register a resource into frozen router" —— 6/17 P18 踩过这个坑。

    只挂 GET。这个端点没有任何副作用, 也不该有。

    鉴权走 hermes 自己的 `APIServerAdapter._check_auth` (P18 / P26 / P30 那几个
    handler 也是各自调它)。adapter 拿不到时 **fail closed 返 503**, 不裸奔 ——
    activity description 是 agent 正在做什么的自由文本, 虽然全程在员工本机,
    但"取不到鉴权就放行"是个永远不该开的口子。
    """
    from aiohttp import web as _w  # noqa: PLC0415

    async def _handler(request):
        adapter = request.app.get("_catfish_apiserver_adapter")
        if adapter is None or not hasattr(adapter, "_check_auth"):
            # P7 stash 没就位 / hermes 改了方法名 → 拒, 不放行
            return _w.json_response(
                {"available": False, "reason": REASON_AUTH_UNAVAILABLE, "turns": []},
                status=503,
            )
        denied = adapter._check_auth(request)
        if denied is not None:
            return denied
        return _w.json_response(collect_activity())

    # 顺手把 _run_agent 的登记 patch 打上 —— 此刻 api_server 模块已加载
    # (Application 都在建了), import 得到 APIServerAdapter; 而且还没开始服务,
    # 包方法是安全的。幂等, 多个 Application 只包一次。
    patch_run_agent_registry()

    try:
        router.add_get(ROUTE_PATH, _handler)
        # 挂上之后立刻自检一次。放这儿是因为此刻 hermes 已经完全加载
        # (Application 都在建了), 可以直接 import 真模块 —— 不像 plugin.py 的
        # _verify_patch_targets 得靠静态读源码绕开 0.15.1 的 circular import。
        #
        # **只 warn 不 raise**: 探不到进度是体验降级。P1-P11 那些 patch 错位会
        # 跨员工串数据, 那才值得让 hermes 起不来。
        missing = verify_patch_targets()
        if missing:
            logger.warning(
                "P44 已挂上但 hermes 目标缺了 %s —— 进度会一直显示不出来, "
                "端点仍返 200。多半是 hermes 升级改了契约, "
                "看 tests/test_p44_activity_probe.py 的形状契约那几条。",
                missing,
            )
        else:
            logger.info("P44 route GET %s registered ✓", ROUTE_PATH)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("P44 add_get 失败 (%s): %s", ROUTE_PATH, e)
        return False


def verify_patch_targets() -> list[str]:
    """启动自检: 返回缺失项的描述列表, 空列表 = 都在。

    跟 plugin.py 的 `_verify_patch_targets` 同款 fail-loud 思路, 但**这条不该让
    hermes 启动失败** —— 进度显示挂了是体验降级, 不是隐私漏洞。所以返回列表让
    调用方决定, 不在这里 raise。

    (P1-P11 那些 patch 不一样: 它们错位会导致跨员工串数据, 那必须 fail loud。)
    """
    missing: list[str] = []
    try:
        from gateway import run as _run  # noqa: PLC0415
        if not hasattr(_run, "_gateway_runner_ref"):
            missing.append("gateway.run._gateway_runner_ref 不存在")
        if not hasattr(_run, "_AGENT_PENDING_SENTINEL"):
            missing.append("gateway.run._AGENT_PENDING_SENTINEL 不存在")
    except Exception as e:  # noqa: BLE001
        missing.append(f"import gateway.run 失败: {e}")

    try:
        from run_agent import AIAgent  # noqa: PLC0415
        if not hasattr(AIAgent, "get_activity_summary"):
            missing.append("AIAgent.get_activity_summary 不存在 (hermes < 0.20?)")
    except Exception as e:  # noqa: BLE001
        missing.append(f"import run_agent.AIAgent 失败: {e}")

    return missing
