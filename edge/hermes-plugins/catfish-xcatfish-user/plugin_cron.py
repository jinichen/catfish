"""cron 一族 —— 定时任务的 picker / env 隔离 / REST 端点 / 自动重试。

从 plugin.py 抽出 (8/13, 拆红线的第二块; 第一块是 P39 Codex 会话池)。
plugin.py 3958 行, CLAUDE.md §1 的硬红线是 800。

# 装了什么

    P21  cron 跑 job 时按 picker 定模型 (不听会话持久化那份)
    P26  cron 的 REST 端点 (pause / resume / delete)
    P27  cron job 失败自动重试

(P25 cron 线程隔离 8/19 退役 —— 见文件尾的墓碑。)

# 依赖是注入进来的

只需要 `model_authority` 一个 (P21 读 picker 用)。兄弟之间不能直接 import:
包名带 dash, 得走 plugin.py 的三段 fallback `_import_sibling`, 而 import 它
就成了循环。由 plugin.py 装载后写进来, `_require_wiring()` 在加载期验。

跟 plugin_codex_session.py 同一套路 —— 那是本仓第一个这么干的模块。

# 注意 web 不在这里 import

三个 REST handler 各自在函数体内 `from aiohttp import web`。**保持原样**,
不要"顺手"提到模块层 —— 那是行为改动 (import 时机变了), 不该搭在拆文件里。
"""
from __future__ import annotations

import functools
import logging
import os
import threading
from typing import Any

logger = logging.getLogger("catfish.xcatfish_user.plugin")

#: 由 plugin.py 注入 —— 见文件头"依赖是注入进来的"。
model_authority: Any = None


def _require_wiring() -> None:
    """没接上依赖就当场炸, 不留"半装载"的余地。

    plugin.py 在模块层调它 —— 加载期就验, 不等到 patch 真跑。P21 要到第一个
    cron job 触发才执行, 那时候报错已经在定时任务路径上了, 而定时任务失败
    最不容易被人看见。
    """
    if model_authority is None:
        raise RuntimeError(
            "plugin_cron 没接上依赖 model_authority —— "
            "plugin.py 应在 _import_sibling 之后把它写进来"
        )




async def _handle_cron_pause(self, request):
    """POST /api/cron/jobs/{job_id}/pause

    Body (optional): {"reason": "用户暂停"}
    Response: {"ok": true, "job": {...}} | {"ok": false, "error": "..."}  404/500
    """
    from aiohttp import web
    auth_err = self._check_auth(request)
    if auth_err:
        return auth_err
    job_id = request.match_info["job_id"]
    body = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    reason = body.get("reason") if isinstance(body, dict) else None
    try:
        from cron.jobs import pause_job  # noqa: PLC0415
    except ImportError as e:
        return web.json_response(
            {"ok": False, "error": f"hermes cron.jobs 没导: {e}"}, status=500,
        )
    try:
        result = pause_job(job_id, reason)
    except Exception as e:  # noqa: BLE001
        logger.exception("P26 pause_job 异常")
        return web.json_response(
            {"ok": False, "error": f"pause_job 异常: {e}"}, status=500,
        )
    if result is None:
        return web.json_response(
            {"ok": False, "error": f"job {job_id!r} 不存在"}, status=404,
        )
    return web.json_response({"ok": True, "job": result})


async def _handle_cron_resume(self, request):
    """POST /api/cron/jobs/{job_id}/resume

    Response: {"ok": true, "job": {...}} | 404/500
    """
    from aiohttp import web
    auth_err = self._check_auth(request)
    if auth_err:
        return auth_err
    job_id = request.match_info["job_id"]
    try:
        from cron.jobs import resume_job  # noqa: PLC0415
    except ImportError as e:
        return web.json_response(
            {"ok": False, "error": f"hermes cron.jobs 没导: {e}"}, status=500,
        )
    try:
        result = resume_job(job_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("P26 resume_job 异常")
        return web.json_response(
            {"ok": False, "error": f"resume_job 异常: {e}"}, status=500,
        )
    if result is None:
        return web.json_response(
            {"ok": False, "error": f"job {job_id!r} 不存在"}, status=404,
        )
    return web.json_response({"ok": True, "job": result})


async def _handle_cron_delete(self, request):
    """DELETE /api/cron/jobs/{job_id}

    Response: {"ok": true} | 404/500
    真删 jobs.json 条目 + 清理 output 目录 (hermes remove_job 内部处理).
    """
    from aiohttp import web
    auth_err = self._check_auth(request)
    if auth_err:
        return auth_err
    job_id = request.match_info["job_id"]
    try:
        from cron.jobs import remove_job  # noqa: PLC0415
    except ImportError as e:
        return web.json_response(
            {"ok": False, "error": f"hermes cron.jobs 没导: {e}"}, status=500,
        )
    try:
        ok = remove_job(job_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("P26 remove_job 异常")
        return web.json_response(
            {"ok": False, "error": f"remove_job 异常: {e}"}, status=500,
        )
    if not ok:
        return web.json_response(
            {"ok": False, "error": f"job {job_id!r} 不存在"}, status=404,
        )
    return web.json_response({"ok": True})


def _patch_p26_cron_rest_endpoints() -> None:
    """P26 (P3.5.105 6/25 鸿波): cron 监控 + 操作 RESTful endpoint.

    跟 P18 同模式: attach handler 到 class, route 在 Application.__init__ 真注册
    (_patched_app_init 块里, 跟 P18 add_post 同时机, router 未 freeze).

    fail-safe: import 失败 / attach 失败 → silent skip 不阻塞 hermes 启动.
    """
    try:
        from gateway.platforms.api_server import APIServerAdapter  # noqa: PLC0415
    except ImportError as e:
        logger.warning("P26: api_server 没导, skip cron RESTful endpoint patch (%s)", e)
        return
    APIServerAdapter._handle_cron_pause = _handle_cron_pause
    APIServerAdapter._handle_cron_resume = _handle_cron_resume
    APIServerAdapter._handle_cron_delete = _handle_cron_delete
    logger.info(
        "P26 APIServerAdapter._handle_cron_pause/resume/delete 已挂 ✓ "
        "(route 由 Application.__init__ patch 真注册, 跟 P18 同时机)"
    )


def _patch_p21_cron_picker_integration() -> None:
    """patch cron.scheduler.run_job — cron job model 跟 picker 联动.

    hermes run_job 代码片段 (cron/scheduler.py:1641):
        model = job.get("model") or os.getenv("HERMES_MODEL") or ""
        ...
        if not job.get("model"):
            ...
            model = _model_cfg.get("default", model)

    patch 思路: wrap run_job, 在调用前若 picker_state set 了 chat_model, 把
    它**inject 到 job dict** (替换 `job["model"]`). 这样 hermes 内部读
    job.get("model") 时拿到 picker, 走 picker 路径 (优先级最高).

    job 是 dict 不是 copy, 直接改 in-place 影响 hermes 后续逻辑. 但 cron
    job 来自 scheduler 的 in-memory state, 不持久化, 改 in-place 不影响其他
    job. (即使持久化, picker 是 user state 跟 job 状态分开, override 一次
    不污染.)

    fail-safe: picker 读失败 / hermes 没 cron 模块 / patch attach 失败 →
    silent fallback 老路径.
    """
    try:
        from cron import scheduler as _cron_scheduler  # noqa: PLC0415
    except ImportError as e:
        logger.warning("P21: hermes cron module 没导, skip patch (%s)", e)
        return

    _orig_run_job = _cron_scheduler.run_job

    def _patched_run_job(job: dict, *args, **kwargs):
        # picker override (优先级最高). job["model"] 若已设, 看 picker 是否覆盖.
        try:
            picker_model = model_authority.read_picker_model()
            if picker_model:
                original_model = job.get("model")
                if original_model != picker_model:
                    job["model"] = picker_model
                    logger.info(
                        "P21 cron picker integration: job '%s' model %r → %r "
                        "(picker_state.json override)",
                        job.get("id", "?"), original_model, picker_model,
                    )
                else:
                    logger.debug(
                        "P21 cron: job '%s' 已是 picker model %r, skip override",
                        job.get("id", "?"), picker_model,
                    )
            # picker 空 → 走 hermes 老路径 (job.model > config.yaml.model.default > env)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "P21 cron: picker_state 读失败 (%s), fallback 老路径 (job.model > yaml > env)",
                e,
            )

        return _orig_run_job(job, *args, **kwargs)

    _cron_scheduler.run_job = _patched_run_job
    logger.info(
        "P21 cron picker integration patched — cron job model 跟 picker_state.json "
        "联动 (优先级: picker > job.model > yaml.default > env) ✓"
    )




# ── P27 (P3.5.106 6/25 鸿波 catch "失败不重试"): cron 任务失败 5/10/15 三档自动重试 ──
#
# 真因 (6/24 鸿波 daily-morning-brief streaming error → 等 24h 才再跑):
#
#   hermes cron/jobs.py:mark_job_run(success=False) 真行为:
#     1. last_status = "error"
#     2. last_error = error
#     3. next_run_at = compute_next_run(schedule, now)   ← 按 cron 算下次
#     4. 0 retry / 0 backoff / 0 通知用户
#
#   "0 9 * * *" 失败一次 → 6/25 9:00 才再跑. 当天早安日报真没了.
#
# 真修法 (鸿波拍 5/10/15 分钟三档):
#
#   wrap cron.jobs.mark_job_run, success=False 时:
#     attempt = (job.get("catfish_retry_attempt") or 0) + 1
#     if attempt <= 3:
#       BACKOFF = [5, 10, 15]  # 分钟
#       next_run_at = now + timedelta(minutes=BACKOFF[attempt-1])
#       update_job(id, {catfish_retry_attempt=attempt, next_run_at=...})
#     else:
#       update_job(id, {catfish_retry_attempt=0, catfish_retry_exhausted=True})
#       (让 hermes 真按 schedule 跑下次, 不再 catfish 干预)
#
#   success=True → clear catfish_retry_attempt=0 + retry_exhausted=False
#
# 真不 retry 的 case (delivery_error):
#   success=True + delivery_error 非空 (agent 出来了但 webhook/wechat 发不出去) →
#   走原 mark_job_run 老路径. 真不浪费 token. 邮件/微信送达问题 Companion UI 真
#   显示 last_delivery_error 让用户手动处理.
#
# 真不冲突路径:
#   - P21 wrap run_job (cron.scheduler 模块) — P27 wrap mark_job_run (cron.jobs
#     模块) — 真两个独立 module, 真零嵌套
#   - _jobs_lock() 真 reentrant (cron/jobs.py:89-95 threadlocal depth counter) —
#     mark_job_run 内已持锁, P27 wrap 后再调 update_job 真不死锁
#   - jobs.json 加 catfish_ 前缀字段 — hermes 真 .get() 兼容, 老 job 真不破
#
# fail-safe: import 失败 / wrap 失败 → silent skip 老路径 (hermes 还是 0 retry 真现状).

def _patch_p27_cron_auto_retry() -> None:
    """wrap cron.jobs.mark_job_run — cron 失败 5/10/15 分钟三档自动重试.

    真行为见上方文档. fail-safe: 真挂时退老路径不阻塞 hermes 启动.
    """
    try:
        from cron import jobs as _cron_jobs  # noqa: PLC0415
    except ImportError as e:
        logger.warning("P27: hermes cron.jobs 没导, skip auto-retry wrap (%s)", e)
        return

    _orig_mark = getattr(_cron_jobs, "mark_job_run", None)
    if _orig_mark is None:
        logger.warning(
            "P27: cron.jobs 没 mark_job_run 属性 (hermes 改名?), skip"
        )
        return
    if getattr(_orig_mark, "_p27_patched", False):
        logger.info("P27 mark_job_run 已 wrap 过, 跳过 (避免双重 wrap, dev hot-reload)")
        return

    # 真 backoff 表 (鸿波 6/25 拍): 5 → 10 → 15 分钟, 3 次后退出.
    _BACKOFF_MINUTES = [5, 10, 15]
    _MAX_ATTEMPTS = len(_BACKOFF_MINUTES)

    @functools.wraps(_orig_mark)
    def _patched_mark_job_run(job_id, success, error=None, delivery_error=None):
        # 1. 先跑原 mark_job_run — 让 hermes 真按 schedule 更 last_status / last_error /
        #    next_run_at. 我们后面真改 next_run_at + 加 catfish_retry_attempt 字段.
        result = _orig_mark(job_id, success, error, delivery_error=delivery_error)

        # 2. 真不需要 retry 的 case 真早走:
        #    - success=True (agent 真跑成功) → clear retry_attempt + exhausted
        #    - success=True + delivery_error (送达失败) → 不动 retry (发不出去不是 cron 问题)
        #    所以 success=False 才进 retry 决策.
        try:
            if success:
                # 真清 retry state (上次失败重试链真走到成功, 计数器归零)
                try:
                    job = _cron_jobs.get_job(job_id)
                except Exception:  # noqa: BLE001
                    job = None
                if job and (job.get("catfish_retry_attempt") or job.get("catfish_retry_exhausted")):
                    try:
                        _cron_jobs.update_job(job_id, {
                            "catfish_retry_attempt": 0,
                            "catfish_retry_exhausted": False,
                        })
                        logger.info(
                            "P27 cron retry: job '%s' 真跑成功, 清 retry 计数 ✓",
                            job_id,
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            "P27 cron retry: job '%s' clear retry state 失败: %s",
                            job_id, e,
                        )
                return result

            # success=False — 真进 retry 决策
            try:
                job = _cron_jobs.get_job(job_id)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "P27 cron retry: job '%s' get_job 失败, skip retry: %s",
                    job_id, e,
                )
                return result

            if not job:
                logger.warning(
                    "P27 cron retry: job '%s' 真不存在 (mark_job_run 内自动删了? 真无视), skip",
                    job_id,
                )
                return result

            # 真兜底: paused / disabled 任务真不该 retry (用户主动停的)
            if not job.get("enabled") or job.get("state") == "paused":
                return result

            # 真只对 cron / interval 重试. once 任务真失败次数算 1 次 (hermes 会 ONESHOT_GRACE 重试),
            # 不动 next_run_at 让 hermes 真原行为. (once schedule 真不在我们 backoff 范畴)
            kind = (job.get("schedule") or {}).get("kind")
            if kind not in {"cron", "interval"}:
                return result

            attempt = int(job.get("catfish_retry_attempt") or 0) + 1

            if attempt <= _MAX_ATTEMPTS:
                # 真重试 — 改 next_run_at = now + backoff
                from datetime import datetime, timedelta, timezone  # noqa: PLC0415
                backoff_min = _BACKOFF_MINUTES[attempt - 1]
                next_run = (datetime.now(timezone.utc) + timedelta(minutes=backoff_min)).isoformat()
                try:
                    _cron_jobs.update_job(job_id, {
                        "catfish_retry_attempt": attempt,
                        "catfish_retry_exhausted": False,
                        "next_run_at": next_run,
                    })
                    logger.info(
                        "P27 cron retry: job '%s' 失败 attempt %d/%d, %d 分钟后真重试 (next=%s)",
                        job_id, attempt, _MAX_ATTEMPTS, backoff_min, next_run,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "P27 cron retry: job '%s' update_job 真失败, 退回 hermes 老路径: %s",
                        job_id, e,
                    )
            else:
                # 真用尽 3 次重试 — 退出 retry, 让 hermes 真按 schedule 跑下次
                try:
                    _cron_jobs.update_job(job_id, {
                        "catfish_retry_attempt": 0,
                        "catfish_retry_exhausted": True,
                    })
                    logger.warning(
                        "P27 cron retry: job '%s' 真重试 %d 次仍失败, 退出真 retry, "
                        "按 schedule 等下次跑 (next=%s)",
                        job_id, _MAX_ATTEMPTS, job.get("next_run_at"),
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "P27 cron retry: job '%s' mark exhausted 失败: %s",
                        job_id, e,
                    )

        except Exception as e:  # noqa: BLE001
            # 真兜底: retry 决策本身挂了, 真不阻塞 mark_job_run 原 result
            logger.error(
                "P27 cron retry: job '%s' retry 决策顶层异常 (退回 hermes 老路径): %s",
                job_id, e, exc_info=True,
            )

        return result

    _patched_mark_job_run._p27_patched = True  # type: ignore[attr-defined]
    _cron_jobs.mark_job_run = _patched_mark_job_run
    logger.info(
        "P27 wrap cron.jobs.mark_job_run 完成 — 失败 5/10/15 分钟三档自动重试, "
        "成功后清 retry 计数 ✓"
    )


# ── P25 已退役 (8/19) ── 墓碑, 别再加回来 ─────────────────────────────
#
# P25 (P3.5.104, 6/24) 治的是: hermes cron 把 HERMES_CRON_SESSION 写进
# os.environ 不清, 而 env 是进程级跨线程的 → 整个 daemon 被污染 → 之后任何
# chat / api 调 execute_code 看见 env=1 + cron_mode=deny 就 BLOCKED。
# 现象是员工那句"execute_code 一直被拦"。
#
# 做法: 一个 threadlocal (_CATFISH_CRON_THREAD_LOCAL) 精准标记"本线程真在
# cron run_job 里", 加 wrap check_execute_code_guard 在非 cron 线程临时 pop 掉
# 污染的 env。
#
# # 为什么退役 —— 上游把病因改掉了
#
# hermes 0.20 的 cron 已经改成 ContextVar + token 还原:
#
#     cron/scheduler.py:3124   _cron_session_var = _VAR_MAP["HERMES_CRON_SESSION"]
#                              _cron_session_token = _cron_session_var.set("1")
#     cron/scheduler.py:3777   _cron_session_var.reset(_cron_session_token)
#
#     tools/approval.py:227    _is_cron_approval_context() 优先读 get_session_env,
#                              docstring 原话 "so one cron job cannot taint
#                              unrelated gateway/API/TUI turns in the same process"
#
# P25 自己注释里指名的病灶行 cron/scheduler.py:1558 —— 现在是 delivery
# thread_id 的代码, 那个 env 写入早就不在了。
#
# 全树搜"谁还在写全局 HERMES_CRON_SESSION": catfish 0 处、~/.hermes/.env 0 处、
# hermes 全树 0 处 (只剩 session_context.py:240 一句文档提到 os.environ fallback)。
#
# # 退役的现场证据 —— 是 P25 自己攒的
#
# P25 内置了退役探针, 而且 8/13 有人特意把它从 logger.debug 提到 warning
# (理由见当年的 tests/test_p25_pollution_visible.py: "一个只在 debug 级打的
# 证据等于没有证据")。8/19 读日志:
#
#     日志窗口                gateway.log 8/08 → 8/20 (12 天)
#     同期 cron job           340 次
#     同期 execute_code       494 次
#     对照组「P25 wrap」      1810 次 (证明 patch 真装上了)
#     「P25 接住一次污染」    0
#     「P25 装载时清掉污染」  0
#
# 部署侧: 鸿波确认员工机 hermes 统一 0.20, 大版本升级一起升 —— 满足当年写下的
# 退役条件「长期不打 → 可以考虑退役 (但要先确认所有部署的 hermes 版本)」。
#
# ⚠ 我们现在**依赖上游那个 ContextVar 化**。它要是改回 os.environ, 故障形状跟
#   当年一样 (execute_code 被 cron_mode=deny 误拦), 而且不报错。两道保险:
#     tests/test_p25_pollution_visible.py            读真 hermes 树验行为
#     audit_hermes_compat.sh Section 20              升级前验锚点
#
# 判定过程见 docs/HERMES-PATCH-AUDIT-2026-08-19.md。
