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
    Response: SSE 事件流
      - event: compress.started      data: {messages_count, approx_tokens, model}
      - event: compress.progress     data: {kind: "lifecycle", text}
      - event: compress.warn         data: {text}
      - event: compress.completed    data: {before_count, after_count, headline, token_line, note, noop}
      - event: compress.failed       data: {error}

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

