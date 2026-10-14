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




# ── P27 (10/14 退役) ─────────────────────────────────────────────────
# 原来包 cron.jobs.mark_job_run 做失败 5/10/15 分钟三档重试。10/14 审计发现它**从来没生效过**:
# cron 调度模块启动时就把 mark_job_run 绑成本地名字 (scheduler.py `from cron.jobs import ...`),
# 替换模块属性碰不到它; v0.22 又给它加了 expected_fire_owner / status 关键字参数, 包装函数也接不住。
# v0.22 自带"模型连不上"那一类失败的 5/15/30 分钟重试 (cron/unreachable_retry.py)。

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
