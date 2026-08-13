"""cron 一族 —— 定时任务的 picker / env 隔离 / REST 端点 / 自动重试。

从 plugin.py 抽出 (8/13, 拆红线的第二块; 第一块是 P39 Codex 会话池)。
plugin.py 3958 行, CLAUDE.md §1 的硬红线是 800。

# 装了什么

    P21  cron 跑 job 时按 picker 定模型 (不听会话持久化那份)
    P25  cron 线程隔离 —— 见下面那条硬约束
    P26  cron 的 REST 端点 (pause / resume / delete)
    P27  cron job 失败自动重试

# 一条不能弄错的硬约束: _CATFISH_CRON_THREAD_LOCAL

hermes 的 `cron/scheduler.py:1558` 把 `HERMES_CRON_SESSION` env set 了**不清**,
而 env 是进程级跨线程的 —— 于是整个 daemon 被污染。之后任何 chat / api 调
execute_code 走 `approval.check_execute_code_guard:1714`, 看见 env=1 +
cron_mode=deny 就 BLOCKED。现象是"execute_code 一直被拦"(P3.5.104, 6/24)。

P25 用这个 threadlocal 精准判定"本线程**真的**在 cron run_job 里", 不依赖那个
被污染的全进程 env。

搬它的时候专门查过: 全仓 3 处读取**全在 `_patch_p25_cron_env_isolation` 内部**,
没有组外用户。要是别处也在读, 搬走就会静默改变 execute_code 的放行判定 ——
那种错不报错, 只是员工的 execute_code 有时被拦有时不被拦。

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


# P3.5.104 P25 (6/24 鸿波 catch "execute_code 一直被拦"): cron 真线程隔离.
# hermes cron/scheduler.py:1558 真把 HERMES_CRON_SESSION env set 后不清, 整
# daemon 进程被污染 (env 是进程级跨线程). 后续任何 chat / api 调 execute_code
# 走 approval.check_execute_code_guard:1714, 看 env=1 + cron_mode=deny → BLOCKED.
# threadlocal 标记本线程是否真在 cron run_job 内, P25 patched check_execute_code_guard
# 用它精准判定, 不依赖被污染的全进程 env.
_CATFISH_CRON_THREAD_LOCAL = threading.local()


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


def _patch_p25_cron_env_isolation() -> None:
    """治 hermes cron HERMES_CRON_SESSION env 污染全 daemon 进程的 bug.

    见上方真因 audit. 两 patch (wrap run_job + wrap check_execute_code_guard) +
    装载急救 pop, 真治本 + 防 race.

    fail-safe: import 失败 / wrap 失败 → silent skip 老路径 (鸿波会看到现有 bug,
    但 hermes 不会因 patch 异常起不来).
    """
    # ── 急救清现有污染 ──
    if os.environ.pop("HERMES_CRON_SESSION", None):
        logger.warning(
            "P25 装载时清掉 HERMES_CRON_SESSION 污染 — hermes daemon 已被某个 "
            "cron job 留下的 env 污染过, 装载瞬间清."
        )

    # ── wrap cron.scheduler.run_job (在 P21 之后, P25 包 P21 包 orig) ──
    try:
        from cron import scheduler as _cron_scheduler  # noqa: PLC0415
    except ImportError as e:
        logger.warning("P25: hermes cron.scheduler 没导, skip run_job wrap (%s)", e)
        _cron_scheduler = None

    if _cron_scheduler is not None:
        _current_run_job = _cron_scheduler.run_job  # 可能是 P21 patched, 也可能是 orig
        if getattr(_current_run_job, "_p25_patched", False):
            logger.info("P25 run_job 已 wrap 过, 跳过 (避免双重 wrap, dev hot-reload)")
        else:
            @functools.wraps(_current_run_job)
            def _patched_run_job(job, *args, **kwargs):
                _CATFISH_CRON_THREAD_LOCAL.in_cron = True
                try:
                    return _current_run_job(job, *args, **kwargs)
                finally:
                    _CATFISH_CRON_THREAD_LOCAL.in_cron = False
                    # 兜底 pop env (即使 hermes run_job 内部 set 了)
                    os.environ.pop("HERMES_CRON_SESSION", None)

            _patched_run_job._p25_patched = True  # type: ignore[attr-defined]
            _cron_scheduler.run_job = _patched_run_job
            logger.info(
                "P25 wrap cron.scheduler.run_job 完成 — threadlocal in_cron 标识 + "
                "finally pop HERMES_CRON_SESSION env"
            )

    # ── wrap tools.approval.check_execute_code_guard ──
    try:
        from tools import approval as _approval  # noqa: PLC0415
    except ImportError as e:
        logger.warning(
            "P25: hermes tools.approval 没导, skip check_execute_code_guard wrap (%s)",
            e,
        )
        return

    _orig_check = getattr(_approval, "check_execute_code_guard", None)
    if _orig_check is None:
        logger.warning(
            "P25: tools.approval 没 check_execute_code_guard 属性 "
            "(hermes 升级改名?), skip"
        )
        return
    if getattr(_orig_check, "_p25_patched", False):
        logger.info("P25 check_execute_code_guard 已 wrap 过, 跳过")
        return

    # P3.5.192 (7/7 鸿波军规审判): hermes v0.18 (P3.5.159, 7/3 升级) 严格
    # `check_execute_code_guard(code, env_type, has_host_access=False)` 加了第 3 参数,
    # code_execution_tool.py:1156 会传 `has_host_access=...`. 本 wrapper 老签名
    # 只 2 参数 → 每次 execute_code 调用 TypeError. Fix: 用 *args, **kwargs
    # 透传所有位置/关键字参数给 _orig_check, 未来 hermes 再加参数也不用改.
    @functools.wraps(_orig_check)
    def _patched_check_execute_code_guard(code, env_type, *args, **kwargs):
        if getattr(_CATFISH_CRON_THREAD_LOCAL, "in_cron", False):
            # 真在 cron 线程 — 走原始 cron deny 路径 (env=1 真意图)
            return _orig_check(code, env_type, *args, **kwargs)
        # 非 cron 线程 — 临时 pop 假装 env 没 set (即使被污染, chat 不该被当 cron)
        _prev_env = os.environ.pop("HERMES_CRON_SESSION", None)
        try:
            return _orig_check(code, env_type, *args, **kwargs)
        finally:
            # 不恢复 — caller 是 chat / api, 帮 hermes 清污染 (上游 bug 兜底)
            if _prev_env is not None:
                # 8/13: debug → warning。
                #
                # 这一行是**整个 P25 唯一能证明自己还有用的时刻** —— 它意味着真
                # 发生了一次污染, 而 threadlocal 判据把它接住了。原来是 debug,
                # 而 agent.log 里 DEBUG 一条都没有 (9347 INFO / 304 WARNING),
                # 所以这个事件三个月来从未可见。
                #
                # 顺便当实验用: hermes 上游已经把 cron session 从 os.environ 改成
                # ContextVar + token 还原 (cron/scheduler.py:3124, 注释原话
                # "one cron job cannot taint unrelated gateway/API/TUI turns"),
                # 也就是 P25 当初 (6/24) 治的那个病在新版 hermes 上不存在了。
                #
                #   · 这条**打出来** → 这台机器上还有走真 env 的路径, P25 仍必要
                #   · 长期**不打** → 是 P25 可以退役的证据 (但退役前要确认所有
                #     部署的 hermes 版本都带那个 ContextVar 修复, 见下方 TODO)
                #
                # ⚠ TODO(退役前必读): 非 cron 分支这个 pop 是**不恢复**的。
                #   hermes 现在把 os.environ 那条留作"standalone cron 入口和测试"
                #   的合法兜底 (scheduler.py 注释明说), 而 get_session_env 的
                #   解析顺序是 ContextVar 优先、从未设过才回落 env。在 gateway
                #   进程里 ContextVar 一定设过, 所以 pop 无影响; 但在独立 cron
                #   入口 / 测试进程里, 一次非 cron 的 execute_code 就会把那个兜底
                #   删掉, 之后 cron 的 deny 策略失效 —— 方向跟本 patch 的意图相反。
                #   真要退役或收紧, 从这里下手。
                logger.warning(
                    "P25 接住一次 cron env 污染: 非 cron 线程调 execute_code 时发现 "
                    "HERMES_CRON_SESSION=%r 残留, 已清掉再放行 (不清的话这次调用会被 "
                    "cron_mode=deny 误拦)。这条出现说明本机 hermes 仍走真 os.environ "
                    "那条路, P25 还不能退役。",
                    _prev_env,
                )

    _patched_check_execute_code_guard._p25_patched = True  # type: ignore[attr-defined]
    _approval.check_execute_code_guard = _patched_check_execute_code_guard
    logger.info(
        "P25 wrap tools.approval.check_execute_code_guard 完成 — "
        "threadlocal 隔离 cron 真线程, 非 cron 线程透传 (env 临时 pop)"
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
#   - P21/P25 wrap run_job (cron.scheduler 模块) — P27 wrap mark_job_run (cron.jobs
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

