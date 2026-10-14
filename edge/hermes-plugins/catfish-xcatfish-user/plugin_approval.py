"""工具审批 —— 从 plugin.py 拆出 (8/15)。

P14 (中文别名 "同意"/"批准" 等) · P15.3 (Companion 会话不当无人值守) ·
P15.2 (审批路由 + 中间件)。P15 / P15.4 已于 10/14 退役 (见下)。

`_chat_approval_middleware` 是本模块的模块级可变全局: P15.2 装好后用
`global` 写进来, plugin_cors 挂中间件链时读。见 plugin_cors 的模块 docstring。
"""
from __future__ import annotations

import logging

# 跟 plugin.py 同名 —— logging.getLogger 同名返回同一对象
logger = logging.getLogger("catfish.xcatfish_user.plugin")



# ── P15 / P15.4 (10/14 退役) ───────────────────────────────────────────
#
# P15 原来用闭包反射拿 hermes 流式回复里的 `_stream_q`, 把审批请求塞进 SSE 给 Companion 弹卡。
# hermes v0.22.0 原生支持了: chat/completions 流里直接发 `event: approval.request`,
# 回执走 `POST /v1/runs/{completion_id}/approval` (api_server_openai_routes.py), Companion 改接它。
# P15.4 原来把 Companion 会话强制成人工审批; 10/14 鸿波定采用 v0.22 的 smart 模式 (AI 先判断,
# 拿不准才问人), 一起退役。见 docs/HERMES-022-UPGRADE-ASSESSMENT.md「审批模式」。

# ── P14 ──────────────────────────────────────────────────────────────────
#
# P14 (6/5 鸿波) — 中文 "批准" / "拒绝" → hermes /approve / /deny slash command
#
# 背景: hermes execute_code guard (approval.py:1455) 在 gateway/ask context 走
# notify_cb 等用户决定. Companion 没注册 notify_cb (P22 BL), fallback 走 message
# field "Asking the user for approval" + 等 user `/approve` 文字命令解除. LLM
# 看到 message 翻译成中文 "请批准", 鸿波打 "批准" — hermes 不识别中文 alias →
# 不 dispatch 到 _handle_approve_command → 死循环.
#
# 本 patch: GatewayRunner._handle_message 真入口前**预处理 event.text, 中文
# alias → 改成 /approve / /deny 真 slash command `字面, 走原 hermes
# dispatch flow. 不动 hermes approval 逻辑 (松耦合).
#
# 别名设计 (鸿波语义习惯, 6/5 拍):
#   "批准"/"同意"/"通过"/"确认"/"审批" → /approve  (单次)
#   "拒绝"/"驳回"/"不同意"/"取消"   → /deny
#
# 10/13 (鸿波定 A): 文字审批渠道**只给单次批准**。微信会话是长寿的 (7/31 开的会话 10/13
# 还在用), 「本会话批准」实际等于长期放行。原来映射成 /approve session · /approve always
# 的口语、中文斜杠、以及英文 `/approve session` `/approve always` 一律降成 /approve。
# 带意思的回答 (数字 / 口语) 在审批等待时由 P52 (plugin_approval_ux.py) 处理, 这里是
# 斜杠命令走 _handle_message 的那条路, 两边口径一致。

_APPROVE_ALIASES = {
    # ── 无 / 版本 (员工口语) ─────────────────────────
    "批准": "/approve",
    "同意": "/approve",
    "通过": "/approve",
    "确认": "/approve",
    "审批": "/approve",
    "ok": "/approve",
    "好的": "/approve",
    "可以": "/approve",
    "总是批准": "/approve",
    "始终批准": "/approve",
    "总是允许": "/approve",
    "本会话批准": "/approve",
    "会话批准": "/approve",
    "本次会话": "/approve",
    "本次会话批准": "/approve",
    "拒绝": "/deny",
    "驳回": "/deny",
    "不同意": "/deny",
    "取消": "/deny",
    "no": "/deny",
    # ── 带 / 版本 (Bot 提示词里出现的 · 员工按提示复制) ─────────
    # BL-P14-SLASH-ALIAS (7/19 鸿波 catch): Bot 提示 `/批准 本次会话` · 但老 P14 patch
    # `if not raw.startswith("/")` 直接跳过 · 员工按提示发 · plugin 认不出 · 又弹审批.
    # 且 · 老死代码 _P28_CMD_ALIASES 定义未使用 · 应 dedupe 到这里.
    "/批准": "/approve",
    "/批准 本次会话": "/approve",
    "/批准本次会话": "/approve",
    "/批准 执行": "/approve",  # Bot 提示词里"回复 /批准 执行 (单次)"
    "/拒绝": "/deny",
    # 英文原生的会话级 / 永久批准也降成单次 (见上面 10/13 说明)
    "/approve session": "/approve",
    "/approve always": "/approve",
}


def _patch_p14_approve_chinese_alias() -> None:
    """GatewayRunner._handle_message 入口前预处理 event.text 中文 → slash.

    只在 session 有 blocking approval 时触发别名 (has_blocking_approval),
    避免误改正常 chat (员工说 "可以" / "好的" 当聊天话不该被吞)真.
    """
    try:
        from gateway.run import GatewayRunner
        from tools.approval import has_blocking_approval
    except ImportError as e:
        logger.warning("P14: GatewayRunner / has_blocking_approval import 失败 (%s), skip", e)
        return

    _orig_handle = GatewayRunner._handle_message

    async def patched(self, event):
        try:
            raw = (event.text or "").strip()
            # BL-P14-SLASH-ALIAS (7/19 鸿波 catch WeChat "/批准 本次会话" 又弹审批):
            # 老逻辑 `if raw and not raw.startswith("/")` 让**带 / 前缀直接跳过** ·
            # 但 Bot outbound 提示词 (P28 line 3418) 就是 `/批准 本次会话` · 员工按
            # 提示复制发 · 全被跳过 · hermes 不认 `/批准` slash · 当新 msg 触发 LLM ·
            # LLM 想帮员工又调 execute_code · 又弹审批. 死循环.
            #
            # BL-P14-SLASH-UNCONDITIONAL (7/19 二次 catch): 更细分 · 带 / 是员工
            # **明确意图** · 无论 pending 有没都翻译 (hermes 收 /approve 无 pending 会
            # silent no-op · 不会像 /批准 那样 Unknown command). 不带 / 是中文口语 ·
            # 需 has_blocking_approval gate 避免误吞正常聊天 ("好的"/"可以").
            if raw:
                alias = _APPROVE_ALIASES.get(raw.lower())
                if alias:
                    if raw.startswith("/"):
                        # 明确 slash · 无条件翻译 · pending timeout 也能续命
                        logger.info(
                            "P14 slash alias (unconditional): '%s' → '%s'", raw, alias
                        )
                        event.text = alias
                        # (原 P25 supplement 手工 approve_session(execute_code) 已删: 10/13 起
                        #  文字渠道只给单次批准, 不再有 /approve session 这条路。)
                    else:
                        # 不带 / 中文口语 · 走原 pending gate 避免误吞 chat
                        try:
                            session_key = self._session_key_for_source(event.source)
                        except Exception:
                            session_key = ""
                        if session_key and has_blocking_approval(session_key):
                            logger.info(
                                "P14 casual alias (gated): '%s' → '%s' (session=%s)",
                                raw, alias, session_key[:12],
                            )
                            event.text = alias
                elif raw.startswith("/") and raw.lower().startswith(("/批准", "/拒绝")):
                    # 带 / 中文 · 但字典没这 key · 打 log 找漏 · Bot 提示词与字典必须对齐
                    logger.warning(
                        "P14 unknown chinese slash: '%s' — 加进 _APPROVE_ALIASES 字典",
                        raw[:60],
                    )
        except Exception as e:  # noqa: BLE001
            logger.debug("P14 alias preprocess 异常 (ignored): %s", e)
        return await _orig_handle(self, event)

    GatewayRunner._handle_message = patched
    logger.info("P14 chinese approval alias patched (GatewayRunner._handle_message)")


# ── P15.3 ──────────────────────────────────────────────────────────────
#
# Hermes v2026.8.31 introduced a fail-closed unattended gate for api_server.
# That is correct for ordinary programmatic requests, but Companion's
# chat/completions path is different: the streaming request registers a
# per-session approval callback (v0.22: Hermes' native approval_notify_callback
# that emits `event: approval.request`; before that our P15).  When that
# callback exists, a human is present in the Companion UI and the request must
# reach Hermes' normal pending-approval path instead of being rejected before
# the button can be shown.
#
# Do not set approvals.unattended_mode=approve.  That would silently approve
# every api_server execute_code request, including callers that have no UI.
# This compatibility hook only changes the predicate for the current session
# while its Companion callback is registered; all other unattended callers
# remain fail-closed.

def _patch_p15_3_unattended_companion_approval() -> None:
    """Keep Companion's per-request approval loop working on new Hermes.

    Older Hermes versions do not have the unattended predicate, so this is a
    no-op there.  If the new predicate or its session state cannot be
    inspected, the original fail-closed result is preserved.
    """
    try:
        import tools.approval as approval
    except ImportError as e:
        logger.info("P15.3: Hermes unattended approval gate absent (%s), skip", e)
        return

    original = getattr(approval, "_is_unattended_platform_approval_context", None)
    if not callable(original):
        logger.info("P15.3: Hermes unattended approval gate absent, skip")
        return
    if getattr(original, "_catfish_p15_3", False):
        return

    get_session_key = getattr(approval, "get_current_session_key", None)
    callbacks = getattr(approval, "_gateway_notify_cbs", None)
    # v0.22 (10/14): 判断搬进了 tools/approval_context.py; approval.py 是 from-import 的一份,
    # execute_code / 终端命令的 _unattended_contexts 用 approval.py 那份, 而
    # _is_gateway_approval_context / _no_user_can_answer (写文件保护、Codex 审批) 用
    # approval_context 自己那份 —— 两份都得换, 不然 Companion 上一半审批会被当成"没人能回答"直接拒。
    try:
        import tools.approval_context as approval_context
    except ImportError:
        approval_context = None
    get_platform = getattr(approval_context, "_get_session_platform", None)

    def _companion_callback_is_registered() -> bool:
        """Return true only for the current session's live SSE callback."""
        if not callable(get_session_key) or not hasattr(callbacks, "get"):
            return False
        try:
            # 只认 api_server (Companion)。webhook / msgraph_webhook 的通用回合每轮都会注册回调,
            # 但那边根本没有能回答审批的界面 (上游 _NOTIFIER_BLIND_APPROVAL_PLATFORMS)
            if callable(get_platform) and get_platform() != "api_server":
                return False
            session_key = get_session_key()
            return bool(session_key and callable(callbacks.get(session_key)))
        except Exception:  # noqa: BLE001
            # Compatibility code must never turn an inspection failure into
            # an approval bypass.
            logger.debug("P15.3: 无法检查 Companion approval callback", exc_info=True)
            return False

    def patched_unattended_context() -> bool:
        if not original():
            return False
        if _companion_callback_is_registered():
            logger.debug("P15.3: current Companion session uses interactive approval")
            return False
        return True

    patched_unattended_context._catfish_p15_3 = True
    patched_unattended_context._catfish_p15_3_original = original
    approval._is_unattended_platform_approval_context = patched_unattended_context
    if approval_context is not None and callable(
            getattr(approval_context, "_is_unattended_platform_approval_context", None)):
        approval_context._is_unattended_platform_approval_context = patched_unattended_context
    logger.info(
        "P15.3 Companion approval compatibility patched: "
        "only sessions with a live SSE callback use interactive approval"
    )


# ── P15.2 ──────────────────────────────────────────────────────────────
#
# P15.2 (6/6 鸿波 audit 真根因): 跨进程 dict 问题.
# tool-bridge 进程 vs hermes daemon 进程独立, _gateway_queues 不共享.
# P15 register_gateway_notify 在 hermes daemon 进程 (_run_agent patch),
# entry 入队 _gateway_queues 在 hermes daemon 内存. tool-bridge 进程的
# _gateway_queues 是空 dict. resolve_gateway_approval 在 tool-bridge
# 进程 lookup 拿不到 entry → 返 0 → block 不解.
#
# 修法: P15.2 改成 patch hermes API server 加新 HTTP route POST
# /v1/sessions/{session_id}/approval, Companion fetch 这条 endpoint
# (走 hermes proxy 8642), 这调用走 hermes daemon 进程, resolve 真起效.

def _require_api_auth(request):
    """P15.2 短路前的 Bearer 校验 —— 返回 None 放行, 返回 Response 拒绝.

    # 为什么需要这个函数 (2026-08-20 补)

    这个 middleware 是**短路**的: 匹配到路径就直接 return, 从不调
    ``handler(request)``. 而 hermes 的鉴权不是 middleware, 是每个 handler 自己
    第一行调 ``self._check_auth(request)`` —— api_server.py 里 29 处, 全是手写的.
    上游那 4 个 middleware (cors / body_limit / security_headers / profile_prefix)
    没有一个做鉴权.

    两件事撞在一起的结果: 请求在进 handler 之前就被我们截走了, ``_check_auth``
    永远跑不到. 从 6/6 P15.2 落地到 8/20, 这条 endpoint 一直是裸的.

    最刺眼的地方不是"忘了加", 是它**看起来**有鉴权:

        Companion  tauri_services.ts:172  headers["Authorization"] = hermesAuthHeader
        Rust 代理   http_proxy.rs:220      for (k, v) in &req.headers  ← 逐条透传
        冒烟测试    smoke-test.sh:198      -H "Authorization: Bearer $HERMES_API_KEY"

    三个客户端都在发, 头一路送到底, 只是没有任何人验它. 判据比真事宽一格,
    而这一格底下是「execute_code 每次必须人工批准」这条红线 —— 本机任意进程
    不带 token POST 一下, 就能替员工点"批准".

    (监听是 127.0.0.1, 不是远程可利用; 但本地进程边界在政企环境里也是边界.)

    # fail-closed 的取舍

    拿不到 adapter, 或上游把 ``_check_auth`` 改名了 —— 这里**拒绝**, 不放行.

      · 拒绝的代价: 审批按钮失效, agent 卡在等批准. 是看得见的故障.
      · 放行的代价: 这道门重新变回摆设, 而且没有任何人会发现.

    装载期另有一道: ``plugin_verify._APISERVER_METHOD_TARGETS`` 里钉了
    ``_check_auth``, 上游改名会在插件加载时 fail-loud —— 不用等到员工点不动
    按钮才发现.

    # 两个已经核过的前提 (别再猜)

      1. ``request.app.get("api_server_adapter")`` 拿得到实例 ——
         api_server.py:6994 ``self._app["api_server_adapter"] = self``
      2. ``_check_auth`` 读 ``_api_request_profile`` ContextVar, 而它由
         ``profile_prefix_middleware`` 设. aiohttp 的顺序是「列表第一个 = 最外层」
         (实测 3.13.5), 上游把 profile_prefix 放在 mws[0], 我们是 append 进去的
         最内层 —— 所以我们跑的时候 ContextVar 已经设好了, profile 作用域有效.
    """
    import aiohttp.web as _aw

    adapter = request.app.get("api_server_adapter")
    check = getattr(adapter, "_check_auth", None)
    if not callable(check):
        logger.error(
            "P15.2: 取不到 api_server_adapter._check_auth, fail-closed 拒绝审批请求. "
            "上游可能动了 api_server.py (self._app['api_server_adapter'] 赋值, "
            "或 APIServerAdapter._check_auth 改名). plugin_verify 应该已经在装载期报过."
        )
        return _aw.json_response(
            {"error": "approval auth unavailable"}, status=503
        )
    # None = 通过; Response(401) = 拒绝. 直接把上游的 401 body 透出去,
    # 跟其它 endpoint 的错误形状保持一致.
    return check(request)


def _patch_p15_2_chat_approval_route() -> None:
    """注册 chat_approval middleware 到 hermes api_server.

    aiohttp Application 在 AppRunner.setup() 后 frozen, 不能加 route. 跟 P7 一样
    用 middleware 拦截 path. _patched_app_init (在 _patch_p8_p9_cors 里) 创建
    hermes _app 时 inject middlewares, 这里把 chat_approval_middleware 也 inject.
    middleware 检测 POST /v1/sessions/{sid}/approval, 调 resolve_gateway_approval.

    跑在 hermes daemon 进程 (因为这是 hermes APIServerAdapter), _gateway_queues
    跟 P15 register 的 entry 共享.
    """
    try:
        import aiohttp.web as _aw
        from tools.approval import resolve_gateway_approval
    except ImportError as e:
        logger.warning("P15.2: aiohttp / approval import 失败 (%s), skip", e)
        return

    @_aw.middleware
    async def chat_approval_middleware(request, handler):
        # path match: /v1/sessions/<sid>/approval
        if request.method == "POST":
            path = request.path
            if path.startswith("/v1/sessions/") and path.endswith("/approval"):
                parts = path.strip("/").split("/")
                if len(parts) == 4 and parts[3] == "approval":
                    # 鉴权必须在这里, 不能往下挪 —— 下面每一条路径都是 return,
                    # handler 里那行 _check_auth 不会有机会跑. 见 _require_api_auth.
                    # 也必须在 request.json() 之前: 未鉴权的请求不该让我们花 IO
                    # 去读它的 body.
                    auth_err = _require_api_auth(request)
                    if auth_err is not None:
                        return auth_err
                    session_id = parts[2]
                    try:
                        body = await request.json()
                    except Exception:
                        return _aw.json_response(
                            {"error": "JSON body required"}, status=400
                        )
                    choice = str(body.get("choice", "")).strip().lower()
                    if choice not in {"once", "session", "always", "deny"}:
                        return _aw.json_response(
                            {"error": f"choice ∈ once/session/always/deny, got: {choice!r}"},
                            status=400,
                        )
                    try:
                        # resolve_all (批量批准) 8/20 删掉: 全仓 grep 零调用方
                        # (命中的全是各 venv 里 pygments/pip 的同名符号). 一个
                        # 没人用、却在安全端点上放大权限的开关 —— 上游默认
                        # resolve_all=False, 这里就是那个默认值, 行为不变.
                        resolved = resolve_gateway_approval(session_id, choice)
                        logger.info(
                            "P15.2 chat_approval resolved=%d choice=%s sid=%s",
                            resolved, choice, session_id[:24],
                        )
                        return _aw.json_response(
                            {"resolved": resolved, "choice": choice}
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.exception("P15.2: chat_approval resolve 异常")
                        return _aw.json_response({"error": str(e)}, status=500)
        return await handler(request)

    # 注册到 module global, _patched_app_init 会读这个 list (跟 P7 一样的 pattern).
    # _patch_p8_p9_cors 里 inject middlewares. 这里 expose 给那边 import.
    global _chat_approval_middleware  # noqa: PLW0603
    _chat_approval_middleware = chat_approval_middleware
    logger.info("P15.2 chat_approval middleware registered (waiting for app init)")


# module-level reference for _patched_app_init in P7 path
_chat_approval_middleware = None  # noqa: PLW0603
