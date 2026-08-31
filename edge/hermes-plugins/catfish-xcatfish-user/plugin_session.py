"""会话相关 patch —— 从 plugin.py 拆出 (8/15 第 2 趟)。

P2 (_current_main_runtime) · P4 (auto_title_session) · P16 (session_search
76s→340ms) · P17 (后台复审注入) · P40 (Companion 用户消息去重)。

共同点: 都要 resolver / session_registry 这两个兄弟模块。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional
import functools
import json
import time

# 跟 plugin.py 同名 —— logging.getLogger 同名返回同一对象
logger = logging.getLogger("catfish.xcatfish_user.plugin")


def _sib(name):
    """延迟取兄弟模块。

    # ⚠ 8/15 晚修的 bug —— 这个函数原来是坏的, 而且坏得很安静

    早上把 plugin.py 从 3226 行拆开时, 我写了这个 helper, body 是一行:

        from plugin import _import_sibling      # ← 裸 absolute import

    **那正是这个插件不能用的写法。** 目录名带 dash (`catfish-xcatfish-user`),
    hermes 用 `spec_from_file_location` + `submodule_search_locations` 加载,
    模块在 sys.modules 里叫 `catfish-xcatfish-user.plugin`, 没有叫 `plugin` 的。

    讽刺的是 plugin.py 里的 `_import_sibling` 有三段 fallback, 存在的理由就是
    这个 (5/28 那次"装好 9 天没工作"之后加的) —— 而我写的这个 helper, 名义上是
    "复用那三段", 实际用了三段要绕开的那一段。

    ## 为什么拖到晚上才发现

    hermes 进程从拆分之前就一直跑着, Python 把老模块缓存在内存里。8/15 18:03
    重启 gateway 之后才第一次加载新拆的模块, 日志里立刻冒出 4 条:

        P23 inbound: picker ... failed: No module named 'plugin'
        P1 post-init apply_headers failed: No module named 'plugin'
        P6/P11 _create_agent post-init failed: No module named 'plugin'

    三处都被 `except Exception` 包着, 只 warning 不抛 —— 员工看不出任何异常,
    只是 header 注入、picker 模型覆盖这些悄悄不干活了。

    单测也没抓住: 测试里 `sys.path.insert(0, PLUGIN_DIR)` 之后裸 import 是通的,
    生产的加载方式不通。**判据比真事窄**, 今天栽的第 N 次。

    # 现在的写法

    段 1 走相对 import —— 跟 plugin.py 的 `_import_sibling` 段 1 是**同一条路**,
    所以命中的是 sys.modules 里同一个 module 对象, 不会造出第二份
    (双 module 对象那个病今天在 catfish-memory 上专门防过)。

    段 2/3 保留原来的路径, 兜住 `__package__` 没设好的加载方式。
    """
    from importlib import import_module

    # 段 1: 相对 —— 生产上走的就是这条 (hermes 给了 submodule_search_locations)
    if __package__:
        try:
            return import_module(f".{name}", package=__package__)
        except (ImportError, SystemError, ValueError, TypeError):
            pass

    # 段 2: plugin 的三段 fallback (它自己能被裸 import 到时才通)
    try:
        from plugin import _import_sibling
        return _import_sibling(name)
    except ImportError:
        pass

    # 段 3: 按文件路径兜底 (跟 plugin._import_sibling 段 3 同款)
    import importlib.util
    import sys as _sys
    from pathlib import Path

    _py = Path(__file__).parent / f"{name}.py"
    if not _py.exists():
        raise ImportError(f"{name}.py 不存在: {_py}")
    _modname = f"_catfish_xcatfish_user_{name}"
    if _modname in _sys.modules:          # 防重复 exec 出第二个 module 对象
        return _sys.modules[_modname]
    _spec = importlib.util.spec_from_file_location(_modname, _py)
    if not _spec or not _spec.loader:
        raise ImportError(f"spec_from_file_location 失败: {_py}")
    _mod = importlib.util.module_from_spec(_spec)
    _sys.modules[_modname] = _mod
    _spec.loader.exec_module(_mod)
    return _mod


def _resolver():
    return _sib("resolver")


def _session_registry():
    return _sib("session_registry")




# ── P2 ───────────────────────────────────────────────────────────────────

def _patch_p2_current_main_runtime() -> None:
    """AIAgent._current_main_runtime 加 catfish_outgoing_user + 注册到 session_registry."""
    from run_agent import AIAgent

    _orig = AIAgent._current_main_runtime

    def patched(self: Any) -> dict:
        d = _orig(self)
        cf_user = _resolver().resolve_for_agent(self)
        if cf_user:
            d["catfish_outgoing_user"] = cf_user
            # 注册给 auxiliary task (auto_title 等) 跨线程查找
            sid = getattr(self, "session_id", "") or ""
            if sid:
                _session_registry().register(sid, cf_user)
        return d

    AIAgent._current_main_runtime = patched




# ── P4 ───────────────────────────────────────────────────────────────────

def _patch_p4_auto_title_session() -> None:
    """跨线程补 runtime，并阻止私有/服务请求启动标题 LLM。"""
    from agent import title_generator

    guard = _sib("private_auxiliary_guard")

    _orig = title_generator.auto_title_session

    def patched_auto_title_session(session_db, session_id, *args, **kwargs):
        main_runtime = kwargs.get("main_runtime")
        if isinstance(main_runtime, dict) and "catfish_outgoing_user" not in main_runtime:
            cf_user = _session_registry().lookup(session_id)
            if cf_user:
                main_runtime["catfish_outgoing_user"] = cf_user
                kwargs["main_runtime"] = main_runtime
        model = (
            (main_runtime or {}).get("model", "")
            if isinstance(main_runtime, dict)
            else ""
        )
        source = _session_registry().lookup_source(session_id)
        if guard.should_skip_title(model, source):
            logger.info(
                "P47: skip auxiliary title_generation for model=%s source=%s",
                model or "unknown", source or "unknown",
            )
            return None
        return _orig(session_db, session_id, *args, **kwargs)

    title_generator.auto_title_session = patched_auto_title_session




# ── P16 (P3.4.C 6/15 鸿波: session_search 76s → 340ms) ──────────────────

def _patch_p16_session_search() -> None:
    """直接替换 tools.session_search_tool.session_search 函数本体.

    # 真因 (鸿波 ~/.hermes/logs/agent.log)
    hermes 原生 session_search 的 _discover mode 76-101s, 改 hard monkey-patch
    走 catfish 340ms 快版 (跟 P1-P15 同模式).

    # 路由
    - discovery (query 非空, 无 session_id/around_message_id/profile) → catfish 快版
    - scroll / read / browse / cross-profile → fallback 原 hermes session_search

    # P3.4.C fail-safe
    **整个函数体外层 try/except** — patch 失败也不阻塞 hermes 启动 (鸿波 6/15 21:35
    撞 "Could not connect to the server" hermes 起不来, 真因可能是 P16 抛异常导
    致 _apply_patches 整体挂). fail-silent 退化到 hermes 原生 76s.
    """
    try:
        from tools import session_search_tool
    except ImportError as e:
        logger.warning("P16: tools.session_search_tool import 不到, 跳过: %s", e)
        return

    try:
        _orig_session_search = session_search_tool.session_search
    except AttributeError as e:
        logger.error(
            "P16: tools.session_search_tool 没 session_search 属性 (hermes 升级改了模块结构?), 跳过: %s",
            e,
        )
        return

    # 懒加载 session_search_router (避免 import-time 循环依赖, 跟 memory_router 同模式)
    try:
        from pathlib import Path as _Path  # noqa: PLC0415
        import importlib.util as _iu  # noqa: PLC0415
        _ssr_py = _Path(__file__).parent / "session_search_router.py"
        _spec = _iu.spec_from_file_location(
            "_catfish_xcatfish_user_session_search_router_p16", _ssr_py
        )
        if _spec is None or _spec.loader is None:
            logger.error("P16: spec_from_file_location 返 None, 跳过 patch")
            return
        _ssr = _iu.module_from_spec(_spec)
        _spec.loader.exec_module(_ssr)
    except Exception as e:  # noqa: BLE001
        logger.error("P16: session_search_router.py 加载失败, 跳过 patch: %s", e)
        return

    def patched_session_search(
        query: str = "",
        role_filter: Optional[str] = None,
        limit: int = 3,
        db: Any = None,
        current_session_id: Optional[str] = None,
        session_id: Optional[str] = None,
        around_message_id: Optional[int] = None,
        window: int = 5,
        sort: Optional[str] = None,
        profile: Optional[str] = None,
    ) -> str:
        """P3.4.C (6/15 鸿波): discovery 走 catfish 快版, 其他透传原生."""
        # patched 函数内部的所有异常都 try/except 包住, 退化到原生.
        try:
            # 非 discovery (scroll / read / browse / cross-profile) → 透传原生
            if session_id or around_message_id is not None or profile:
                return _orig_session_search(
                    query=query, role_filter=role_filter, limit=limit, db=db,
                    current_session_id=current_session_id, session_id=session_id,
                    around_message_id=around_message_id, window=window, sort=sort,
                    profile=profile,
                )
            if not query or not isinstance(query, str) or not query.strip():
                return _orig_session_search(
                    query=query, role_filter=role_filter, limit=limit, db=db,
                    current_session_id=current_session_id,
                )

            # Discovery → catfish 快版
            try:
                return _ssr._discovery_via_catfish(query.strip(), {"limit": limit})
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "P16 catfish 快版异常, fallback hermes 原生: %s", e, exc_info=True
                )
                return _orig_session_search(
                    query=query, role_filter=role_filter, limit=limit, db=db,
                    current_session_id=current_session_id,
                )
        except Exception as e:  # noqa: BLE001
            # 兜底: patched 函数本身挂 (e.g. _orig_session_search 不接受某个 kwarg) →
            # 不抛出, 返友好错误 JSON. 不阻塞 agent loop.
            logger.error("P16 patched_session_search 顶层异常: %s", e, exc_info=True)
            import json as _json  # noqa: PLC0415
            return _json.dumps({
                "success": False,
                "error": f"P16 patched session_search 异常: {e}",
                "_catfish_p16_fault": True,
            }, ensure_ascii=False)

    try:
        session_search_tool.session_search = patched_session_search
    except Exception as e:  # noqa: BLE001
        # 极少: module 是 read-only / immutable — 不阻塞 hermes 启动
        logger.error(
            "P16: session_search_tool.session_search 赋值失败 (跳过 patch, 用 hermes 原生): %s",
            e,
        )
        return

    logger.info(
        "catfish-xcatfish-user: P16 session_search hard patch ✓ "
        "(P3.4.C: 76-101s → ~340ms discovery, scroll/read/browse 透传)"
    )




# ── P17 (P3.4.D 6/15 鸿波: bg-review HTTP 400 X-Catfish-User missing) ──

def _patch_p17_bg_review_inject() -> None:
    """patch AIAgent.__init__ post-init — bg-review review_agent 自动注 X-Catfish-User.

    # 真因 (鸿波 6/15 ~/.hermes/logs/errors.log 反复出现)

    "[api-XXX] agent.conversation_loop: API call failed (attempt 1/3)
     error_type=BadRequestError thread=bg-review:NNN ... HTTP 400:
     'service token (sub=client:hermes-cli) requires X-Catfish-User header
     to identify on-behalf-of user'"

    每次 advisor agent loop 跑完都触发, 浪费 1 次重试 + 污染 log.

    # 真因路径 (audit ~/.hermes/hermes-agent/agent/background_review.py:402)

    bg-review 走 `review_agent = AIAgent(...)` 直接构造, **不调 agent_init.init_agent**.
    catfish-xcatfish-user 的 P1 wrap 的是 init_agent, bg-review 漏掉.

    review_agent 没继承 parent agent 的 `_catfish_outgoing_user` 属性
    (catfish 自己的属性, hermes init 不知道). resolver.resolve_for_agent 拿不到,
    headers 不注入 X-Catfish-User, gateway 400.

    # 修法

    hook AIAgent.__init__ post-init:
      1. 检测 thread name 含 "bg-review" → bg-review review_agent 场景
      2. parent_session_id 不空 → session_registry.lookup 拿 parent cf_user
      3. 设 self._catfish_outgoing_user = cf_user
      4. 调 _apply_client_headers_for_base_url + _replace_primary_openai_client (跟 P1 同)
      5. idempotent (检 _catfish_p17_injected 标记防重)
      6. fail-safe (任一步挂不阻塞 AIAgent 构造)

    主 chat agent (走 init_agent → P1 处理) 不受影响 — P17 仅在 bg-review thread 触发.
    """
    try:
        from run_agent import AIAgent
    except ImportError as e:
        logger.warning("P17: run_agent.AIAgent import 不到, 跳过: %s", e)
        return

    try:
        _orig_init = AIAgent.__init__
    except AttributeError as e:
        logger.error("P17: AIAgent 没 __init__ (hermes 升级?), 跳过: %s", e)
        return

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        # P3.4.D follow-up 1: 在 _orig_init 跑前抓 kwargs 里的 parent_session_id —
        #   实测 hermes AIAgent.__init__ 接 parent_session_id kwarg (见 hermes
        #   agent/background_review.py:402), 但**不存到 self.parent_session_id**.
        #   下面 getattr 拿不到, 必须从 kwargs 直接抓 (在原 init 跑前抓, 防 kwargs 被 pop).
        parent_session_id_from_kwargs = (kwargs.get("parent_session_id") or "")
        if not isinstance(parent_session_id_from_kwargs, str):
            parent_session_id_from_kwargs = ""

        # 1. 先跑原 __init__
        _orig_init(self, *args, **kwargs)

        # 2. post-init bg-review 注入 (fail-safe — 任一步挂不阻塞)
        try:
            import threading  # noqa: PLC0415
            thread_name = threading.current_thread().name or ""

            # 只覆盖 bg-review thread, chat agent 走 P1 (init_agent post-hook)
            if "bg-review" not in thread_name.lower():
                return

            # idempotent — 同 agent 多次 init 不要重复注入
            if getattr(self, "_catfish_p17_injected", False):
                return

            # 优先 kwargs (hermes 不存 self.parent_session_id), fallback attribute
            parent_session_id = parent_session_id_from_kwargs or (
                getattr(self, "parent_session_id", "") or ""
            )
            if not parent_session_id:
                logger.warning(
                    "P17: bg-review thread '%s' review_agent 无 parent_session_id "
                    "(kwargs 也无), 无法 lookup cf_user. session_id=%s. "
                    "(hermes 升级改了 background_review.py 构造参数?)",
                    thread_name,
                    getattr(self, "session_id", "(unset)"),
                )
                return

            cf_user = _session_registry().lookup(parent_session_id)
            if not cf_user:
                # fallback (P3.4.D follow-up): 直接调 resolver.resolve_for_agent 走 5 步,
                # 含 (e) env CATFISH_DEFAULT_USER 兜底. 这样即使 session_registry 没 register
                # (chat agent 的 _current_main_runtime 没跑过), env 兜底也能注入.
                cf_user = _resolver().resolve_for_agent(self)
                if cf_user:
                    logger.info(
                        "P17: bg-review parent_session=%s session_registry 没 cf_user, "
                        "resolver fallback 拿到: %s",
                        parent_session_id[:12], cf_user,
                    )
                else:
                    import os as _os  # noqa: PLC0415
                    logger.warning(
                        "P17: bg-review 拿不到 cf_user — session_registry.lookup('%s')=None, "
                        "resolver.resolve_for_agent 也 None (CATFISH_DEFAULT_USER=%r). "
                        "chat agent 这条路径可能没 register cf_user (e.g. advisor companion-internal "
                        "走 service token skip_identity, 没设 _catfish_outgoing_user). "
                        "set env CATFISH_DEFAULT_USER 或确认 P2 cf_user 注入路径",
                        parent_session_id[:12],
                        _os.environ.get("CATFISH_DEFAULT_USER", "(unset)"),
                    )
                    return

            # 真注入 — 跟 P1 同 2 步: 设 attr + 调 2 个 method
            self._catfish_outgoing_user = cf_user
            if hasattr(self, "_apply_client_headers_for_base_url"):
                self._apply_client_headers_for_base_url(
                    str(getattr(self, "base_url", "") or "")
                )
            if hasattr(self, "_replace_primary_openai_client"):
                self._replace_primary_openai_client(
                    reason="catfish_xcatfish_user_p17_bg_review_inject"
                )
            self._catfish_p17_injected = True
            logger.info(
                "P17: bg-review review_agent X-Catfish-User 注入 ✓ "
                "(cf_user=%s, parent_session=%s, thread=%s)",
                cf_user,
                parent_session_id[:12],
                thread_name,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "P17: bg-review X-Catfish-User 注入异常 (不阻塞 agent 构造): %s",
                e, exc_info=True,
            )

    try:
        AIAgent.__init__ = patched_init
    except Exception as e:  # noqa: BLE001
        logger.error("P17: AIAgent.__init__ 赋值失败 (跳过 patch): %s", e)
        return

    logger.info(
        "catfish-xcatfish-user: P17 bg-review X-Catfish-User 注入 hook ✓ "
        "(P3.4.D: bg-review HTTP 400 fix)"
    )




# ── P39 (7/31): Codex App Server 不复制 OAuth 凭据 ─────────────────────
#
# Hermes 0.18 的 gateway `_resolve_runtime_agent_kwargs()` 先调用
# `resolve_runtime_provider()`. 当 model.provider=openai-codex 时，这一步先从
# ~/.hermes/auth.json 取 OAuth；取不到就抛错，后面的
# `model.openai_runtime=codex_app_server` 判断根本没有机会执行。
#
# 但 App Server runtime 的真实客户端是 `codex app-server` 子进程，它自行读取
# ~/.codex/auth.json，Hermes 不需要也不应该另存 OAuth token。这里仅在显式
# `codex_app_server + openai-codex` 组合下返回无网络用途的本机 runtime 描述，
# 让 AIAgent 进入 codex_app_server 分支。普通 provider/runtime 完全走原函数。



# ── P40 (7/31): Companion/Hermes/Codex 共享 state.db 的 user 双写防护 ─────

def _patch_p40_companion_user_message_dedup() -> None:
    """相邻相同 user message 只保留一条，且只作用于 Companion 会话。

    Companion 把同一个 session id 交给 Hermes API server；Codex App Server
    runtime 里，请求入口和 turn flush 可能各调一次 SessionDB.append_message。
    两次之间可能隔着整个冷启动（实测 1–12 秒），所以不能靠很短的时间窗。

    规则刻意保守：
    - session.source 必须是 ``companion``；微信/Slack/CLI 完全不受影响；
    - 当前最后一条 active message 也必须是相同内容的 user；
    - 两条相距不超过 30 秒（8/1 从 120 收窄）。正常一问一答中间有 assistant，
      不会误去重；30 秒 = 上面实测冷启动上限 12 秒的 2.5 倍留余量。
      120 秒太宽：判据是"内容相同"不是"同一个 id"，两分钟内员工连点两次发送、
      连发两次"继续"、auto-continue 的固定 prompt 都会被当成重复吞掉，
      而且吞掉之后调用方以为写成功了，界面上没有任何提示。
      彻底的解法是幂等键（Companion 带一个 client id 进来按它去重），
      那样窗口宽窄就无所谓了。
    """
    try:
        from hermes_state import SessionDB  # noqa: PLC0415
    except ImportError as e:
        logger.warning("P40: hermes_state.SessionDB 没导, skip patch (%s)", e)
        return

    original = getattr(SessionDB, "append_message", None)
    if original is None:
        raise RuntimeError("hermes_state.SessionDB.append_message 不存在")
    if getattr(original, "_catfish_p40_patched", False):
        return

    locks_guard = threading.Lock()
    session_locks: dict[str, threading.Lock] = {}

    def _content_key(value) -> str:
        if isinstance(value, str):
            return value.strip()
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except Exception:
            return str(value).strip()

    @functools.wraps(original)
    def patched_append_message(
        self,
        session_id: str,
        role: str,
        content=None,
        *args,
        **kwargs,
    ):
        if role != "user" or not session_id:
            return original(self, session_id, role, content, *args, **kwargs)

        with locks_guard:
            session_lock = session_locks.setdefault(session_id, threading.Lock())

        with session_lock:
            try:
                session = self.get_session(session_id)
                if str((session or {}).get("source") or "") == "companion":
                    messages = self.get_messages(session_id)
                    if messages:
                        last = messages[-1]
                        last_ts = float(last.get("timestamp") or 0)
                        incoming_ts = kwargs.get("timestamp")
                        if hasattr(incoming_ts, "timestamp"):
                            incoming_ts = incoming_ts.timestamp()
                        try:
                            incoming_ts = float(incoming_ts)
                        except (TypeError, ValueError):
                            incoming_ts = time.time()

                        if (
                            last.get("role") == "user"
                            and _content_key(last.get("content"))
                            == _content_key(content)
                            # 窗口 120 秒 → 30 秒。
                            #
                            # 判据是**内容相同**而不是同一个 id, 所以窗口越宽,
                            # 吞掉合法重复的机会越大: 员工觉得卡了连点两次发送、
                            # 连发两次"继续"、ChatPanel 的 onNudge 固定发"继续"
                            # 被点两次、auto-continue 的固定 prompt —— 这些在
                            # 两分钟内都很常见, 而命中之后 return existing_id,
                            # 调用方以为写成功了, 那条消息就这么没了, 界面零提示。
                            #
                            # 为什么不收得更狠: 上面 docstring 记着实测冷启动要
                            # 1–12 秒, 两次写之间可能隔着它。30 秒 = 实测上限的
                            # 2.5 倍留余量, 而 120 秒是 10 倍, 白白把一堆合法
                            # 重复圈了进来。
                            #
                            # 真正的解法是幂等键 (Companion 生成一个 client id
                            # 一起写进来, 按它去重), 那样窗口可以无限宽也不会
                            # 误伤。这里先把窗口收到合理范围。
                            and 0 <= incoming_ts - last_ts <= 30
                        ):
                            existing_id = int(last["id"])
                            # ⚠ 不打聊天正文。这是全仓唯一一处会把用户消息内容
                            # 落到 hermes 日志文件里的地方, 而护栏第 3 条是
                            # "数据零出端"。定位问题有 session + rowid 就够了。
                            logger.info(
                                "P40 dedup skip: session=%s user rowid=%s (len=%d)",
                                session_id,
                                existing_id,
                                len(_content_key(content)),
                            )
                            return existing_id
            except Exception:
                # 防重失败不能阻断聊天；回落 Hermes 原写入并留日志。
                logger.warning(
                    "P40 dedup check failed; fallback original append",
                    exc_info=True,
                )

            return original(self, session_id, role, content, *args, **kwargs)

    patched_append_message._catfish_p40_patched = True  # type: ignore[attr-defined]
    SessionDB.append_message = patched_append_message
    logger.info(
        "P40 Companion user-message dedup patched at SessionDB.append_message ✓"
    )
