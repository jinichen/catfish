"""零散 patch —— 从 plugin.py 拆出 (8/15 第 2 趟)。

P12 (update_system_prompt 静默失败) · P13 (dump 命名 type tag) ·
P19 (status_callback 桥) · P20 (禁 execute_code 永久模式) ·
P29 (/learn 中文翻译) · P36 (terminal cwd 回家目录)。

放一起不是因为它们有关系, 恰恰是因为**没关系** —— 每个都独立 wrap 一个
hermes 方法, 互不引用, 也不共享模块级状态。凑在一个文件里只是为了让
plugin.py 降到红线以下; 哪天某一组长大了, 从这里再分出去就是。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional
import os

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


# ── P12 ──────────────────────────────────────────────────────────────────

def _patch_p12_update_system_prompt_safe() -> None:
    """BL-HERMES-SYSTEM-PROMPT-PERSIST-BROKEN (6/4): hermes_state.update_system_prompt
    silent fail bug `monkey-patch fix`.

    Bug: hermes_state.HermesState.update_system_prompt 真 SQL 直接 `UPDATE sessions
    SET system_prompt = ? WHERE id = ?`, `没 _insert_session_row 保护`真. 真
    concurrent load (cron + kanban + delegate_task) 时, create_session() race condition
    `session row 没真 insert 上`真 → UPDATE silent affect 0 rows → 下次 read
    system_prompt `null` → conversation_loop `'Stored system prompt is null'`
    warning + `每 turn rebuild + prefix cache miss (~29K tokens)`.

    对比同 file `update_token_counts` (line 967-971) 已加 INSERT OR IGNORE pre-call
    保护 — `update_system_prompt 漏改了`.

    Fix: wrap `call 前 _insert_session_row(session_id, "unknown")` `保证 row 存`真.
    幂等 — INSERT OR IGNORE `真``真`已 在 row `noop`真.
    """
    try:
        # `真``真`实际 class name 是 SessionDB (audit hermes_state.py:354), 不是 HermesState`**真
        from hermes_state import SessionDB
    except ImportError as e:
        logger.warning("P12: hermes_state.SessionDB import 失败 (%s), skip patch", e)
        return

    _orig = SessionDB.update_system_prompt

    def patched(self, session_id: str, system_prompt: str) -> None:
        # `保证 session row 存` — `INSERT OR IGNORE 幂等`真.
        try:
            self._insert_session_row(session_id, "unknown")
        except Exception as e:  # noqa: BLE001
            logger.debug("P12 _insert_session_row 异常 (ignored): %s", e)
        return _orig(self, session_id, system_prompt)

    SessionDB.update_system_prompt = patched
    logger.info("P12 SessionDB.update_system_prompt + INSERT OR IGNORE patched")




# ── P13 ──────────────────────────────────────────────────────────────────

def _patch_p13_dump_naming_type_tag() -> None:
    """BL-DUMP-FILE-NAMING-INCONSISTENT (6/4): dump filename 加 caller type tag.

    Bug: agent_runtime_helpers.dump_api_request_debug @ line 1123 `生`
    `request_dump_{session_id}_{timestamp}.json` — `无 type prefix`真.
    audit 时 chat / background-review (curator) / cron `真` `dump 都`
    `一起 排序混杂`, `grep 找员工真 chat dump 真` `真`6 小时 audit slow`**真
    (6/4 凌晨 catfish-memory P0 验证 真踩坑).

    Fix: dump filename 真前缀加 type tag, 从 threading.current_thread().name 真`检`**:
    - `bg-review` thread → `bg`
    - 默认 (main thread, chat session) → `chat`

    new format: `request_dump_<type>_<session_id>_<ts>.json`
    e.g. `request_dump_chat_20260604_125823_d77073_20260604_130043.json`
         `request_dump_bg_20260604_125823_d77073_20260604_130100.json`

    audit 时**`ls request_dump_chat_*` `真`只`** `真`员工 chat dump`** — `真`不混真 curator`**真.
    """
    try:
        from agent import agent_runtime_helpers
    except ImportError as e:
        logger.warning("P13: agent.agent_runtime_helpers import 失败 (%s), skip patch", e)
        return

    _orig = agent_runtime_helpers.dump_api_request_debug

    def patched(agent, api_kwargs, *, reason, error=None):
        import threading
        import re
        from pathlib import Path

        thread_name = threading.current_thread().name or ""
        if "bg-review" in thread_name.lower() or "background" in thread_name.lower():
            type_tag = "bg"
        elif thread_name.lower().startswith("thread-") or thread_name == "MainThread":
            type_tag = "chat"
        else:
            type_tag = "chat"  # safe default

        # Call orig — `真``真`原 logic 写`** `request_dump_<sid>_<ts>.json`**真
        result = _orig(agent, api_kwargs, reason=reason, error=error)
        if result is None or not isinstance(result, Path):
            return result

        # Rename 加 type tag: `request_dump_<sid>_<ts>.json` → `request_dump_<type>_<sid>_<ts>.json`
        try:
            old_name = result.name
            if old_name.startswith("request_dump_") and f"_{type_tag}_" not in old_name:
                new_name = old_name.replace("request_dump_", f"request_dump_{type_tag}_", 1)
                new_path = result.parent / new_name
                if not new_path.exists():
                    result.rename(new_path)
                    return new_path
        except Exception as e:  # noqa: BLE001
            logger.debug("P13 rename 异常 (ignored, 保留 orig path): %s", e)
        return result

    agent_runtime_helpers.dump_api_request_debug = patched
    logger.info("P13 dump_api_request_debug + type tag (chat/bg) patched")
















# ── P26 (P3.5.105, 6/25 鸿波 catch "定时任务跑没跑结果如何都看不到") ─
#
# # 真因 (P3.5.105 audit-1/2/3 完整)
#
# hermes 真有完整 cron 监控数据:
#   ~/.hermes/cron/jobs.json (last_run_at / last_status / last_error / repeat.completed)
#   ~/.hermes/cron/output/<job_id>/<ts>.md (每次跑真完整输出)
# 但 Companion UI 真 0 处展示. 鸿波 daily-morning-brief 6/24 9:00 streaming error,
# 失败 1 次完全看不见. F1 / 股票日报 / 邮件 scheduler 全瞎跑.
#
# hermes cron 真 0 RESTful endpoint, public function 真在 cron.jobs:
#   pause_job(id, reason) → Optional[Dict]
#   resume_job(id) → Optional[Dict]
#   remove_job(id) → bool
#
# # 修法 (跟 P18 同模式, monkey-patch 不 fork)
#
# 1. 3 个 handler (_handle_cron_pause/resume/delete) attach 到 APIServerAdapter
# 2. _patched_app_init 块 P18 add_post 后追加 add_post/add_delete (router 未 freeze)
# 3. handler 鉴权: self._check_auth(request) 跟 P18 同款
# 4. 内部调 hermes cron.jobs public function
#
# Companion 真路径:
#   - 读 list / output: 直读 ~/.hermes/cron/jobs.json + output/<id>/*.md (jobs.lock 只写时锁)
#   - 写 pause/resume/delete: HTTP POST/DELETE → P26 endpoint → hermes Python public function
#     (自动触发 scheduler 重新 load, 加锁安全)
# 鉴权: ~/.catfish/companion.yaml hermes_api.key + Authorization: Bearer <key>












# ── P19 (P3.5.18 Phase 2, 6/17 鸿波 audit miss revert 后 正确路径) ─
#
# # 鸿波诉求 verbatim 链 (P3.5.17.c.1 commit + P3.5.18 design doc)
#
# > "自动进行压缩, 提示这个不是觉得奇怪" (P3.5.17.c.1 commit verbatim)
# > "为什么还是提示, 直接压缩, 压缩过程可以弹窗显示压缩进度" (P3.5.18 design)
#
# P3.5.17.b 已修 hermes 自带 ContextCompressor (catfish-gateway auth fallback
# 让 hermes-cli auxiliary 缺 X-Catfish-User 不再 400 paused). hermes preflight
# 真自动 trigger compress_context**, 但 真Companion 0 反馈** — chat 卡 30s
# 不知道发生啥, 鸿波感知 "怎么还没回?".
#
# # 真audit 真因** (6/17 22:50)
#
# hermes `_create_agent` (api_server.py:1068) 真0 status_callback 参数**:
#   def _create_agent(self, ..., stream_delta_callback, tool_progress_callback,
#                     tool_start_callback, tool_complete_callback, ...):
#
# `_run_agent` (line 3584) 真call _create_agent 也没传 status_callback.
# `AIAgent(model=, ..., status_callback=status_callback)` 真永 None**.
#
# preflight `agent._emit_status("📦 Preflight compression: ...")` (run_agent.py:761)
# → `self._vprint(...)` 真 CLI 显 + `self.status_callback(...)` 真 None skip.
# → API server (Companion) 0 收, telegram/discord/slack 真 wire callback 真 收.
#
# # 修法
#
# P19 wrap `APIServerAdapter._create_agent` post-init:
#   1. 捕获 kwargs.tool_progress_callback (hermes `_run_agent` 真传)
#   2. create `catfish_status_callback(kind, message)` 桥 tool_progress_callback:
#      tool_progress_callback(
#          event_type=f"catfish.lifecycle.{kind}",
#          tool_name="catfish-lifecycle",
#          preview=message,
#      )
#   3. `agent.status_callback = catfish_status_callback` (instance attr set)
#
# `tool_progress_callback` 真stream_q.put(("__tool_progress__", payload))**
# → SSE 真`event: hermes.tool.progress` (api_server.py:2207) Companion 接 显.
#
# Companion 真配套改 lib/chat.ts**: handle `tool === "catfish-lifecycle"`
# → onLifecycle callback → useChat → ChatPanel inline 显 "📦 Compacting...".
#
# # 和 P11 model_override 真叠加 wrap**
#
# P5/P6/P11 已 wrap `_create_agent` (line 930). P19 叠加同样 pattern —
# `_orig_create_agent = APIServerAdapter._create_agent` 这时拿到 真P5/P6/P11-wrapped
# 版本**, call 完后 真post-init inject status_callback**. 不破 P5/P6/P11.

def _patch_p19_status_callback_bridge() -> None:
    """P19: post-init 注入 agent.status_callback 桥 tool_progress_callback.

    让 hermes preflight 自动压缩 真SSE 推 progress 给 Companion**, 用户
    看 chat 真不再卡 30s 不知道发生啥**.
    """
    from gateway.platforms.api_server import APIServerAdapter

    _orig_create_agent_p19 = APIServerAdapter._create_agent

    def patched_create_agent_p19(self, *args, **kwargs):
        agent = _orig_create_agent_p19(self, *args, **kwargs)
        # 捕获 tool_progress_callback (hermes _run_agent line 3617 真传)
        tpc = kwargs.get("tool_progress_callback")
        if tpc is None or not callable(tpc):
            # caller 真没传 callback** (e.g. non-stream path) — skip wire.
            return agent

        def catfish_status_callback(kind, message=""):  # noqa: ANN001
            """桥 `agent._emit_status(msg)` → SSE hermes.tool.progress.

            kind 真hermes 'lifecycle' / 'warn' (run_agent.py:777/794).
            Companion 真tool="catfish-lifecycle" marker 分发 inline
            进度 UI 真不污染 tool_calls list**.
            """
            try:
                tpc(
                    event_type=f"catfish.lifecycle.{kind}",
                    tool_name="catfish-lifecycle",
                    preview=str(message)[:500],
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("P19 catfish_status_callback push 失败 (静默): %s", e)

        try:
            agent.status_callback = catfish_status_callback
            logger.debug(
                "P19 agent.status_callback 已注入 (桥 tool_progress_callback)"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("P19 agent.status_callback set 失败: %s", e)
        return agent

    APIServerAdapter._create_agent = patched_create_agent_p19
    logger.info(
        "P19 APIServerAdapter._create_agent post-init wraps status_callback "
        "→ SSE catfish-lifecycle ✓"
    )




# ── P20 (P3.5.65, 6/22 鸿波 catch "审批按钮一直不弹") ──────────────────
#
# 真因 (read-only diagnostic 实证 14:58):
#   user mac _permanent_approved set 含 "execute_code" 字面字符串 →
#   check_execute_code_guard:1749 is_approved("execute_code") → True →
#   line 1750 silent auto-approve → 按钮永远不弹.
#
# 由来: 用户某次 chat 收到 execute_code approval 弹窗, 点了 "always".
#   approve_permanent("execute_code") 把字符串加进 _permanent_approved +
#   save_permanent_allowlist 写进 ~/.hermes/config.yaml command_allowlist.
#   永久生效 (跨 hermes 重启).
#
# 治本红线: execute_code = 给 LLM 任意 Python 沙箱权限. 一旦 always-approved
#   = 给 LLM 完全 shell 权限. **永远不允许永久 approve**, 每次必须员工
#   explicit 选 once / session (session 也只是当前对话). 是 catfish 安全
#   红线, 不该向员工开放这个选项.
#
# 修法:
#   1. wrap approve_permanent(pattern_key) — 如果 pattern_key == "execute_code",
#      丢弃 + warning log, **不**加进 _permanent_approved
#   2. wrap load_permanent(patterns: set) — 从 config 加载时也过滤掉
#      "execute_code" (用户老 config 自动洗白)
#   3. 启动时一次性 sweep: 如果 _permanent_approved 已含 "execute_code",
#      remove + save_permanent_allowlist (auto-fix 用户老安装)
#
# 不 wrap check_execute_code_guard 本身 (上次 P15.3 wrap 不稳教训), 改
# wrap **加入点** (approve_permanent / load_permanent) — 入口阻断比 wrap
# 关键 guard 函数安全得多.

_EXEC_CODE_PATTERN_KEYS_BLOCKED = {"execute_code"}




def _patch_p20_block_execute_code_permanent() -> None:
    """禁止 execute_code 进 _permanent_approved set / config command_allowlist."""
    try:
        from tools import approval as _approval_mod
    except ImportError as e:
        logger.warning("P20: tools.approval import 失败 (%s), skip patch", e)
        return

    _orig_approve_permanent = _approval_mod.approve_permanent
    _orig_load_permanent = _approval_mod.load_permanent

    def _wrapped_approve_permanent(pattern_key: str):
        if pattern_key in _EXEC_CODE_PATTERN_KEYS_BLOCKED:
            logger.warning(
                "P20 红线: 拒绝 approve_permanent(%r) — execute_code 永久 approve "
                "等于给 LLM 完全 shell 权限, 每次必须员工 explicit 确认.",
                pattern_key,
            )
            return  # 静默丢弃, 不抛 (Hermes UI 仍正常 close)
        return _orig_approve_permanent(pattern_key)

    def _wrapped_load_permanent(patterns: set):
        # 从 config 加载时过滤掉禁止条目 (洗白用户老 config)
        cleaned = {p for p in patterns if p not in _EXEC_CODE_PATTERN_KEYS_BLOCKED}
        dropped = patterns - cleaned
        if dropped:
            logger.warning(
                "P20 红线: load_permanent 过滤掉 %d 条危险 pattern: %r",
                len(dropped), sorted(dropped),
            )
        return _orig_load_permanent(cleaned)

    _approval_mod.approve_permanent = _wrapped_approve_permanent
    _approval_mod.load_permanent = _wrapped_load_permanent

    # 一次性 sweep: 老 install _permanent_approved 已含 execute_code → 移除 + 持久化
    try:
        with _approval_mod._lock:
            to_drop = _approval_mod._permanent_approved & _EXEC_CODE_PATTERN_KEYS_BLOCKED
            if to_drop:
                _approval_mod._permanent_approved -= to_drop
        if to_drop:
            try:
                _approval_mod.save_permanent_allowlist(_approval_mod._permanent_approved)
                logger.warning(
                    "P20 一次性 sweep: 从 _permanent_approved 移除 %r, 写回 config.yaml. "
                    "审批按钮恢复正常.",
                    sorted(to_drop),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("P20 sweep: save_permanent_allowlist 失败 (%s)", e)
    except Exception as e:  # noqa: BLE001
        logger.warning("P20 sweep: 读 _permanent_approved 失败 (%s)", e)

    logger.info(
        "P20 approve_permanent / load_permanent wrapped — execute_code 永久 "
        "approve 已封死, 每次审批必须员工 explicit 确认 ✓"
    )




# ── P22 (P3.5.76) reverted in P3.5.78 (6/22 鸿波) ──
#
# 原 P22 patch 把 bookkeep_add/query/summarize pin 到 hermes _HERMES_CORE_TOOLS
# (let tool_search 不 defer). 但鸿波 catch 真因不在 tool 可见性, 在 catfish-memory
# 的 _render_schema "AUTHORITATIVE" 5 kind 决策树把 LLM 锁死在 memory router 5
# 选 1 里, 看不见 bookkeep 范式存在.
#
# P3.5.78 治本: bookkeep 不当独立 plugin, 整进 catfish-memory 第 6 kind=expense.
# memory tool 本来就是 _HERMES_CORE_TOOLS, expense 走 memory tool 自然 visible,
# 不需要 P22 patch. catfish-bookkeep plugin 整砍.


# ── P25 (P3.5.104, 6/24 鸿波 catch "execute_code 不弹审批一直被拦") ────────
#
# # 真因 (6/24 hermes 源码完整 audit)
#
# hermes cron/scheduler.py:1558 run_job 内执行:
#     os.environ["HERMES_CRON_SESSION"] = "1"
# 注释明说: "process-wide and persists for the lifetime of the scheduler
# process — every job this process runs is a cron job."
#
# hermes 上游假设: cron scheduler 在独立进程跑. 但 catfish 部署是 hermes daemon
# 单进程, scheduler + chat + api 全同一 Python 进程. env 是进程级跨线程, 第一
# 个 cron job 跑过后**整 daemon 都被污染**.
#
# 后续任何 chat / api 调 execute_code →
#   approval.py:1714 check_execute_code_guard:
#     if env_var_enabled("HERMES_CRON_SESSION"):
#         if _get_cron_approval_mode() == "deny":
#             return BLOCKED
# → 鸿波看到 "execute_code 持续被安全策略拦截" (实际是 LLM 看到 BLOCKED 错
#   后自己幻觉式解读, "安全策略 / sandbox 时序问题" 是 LLM 编的, 不是真消息).
#
# 并发 race: cron job A 跑过程中 env=1, 同时 chat 进来调 execute_code 也被拒.
# 仅 finally pop env 不够 — 必须用 threadlocal 真按线程隔离.
#
# # 治本 (P25 patch)
#
# 1. wrap cron.scheduler.run_job — 入口 set threadlocal in_cron=True, finally
#    清 threadlocal + pop env. (P21 已经 wrap run_job 改 model, P25 包 P21 包 orig,
#    顺序保 P25 在最外层 finally pop env.)
# 2. wrap tools.approval.check_execute_code_guard — 看 threadlocal 不看 env.
#    在 cron 线程内 → 透传 orig (env=1 cron deny 行为不变, 保 cron 真安全).
#    非 cron 线程 → 临时 pop env 调 orig (假装没污染), 不恢复 (帮 hermes 清理).
# 3. 装载时急救 pop 一次 — 重启 hermes daemon 前如果已被污染, 装载瞬间清掉.
#
# # 为什么必须 wrap check_execute_code_guard 而不只 wrap run_job
#
# 仅 wrap run_job + finally pop env 治不了**concurrent race**:
# cron job 跑过程中 (env=1, finally 还没触发), 同时 chat 线程进来调 execute_code,
# chat 看到 env=1 被拒. patched check_execute_code_guard 用 threadlocal 判定真
# cron 线程, 隔离全进程 env 污染.
#
# # 为什么必须 wrap run_job 而不只 wrap check_execute_code_guard
#
# 仅 wrap check_execute_code_guard 治不了**残留污染**: cron job 跑完后 env=1 仍
# 残留, 后续 chat 调 check_execute_code_guard 仍能透过 (因为 threadlocal 默认
# False), 但**其他用 env_var_enabled("HERMES_CRON_SESSION") 判定的路径** (e.g.
# _is_gateway_approval_context:148 短路) 还会撞污染. finally pop env 兜底.






# ── P28 (P3.5.123 6/25 鸿波 catch "微信里 ClawBot 英文不合适"): WeixinAdapter 中文化 ──
#
# 真因 (audit gateway/run.py:4269 + 15614-15619):
#   hermes 4 段英文 hardcoded f-string 经 _status_adapter.send outbound:
#     1. "⚡ Interrupting current task. I'll respond to your message shortly."
#     2. "⚠️ **Dangerous command requires approval:**"
#     3. "Reason: execute_code script execution. The script can spawn subprocesses
#        or mutate files without passing through terminal command approval;
#        approval is one-shot for this run."
#     4. "Reply `/approve` to execute, `/approve session` to approve this pattern
#        for the session, `/approve always` to approve permanently, or `/deny` to cancel."
#
# 鸿波 catch: 中文微信场景英文违和 + 员工不懂 `/approve always` 真反而 绕过铁律**.
#
# 修法: wrap WeixinAdapter.send — str.replace 英文 → 中文. 只 wrap weixin,
# slack / matrix / dingtalk 保英文.
#
# 鸿波铁律加强: 中文 reply 段砍掉 `/approve always` 入口, : 保留
# `/批准` (=/approve) + `/批准 本次会话` (=/approve session) + `/拒绝` (=/deny) 3
# 命令 — `/approve always` : : : hermes 真 dispatch 仍 work
# (鸿波本人 mac 命令行可用), 但**微信员工看不到这条入口, 真:** : : 真
# : : : : : : : : : : : : : : :
#
# fail-safe: import 失败 / wrap 失败 → silent skip 老英文路径 (不阻塞 hermes 启动).

# : 真:** : : : : : : 长 first — : : : : : :
def _patch_p29_learn_slash_translate() -> None:
    """wrap APIServerAdapter._run_agent — /learn 前置翻译到 build_learn_prompt."""
    try:
        from gateway.platforms.api_server import APIServerAdapter
    except ImportError as e:
        logger.warning(
            "P29: gateway.platforms.api_server import 失败 (%s), skip patch. "
            "员工 Companion 输 /learn 将不会被翻译, LLM 会当纯文本 prompt.", e,
        )
        return

    try:
        from agent.learn_prompt import build_learn_prompt
    except ImportError as e:
        logger.warning(
            "P29: hermes agent.learn_prompt import 失败 (%s), skip patch. "
            "hermes v0.19+ 可能改路径, verify 后调整 import. 员工 Companion 输 "
            "/learn 将不会被翻译.", e,
        )
        return

    _orig = APIServerAdapter._run_agent

    async def patched_learn_translate(self, *args, **kwargs):
        # 严格拿 message (第 1 位置参 or kwargs["message"])
        message: Any = None
        message_source: Optional[str] = None
        if args:
            message = args[0]
            message_source = "args"
        elif "message" in kwargs:
            message = kwargs["message"]
            message_source = "kwargs"

        # 前置检测 /learn slash command (仅 str, 空白 tolerant)
        if isinstance(message, str):
            stripped = message.strip()
            if stripped.startswith("/learn"):
                # 严格拿 arg: "/learn xxx" → "xxx", 光 "/learn" (无 arg) → ""
                # build_learn_prompt 内部对空 arg 有 fallback: "the workflow we just
                # went through in this conversation" (learn_prompt.py:112-115).
                _learn_arg = stripped[len("/learn"):].strip()
                try:
                    translated = build_learn_prompt(_learn_arg)
                    logger.info(
                        "P29 /learn translate: arg=%r (len=%d) → build_learn_prompt (len=%d)",
                        _learn_arg[:60], len(_learn_arg), len(translated),
                    )
                    # 替换 message
                    if message_source == "args":
                        args = (translated,) + args[1:]
                    else:
                        kwargs["message"] = translated
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "P29 /learn translate 失败 (%s), fall through as normal message. "
                        "LLM 会当 '/learn %s' 纯文本 prompt (原行为, 无副作用).",
                        e, _learn_arg[:40], exc_info=True,
                    )

        return await _orig(self, *args, **kwargs)

    APIServerAdapter._run_agent = patched_learn_translate
    logger.info(
        "P29 patch applied: APIServerAdapter._run_agent /learn slash translate. "
        "Companion 员工输 /learn <描述> 会翻译成 hermes build_learn_prompt 走完整 "
        "agent turn 拉 skill (通过 skill_manage tool 存 ~/.hermes/skills/)."
    )




# ── P30 (P3.5.198 7/8 鸿波: 微信扫码绑定 404 补 endpoint) ─────────────
#
# # 真因 (7/8 audit)
#
# 前端 wechat_qr.ts v3 (5/26) 假设 hermes 上 有 POST /api/platforms/wechat/
# qr_login/start + GET /api/platforms/wechat/qr_login/poll 两条 route, 但
# grep hermes v0.18 (以及 v0.17 备份) 全库均无. weixin.py:1003 只有 CLI 阻塞
# 的 async qr_login() helper (打印 ASCII 到 stdout, 单 flow 循环 poll), 不是
# HTTP-ready. catfish plugin 里 P1-P29 也没 patch 添加过. 结论: endpoint
# 从未在 hermes 落地. 员工点 "微信扫码绑定" → 前端 fetchWithAuth start → 404.
#
# # 修法: 拆 CLI helper 为 stateless 双 endpoint, 复用 hermes 常量
#
# start:
#   - GET https://ilinkai.weixin.qq.com/ilink/bot/get_bot_qrcode?bot_type=3
#   - 返 qrcode (session token) + qrcode_img_content (前端 npm qrcode 渲图用)
#   - 存 qrcode → base_url 到 in-memory session (跨 poll 用)
# poll:
#   - GET https://ilinkai.weixin.qq.com/ilink/bot/get_qrcode_status?qrcode=xxx
#   - 处理 wait/scaned/scaned_but_redirect(升 base_url)/expired/confirmed
#   - confirmed → save_weixin_account(hermes_home, account_id, token, base_url,
#     user_id) 落地 ~/.hermes/weixin/accounts/{account_id}.json (chmod 600)
#
# # 前端契约兑现
#
# start 响应: {qrcode, qrcode_url, scan_data}  (scan_data 是 qrcode_url 或 qrcode)
# poll 响应: {status: wait|scaned|confirmed|expired, account_id?, user_id?, _warning?}
# 前端 comment (wechat_qr.ts:20-22) 声明: poll 出错 → wait + _warning (UI 下次
# 再 poll), 真 expired 才提示. 这里遵守.
#
# # session state (in-memory)
#
# 只有一个字段 base_url (redirect_host 升级用). QR 有效期 35s, plugin 重启
# 丢 session 用户点重试即可, 不做磁盘持久化.
#
# # 边界
#
# - refresh 逻辑 (QR expired 自动重发) 不做. 前端 UI 层 startSession 已经
#   处理 expired → 一键重试.
# - Auth 走 self._check_auth (跟 P26 一样, per-handler 显式 check).
# - _make_ssl_connector() 复用 hermes 已有 SSL 配置 (企业代理 / 内证书).

# 8/13: `_wechat_qr_sessions` 搬去 plugin_wechat_qr.py 了 —— 用它的三个 handler
# 8/8 就搬过去了, 定义却留在这边, 那三个函数一跑就 NameError (五天没人发现,
# 因为 P30 只注册路由, 处理器要等员工点扫码才第一次执行)。定义跟使用者放一起。
# re-export 在文件头那段。


def _patch_p36_terminal_cwd_home() -> None:
    """P36 setenv TERMINAL_CWD=$HOME 兜底.

    hermes execute_code / terminal / file_tools 都读这个 env 决定相对路径起点.
    launchd 起 hermes 时 os.getcwd()="/", LLM 相对路径全部撞死. setenv 一次覆盖.
    """
    existing = os.environ.get("TERMINAL_CWD", "").strip()
    if existing:
        logger.info(
            "P36: TERMINAL_CWD 已显式设 (%s), 尊重员工/装机脚本 export, 不覆盖.",
            existing,
        )
        return
    home = os.path.expanduser("~")
    if not home or not os.path.isdir(home):
        logger.warning(
            "P36: $HOME=%r 不是有效目录, skip cwd 兜底 (execute_code 相对路径可能仍撞 /)",
            home,
        )
        return
    os.environ["TERMINAL_CWD"] = home
    logger.info(
        "P36 setenv TERMINAL_CWD=%s ✓ "
        "(hermes execute_code / terminal / file_tools 相对路径从 $HOME 起, "
        "不再走 launchd 遗留的 os.getcwd()=/)",
        home,
    )
