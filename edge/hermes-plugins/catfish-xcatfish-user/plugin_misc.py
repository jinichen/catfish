"""零散 patch —— 从 plugin.py 拆出 (8/15 第 2 趟)。

P12 (update_system_prompt 静默失败) · P13 (dump 命名 type tag) ·
P19 (status_callback 桥) · P20 (删除类命令不许永久批准)。
(P29 /learn 翻译 10/20 退役, 墓碑在文件尾。P36 terminal cwd 8/19 退役 —— 上游
gateway/run.py:2207 的 home_fallback 做了同一件事, 墓碑在 plugin_wechat_qr.py 尾。)

放一起不是因为它们有关系, 恰恰是因为**没关系** —— 每个都独立 wrap 一个
hermes 方法, 互不引用, 也不共享模块级状态。凑在一个文件里只是为了让
plugin.py 降到红线以下; 哪天某一组长大了, 从这里再分出去就是。
"""
from __future__ import annotations

import logging
import threading
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












# ── P19 (10/14 退役) ─────────────────────────────────────────────────
# 原来把 hermes 的压缩 / 等待提示 (agent.status_callback) 桥成 hermes.tool.progress 推给 Companion。
# 实测它在 Companion 的主路径 (流式 chat/completions) 上从没生效过 (那条路不传 tool_progress_callback);
# hermes v0.22.0 原生在流里发 `event: hermes.status`, 在 /v1/responses、/v1/runs 上 P19 反而会盖掉原生的。
# Companion 改接 hermes.status (src/lib/chat.ts)。

# ── P20 · 删除等破坏性命令不许「永久批准」 ─────────────────────────────
#
# 6/22 起 P20 拦的是 execute_code 的永久批准 (怕「始终允许」等于把 shell 交给模型)。
# 10/15 鸿波改口径: 「只要点过始终允许, 下一次就不要再审批了, 除非那种删除等严重破坏
# 内容的指令」—— 当时拦掉之后按钮还在, 员工点了 9 次全白点, 而 skill 跑一轮弹好几次审批。
#
# 现在:
#   · execute_code 可以永久批准了。删除类脚本照样每次问, 那道闸在 P59
#     (plugin_approval_always.py) 里按代码内容现判, 不靠批准记录。
#   · 终端危险命令里删除 / 结束进程 / 格式化 / 改安全设置这几类 (destructive_code._zh 认得出的)
#     不许永久批准 —— 点了「始终允许」也只算这一次。其余危险命令 (python -c 等) 照 hermes 原样。
#
# 拦在加入点 (approve_permanent / 读写 command_allowlist), 不碰 check_all_command_guards
# (6/22 那次 wrap 关键 guard 不稳的教训)。老 config 里已有的删除类条目启动时清掉。


def _blocked_permanent(pattern_key: str) -> bool:
    try:
        return _sib("plugin_approval_always").is_destructive_pattern_key(pattern_key)
    except Exception as e:  # noqa: BLE001 — 判不了不拦 (sweep 会删 config 条目, 不能误删员工已有的)
        logger.warning("P20: 破坏性判断不可用 (%s), 本次不拦 %r", e, pattern_key)
        return False




def _patch_p20_block_execute_code_permanent() -> None:
    """删除类终端命令不许进 _permanent_approved / config command_allowlist (10/15 起 execute_code 放开)。"""
    try:
        from tools import approval as _approval_mod
    except ImportError as e:
        logger.warning("P20: tools.approval import 失败 (%s), skip patch", e)
        return

    _orig_approve_permanent = _approval_mod.approve_permanent
    _orig_load_permanent = _approval_mod.load_permanent
    _orig_read_allowlist = _approval_mod._read_permanent_allowlist
    _orig_save_allowlist = _approval_mod.save_permanent_allowlist

    def _wrapped_approve_permanent(pattern_key: str):
        if _blocked_permanent(pattern_key):
            logger.warning("P20: 拒绝 approve_permanent(%r) — 删除等破坏性命令每次都要员工同意", pattern_key)
            return  # 静默丢弃, 不抛 (Hermes UI 仍正常 close)
        return _orig_approve_permanent(pattern_key)

    def _drop_blocked(patterns, where: str) -> set:
        cleaned = {p for p in patterns if not _blocked_permanent(p)}
        dropped = set(patterns) - cleaned
        if dropped:
            logger.warning("P20 红线: %s 过滤掉 %d 条危险 pattern: %r", where, len(dropped), sorted(dropped))
        return cleaned

    def _wrapped_load_permanent(patterns: set):
        return _orig_load_permanent(_drop_blocked(patterns, "load_permanent"))

    def _wrapped_read_allowlist() -> set:
        # v0.22 (10/14): 按员工 profile 懒加载 (_permanent_set) 直接读这里, 不经过 load_permanent,
        # 只包 load_permanent 拦不住 —— 读取入口一并过滤
        return _drop_blocked(_orig_read_allowlist(), "_read_permanent_allowlist")

    def _sweep() -> None:
        """config.yaml 的 command_allowlist 里有删除类条目就删掉, 内存里同步去掉。

        v0.22 的 save_permanent_allowlist 只增不删 (磁盘上的一律保留), 不能再靠它写回 ——
        直接改 config。"""
        from hermes_cli.config import load_config, save_config  # noqa: PLC0415

        config = load_config()
        raw = config.get("command_allowlist")
        bad = sorted(p for p in raw if isinstance(p, str) and _blocked_permanent(p)) \
            if isinstance(raw, list) else []
        if bad:
            config["command_allowlist"] = [p for p in raw if p not in bad]
            save_config(config)
            logger.warning("P20 sweep: 从 config.yaml command_allowlist 移除 %r", bad)

        def _clean(s: set) -> None:
            s.difference_update({p for p in s if _blocked_permanent(p)})

        with _approval_mod._lock:
            _clean(_approval_mod._permanent_set())
            _clean(_approval_mod._permanent_approved)
            for approved in _approval_mod._permanent_approved_by_home.values():
                _clean(approved)
            for baseline in _approval_mod._permanent_baseline_by_home.values():
                _clean(baseline)

    def _wrapped_save_allowlist(patterns: set):
        # 存盘时会把磁盘上的整份并回内存 —— 员工手工加进 config 的 execute_code 也会被并进来
        result = _orig_save_allowlist(_drop_blocked(patterns, "save_permanent_allowlist"))
        try:
            _sweep()
        except Exception as e:  # noqa: BLE001
            logger.warning("P20 save 后清理失败 (%s)", e)
        return result

    _approval_mod.approve_permanent = _wrapped_approve_permanent
    _approval_mod.load_permanent = _wrapped_load_permanent
    _approval_mod._read_permanent_allowlist = _wrapped_read_allowlist
    _approval_mod.save_permanent_allowlist = _wrapped_save_allowlist

    # 一次性 sweep: 老 config 里已有删除类条目 → 移除 + 持久化
    try:
        _sweep()
    except Exception as e:  # noqa: BLE001
        logger.warning("P20 sweep 失败 (%s)", e)

    logger.info("P20 删除类命令不许永久批准 (approve_permanent / allowlist 读写) wrapped")




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

# ── P29 已退役 (10/20) ── 墓碑 ────────────────────────────────────────
#
# P29 (7/3) wrap APIServerAdapter._run_agent: 消息以 "/learn" 开头就换成
# hermes agent.learn_prompt.build_learn_prompt(arg) 的结果再跑。原因是 hermes 的
# /learn 只在 GatewayRunner._handle_message (微信那条路) 过 slash 分发,
# /v1/chat/completions 不过, Companion 发 "/learn xxx" 模型只会当普通文字。
#
# 唯一发 /learn 的是 Companion 聊天框「📚 → 💡 让 AI 学」(LearnModal)。10/20 鸿波定:
# 录屏演示和让 AI 学两边都从没用过, 拿掉。入口没了, 这个 patch 就没有调用方。
# 微信里打 /learn 走 GatewayRunner, hermes 原生处理, 不受影响。
#
# 要加回来: 先把 Companion 入口加回来, 再从 git 历史 (10/20 之前) 取这段。


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


