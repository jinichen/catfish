"""P39 · Codex App Server 会话池 + auth 旁路。

从 plugin.py 抽出 (8/13)。plugin.py 4349 行, 越过 CLAUDE.md §1 的 800 行硬红线,
这是拆的第一块 —— 挑它是因为它最自足: 5 个模块级状态 + 4 个 helper + 1 个 patch,
plugin.py 其余部分只在 `_apply_patches` 里调它一次。

# 这块在做什么

Hermes API server 每条消息都新建一个 AIAgent, 上游把 Codex session 挂在 agent 上,
于是每轮都是冷启动。这里按 (聊天 session, tenant, model) 复用真正的 Codex session,
最多留 4 个, 空闲 15 分钟由后台 janitor 关掉。

拆解顺序上有一条硬约束: **绝不能在 Codex 正在出结果时把 subprocess 拆掉**。
所以 janitor 只回收 `lock` 拿得到的条目, 拿不到就原样放回池子 (见
`_p39_close_cache_entries`)。

# 依赖是注入进来的, 不是 import 进来的

本模块需要两个兄弟模块 (`resolver` / `model_authority`), 但兄弟之间不能直接 import:
包名带 dash, 得走 plugin.py 里那个三段 fallback 的 `_import_sibling`, 而
import 它就成了循环。

所以由 plugin.py 在装载后写进来 (见 plugin.py 的 re-export 段)。`install()` 开头
会检查有没有接上 —— 没接上就当场 raise, 不给"半装载"留机会。这跟 plugin.py 文件头
写的 fail-loud 原则一致: 静默半装载 = 跨员工串数据的 P0。
"""
from __future__ import annotations

import atexit
import functools
import logging
import threading
import time
from typing import Any

logger = logging.getLogger("catfish.xcatfish_user.plugin")

#: 由 plugin.py 注入 —— 见文件头"依赖是注入进来的"。
resolver: Any = None
model_authority: Any = None


def _require_wiring() -> None:
    """没接上依赖就当场炸, 不留"半装载"的余地。"""
    missing = [n for n in ("resolver", "model_authority") if globals().get(n) is None]
    if missing:
        raise RuntimeError(
            f"plugin_codex_session 没接上依赖 {missing} —— "
            "plugin.py 应在 _import_sibling 之后把它们写进来"
        )


# P39 Codex App Server session pool. Hermes API server 会每条消息新建 AIAgent，
# 所以上游把 session 挂在 agent 上等于每轮冷启动。Catfish 按聊天 session +
# tenant + model 复用真正的 Codex session；最多保留 4 个，空闲 15 分钟自动关闭。
_P39_CODEX_CACHE: dict[str, dict[str, Any]] = {}
_P39_CODEX_CACHE_LOCK = threading.RLock()
_P39_CODEX_CACHE_TTL_SECONDS = 15 * 60
_P39_CODEX_CACHE_MAX = 4
_P39_CODEX_JANITOR_STARTED = False


def _p39_close_cache_entries(entries, reason: str) -> None:
    """Close already-detached cache entries outside the global lock."""
    for key, entry in entries:
        entry_lock = entry.get("lock")
        if entry_lock is None or not entry_lock.acquire(timeout=0.2):
            # A live turn owns it. Put it back so a janitor never tears down
            # a subprocess while Codex is producing a response.
            with _P39_CODEX_CACHE_LOCK:
                _P39_CODEX_CACHE.setdefault(key, entry)
            continue
        try:
            session = entry.get("session")
            if session is not None:
                session.close()
                logger.info("P39 Codex session closed: key=%s reason=%s", key, reason)
        except Exception as e:  # noqa: BLE001
            logger.warning("P39 Codex session close failed (%s): %s", key, e)
        finally:
            entry_lock.release()


def _p39_prune_codex_cache() -> None:
    now = time.monotonic()
    victims = []
    with _P39_CODEX_CACHE_LOCK:
        for key, entry in list(_P39_CODEX_CACHE.items()):
            entry_lock = entry.get("lock")
            idle = now - float(entry.get("last_used") or now)
            if idle >= _P39_CODEX_CACHE_TTL_SECONDS and not entry_lock.locked():
                victims.append((key, _P39_CODEX_CACHE.pop(key)))

        overflow = max(0, len(_P39_CODEX_CACHE) - _P39_CODEX_CACHE_MAX)
        if overflow:
            candidates = sorted(
                (
                    (key, entry)
                    for key, entry in _P39_CODEX_CACHE.items()
                    if not entry["lock"].locked()
                ),
                key=lambda value: float(value[1].get("last_used") or 0),
            )
            for key, _entry in candidates[:overflow]:
                victims.append((key, _P39_CODEX_CACHE.pop(key)))
    _p39_close_cache_entries(victims, "idle_or_lru")


def _p39_close_all_codex_sessions() -> None:
    with _P39_CODEX_CACHE_LOCK:
        entries = list(_P39_CODEX_CACHE.items())
        _P39_CODEX_CACHE.clear()
    _p39_close_cache_entries(entries, "process_exit")


def _p39_start_codex_janitor() -> None:
    global _P39_CODEX_JANITOR_STARTED
    with _P39_CODEX_CACHE_LOCK:
        if _P39_CODEX_JANITOR_STARTED:
            return
        _P39_CODEX_JANITOR_STARTED = True

    def _run() -> None:
        # 循环体必须整个包住。裸的 `while True: prune()` 只要抛一次异常线程就
        # **永久退出** —— daemon 线程没人重启也没人告警, 从那一刻起 Codex 的
        # app-server 子进程再也不回收, 而现象要等到机器上进程堆满才看得出来,
        # 那时早就跟这里对不上号了。
        while True:
            try:
                time.sleep(60)
                _p39_prune_codex_cache()
            except Exception:
                logger.warning("P39 Codex 会话清理这一轮失败, 下一轮继续", exc_info=True)

    threading.Thread(
        target=_run,
        name="catfish-codex-session-janitor",
        daemon=True,
    ).start()


def _patch_p39_codex_app_server_auth_bypass() -> None:
    """让 Codex CLI 独占管理 ChatGPT 登录，跳过无关的 Hermes OAuth 预检。"""
    try:
        from gateway import run as _gateway_run  # noqa: PLC0415
    except ImportError as e:
        logger.warning("P39: hermes gateway.run module 没导, skip patch (%s)", e)
        return

    original = getattr(_gateway_run, "_resolve_runtime_agent_kwargs", None)
    if original is None:
        logger.warning("P39: _resolve_runtime_agent_kwargs 不存在, skip patch")
        return
    if getattr(original, "_catfish_p39_patched", False):
        return

    @functools.wraps(original)
    def patched_runtime_agent_kwargs():
        try:
            config = _gateway_run._load_gateway_runtime_config()
        except Exception as e:  # noqa: BLE001
            logger.warning("P39 读取 runtime 配置失败, fallback Hermes 原路径: %s", e)
            return original()

        model_cfg = config.get("model", {}) if isinstance(config, dict) else {}
        if not isinstance(model_cfg, dict):
            return original()

        runtime = str(model_cfg.get("openai_runtime") or "").strip().lower()
        provider = str(model_cfg.get("provider") or "").strip().lower()
        config_is_codex = runtime == "codex_app_server" and provider == "openai-codex"

        # Picker 状态和 gateway runtime 在切换窗口内不一致时，宁可明确拒绝
        # 这一条，也绝不能把 DeepSeek 发进 Codex（或反过来）。正常情况下 Rust
        # 会等到旧 gateway 真退出、新进程稳定后才让 UI 解锁；这是第二道保险。
        #
        # ⚠ 这道保险有过两条**自己关掉自己**的路, 8/1 补上:
        #
        #   1. `except Exception: codex_ids = set()` 一声不吭, 而下一行的
        #      `if codex_ids and ...` 遇到空集合直接短路 —— hermes_cli 的
        #      codex_models 导入失败或改了 API, 这道"绝不能把 DeepSeek 发进
        #      Codex"的保险就**永远通过**, 而且没有任何痕迹。一个永远放行的
        #      保险丝比没有保险丝更危险: 它让人以为有。
        #   2. `model_authority.read_picker_model()` 在 json 解析失败时返回 ""，
        #      `if picker_model:` 于是整段跳过, 同样无声。
        #
        # 现在两条都记日志。注意**不能**改成"取不到就拒绝请求"——
        # picker 文件不存在是全新装机的正常状态, 那样会让新员工一条都发不出去。
        # 能做的是: 保险失效时必须在日志里留下痕迹, 让排查的人找得到。
        picker_model = model_authority.read_picker_model()
        if not picker_model:
            logger.debug(
                "P39 跨 runtime 保险跳过: 读不到 picker_model "
                "(全新装机时正常; 若员工确实在切模型, 说明 ~/.catfish/picker_model 有问题)"
            )
        if picker_model:
            try:
                from hermes_cli.codex_models import get_codex_model_ids  # noqa: PLC0415

                codex_ids = {
                    value
                    for value in get_codex_model_ids()
                    if isinstance(value, str) and value.startswith("gpt-")
                }
            except Exception:
                logger.warning(
                    "P39 跨 runtime 保险**已失效**: 取不到 Codex 模型清单, "
                    "本次请求不做 picker/runtime 一致性校验",
                    exc_info=True,
                )
                codex_ids = set()
            else:
                if not codex_ids:
                    logger.warning(
                        "P39 跨 runtime 保险**已失效**: Codex 模型清单为空 "
                        "(可能 Codex 换用了非 gpt- 前缀的模型 id), 本次不做一致性校验"
                    )
            picker_is_codex = picker_model in codex_ids
            if codex_ids and picker_is_codex != config_is_codex:
                logger.warning(
                    "P39 blocked cross-runtime request during model switch: "
                    "picker=%s runtime=%s provider=%s",
                    picker_model,
                    runtime,
                    provider,
                )
                raise RuntimeError(
                    "模型运行通道仍在切换，请等待下拉框恢复后重试"
                )

        if config_is_codex:
            max_tokens = model_cfg.get("max_tokens")
            logger.info(
                "P39 Codex App Server runtime selected — credentials stay "
                "inside Codex CLI; skipping Hermes OAuth preflight"
            )
            return {
                "api_key": "codex-app-server-local",
                # init_agent 仍要求非空 base_url 才进入“显式 runtime”
                # 分支；App Server turn 不会使用这个 loopback URL。
                "base_url": "http://127.0.0.1/codex-app-server",
                "provider": "openai-codex",
                "api_mode": "codex_app_server",
                "command": None,
                "args": [],
                "credential_pool": None,
                "max_tokens": max_tokens if isinstance(max_tokens, int) else None,
            }
        return original()

    patched_runtime_agent_kwargs._catfish_p39_patched = True  # type: ignore[attr-defined]
    _gateway_run._resolve_runtime_agent_kwargs = patched_runtime_agent_kwargs

    # Hermes API server 每个 HTTP 回合都会新建 AIAgent，而上游把 Codex session
    # 挂在临时 agent 上。这会让每条消息都重启 codex app-server、重载 MCP，且
    # 临时 agent 销毁后子进程没有被 close。这里按聊天 + 模型 + cwd 复用 session，
    # 同时把 Codex 的 final_answer delta 接回 Hermes SSE。
    from agent import codex_runtime as _codex_runtime  # noqa: PLC0415

    original_turn = getattr(_codex_runtime, "run_codex_app_server_turn", None)
    if original_turn is None:
        raise RuntimeError("agent.codex_runtime.run_codex_app_server_turn 不存在")
    if not getattr(original_turn, "_catfish_p39_stream_patched", False):
        atexit.register(_p39_close_all_codex_sessions)

        def _cache_key_for(agent, cwd: str) -> str:
            # Tenant identity belongs in the key even though Companion session
            # IDs are normally unique. This plugin serves multiple employees;
            # a caller-controlled ID collision must never reuse another user's
            # Codex conversation context.
            try:
                tenant = str(resolver.resolve_for_agent(agent) or "").strip()
            except Exception:
                tenant = str(
                    getattr(agent, "_catfish_outgoing_user", "") or ""
                ).strip()
            tenant = tenant or "local"
            session_id = str(
                getattr(agent, "session_id", "")
                or getattr(agent, "gateway_session_key", "")
                or getattr(agent, "_gateway_session_key", "")
                or ""
            ).strip()
            if not session_id:
                # A request with no stable conversation identity must not share
                # context with another anonymous request.
                session_id = f"anonymous-{id(agent)}"
            model = str(getattr(agent, "model", "") or "").strip()
            return f"{tenant}\x1f{session_id}\x1f{model}\x1f{cwd}"

        def _new_codex_session(agent, cwd: str):
            from agent.transports.codex_app_server_session import (  # noqa: PLC0415
                CodexAppServerSession,
                _ServerRequestRouting,
            )

            try:
                from tools.terminal_tool import _get_approval_callback  # noqa: PLC0415

                approval_callback = _get_approval_callback()
            except Exception:
                approval_callback = None

            auto_approve_requests = False
            try:
                from tools.approval import is_approval_bypass_active  # noqa: PLC0415

                auto_approve_requests = is_approval_bypass_active()
            except Exception:
                logger.debug(
                    "P39 Codex approval-bypass lookup failed; keeping fail-closed",
                    exc_info=True,
                )

            return CodexAppServerSession(
                cwd=cwd,
                approval_callback=approval_callback,
                request_routing=_ServerRequestRouting(
                    auto_approve_exec=auto_approve_requests,
                    auto_approve_apply_patch=auto_approve_requests,
                ),
                # Refreshed for every checked-out turn below. Keeping it empty
                # here avoids retaining the first temporary AIAgent forever.
                on_event=None,
            )

        def _checkout_cache_entry(key: str, factory):
            """Return an entry with its per-session turn lock held."""
            while True:
                with _P39_CODEX_CACHE_LOCK:
                    entry = _P39_CODEX_CACHE.get(key)
                    cache_hit = entry is not None
                    if entry is None:
                        entry = {
                            "session": factory(),
                            "lock": threading.Lock(),
                            "last_used": time.monotonic(),
                        }
                        _P39_CODEX_CACHE[key] = entry

                    entry_lock = entry["lock"]
                    acquired = entry_lock.acquire(blocking=False)

                if not acquired:
                    # Do not hold the global pool lock while another message in
                    # this same chat is still generating.
                    entry_lock.acquire()
                    with _P39_CODEX_CACHE_LOCK:
                        if _P39_CODEX_CACHE.get(key) is not entry:
                            entry_lock.release()
                            continue

                return entry, cache_hit

        @functools.wraps(original_turn)
        def patched_codex_turn(agent, *args, **kwargs):
            callback = getattr(agent, "stream_delta_callback", None)
            seen_delta = False
            final_answer_items: set[str] = set()

            def tracking_callback(delta):
                nonlocal seen_delta
                if delta is not None and str(delta):
                    seen_delta = True
                if callback is not None:
                    return callback(delta)
                return None

            def on_codex_event(note):
                if not isinstance(note, dict):
                    return
                method = str(note.get("method") or "")
                params = note.get("params") or {}
                if not isinstance(params, dict):
                    params = {}

                if method == "item/started":
                    item = params.get("item") or {}
                    if isinstance(item, dict):
                        item_id = str(item.get("id") or "")
                        phase = str(item.get("phase") or "")
                        if (
                            item_id
                            and item.get("type") == "agentMessage"
                            and phase == "final_answer"
                        ):
                            final_answer_items.add(item_id)

                elif method == "item/agentMessage/delta":
                    item_id = str(params.get("itemId") or "")
                    delta = params.get("delta")
                    # Only surface final-answer text. Commentary/reasoning also
                    # arrives as agentMessage deltas and must stay out of the
                    # visible assistant bubble.
                    if item_id in final_answer_items and delta:
                        tracking_callback(delta)

                elif method == "item/completed":
                    item = params.get("item") or {}
                    if isinstance(item, dict):
                        final_answer_items.discard(str(item.get("id") or ""))

                progress_callback = getattr(agent, "tool_progress_callback", None)
                if progress_callback is not None:
                    mapped = _codex_runtime._codex_note_to_tool_progress(note)
                    if mapped is not None:
                        tool_name, preview, tool_args = mapped
                        try:
                            progress_callback(
                                "tool.started", tool_name, preview, tool_args
                            )
                        except Exception:
                            logger.debug(
                                "P39 Codex tool-progress callback raised",
                                exc_info=True,
                            )

            _p39_start_codex_janitor()
            _p39_prune_codex_cache()

            from agent.runtime_cwd import resolve_agent_cwd  # noqa: PLC0415

            cwd = str(getattr(agent, "session_cwd", None) or resolve_agent_cwd())
            key = _cache_key_for(agent, cwd)
            entry, cache_hit = _checkout_cache_entry(
                key, lambda: _new_codex_session(agent, cwd)
            )
            session = entry["session"]
            logger.info(
                "P39 Codex session cache %s: session=%s model=%s",
                "hit" if cache_hit else "miss",
                getattr(agent, "session_id", "")
                or getattr(agent, "_gateway_session_key", ""),
                getattr(agent, "model", ""),
            )

            if callback is not None:
                agent.stream_delta_callback = tracking_callback
            session._on_event = on_codex_event
            agent._codex_session = session
            result = None
            keep_cached = False
            try:
                result = original_turn(agent, *args, **kwargs)
            finally:
                # Upstream sets agent._codex_session=None after a crash, timeout,
                # auth failure, or explicit retirement. Never put that dead
                # subprocess back in the pool.
                keep_cached = (
                    getattr(agent, "_codex_session", None) is session
                    and not getattr(session, "_closed", False)
                )
                session._on_event = None
                agent._codex_session = None
                if callback is not None:
                    agent.stream_delta_callback = callback
                entry["last_used"] = time.monotonic()
                if not keep_cached:
                    with _P39_CODEX_CACHE_LOCK:
                        if _P39_CODEX_CACHE.get(key) is entry:
                            _P39_CODEX_CACHE.pop(key, None)
                entry["lock"].release()

            final_text = result.get("final_response") if isinstance(result, dict) else None
            if callback is not None and not seen_delta and final_text:
                callback(final_text)
                logger.info("P39 Codex final_response bridged to SSE delta")
            elif callback is not None and seen_delta:
                logger.info("P39 Codex final_answer streamed incrementally to SSE")

            # Enforce LRU after a miss; the active entry was locked during the
            # pre-turn prune and therefore could not be selected as a victim.
            if keep_cached and not cache_hit:
                _p39_prune_codex_cache()
            return result

        patched_codex_turn._catfish_p39_stream_patched = True  # type: ignore[attr-defined]
        _codex_runtime.run_codex_app_server_turn = patched_codex_turn
    logger.info(
        "P39 Codex App Server auth + pooled sessions + live SSE patched — "
        "ChatGPT credentials remain owned by Codex CLI ✓"
    )

