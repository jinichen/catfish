"""P18 · 主动压缩会话 + SSE 进度推送。

从 plugin.py 抽出 (8/13, 拆红线的第三块; 前两块是 P39 Codex 会话池、cron 一族)。

# 这块在做什么

员工在工作台点"压缩这个会话", Companion POST
`/api/sessions/{id}/compress/stream`, 这里一边压一边用 SSE 把进度吐回去
(不然长会话压几十秒, 界面上什么都没有)。

# 挂载时机是这块最容易踩的地方

`_patch_p18_compress_endpoint` **只把 handler attach 到 APIServerAdapter 类上**,
真正的 `router.add_post` 在 plugin.py 的 `_patched_app_init` 里。

原因 (6/17 22:16 实撞): 老写法在 wrap connect 里 add_post, 那时
`runner.setup()` → `app.freeze()` 已经跑完, 撞
`Cannot register a resource into frozen router`。改成在 `Application.__init__`
之后注册 —— 此刻 router 未 freeze, 跟 hermes 自己 add_post 同时机。

所以搬这块**不动路由注册**: 那段留在 plugin.py, 它通过 P7 stash 的
`request.app["_catfish_apiserver_adapter"]` 拿到 adapter 实例, 再调
`adapter._handle_compress_session_stream(request)` —— 走的是实例方法, 跟这个
函数住哪个模块无关。P26 的 cron 端点是同一套, 上一块拆的时候已经验过了。

# 没有注入位

这块**不需要任何兄弟模块** —— 模块层依赖只有 logger, 而 asyncio / json / web
三个都在函数体内自带 import。所以没有 `_require_wiring()`。

那三个函数内 import 保持原样, 不要"顺手"提到模块层: 那是行为改动 (import 时机
从首次调用变成模块加载), 不该搭在拆文件里。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("catfish.xcatfish_user.plugin")


async def _handle_compress_session_stream(self, request):
    """POST /api/sessions/{session_id}/compress/stream

    Body (JSON, optional): {"focus_topic": "...", "force": true|false}

    Response: SSE 事件流。**下面这份是逐个 send_event 调用点核对过的真值**
    (8/13) —— 原来这里写的是 6/17 设计文档那版, 跟实际发送对不上, 而且
    design doc 自己也漏了两个字段。写客户端**以这里为准**。

      - compress.started    {messages_count, approx_tokens, model}
      - compress.progress   {kind, text}          kind 来自 AIAgent status_callback
      - compress.warn       {text}
      - compress.failed     {error}               5 个触发点, 见下
      - compress.completed  10 个字段:
            before_count, after_count, before_tokens, after_tokens   ← 本函数算的
            headline, token_line, note, noop, aborted, fallback_used ← summarize_
                                              manual_compression 返的 (**summary)

    ⚠ completed 不等于成功。三个必须看的标志位:
        aborted=true        摘要 LLM 挂了, **一条消息都没删** (上下文没压)
        fallback_used=true  摘要挂了但走了降级路径, **硬删了 N 条**
        noop=true           压缩跑完但前后一模一样
      三个都 false 才是真压缩成功。UI 不能只显示 headline 就算完 ——
      headline 在 aborted / fallback 时措辞不同, 但 UI 应该按标志位给出不同的
      视觉状态, 而不是指望员工读英文句子。

    compress.failed 的 5 个触发点 (都是**发完就 write_eof 返回**, 不会再有事件):
        SessionDB unavailable / session not found / too few messages (<4) /
        老 session 无 model 字段 / 兜底 except

    fail-silent on disconnect — 用户切走 / 弹窗关 抛 ConnectionResetError,
    try/except 兜底 不阻塞 compress_future. compress_future 继续跑完写 db.

    fail-silent on disconnect — 用户切走 / 弹窗关 抛 ConnectionResetError,
    try/except 兜底 不阻塞 compress_future. compress_future 继续跑完写 db.
    """
    import asyncio
    import json

    from aiohttp import web

    # 走跟 _handle_session_chat_stream 同款 auth (X-Hermes-API-Key / Bearer)
    auth_err = self._check_auth(request)
    if auth_err:
        return auth_err

    session_id = request.match_info["session_id"]

    # SSE setup
    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await resp.prepare(request)

    async def send_event(event: str, data: dict) -> bool:
        """写 SSE 帧. ConnectionResetError 兜底返 False (client 断), 调用方别再写.
        compress_future 继续跑 (执行器线程), 写 db 完整.
        """
        try:
            line = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            await resp.write(line.encode("utf-8"))
            return True
        except (ConnectionResetError, asyncio.CancelledError, RuntimeError) as e:
            logger.debug("P18 SSE write 失败 (client 断?): %s", e)
            return False

    try:
        # 1. SessionDB load + verify session 真存在
        db = self._ensure_session_db()
        if db is None:
            await send_event("compress.failed", {"error": "SessionDB unavailable"})
            await resp.write_eof()
            return resp

        session_row = db.get_session(session_id)
        if session_row is None:
            await send_event("compress.failed", {"error": f"session not found: {session_id}"})
            await resp.write_eof()
            return resp

        messages = db.get_messages(session_id)
        if len(messages) < 4:
            await send_event("compress.failed", {
                "error": f"too few messages to compress ({len(messages)} < 4)",
            })
            await resp.write_eof()
            return resp

        # 2. 估 token + emit started
        from agent.model_metadata import estimate_request_tokens_rough
        # session_row 真 dict 有 model / ephemeral_system_prompt / system_prompt 字段.
        # 军规 (P3.5.79+ 7/22 鸿波): 老 session 无 model 字段 → **fail-loud**, 不再硬编
        # catfish-private-main 兜底. 硬编让老 session compress 静默走内网 model, 员工无感
        # 且掩盖数据源问题. 明报 error 逼员工手动删老 session 或重开.
        model_name = (
            session_row.get("model")
            or session_row.get("model_name")
        )
        if not model_name:
            await send_event("compress.failed", {
                "error": (
                    "老 session 无 model 字段 · 无法 compress · "
                    "军规不硬编 model 兜底 · 请手动删/重开此 session"
                ),
            })
            await resp.write_eof()
            return resp
        system_prompt = (
            session_row.get("ephemeral_system_prompt")
            or session_row.get("system_prompt")
            or ""
        )
        approx_tokens = estimate_request_tokens_rough(
            messages, system_prompt=system_prompt, tools=None,
        )
        before_count = len(messages)
        if not await send_event("compress.started", {
            "messages_count": before_count,
            "approx_tokens": approx_tokens,
            "model": model_name,
        }):
            # client 已断: 跑 compress 但不再 emit SSE (写 db 仍 useful).
            pass

        # 3. body 解析 (optional focus_topic / force)
        try:
            body = await request.json() if request.can_read_body else {}
        except Exception:  # noqa: BLE001
            body = {}
        focus_topic = str(body.get("focus_topic", "") or "").strip() or None
        force = bool(body.get("force", False))

        # 4. 临时 AIAgent 跟 Slack /compress 同款 tmp_agent pattern.
        # status_callback 桥 AIAgent._emit_status / _emit_warning → SSE queue.
        from run_agent import AIAgent
        from agent.conversation_compression import compress_context
        from agent.manual_compression_feedback import summarize_manual_compression

        status_queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def status_callback(kind: str, message: str = "") -> None:
            # executor 线程调 — run_coroutine_threadsafe 把 event 推 main loop 真 queue.
            try:
                asyncio.run_coroutine_threadsafe(
                    status_queue.put((kind, message)), loop,
                )
            except RuntimeError:
                # main loop 已关 (client 断 + cleanup) — 忽略, compress_future 自跑完.
                pass

        tmp_agent = AIAgent(
            session_id=session_id,
            model=model_name,                          # hermes API 真 model=, 不是 model_name=
            ephemeral_system_prompt=system_prompt,     # hermes API 真 ephemeral_system_prompt=
            status_callback=status_callback,
            session_db=db,                             # 复用 同 SessionDB, compress_context 写回
        )

        # 5. compress in executor + 并发 drain status_queue 推 SSE
        compress_future = loop.run_in_executor(
            None,
            lambda: compress_context(
                tmp_agent, messages, system_prompt,
                approx_tokens=approx_tokens,
                focus_topic=focus_topic,
                force=force,
            ),
        )

        async def drain_status() -> None:
            """轮询 status_queue 真0.5 秒**, compress_future 完了退出."""
            while True:
                try:
                    kind, text = await asyncio.wait_for(
                        status_queue.get(), timeout=0.5,
                    )
                except asyncio.TimeoutError:
                    if compress_future.done():
                        return
                    continue
                if kind == "warn":
                    await send_event("compress.warn", {"text": text})
                else:
                    await send_event("compress.progress", {"kind": kind, "text": text})

        drain_task = asyncio.create_task(drain_status())
        try:
            compressed_messages, _new_system_prompt = await compress_future
        finally:
            drain_task.cancel()
            try:
                await drain_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        # 6. compress_context 已经写回 SessionDB (line 271 split the session in SQLite).
        # 无需 再调 db.replace_messages — 老 /fork pattern create child + replace,
        # 但 compress_context 直接 in-place rotate 真 session.

        # 7. summary + emit completed
        after_count = len(compressed_messages)
        after_tokens = estimate_request_tokens_rough(
            compressed_messages, system_prompt=system_prompt, tools=None,
        )
        summary = summarize_manual_compression(
            before_messages=messages,
            after_messages=compressed_messages,
            before_tokens=approx_tokens,
            after_tokens=after_tokens,
            # 注意: hermes summarize_manual_compression 不接 focus_topic 参数
            # (design doc bug 6/17 audit catch).
            #
            # ── 8/13 修: compression_state 之前根本没传 ──
            #
            # 不传的后果不是"少个字段", 是**压缩失败会被报成成功**:
            # summarize_manual_compression 里
            #     aborted       = compression_state is not None and getattr(...)
            #     fallback_used = compression_state is not None and getattr(...)
            # 不传 → 两个恒 False → headline 永远走
            #     "Compressed: N → M messages"
            # 这条分支, 而真实情况可能是「摘要 LLM 挂了, 一条消息都没删」(aborted)
            # 或「摘要挂了, 走降级路径硬删了 N 条」(fallback_used)。员工看到的是
            # "压缩完成", 实际上下文可能被砍了或者压根没压。
            #
            # hermes 自己 5 个调用点全传, 其中 4 处逐字就是下面这个写法
            # (tui_gateway/server.py:12582, methods_session.py:2464,
            #  methods_tools.py:1034, gateway/slash_commands.py:4121)。
            #
            # 用 getattr 兜底而不是直接 tmp_agent.context_compressor:
            # 这个属性是**条件存在**的 —— hermes 全仓访问它都走
            # getattr(..., None) / hasattr, AIAgent 上没有无条件赋值。
            # 拿不到时退回今天的行为 (两个 False), 不会比现在更差。
            compression_state=getattr(tmp_agent, "context_compressor", None),
        )
        await send_event("compress.completed", {
            "before_count": before_count,
            "after_count": after_count,
            "before_tokens": approx_tokens,
            "after_tokens": after_tokens,
            **summary,  # {headline, token_line, note, noop}
        })

    except Exception as e:  # noqa: BLE001
        logger.exception("P18 _handle_compress_session_stream 异常")
        try:
            await send_event("compress.failed", {"error": str(e)})
        except Exception:  # noqa: BLE001
            pass

    try:
        await resp.write_eof()
    except Exception:  # noqa: BLE001
        pass
    return resp


def _patch_p18_compress_endpoint() -> None:
    """P18: 注册 POST /api/sessions/{session_id}/compress/stream SSE handler.

    6/17 22:16 鸿波本机 bug fix: 之前 P18 wrap connect → _orig_connect runner.setup()
    后 router 已 freeze → add_post 撞 'Cannot register a resource into frozen router'.

    真新 path**: route Application.__init__ patch (line 1200+) post _orig_app_init
    add_post (router 未 freeze, 跟 hermes 自己 connect add_post 同时机). 这里只 attach
    handler method 给 APIServerAdapter class — handler 实例 method, Application.__init__
    时 已经 attached (plugin import 时 _apply_patches 真先跑 _patch_p18 attach class
    attribute, 之后 hermes create APIServerAdapter 实例 + Application 真触发
    _patched_app_init** add_post 真 closure handler 真 runtime call adapter method).
    """
    from gateway.platforms.api_server import APIServerAdapter

    APIServerAdapter._handle_compress_session_stream = _handle_compress_session_stream
    logger.info(
        "P18 APIServerAdapter._handle_compress_session_stream 已挂 ✓ "
        "(route 由 Application.__init__ patch 未 freeze 时注册)"
    )

