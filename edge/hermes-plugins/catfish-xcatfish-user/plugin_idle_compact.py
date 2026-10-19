"""P61 空闲时在后台压缩 (10/19, 鸿波「不太提示, 直接压缩, 压缩后的内容往后新增」)。

# 为什么

hermes 的压缩是在**员工发完下一句、小鲶开始干活之后**撞到压缩线 (120K) 才开始, 员工干等 —— 10/19 实测
每次 3-6 分钟。hermes 自带的 `compression.idle_compact_after_seconds` 也是「下一轮开头才压」, 照样等。

这里改成: 每一轮答完 (对话停下来) 就看一眼, 上下文超过 80K 就在**后台**另起一个临时 agent 压缩好,
员工下次发消息时已经压完了, 从压缩后的版本接着往下聊。

# 怎么保证不出事 (都是 hermes 自己的机制, 照「会话卫生」detached 压缩那套)

- 用临时 agent (同 `_hmwa_hygiene_build_agent`): 只开 memory 工具、最多 4 轮、不结束会话行、in-place 压缩。
- **压缩期间员工又发了消息**: 不等, 照常跑 (用压缩前的历史); 那一轮自己的压缩抢不到锁就让开;
  后台压完提交时, 期间新写进去的记录 (id > 水位线) 原样接在压缩结果后面 —— hermes 为此专门设计过 (#75316)。
- 不重复压: 同一会话正在压 / 别处持有压缩锁 / 离上次空闲压缩没再长 30K, 都跳过。
- 多 profile: 凭据在 ContextVar 里, 后台线程要在 profile 作用域里拷一份 context 再跑 (否则摘要模型拿不到 key,
  hermes 注释里说会悄悄退化成有损截断)。
- 出任何错只记日志, 不影响这一轮的回复。

两个入口: 工作台走 API server (`APIServerAdapter._run_agent`), 微信走网关 (`GatewayRunner._handle_message`)。
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger("catfish.xcatfish_user.plugin")

#: 上下文超过这个就趁空闲在后台压 (比压缩线 120K 低, 下一轮从压缩后的版本开始)
IDLE_TRIGGER_TOKENS = int(os.environ.get("CATFISH_IDLE_COMPACT_TOKENS", "80000"))
#: 压完之后至少再长这么多才再压 (防每轮都压)
GROWTH_FLOOR_TOKENS = 30000
#: 后台压缩最长等多久 (超过 hermes 的 commit fence 会放弃提交)
TOTAL_CEILING_SECONDS = 600

_lock = threading.Lock()
_inflight: set[str] = set()
_last_done: dict[str, int] = {}  # session_id → 上次空闲压缩时的 token 数


def should_compact(session_id: Optional[str], tokens: int) -> bool:
    if not session_id or tokens < IDLE_TRIGGER_TOKENS:
        return False
    with _lock:
        if session_id in _inflight:
            return False
        if tokens < _last_done.get(session_id, 0) + GROWTH_FLOOR_TOKENS:
            return False
        _inflight.add(session_id)
        return True


def _finish(session_id: str, tokens: int, compressed: bool) -> None:
    with _lock:
        _inflight.discard(session_id)
        if compressed:
            _last_done[session_id] = tokens


def compact_now(build_agent: Callable[[], Any], session_db: Any, session_id: str, tokens: int) -> bool:
    """在当前线程里压一次 (调用方负责放到后台线程 + 正确的 context)。返回是否真的压了。"""
    if session_db is None:
        return False
    holder = session_db.get_compression_lock_holder(session_id)
    if holder:
        logger.info("P61 空闲压缩跳过 %s: 别处正在压 (%s)", session_id, holder)
        return False
    from agent.conversation_compression import CompressionCommitFence  # noqa: PLC0415
    from gateway.run import _GATEWAY_HYGIENE_PLATFORM, _seed_hygiene_system_prompt  # noqa: PLC0415

    agent = build_agent()
    try:
        agent.compression_in_place = True
        bind = getattr(getattr(agent, "context_compressor", None), "bind_session_state", None)
        if callable(bind):
            bind(session_db, session_id)
        agent._end_session_on_close = False  # 绝不能结束员工正在用的会话行
        agent._print_fn = lambda *a, **kw: None
        _seed_hygiene_system_prompt(agent, session_db.get_session(session_id))
        agent.platform = _GATEWAY_HYGIENE_PLATFORM
        msgs = session_db.get_messages_as_conversation(session_id)
        if len(msgs) < 6:
            return False
        t0 = time.monotonic()
        out, _ = agent._compress_context(
            msgs, "", approx_tokens=tokens, task_id=session_id, trigger="idle",
            commit_fence=CompressionCommitFence(total_ceiling_seconds=TOTAL_CEILING_SECONDS),
        )
        compressed = out is not msgs
        logger.info("P61 空闲压缩 %s: %s (%d tokens, %d 条 → %d 条, %.0fs)", session_id,
                    "压好了" if compressed else "没压 (hermes 判定不需要 / 冷却中)", tokens, len(msgs), len(out),
                    time.monotonic() - t0)
        return compressed
    finally:
        try:
            agent.close()
        except Exception:  # noqa: BLE001
            logger.debug("P61 关临时 agent 出错", exc_info=True)


async def run_in_background(build_agent: Callable[[], Any], session_db: Any, session_id: str, tokens: int,
                            ctx: contextvars.Context, after: Optional[Callable[[bool], Any]] = None) -> None:
    """后台跑 compact_now (默认 executor, 不占 agent 工作槽), 结束后调 after(compressed)。"""
    compressed = False
    try:
        loop = asyncio.get_running_loop()
        compressed = await loop.run_in_executor(None, ctx.run, compact_now, build_agent, session_db, session_id, tokens)
    except Exception:  # noqa: BLE001 —— 后台压缩失败不能影响任何东西, 下一轮 hermes 自己的压缩照常兜底
        logger.warning("P61 空闲压缩 %s 失败, 下一轮照常 (hermes 自己的压缩兜底)", session_id, exc_info=True)
    finally:
        _finish(session_id, tokens, compressed)
    if after is not None:
        try:
            r = after(compressed)
            if asyncio.iscoroutine(r):
                await r
        except Exception:  # noqa: BLE001
            logger.debug("P61 压完的收尾出错", exc_info=True)


def _keep(task: asyncio.Task) -> None:
    """持有后台 task 的引用 (asyncio 只弱引用, 不存会被 GC 掉)。"""
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


_tasks: set[asyncio.Task] = set()


# ── 入口 1: 工作台 (API server) ───────────────────────────────────

def wrap_api_run_agent(orig):
    async def _run_agent(self, *args, **kwargs):
        res = await orig(self, *args, **kwargs)
        try:
            session_id = kwargs.get("session_id")
            result = res[0] if isinstance(res, tuple) and res else res
            tokens = int((result or {}).get("last_prompt_tokens") or 0) if isinstance(result, dict) else 0
            if should_compact(session_id, tokens):
                db = self._ensure_session_db()

                def build():
                    from gateway.run import _resolve_gateway_model, _resolve_runtime_agent_kwargs  # noqa: PLC0415
                    from run_agent import AIAgent  # noqa: PLC0415
                    rk = _resolve_runtime_agent_kwargs()
                    model = rk.pop("model", None) or _resolve_gateway_model()
                    rk.pop("_fallback_notice", None)
                    return AIAgent(**rk, model=model, max_iterations=4, quiet_mode=True, skip_memory=True,
                                   enabled_toolsets=["memory"], session_id=session_id, session_db=db)

                # 这里还在请求的 context 里 (多 profile 的作用域 ContextVar 在), 拷一份给后台线程
                _keep(asyncio.get_running_loop().create_task(
                    run_in_background(build, db, session_id, tokens, contextvars.copy_context())))
        except Exception:  # noqa: BLE001
            logger.debug("P61 工作台入口判断出错", exc_info=True)
        return res

    _run_agent._catfish_p61 = True  # type: ignore[attr-defined]
    return _run_agent


# ── 入口 2: 微信等网关 (GatewayRunner) ────────────────────────────

def wrap_gateway_handle_message(orig):
    async def _handle_message(self, event):
        res = await orig(self, event)
        try:
            source = event.source
            key = self._session_key_for_source(source)
            entry = self.session_store.lookup_by_session_key(key)
            session_id = getattr(entry, "session_id", None)
            tokens = int(getattr(entry, "last_prompt_tokens", 0) or 0)
            if self._is_session_running(key) or not should_compact(session_id, tokens):
                return res
            db = getattr(self._session_db, "_db", self._session_db)
            model, runtime = self._resolve_session_agent_runtime(source=source, session_key=key)

            def build():
                from run_agent import AIAgent  # noqa: PLC0415
                return AIAgent(**runtime, model=model, max_iterations=4, quiet_mode=True, skip_memory=True,
                               enabled_toolsets=["memory"], session_id=session_id, session_db=db)

            async def after(compressed: bool) -> None:
                if compressed:
                    await self.async_session_store.update_session(key, last_prompt_tokens=0)
                    self._evict_cached_agent(key)  # 下一轮从压缩后的版本重建

            if getattr(getattr(self, "config", None), "multiplex_profiles", False):
                from gateway.run import _profile_runtime_scope  # noqa: PLC0415
                with _profile_runtime_scope(self._resolve_profile_home_for_source(source)):
                    ctx = contextvars.copy_context()
            else:
                ctx = contextvars.copy_context()
            _keep(asyncio.get_running_loop().create_task(run_in_background(build, db, session_id, tokens, ctx, after)))
        except Exception:  # noqa: BLE001
            logger.debug("P61 网关入口判断出错", exc_info=True)
        return res

    _handle_message._catfish_p61 = True  # type: ignore[attr-defined]
    return _handle_message


def _patch_p61_idle_background_compaction() -> None:
    from gateway.platforms.api_server import APIServerAdapter  # noqa: PLC0415
    from gateway.run import GatewayRunner  # noqa: PLC0415

    orig_api = getattr(APIServerAdapter, "_run_agent", None)
    orig_gw = getattr(GatewayRunner, "_handle_message", None)
    if orig_api is None or orig_gw is None:
        logger.warning("P61: APIServerAdapter._run_agent / GatewayRunner._handle_message 不在 (hermes 改了?), skip")
        return
    if not getattr(orig_api, "_catfish_p61", False):
        APIServerAdapter._run_agent = wrap_api_run_agent(orig_api)
    if not getattr(orig_gw, "_catfish_p61", False):
        GatewayRunner._handle_message = wrap_gateway_handle_message(orig_gw)
    logger.info("P61 空闲后台压缩 patched (超过 %d tokens 在回复后压, 再长 %d 才再压)",
                IDLE_TRIGGER_TOKENS, GROWTH_FLOOR_TOKENS)


# ── 摘要别越写越长 (10/19 实测: qwen-flash 不管 hermes 给的篇幅目标, 一份摘要写 3 万 token / 7.7 万字, 9 分钟) ──
#
# 拆开看: 「详细会话日志」把每次工具调用都列一遍 (2.2 万字)、「已完成」1.7 万字、「关键上下文」把每个股票代码单列一行;
# 再加上一轮摘要原样带进下一轮 (hermes 要求「保留旧摘要的全部信息」), 越压越长、越压越慢。
# 两手: ① 带进下一轮的旧摘要截到 PREV_SUMMARY_MAX_CHARS (留开头和最近的部分); ② 请求末尾加写死数字的篇幅上限
# (这个模型对「不超过 N 字 / 最多 N 条」比对 hermes 那句 target tokens 听话)。原文都还在会话记录里, 小鲶能 session_search 找回。

PREV_SUMMARY_MAX_CHARS = 16000
SUMMARY_MAX_CHARS = 12000

_LENGTH_RULE = f"""

HARD LENGTH LIMIT (overrides any longer target above): the WHOLE summary must stay under {SUMMARY_MAX_CHARS} characters.
- Completed Actions: at most 15 items; merge repeated or similar steps into one item.
- Detailed Session Log: at most 15 lines; merge runs of similar tool calls into one line (e.g. "12 次 web_search 查 5 只股票收盘价").
- Critical Context: at most 20 lines. Never list files, symbols, URLs or IDs one per line — group them on one line.
- Prefer merging over enumerating; drop details that can be looked up again in the session history.
篇幅硬上限: 整份摘要不超过 {SUMMARY_MAX_CHARS} 字; 「已完成」最多 15 条, 「会话日志」最多 15 行, 「关键上下文」最多 20 行; 同类操作合并成一条, 文件 / 代码 / 网址合在一行写。"""


def _clip_previous(text: str) -> str:
    if len(text) <= PREV_SUMMARY_MAX_CHARS:
        return text
    head = PREV_SUMMARY_MAX_CHARS // 4
    tail = PREV_SUMMARY_MAX_CHARS - head
    return text[:head] + "\n\n[… 更早的摘要内容已精简, 原文在会话记录里, 可 session_search 找回 …]\n\n" + text[-tail:]


def wrap_build_summary_prompt(orig):
    def _build_summary_prompt(self, *args, **kwargs):
        prev = getattr(self, "_previous_summary", None)
        if isinstance(prev, str) and len(prev) > PREV_SUMMARY_MAX_CHARS:
            self._previous_summary = _clip_previous(prev)
        prompt = orig(self, *args, **kwargs)
        return prompt + _LENGTH_RULE if isinstance(prompt, str) else prompt

    _build_summary_prompt._catfish_p61 = True  # type: ignore[attr-defined]
    return _build_summary_prompt


def _patch_p61b_summary_length() -> None:
    from agent.context_compressor import ContextCompressor  # noqa: PLC0415
    orig = getattr(ContextCompressor, "_build_summary_prompt", None)
    if orig is None:
        logger.warning("P61b: ContextCompressor._build_summary_prompt 不在 (hermes 改了?), skip")
        return
    if not getattr(orig, "_catfish_p61", False):
        ContextCompressor._build_summary_prompt = wrap_build_summary_prompt(orig)
    logger.info("P61b 摘要篇幅上限 patched (旧摘要截到 %d 字, 新摘要不超过 %d 字)", PREV_SUMMARY_MAX_CHARS, SUMMARY_MAX_CHARS)
