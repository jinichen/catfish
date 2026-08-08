"""catfish-xcatfish-user hermes plugin: 注入 X-Catfish-User 多租户 header.

# 设计

把 5/27-5/29 留在 hermes 仓的 11 处 patch 全搬出来, monkey-patch 风格. hermes 仓
回归 pristine, 升级路径 `git pull` 干净. 任何 hermes refactor 改了我们引用的
attribute/方法名 → plugin load 立刻报错 (fail loud), 比 silent 跨员工串数据
(silent break = P0 隐私漏洞) 好太多.

# 覆盖的 11 处 patch

  P1.  agent_init.py: init-time apply_headers
  P2.  run_agent.py _current_main_runtime: 加 catfish_outgoing_user
  P3a. auxiliary_client._MAIN_RUNTIME_FIELDS: 加字段
  P3b. auxiliary_client._resolve_auto: rebuild OpenAI client 带 default_headers
  P4.  gateway/run.py auto-title: 走 session_registry lookup
  P5.  api_server._extract_catfish_outgoing_user: 新方法
  P6.  api_server._create_agent: post-init set attribute + apply_headers + picker model override
  P7.  api_server._handle_companion_proxy: catch-all proxy 路由
  P8.  api_server CORS: _TAURI_ORIGINS / _is_tauri_origin / _cors_headers_for_origin
  P9.  api_server _CORS_HEADERS Allow-Headers 扩 X-Catfish-* / X-Hermes-Session-*
  P10. run_agent._apply_client_headers_for_base_url: 加 localhost:8999 分支
  P11. api_server picker model_override: aiohttp middleware 截 body.model + post-init agent.model 覆盖

# 加载

hermes plugin 加载机制:
  - python -m catfish_xcatfish_user_plugin (entry point)
  - 或 hermes 加载时自动 import (hermes 0.14+ plugin discovery)
具体方式跟 catfish-memory 一样, 看 catfish-memory __init__.py.

# 启动 self-check

import 阶段就跑 _verify_patch_targets() — 看每个 patch 引用的 attribute
是否还在. 任一缺失立刻 raise ImportError, hermes 启动失败. 这就是"fail loud":
不允许 plugin 半推半就加载 (会导致 silent 跨员工串数据).
"""
from __future__ import annotations

import functools
import json
import logging
import os
import threading
import time
import atexit
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger("catfish.xcatfish_user.plugin")

# P3.5.104 P25 (6/24 鸿波 catch "execute_code 一直被拦"): cron 真线程隔离.
# hermes cron/scheduler.py:1558 真把 HERMES_CRON_SESSION env set 后不清, 整
# daemon 进程被污染 (env 是进程级跨线程). 后续任何 chat / api 调 execute_code
# 走 approval.check_execute_code_guard:1714, 看 env=1 + cron_mode=deny → BLOCKED.
# threadlocal 标记本线程是否真在 cron run_job 内, P25 patched check_execute_code_guard
# 用它精准判定, 不依赖被污染的全进程 env.
_CATFISH_CRON_THREAD_LOCAL = threading.local()

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


# ── P29 (6/5 鸿波) — gateway URL detection helper ─────────────────────────
#
# 商用部署改 IP 时 base_url 会变 (e.g. http://10.10.40.50:8999), 之前 P3/P10
# 硬编码 "localhost:8999" / "127.0.0.1:8999" 字符串匹配 → 中央部署检测不到 →
# X-Catfish-User 跨员工 header 不注入 → 跨员工数据 P0 风险.
#
# helper: 走 env CATFISH_GATEWAY_URL (跟 Companion / catfish-memory 同名) 解析
# host:port → 跟 base_url host:port 比. 兼容老硬编码 default (localhost:8999).
# host + port 都 match 才算同一 gateway (防别人也起 8999 端口被误判).

def _get_catfish_gateway_host_port() -> set[tuple[str, int]]:
    """返当前 catfish-gateway `(host, port)` 真集合 (兼容多默认).

    包括: env CATFISH_GATEWAY_URL 解析出 + 老硬编码默认 (localhost:8999 +
    127.0.0.1:8999). 多 default 防员工只设 host 不设 port 时漏配.
    """
    hosts_ports: set[tuple[str, int]] = {
        ("localhost", 8999),
        ("127.0.0.1", 8999),
    }
    env_url = os.environ.get("CATFISH_GATEWAY_URL", "").strip()
    if env_url:
        try:
            parsed = urlparse(env_url if "://" in env_url else f"http://{env_url}")
            if parsed.hostname and parsed.port:
                hosts_ports.add((parsed.hostname.lower(), parsed.port))
        except Exception:  # noqa: BLE001
            pass
    return hosts_ports


def _is_catfish_gateway_base_url(base_url: str) -> bool:
    """base_url 真是不是 catfish-gateway (P3 / P10 `X-Catfish-User` header 注入判断).

    走 _get_catfish_gateway_host_port() 真 set, 比 host + port. 失败 fallback
    走老 "localhost:8999 / 127.0.0.1:8999" 字符串匹配 (兼容性).
    """
    if not base_url:
        return False
    try:
        parsed = urlparse(base_url if "://" in base_url else f"http://{base_url}")
        if parsed.hostname and parsed.port:
            return (parsed.hostname.lower(), parsed.port) in _get_catfish_gateway_host_port()
    except Exception:  # noqa: BLE001
        pass
    # fallback: 字符串包含 (老硬编码兼容, 中央部署不 work 但至少 dev 仍 work)
    bu = base_url.lower()
    return "localhost:8999" in bu or "127.0.0.1:8999" in bu


def _import_sibling(module_name: str):
    """Dash-safe sibling import (跟 catfish-memory 同套路).

    plugin 包名带 dash, hermes spec_from_file_location 加载下 relative import
    不稳, 3 段 fallback: relative → absolute → spec_from_file_location.
    """
    # 段 1: relative
    try:
        from importlib import import_module
        return import_module(f".{module_name}", package=__package__)
    except (ImportError, SystemError, ValueError, TypeError):
        pass
    # 段 2: absolute
    try:
        return __import__(module_name)
    except ImportError:
        pass
    # 段 3: spec_from_file_location 按文件路径 (无敌兜底)
    from pathlib import Path
    import importlib.util
    import sys as _sys
    _dir = str(Path(__file__).parent)
    _py = Path(__file__).parent / f"{module_name}.py"
    if not _py.exists():
        raise ImportError(f"{module_name}.py 不存在: {_py}")
    _added = _dir not in _sys.path
    if _added:
        _sys.path.insert(0, _dir)
    try:
        _spec = importlib.util.spec_from_file_location(
            f"_catfish_xcatfish_user_{module_name}", _py
        )
        if not _spec or not _spec.loader:
            raise ImportError(f"spec_from_file_location 失败: {_py}")
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        return _mod
    finally:
        if _added and _dir in _sys.path:
            _sys.path.remove(_dir)


resolver = _import_sibling("resolver")
session_registry = _import_sibling("session_registry")

# ─────────────────────────────────────────────────────────────────────────
# Step 1: import-time verify — patch 目标 attribute 必须存在, 否则 fail loud
# ─────────────────────────────────────────────────────────────────────────

_PATCH_TARGETS = [
    # (module, attribute, kind) — kind ∈ {"func", "method", "attr"}
    ("agent.agent_init", "init_agent", "func"),
    ("run_agent", "AIAgent", "attr"),
    ("agent.auxiliary_client", "_MAIN_RUNTIME_FIELDS", "attr"),
    ("agent.auxiliary_client", "_resolve_auto", "func"),
    ("agent.auxiliary_client", "_normalize_main_runtime", "func"),
    ("agent.title_generator", "auto_title_session", "func"),
    ("gateway.platforms.api_server", "APIServerAdapter", "attr"),
    # P15.1 (6/22): hermes v0.17 _is_gateway_approval_context() 检查
    # HERMES_SESSION_PLATFORM contextvar. P15 patch 必须调 set_session_vars()
    # set platform="api_server" 让 check_execute_code_guard 走 gateway approval
    # 路径弹按钮. 列入 _PATCH_TARGETS fail-loud: hermes 重构掉这俩函数 →
    # plugin install 时 ImportError 立刻报, 而不是 silent skip 让按钮不弹.
    ("gateway.session_context", "set_session_vars", "func"),
    ("gateway.session_context", "clear_session_vars", "func"),
    # P21 (P3.5.74, 6/22): hermes cron.scheduler.run_job — cron picker 联动 patch
    # 的 target. hermes 重构掉这函数 → plugin install 时 fail-loud 报, 不让 cron
    # silent 走老路径.
    ("cron.scheduler", "run_job", "func"),
    # P22 (P3.5.76) revert 在 P3.5.78 (6/22 鸿波): bookkeep 从独立 plugin 重构到
    # catfish-memory kind=expense 后, 不再需要 pin 到 _HERMES_CORE_TOOLS — memory
    # tool 本来就在 hermes core, expense 走 memory tool 自然 visible.
    # P23 (P3.5.79, 6/23): hermes gateway.run._resolve_gateway_model — inbound
    # message 路径 model 真决定函数, 微信/Discord/Slack 都走它. hermes 重构 →
    # plugin install fail-loud, 不让微信 silent 走 config.yaml.model.default 老路径
    # (跟 picker 脱钩).
    ("gateway.run", "_resolve_gateway_model", "func"),
    # P39 (7/31): codex_app_server 自己从 Codex CLI 读取 ChatGPT 登录，
    # 不该先要求 Hermes auth store 里另存一份 OAuth token。Hermes 0.18
    # 的 gateway resolver 顺序相反，导致有效 Codex 登录仍报 credentials missing。
    ("gateway.run", "_resolve_runtime_agent_kwargs", "func"),
    ("agent.codex_runtime", "run_codex_app_server_turn", "func"),
    # P25 (P3.5.104, 6/24): hermes tools/approval.py check_execute_code_guard —
    # cron env 污染防御. hermes cron/scheduler.py:1558 在 run_job 内 set
    # HERMES_CRON_SESSION env 但永不清, 整 daemon 进程污染, 后续 chat 调
    # execute_code 全被当 cron 拒. P25 wrap check_execute_code_guard 用 threadlocal
    # 精准判定 cron 范围. _PATCH_TARGETS 列 fail-loud — hermes 改名 → plugin
    # install 立刻报, 不让 chat silent 走污染路径被拒.
    ("tools.approval", "check_execute_code_guard", "func"),
    # P26 (P3.5.105, 6/25): hermes cron.jobs pause/resume/remove — cron 监控
    # RESTful endpoint (POST /api/cron/jobs/{id}/pause+resume / DELETE) 用. hermes
    # 改名 → plugin install fail-loud, 不让 Companion silent 拿到 500 误以为是
    # 网络问题.
    ("cron.jobs", "pause_job", "func"),
    ("cron.jobs", "resume_job", "func"),
    ("cron.jobs", "remove_job", "func"),
    # P27 (P3.5.106, 6/25 鸿波 catch "失败不重试"): hermes cron.jobs.mark_job_run +
    # update_job — 自动重试 5/10/15 三档 patch 的 target. wrap mark_job_run 真单点
    # (scheduler.py:2089/2094 都收敛这里, 不动 run_job 避开 P21/P25 第 3 层 wrap).
    # 改名 → plugin install fail-loud, 不让定时任务 silent 走 0 retry 老路径.
    ("cron.jobs", "mark_job_run", "func"),
    ("cron.jobs", "update_job", "func"),
    # P28 (P3.5.123, 6/25 鸿波 catch "微信里 ClawBot 英文不合适"):
    # hermes gateway/platforms/weixin.py:WeixinAdapter — wrap send 中文化 hermes
    # 英文 outbound 消息 (approval 提示 / 中断提示). 重构 / 改名 → plugin
    # install fail-loud, 不让微信 silent 走老英文路径.
    ("gateway.platforms.weixin", "WeixinAdapter", "attr"),
    # P24 (P3.5.89, 6/23): hermes api_server._CORS_HEADERS — 浏览器 preflight
    # allowlist. hermes 默认只 3 header (Authorization/Content-Type/Idempotency-Key).
    # 重构 / 改名 → plugin install fail-loud, 不让 catfish X-Catfish-* header
    # silent 撞 preflight block (TypeError: Load failed).
    ("gateway.platforms.api_server", "_CORS_HEADERS", "attr"),
    # P30 (P3.5.198, 7/8 鸿波): hermes weixin.py 的 qr 相关 helper — Companion
    # 微信扫码绑定 modal (WeChatQrLoginModal.tsx) 的后端 dep. hermes v0.19+ 改名
    # 掉这仨 → plugin install fail-loud, 不让 员工点扫码 silent 撞 500. (常量
    # ILINK_BASE_URL / EP_GET_BOT_QR / EP_GET_QR_STATUS 只要 module import 成功
    # 就 fresh 报 AttributeError, 不列在这里避免噪音.)
    ("gateway.platforms.weixin", "_api_get", "func"),
    ("gateway.platforms.weixin", "save_weixin_account", "func"),
    ("hermes_constants", "get_hermes_home", "func"),
]

_AIAGENT_METHOD_TARGETS = [
    "_current_main_runtime",
    "_apply_client_headers_for_base_url",
]

_APISERVER_METHOD_TARGETS = [
    "_create_agent",
    # _extract_catfish_outgoing_user 是我们新加的, 不要求 hermes 上游有
    # _handle_companion_proxy 同理
]


# 6/1 BL-PLUGIN-HERMES-015-LAZY-INSTALL (鸿波 6/1 必须今晚): 改延迟 install.
#
# # 真因 (audit 完整 stack)
# hermes 0.15.1 (5/29 升级) tools/skills_tool.py:850 顶部触发 discover_plugins().
# 由于 model_tools.py:32 顶部 `from tools.registry import discover_builtin_tools`,
# tools.registry 扫 tools/*.py 文件, 包括 tools/skills_tool.py 触发 plugin
# discovery, 链路:
#
#   主线程: import model_tools (partial init 中)
#     → tools/registry top-level (扫 tools/*)
#       → tools/skills_tool.py:850 调 discover_plugins()
#         → catfish-xcatfish-user.register(ctx)
#           → install() → _verify → import_module("run_agent")
#             → run_agent 顶部 `from model_tools import get_tool_definitions`
#               → model_tools 主线程 partial init 中 → ImportError circular ❌
#
# 主线程同步栈, retry / wait / `import model_tools` 都解不了 (同 frame 卡 partial).
#
# # 修法 (这次真有效)
# 1. _verify_patch_targets 改静态文件检查 (grep source code, 不 import 触发链路)
# 2. install() 不在 register(ctx) 立刻跑, 用后台线程等主流程 ready 再 install
# 3. _INSTALLED flag + pre_tool_call hook 兜底 fail-loud — 真 LLM call 时 plugin
#    没装载就 raise (维持"避免 silent 跨员工串数据 P0 漏洞" 设计意图)

# 全局 install 状态 flag — register 立刻 False, 真 install 完成置 True.
# pre_tool_call hook 检查它, 没装载就 raise (LLM 进不到真 tool call, 安全 fail).
_INSTALLED = False


def _check_attr_in_source(module_path: str, attr: str) -> bool:
    """静态文件检查 attribute 是否在 source code 里 (不 import, 避开 circular).

    用法: plugin 加载早期主线程在 model_tools partial init 中, 不能 import 真模块.
    用文件 grep 兜底 — 看 attribute 是否 def/class/var 形式在 source 里.
    真 patch 时 (主流程 ready 后) module 会被正常 import.
    """
    import os
    import re
    hermes_root = os.environ.get("HERMES_ROOT") or os.path.expanduser("~/.hermes/hermes-agent")
    # module_path "agent.agent_init" → 文件 agent/agent_init.py
    file_path = os.path.join(hermes_root, module_path.replace(".", "/") + ".py")
    if not os.path.exists(file_path):
        return False
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            src = f.read()
        # 匹配 def attr( / async def attr( / class attr / attr = / attr:
        pattern = rf"^\s*(?:def|async def|class)\s+{re.escape(attr)}\b|^{re.escape(attr)}\s*[:=]"
        return bool(re.search(pattern, src, re.MULTILINE))
    except Exception:
        return False


def _check_p15_stream_q_in_source() -> bool:
    """P15 fragile patch verify: 检测 hermes _handle_chat_completions 内部
    streaming branch 仍含 ``_stream_q.put`` 用法.

    P15 通过 ``_on_delta.__closure__`` 闭包反射拿 ``_stream_q`` (queue.Queue)
    引用, 推 approval event 给 SSE writer. 这条**硬依赖 hermes 内部 local 变量名**:
    hermes 重构改名 `_stream_q` → `_stream_queue` / `_q` / 不用 queue, P15
    silent break (闭包反射拿不到, _approval_notify 不注册, fallback path 跑,
    button 不弹, 用户看不出来).

    本检查在 install 时静态 grep, 一旦变量名变了 → fail-loud, 不让 plugin 半加载.
    """
    import os
    import re
    hermes_root = os.environ.get("HERMES_ROOT") or os.path.expanduser("~/.hermes/hermes-agent")
    file_path = os.path.join(hermes_root, "gateway/platforms/api_server.py")
    if not os.path.exists(file_path):
        return False
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            src = f.read()
        # 必须见: `_stream_q` 变量 + `_stream_q.put` (P15 闭包反射的 free var)
        # 必须见: `async def _on_delta` (P15 闭包反射的 target callback)
        has_stream_q_put = bool(re.search(r"\b_stream_q\.put\b", src))
        has_on_delta = bool(
            re.search(r"def\s+_on_delta\s*\(", src)
        )
        return has_stream_q_put and has_on_delta
    except Exception:
        return False


def _check_class_method_in_source(module_path: str, class_name: str, method: str) -> bool:
    """静态文件检查 class 是否含某 method (不 import). 同 _check_attr_in_source."""
    import os
    import re
    hermes_root = os.environ.get("HERMES_ROOT") or os.path.expanduser("~/.hermes/hermes-agent")
    file_path = os.path.join(hermes_root, module_path.replace(".", "/") + ".py")
    if not os.path.exists(file_path):
        return False
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            src = f.read()
        # 找 class XXX 后任意位置 def method(
        class_pattern = rf"class\s+{re.escape(class_name)}\b[^:]*:"
        class_match = re.search(class_pattern, src)
        if not class_match:
            return False
        # class 之后的代码里找 def method (允许 indent)
        rest = src[class_match.end():]
        method_pattern = rf"^\s+(?:def|async def)\s+{re.escape(method)}\b"
        return bool(re.search(method_pattern, rest, re.MULTILINE))
    except Exception:
        return False


def _verify_patch_targets() -> None:
    """静态文件检查 patch target 存在 (不 import — 避开 hermes 0.15.1 circular).

    真 patch 时 (后台线程主流程 ready 后) 才 import 真模块, 那时不再撞 partial.

    fail-loud 时机: 真 patch fail (in _patch_pX) OR pre_tool_call hook 检测 _INSTALLED.
    """
    missing = []
    for module_path, attr, kind in _PATCH_TARGETS:
        if not _check_attr_in_source(module_path, attr):
            missing.append(f"{module_path}.{attr} (expected {kind})")

    # AIAgent method 静态检查
    for method in _AIAGENT_METHOD_TARGETS:
        if not _check_class_method_in_source("run_agent", "AIAgent", method):
            missing.append(f"run_agent.AIAgent.{method}")

    # APIServerAdapter method 静态检查
    for method in _APISERVER_METHOD_TARGETS:
        if not _check_class_method_in_source(
            "gateway.platforms.api_server", "APIServerAdapter", method
        ):
            missing.append(
                f"gateway.platforms.api_server.APIServerAdapter.{method}"
            )

    # P15 fragile verify: 闭包反射 _stream_q 强依赖 hermes 内部变量名 (silent
    # break 风险). hermes 升级改 _on_delta 闭包 / streaming impl 时, 这里 fail-loud.
    if not _check_p15_stream_q_in_source():
        missing.append(
            "gateway.platforms.api_server._handle_chat_completions 内 "
            "_stream_q / _on_delta 闭包结构 (P15 闭包反射依赖此变量名). "
            "hermes 重构改了 streaming impl, P15 会 silent break. "
            "audit api_server.py:1892+ streaming branch, 同步更新 P15 反射目标 "
            "(plugin.py:_patch_p15_chat_completions_approval _stream_q name)."
        )

    if missing:
        raise ImportError(
            "catfish-xcatfish-user plugin: hermes refactor 破坏了 patch targets. "
            "缺失 attributes (静态文件检查): " + ", ".join(missing) + ". "
            "不允许半加载 (会导致跨员工串数据). 升级 plugin 或回滚 hermes."
        )

    logger.info(
        "catfish-xcatfish-user: patch target verify ✓ "
        "(静态文件检查 %d module + %d method + P15 _stream_q 闭包)",
        len(_PATCH_TARGETS),
        len(_AIAGENT_METHOD_TARGETS) + len(_APISERVER_METHOD_TARGETS),
    )


# ─────────────────────────────────────────────────────────────────────────
# Step 2: 应用 11 处 monkey-patch
# ─────────────────────────────────────────────────────────────────────────

def _apply_patches() -> None:
    """应用所有 monkey-patch. 顺序无关 (各自独立)."""
    _patch_asyncio_executor_for_contextvars()  # P0: CV 跨 thread, 必须第一个跑
    _patch_p1_agent_init()
    _patch_p2_current_main_runtime()
    _patch_p3_auxiliary_client()
    _patch_p4_auto_title_session()
    _patch_p5_p6_p11_api_server_create_agent_and_picker()
    _patch_p7_companion_proxy_route()
    _patch_p8_p9_cors()
    _patch_p10_apply_client_headers_localhost()
    _patch_p12_update_system_prompt_safe()
    _patch_p13_dump_naming_type_tag()
    _patch_p14_approve_chinese_alias()
    _patch_p15_chat_completions_approval()
    _patch_p15_2_chat_approval_route()
    # P3.4.C 6/15 鸿波: hard replace session_search. 用 try/except 包住 — P16 挂也不
    #   阻塞 hermes 启动 (鸿波 6/15 21:35 撞 hermes 起不来 "Could not connect", 真因
    #   推测是 P16 抛异常导致 _apply_patches 整体挂). P3.4.C fail-safe 设计.
    try:
        _patch_p16_session_search()
    except Exception as e:  # noqa: BLE001
        logger.error(
            "P16: _patch_p16_session_search 顶层异常 (跳过, 不阻塞 hermes 启动): %s",
            e, exc_info=True,
        )
    # P3.4.D 6/15 鸿波: bg-review HTTP 400 X-Catfish-User missing — patch AIAgent.__init__
    #   post-init, bg-review review_agent 从 session_registry 拿 parent cf_user 注入.
    try:
        _patch_p17_bg_review_inject()
    except Exception as e:  # noqa: BLE001
        logger.error(
            "P17: _patch_p17_bg_review_inject 顶层异常 (跳过, 不阻塞 hermes 启动): %s",
            e, exc_info=True,
        )
    # P3.5.18 (6/17 鸿波"直接压缩, 弹窗显示压缩进度"): 加 POST /api/sessions/{id}/compress/stream
    # SSE endpoint, Companion 主动 trigger hermes compress + 进度推 UI.
    # 抄 hermes Slack /compress + _handle_session_chat_stream SSE 模板.
    try:
        _patch_p18_compress_endpoint()
    except Exception as e:  # noqa: BLE001
        logger.error(
            "P18: _patch_p18_compress_endpoint 顶层异常 (跳过, 不阻塞 hermes 启动): %s",
            e, exc_info=True,
        )
    # P3.5.18 Phase 2 (6/17 鸿波"自动进行压缩, 提示这个不是觉得奇怪"): hermes preflight
    # 自动压缩时**Companion 0 反馈** — chat 卡 30 秒不知道发生啥. 修法:
    # _create_agent post-init 注入 status_callback 桥 tool_progress_callback,
    # preflight `_emit_status('📦 Preflight compression...')` 经 catfish-lifecycle
    # tool name 走 SSE hermes.tool.progress channel → Companion 接 + 显 inline.
    try:
        _patch_p19_status_callback_bridge()
    except Exception as e:  # noqa: BLE001
        logger.error(
            "P19: _patch_p19_status_callback_bridge 顶层异常 (跳过, 不阻塞 hermes 启动): %s",
            e, exc_info=True,
        )
    # P3.5.65 P20 (6/22 鸿波 catch "审批按钮一直不弹"): wrap approve_permanent /
    # load_permanent, 拦截 execute_code 进 _permanent_approved.
    # 真因 (read-only diagnostic 实证): 用户某次 audit UI 点 "always", 把
    # "execute_code" 字面字符串加进 _permanent_approved + config.yaml
    # command_allowlist. 之后每次 execute_code 调走 check_execute_code_guard:1749
    # is_approved("execute_code") → True → silent auto-approve 永远不弹按钮.
    # execute_code 是给 LLM **任意 Python 沙箱权限**的危险 pattern, 一旦 always-
    # approved = 给 LLM 完全 shell 权限. 红线: 永远不允许永久 approve.
    try:
        _patch_p20_block_execute_code_permanent()
    except Exception as e:  # noqa: BLE001
        logger.error(
            "P20: _patch_p20_block_execute_code_permanent 顶层异常 (跳过, 不阻塞 hermes 启动): %s",
            e, exc_info=True,
        )
    # P3.5.74 P21 (6/22 鸿波 catch "cron 不是用 picker 吗"): hermes cron/scheduler.py
    # run_job 读 config.yaml model.default 跟 picker_state.json 解耦, 员工切 picker
    # 后 cron job 仍走老 model. P3.5.28/42/42.1 把 picker 联动到 chat / advisor /
    # email scheduler / vision / catfish-memory summarize, 这里补 cron job — 最后
    # 一个 sprint gap.
    #
    # 复用 catfish-memory plugin 已有的 _read_picker_state_model helper (同款架构),
    # 优先级 picker_state.json > job.model > config.yaml.model.default > env (跟
    # _get_summarize_model 优先级一致).
    try:
        _patch_p21_cron_picker_integration()
    except Exception as e:  # noqa: BLE001
        logger.error(
            "P21: _patch_p21_cron_picker_integration 顶层异常 (跳过, 不阻塞 hermes 启动): %s",
            e, exc_info=True,
        )
    # P22 reverted in P3.5.78 (6/22 鸿波): bookkeep 重构进 catfish-memory.expense,
    # 不再需要 pin _HERMES_CORE_TOOLS — memory tool 本来就 core, kind=expense 自然命中.

    # P23 (P3.5.79 6/23 鸿波): inbound message 路径 (微信/Discord/Slack/Telegram)
    # picker 联动 — 修 catfish picker 联动 sprint 漏 cover 的最后一个 platform.
    try:
        _patch_p23_inbound_picker_integration()
    except Exception as e:  # noqa: BLE001
        logger.error(
            "P23: _patch_p23_inbound_picker_integration 顶层异常 (跳过, 不阻塞 hermes 启动): %s",
            e, exc_info=True,
        )

    # P24 (P3.5.89 6/23 鸿波): CORS allowlist 扩 X-Catfish-* — 修教学按钮 / 任何
    # 自定义 header 浏览器 preflight block 触发 TypeError: Load failed 真因.
    try:
        _patch_p24_cors_allowlist()
    except Exception as e:  # noqa: BLE001
        logger.error(
            "P24: _patch_p24_cors_allowlist 顶层异常 (跳过, 不阻塞 hermes 启动): %s",
            e, exc_info=True,
        )

    # P25 (P3.5.104 6/24 鸿波 catch "execute_code 不弹审批一直被拦"): cron env
    # 隔离, 治 hermes cron/scheduler.py:1558 设 HERMES_CRON_SESSION 后不清污染
    # 全 daemon 进程的 bug. P25 用 threadlocal 精准判定 cron 线程, 不依赖被污染
    # 的全进程 env. 必须在 P21 (wrap run_job) 之后调用 — P25 内部也 wrap run_job
    # set threadlocal, 顺序保证 P25 包 P21 包 orig, finally pop env 在最外层.
    try:
        _patch_p25_cron_env_isolation()
    except Exception as e:  # noqa: BLE001
        logger.error(
            "P25: _patch_p25_cron_env_isolation 顶层异常 (跳过, 不阻塞 hermes 启动): %s",
            e, exc_info=True,
        )

    # P26 (P3.5.105 6/25 鸿波 catch "定时任务跑没跑结果如何都看不到"): cron 监控
    # 操作 RESTful endpoint. attach handler 给 APIServerAdapter class; 真 route
    # 注册在 _patched_app_init 块 (Application 创建时, router 未 freeze), 跟 P18
    # add_post 同时机.
    try:
        _patch_p26_cron_rest_endpoints()
    except Exception as e:  # noqa: BLE001
        logger.error(
            "P26: _patch_p26_cron_rest_endpoints 顶层异常 (跳过, 不阻塞 hermes 启动): %s",
            e, exc_info=True,
        )

    # P27 (P3.5.106 6/25 鸿波 catch "失败不重试"): cron 任务失败 5/10/15 分钟 三档
    # 自动重试. wrap cron.jobs.mark_job_run, success=False 时改 next_run_at = now +
    # backoff, 累计 3 次后让 hermes 真按 schedule 跑下次 (退出 retry). delivery_error
    # 不触发 retry (agent 跑出来了, 发不出去是渠道问题). 跟 P21/P25 真独立 — 它们
    # wrap run_job, P27 wrap mark_job_run, 0 嵌套冲突.
    try:
        _patch_p27_cron_auto_retry()
    except Exception as e:  # noqa: BLE001
        logger.error(
            "P27: _patch_p27_cron_auto_retry 顶层异常 (跳过, 不阻塞 hermes 启动): %s",
            e, exc_info=True,
        )

    # P28 (P3.5.123 6/25 鸿波 catch "微信里 ClawBot 英文不合适"): :
    # wrap WeixinAdapter.send 真str.replace 英文 → 中文** (approval / 中断提示 /
    # /approve 命令说明). : 只 wrap weixin, slack / matrix 保英文.
    # 鸿波铁律: 中文 reply 段砍 /approve always (永久免批) :.
    try:
        _patch_p28_weixin_zh()
    except Exception as e:  # noqa: BLE001
        logger.error(
            "P28: _patch_p28_weixin_zh 顶层异常 (跳过, 不阻塞 hermes 启动): %s",
            e, exc_info=True,
        )

    # P29 (P3.5.168 7/3 鸿波 catch v0.18 upgrade backlog):
    # Companion 走 hermes 8642 /v1/chat/completions 时 /learn slash command 前置翻译.
    # 真因 (P3.5.164 严格 audit): hermes v0.18 /learn 只在 GatewayRunner._handle_message
    # 处理 (gateway/run.py:9263-9289), /v1/chat/completions (APIServerAdapter) 严格
    # 不过 slash command dispatcher. Companion 员工输 "/learn xxx" → LLM 只当 prompt
    # 释义. P29 wrap _run_agent 前置检测 → 调 hermes agent.learn_prompt.build_learn_prompt
    # 翻译 → 替换 message → P15 approval 闭包 → hermes original _run_agent.
    try:
        _patch_p29_learn_slash_translate()
    except Exception as e:  # noqa: BLE001
        logger.error(
            "P29: _patch_p29_learn_slash_translate 顶层异常 (跳过, 不阻塞 hermes 启动): %s",
            e, exc_info=True,
        )

    # P30 (P3.5.198 7/8 鸿波 catch "微信扫码绑定 Error: start 失败 404"):
    # 前端 wechat_qr.ts v3 (5/26) 契约缺 backend impl — hermes v0.17→v0.18
    # 升级弄丢 or 从来没落地. hermes weixin.qr_login helper (weixin.py:1003)
    # 是 CLI 阻塞打印 ASCII 二维码到 stdout 的单 flow 函数, 不能直接暴露 REST.
    # P30 拆成 stateless start/poll 双 endpoint, 复用 hermes ILINK_BASE_URL /
    # EP_GET_BOT_QR / EP_GET_QR_STATUS / _api_get / _make_ssl_connector /
    # save_weixin_account, 前端 wechat_qr.ts 零改动. 跟 P26 同模式: attach
    # handler 到 APIServerAdapter, route 在 _patched_app_init 注册.
    try:
        _patch_p30_wechat_qr_endpoints()
    except Exception as e:  # noqa: BLE001
        logger.error(
            "P30: _patch_p30_wechat_qr_endpoints 顶层异常 (跳过, 不阻塞 hermes 启动): %s",
            e, exc_info=True,
        )

    # P36 (P3.5.199 7/8 鸿波 catch "chat sandbox 相对路径找不到 uploads"):
    # hermes launchd 起 gateway → process cwd="/". LLM execute_code 里
    # `.catfish/uploads/x.csv` 相对路径 → subprocess.Popen(cwd="/") → 404.
    # 员工从来没期望 subprocess 从 `/` 起 (mac 员工机日常在 $HOME 干活).
    # hermes upstream 公开 TERMINAL_CWD env API: execute_code / terminal /
    # file_tools 都读它. setdefault 一次覆盖三条路径, 零 monkey-patch.
    try:
        _patch_p36_terminal_cwd_home()
    except Exception as e:  # noqa: BLE001
        logger.error(
            "P36: _patch_p36_terminal_cwd_home 顶层异常 (跳过, 不阻塞 hermes 启动): %s",
            e, exc_info=True,
        )

    # P39 (7/31): Codex App Server 模式在 provider credential resolver 之前
    # short-circuit。登录继续只由 Codex CLI 管，不复制 OAuth token 到 Hermes。
    try:
        _patch_p39_codex_app_server_auth_bypass()
    except Exception as e:  # noqa: BLE001
        logger.error(
            "P39: Codex App Server auth bypass patch 失败: %s",
            e, exc_info=True,
        )

    # P40 (7/31): Companion 与 Hermes/Codex 共用 state.db 时，某些 runtime 会在
    # 请求开始和 turn 完成各写一次相同 user message。把防重放在 SessionDB 公共
    # 写入层，覆盖 App、API server、Codex adapter 的所有组合。
    try:
        _patch_p40_companion_user_message_dedup()
    except Exception as e:  # noqa: BLE001
        logger.error(
            "P40: Companion user message dedup patch 失败: %s",
            e, exc_info=True,
        )


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

            cf_user = session_registry.lookup(parent_session_id)
            if not cf_user:
                # fallback (P3.4.D follow-up): 直接调 resolver.resolve_for_agent 走 5 步,
                # 含 (e) env CATFISH_DEFAULT_USER 兜底. 这样即使 session_registry 没 register
                # (chat agent 的 _current_main_runtime 没跑过), env 兜底也能注入.
                cf_user = resolver.resolve_for_agent(self)
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


# ── P1 ───────────────────────────────────────────────────────────────────

def _patch_p1_agent_init() -> None:
    """agent_init.init_agent 跑完 → apply_headers + 重建 client (拿到 X-Catfish-User)."""
    from agent import agent_init

    _orig = agent_init.init_agent

    def patched_init_agent(agent: Any, **kwargs: Any) -> Any:
        result = _orig(agent, **kwargs)
        try:
            if hasattr(agent, "_apply_client_headers_for_base_url"):
                agent._apply_client_headers_for_base_url(str(getattr(agent, "base_url", "") or ""))
            if hasattr(agent, "_replace_primary_openai_client"):
                agent._replace_primary_openai_client(reason="catfish_xcatfish_user_inject")
        except Exception as e:
            logger.warning("P1 post-init apply_headers failed: %s", e)
        return result

    agent_init.init_agent = patched_init_agent


# ── P2 ───────────────────────────────────────────────────────────────────

def _patch_p2_current_main_runtime() -> None:
    """AIAgent._current_main_runtime 加 catfish_outgoing_user + 注册到 session_registry."""
    from run_agent import AIAgent

    _orig = AIAgent._current_main_runtime

    def patched(self: Any) -> dict:
        d = _orig(self)
        cf_user = resolver.resolve_for_agent(self)
        if cf_user:
            d["catfish_outgoing_user"] = cf_user
            # 注册给 auxiliary task (auto_title 等) 跨线程查找
            sid = getattr(self, "session_id", "") or ""
            if sid:
                session_registry.register(sid, cf_user)
        return d

    AIAgent._current_main_runtime = patched


# ── P3 ───────────────────────────────────────────────────────────────────

def _patch_p3_auxiliary_client() -> None:
    """
    P3a: _MAIN_RUNTIME_FIELDS 加 catfish_outgoing_user — _normalize_main_runtime
         和 _client_cache_key 都是 runtime lookup, module 属性赋新 tuple 即生效.

    P3b: _resolve_auto wrap — return 前 rebuild OpenAI/AsyncOpenAI client
         带 default_headers={"X-Catfish-User": cf_user}.
    """
    from agent import auxiliary_client as aux

    # P3a
    if "catfish_outgoing_user" not in aux._MAIN_RUNTIME_FIELDS:
        aux._MAIN_RUNTIME_FIELDS = aux._MAIN_RUNTIME_FIELDS + ("catfish_outgoing_user",)

    # P3b
    _orig_resolve = aux._resolve_auto

    def patched_resolve_auto(main_runtime=None, task=None):
        # P3.5.88 (6/23 鸿波 catch context_compressor TypeError): hermes upstream
        # _resolve_auto 升级签名加 task=None 参数 (用于 task-specific aux routing).
        # P3 老 signature 单 main_runtime 撞 TypeError → context compression fail →
        # Companion 报 Load failed. 补 task=None + forward 给 _orig.
        client, model = _orig_resolve(main_runtime=main_runtime, task=task)
        try:
            if client is None:
                return client, model
            runtime = aux._normalize_main_runtime(main_runtime)
            cf_user = (runtime.get("catfish_outgoing_user") or "").strip()
            if not cf_user:
                return client, model
            base_url = str(getattr(client, "base_url", "") or "")
            # P29 (6/5): 走 _is_catfish_gateway_base_url 真 env-aware 检测,
            # 不再硬编码 localhost:8999 字符串 (中央部署 IP 会变)真.
            if not _is_catfish_gateway_base_url(base_url):
                return client, model
            cls = type(client)
            if cls.__name__ not in ("OpenAI", "AsyncOpenAI"):
                return client, model
            api_key = getattr(client, "api_key", "") or runtime.get("api_key", "") or ""
            client = cls(
                api_key=api_key,
                base_url=str(getattr(client, "base_url", "") or ""),
                default_headers={"X-Catfish-User": cf_user},
            )
        except Exception as e:
            logger.debug("P3b _resolve_auto rebuild failed: %s", e)
        return client, model

    aux._resolve_auto = patched_resolve_auto


# ── P4 ───────────────────────────────────────────────────────────────────

def _patch_p4_auto_title_session() -> None:
    """auto_title_session 跨线程跑, 走 session_registry lookup 补 main_runtime."""
    from agent import title_generator

    _orig = title_generator.auto_title_session

    def patched_auto_title_session(session_db, session_id, *args, **kwargs):
        main_runtime = kwargs.get("main_runtime")
        if isinstance(main_runtime, dict) and "catfish_outgoing_user" not in main_runtime:
            cf_user = session_registry.lookup(session_id)
            if cf_user:
                main_runtime["catfish_outgoing_user"] = cf_user
                kwargs["main_runtime"] = main_runtime
        return _orig(session_db, session_id, *args, **kwargs)

    title_generator.auto_title_session = patched_auto_title_session


# ── P5 / P6 / P11 ────────────────────────────────────────────────────────

# Companion 端 picker model_override 走 aiohttp middleware: 拦截 body.model 暂存
# 到 request, _create_agent wrap 时取出来覆盖 agent.model. 不动 hermes 内部 caller.

from aiohttp import web
import contextvars as _cv

_PICKER_PLACEHOLDERS = {"", "hermes-agent", None}

# contextvar: 真独立的 X-Catfish-User / picker model 传输通道.
# middleware 从 request 提取 → set CV → handler async 链路全程能 get().
# _create_agent wrap 从 CV 兜底读, 不依赖 hermes caller 在 handler mid-method
# 提取再 kwarg 传 (那是 hermes 仓 5/27-5/29 patch 的活, plugin 不该假设它在).
#
# 为什么 contextvar 而不是 request["x"]: _create_agent 签名全 kwargs 没 request,
# wrap 拿不到. contextvar 在 asyncio 同 context 树自动透传, 任何深处函数都能 get.
CV_CF_USER: "_cv.ContextVar[str]" = _cv.ContextVar("catfish_outgoing_user", default="")
CV_PICKER_MODEL: "_cv.ContextVar[str]" = _cv.ContextVar("picker_model_override", default="")

# P41 (8/8 鸿波"advisor 到底调没调"): 把 Companion 标的 catfish_source 透过 hermes
# 带到网关。
#
# 病: Companion 的每条内部调用都在 URL 上标了来源
# (?catfish_source=companion-advisor / companion-profile / companion-email-draft),
# 但**只有直连 8999 的那几条标得住**。走 hermes agent loop 的 (advisor Call 1)
# 会被 hermes 重新 framing 成一次 agent run, 再由 hermes 自己的 OpenAI client
# 打给网关 —— query 没了, 网关侧只看到 source=unknown。
#
# 代价 (8/8 实盘): advisor Call 1 和员工聊天在网关日志里**完全无法区分** ——
# 都是 sub=client:hermes-cli / total=2 / tools_count=107 / prompt 70K 量级。
# 排查早安页卡死时我按 source grep 判成"advisor 从没被调用", 又把一次聊天
# 认成 advisor, 连错两次方向。真相是从 hermes 自己的 agent.log 的
# `conversation turn: ... msg='hi'` 才看出来的。
#
# 修法跟 X-Catfish-User 完全同构 (同一条 CV 通道 + 同一处 default_headers),
# 不新增 patch 点。网关侧一行不用改 —— app.py:2903 本来就先读
# `x-catfish-source` header, query 只是它的 fallback。
CV_CF_SOURCE: "_cv.ContextVar[str]" = _cv.ContextVar("catfish_source", default="")


def _patch_asyncio_executor_for_contextvars():
    """让 asyncio loop.run_in_executor 自动 copy_context() 包 func.

    # 为什么需要

    hermes _run_agent 用 loop.run_in_executor(None, _run) 把 _run() (内含
    _create_agent) 推到 thread executor. middleware 在 asyncio main loop 设的
    CV (CV_CF_USER / CV_PICKER_MODEL) **不会自动跨到 executor thread**.
    contextvars 默认 thread-local; ThreadPoolExecutor 工人线程不继承提交方
    context.

    # 修法

    monkey-patch asyncio.BaseEventLoop.run_in_executor — 提交 func 前用
    copy_context().run() 包一层. 整 process 范围, 但安全 (其它代码也得益,
    没人 require executor 不见 CV).

    幂等: 重复 install 不二次 wrap.
    """
    import asyncio as _aio
    import functools as _functools

    # 不同 Python 版本路径不同: 3.11+ 顶层 asyncio.BaseEventLoop;
    # 兜底 asyncio.base_events.BaseEventLoop.
    _target_cls = None
    for _path in ("BaseEventLoop",):
        _t = getattr(_aio, _path, None)
        if _t is not None:
            _target_cls = _t
            break
    if _target_cls is None:
        try:
            from asyncio.base_events import BaseEventLoop as _BEL  # type: ignore
            _target_cls = _BEL
        except ImportError:
            logger.warning(
                "无法定位 asyncio BaseEventLoop, CV 跨 thread 不工作 "
                "(executor task 中 X-Catfish-User / picker 可能丢)"
            )
            return

    _orig = _target_cls.run_in_executor
    if getattr(_orig, "_catfish_cv_patched", False):
        return  # 已 patch, 跳过

    def _ctx_aware_run_in_executor(self, executor, func, *args):
        ctx = _cv.copy_context()

        @_functools.wraps(func)
        def _ctx_func(*a):
            return ctx.run(func, *a)

        return _orig(self, executor, _ctx_func, *args)

    _ctx_aware_run_in_executor._catfish_cv_patched = True  # type: ignore[attr-defined]
    _target_cls.run_in_executor = _ctx_aware_run_in_executor
    logger.info(
        "%s.run_in_executor wrapped with copy_context ✓",
        _target_cls.__module__ + "." + _target_cls.__name__,
    )


@web.middleware
async def _request_stash_middleware(request, handler):
    """拦 POST /v1/chat/completions /v1/responses:
       - X-Catfish-User header → CV_CF_USER
       - X-Catfish-Source header 或 ?catfish_source= query → CV_CF_SOURCE (P41 8/8)
       - body.model (非占位) → CV_PICKER_MODEL
       - 顺手塞 request[] (兼容 hermes 仓 patch 还在的场景)

    set 用 contextvars.copy_context 隔离 — 每个 request 独立, 不串.
    aiohttp middleware 默认在每个 request 自己的 asyncio.Task 跑, CV set 不影响
    其它并发 request.
    """
    if request.method == "POST" and request.path in ("/v1/chat/completions", "/v1/responses"):
        try:
            # X-Catfish-User header (大小写不敏感)
            cf_user = (request.headers.get("X-Catfish-User", "") or "").strip()
            if cf_user:
                CV_CF_USER.set(cf_user)
                request["catfish_outgoing_user"] = cf_user  # 兼容 hermes 仓 patch

            # P41 (8/8): 来源标记。header 优先, query 兜底 —— 跟网关
            # app.py:2903 的取值顺序保持一致。Companion 走 hermes 这条路时
            # 标在 query 上 (SERVICE_LLM_QUERY), 所以 query 分支才是常走的那条。
            cf_source = (
                (request.headers.get("X-Catfish-Source", "") or "").strip()
                or (request.query.get("catfish_source", "") or "").strip()
            )
            if cf_source:
                CV_CF_SOURCE.set(cf_source)
                request["catfish_source"] = cf_source

            # body.model — 需读 body. 读完塞回让 handler 再读 (aiohttp body 是 stream).
            body_bytes = await request.read()
            import json as _json
            try:
                body = _json.loads(body_bytes)
            except Exception:
                body = {}
            model = body.get("model")
            if isinstance(model, str) and model.strip() and model.strip() not in _PICKER_PLACEHOLDERS:
                model_clean = model.strip()
                CV_PICKER_MODEL.set(model_clean)
                request["catfish_model_override"] = model_clean
            request._read_bytes = body_bytes  # noqa: SLF001
        except Exception as e:
            logger.debug("request stash middleware failed: %s", e)
    return await handler(request)


# 保留旧名让别处 (P8/P9 注册) 引用兼容.
_picker_model_stash_middleware = _request_stash_middleware


def _patch_p5_p6_p11_api_server_create_agent_and_picker() -> None:
    """
    P5: APIServerAdapter._extract_catfish_outgoing_user 新方法
    P6: APIServerAdapter._create_agent wrap: post-init set attribute + apply_headers + picker
        model 覆盖
    P11: aiohttp middleware 拦截 body.model (在 _patch_p8_p9 一起注册到 app, 因为
         middleware 注册时机一致)
    """
    from gateway.platforms.api_server import APIServerAdapter

    # P5: _extract_catfish_outgoing_user
    def _extract_catfish_outgoing_user(self, request) -> str:
        """从 request 提 X-Catfish-User header (HTTP path).

        Companion / hermes-cli HTTP client 调 hermes 时已经把 user 在 header 里,
        直接提. CLI / 手动起的 agent 没这 header 时返回 "", agent 走 5 步链兜底.
        """
        try:
            user = (request.headers.get("X-Catfish-User", "") or "").strip()
            if user:
                return user
        except Exception:
            pass
        return ""

    APIServerAdapter._extract_catfish_outgoing_user = _extract_catfish_outgoing_user

    # P6 + P11: _create_agent wrap
    #
    # hermes 0.15 真实签名 (api_server.py:1101):
    #   def _create_agent(self, ephemeral_system_prompt=None, session_id=None,
    #                     stream_delta_callback=None, ..., model_override=None,
    #                     catfish_outgoing_user=None) -> Any
    # **全 kwargs**, 没有 request/body. 用 *args, **kwargs 通用 wrap, 不假设签名.
    #
    # 关键: 我们这版 plugin 不能拿到 request, 因为 hermes 0.15 native handler
    # 在 _create_agent 之前就把 body.model + X-Catfish-User header 提出来当 kwarg
    # 传进来 (model_override / catfish_outgoing_user). 所以 P6/P11 实质上是
    # **依靠 hermes 已经接受这俩 kwarg** — 我们仓内 5/27-5/29 patch 把它们 wire
    # 上了. 这是 plugin 跟 hermes 仓 patch 的耦合点.
    #
    # 如果 hermes 0.15 没 wire (上游 pristine), model_override / catfish_outgoing_user
    # 永远不会被 caller 传, plugin 就 noop. 这是 picker 这条最难走 plugin 的原因
    # (前面分析过). 真要 100% 不依赖 hermes 仓 patch, 必须走 aiohttp middleware
    # 拦 body.model + X-Catfish-User, 再 wrap _create_agent 取出来. middleware
    # 已经在 P11 注册 (_picker_model_stash_middleware), 但 X-Catfish-User 没拦.
    # 这条 TODO 留给 revert 后真破 P6 时再补.
    _orig_create_agent = APIServerAdapter._create_agent

    def patched_create_agent(self, *args, **kwargs):
        agent = _orig_create_agent(self, *args, **kwargs)
        # P6: X-Catfish-User 注入. 三段查找:
        # (1) kwargs.catfish_outgoing_user (hermes 仓 5/27 patch 在时 caller 传)
        # (2) CV_CF_USER (middleware 从 X-Catfish-User header 提取, plugin 独立路径)
        # (3) 不动 (orig _create_agent 自己 set 过的情况)
        #
        # 拿到 user 后: 写 agent attribute → 重 apply headers → 重建 OpenAI client.
        try:
            cf_user = kwargs.get("catfish_outgoing_user") or CV_CF_USER.get() or ""
            current = getattr(agent, "_catfish_outgoing_user", "") or ""
            if cf_user and cf_user != current:
                agent._catfish_outgoing_user = cf_user
                if hasattr(agent, "_apply_client_headers_for_base_url"):
                    agent._apply_client_headers_for_base_url(
                        str(getattr(agent, "base_url", "") or "")
                    )
                if hasattr(agent, "_replace_primary_openai_client"):
                    agent._replace_primary_openai_client(reason="catfish_user_from_cv")

            # P11: picker model override. 同样三段:
            # (1) kwargs.model_override (hermes 仓 5/28 picker patch 在)
            # (2) CV_PICKER_MODEL (middleware 从 body.model 提取)
            # (3) 不动
            model_override = kwargs.get("model_override") or CV_PICKER_MODEL.get() or ""
            # P3.5.51 probe verified P11/P6 真生效 — 真因不在 plugin 这条路径, 在
            # Companion store reset() 清 modelPickedByUser 让 catalog effect 覆盖
            # user picker (P3.5.52 修). probe 回 debug 不留 WARNING 噪音.
            if model_override and agent.model != model_override:
                logger.debug(
                    "P11 picker: overriding agent.model %s → %s",
                    agent.model, model_override,
                )
                agent.model = model_override
        except Exception as e:
            logger.warning("P6/P11 _create_agent post-init failed: %s", e)
        return agent

    APIServerAdapter._create_agent = patched_create_agent


# ── P7 ───────────────────────────────────────────────────────────────────

def _patch_p7_companion_proxy_route() -> None:
    """catch-all proxy 路由: Companion 调任何 catfish-gateway 不在 hermes native 路由
    的 endpoint, hermes 透传过去 (skill catalog / tool config / pairing 管理).

    实现: wrap APIServerAdapter 的路由注册函数, 末尾追加 catch-all.
    """
    from gateway.platforms.api_server import APIServerAdapter

    async def _handle_companion_proxy(self, request):
        """透传 request 到 catfish-gateway 8999, X-Catfish-User 沿用 request header.

        BL-PLUGIN-P7-PROXY-TOKEN-SWAP (6/1 鸿波实盘 audit):
        老实现透传 client Authorization, Companion 客户端用 hermes API_SERVER_KEY
        (64 hex), gateway 期 OIDC JWT, 不认 → 401 invalid token. 影响所有
        /api/me /api/audit/me /api/quota/me /api/proactive/* 端点.

        修: 替换 Authorization 成 service token (HERMES_SERVICE_TOKEN, sub=
        client:hermes-cli). hermes_cli 自己的 /v1/chat/completions 路径不走这,
        走内部 LiteLLM acompletion, model.api_key 拿 service token (同 token
        不同入口). User identity 走 X-Catfish-User 透传, gateway 用它取 user.

        前提: HERMES_SERVICE_TOKEN env 已配 (~/.hermes/.env, 由
        scripts/setup-catfish-edge.sh 装机时写). 没配则 swap 不发生, 老
        行为 — gateway 仍 401, 用户从错误看出 setup 没走完.
        """
        import aiohttp
        import os
        # BL-PLUGIN-P7-PROXY-TOKEN-SWAP (6/1 鸿波 audit): 砍硬编码, env 覆盖.
        # 老代码 `gateway_base = "http://127.0.0.1:8999"` 注释 "应该读 config 这里
        # hardcode 占位" 一直没改. 加 CATFISH_GATEWAY_URL env, 默认仍 localhost:8999
        # (开发机 / 标准装机). 生产环境 catfish-cli 装机 (setup-catfish-edge.sh)
        # 应该写这个 env 进 ~/.hermes/.env. TODO (P2): 改读 hermes config
        # `model.base_url`, 去 /v1 后缀.
        gateway_base = os.environ.get(
            "CATFISH_GATEWAY_URL", "http://127.0.0.1:8999"
        ).rstrip("/")
        target_url = f"{gateway_base}{request.path_qs}"
        body = await request.read()
        headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}

        # BL-PLUGIN-P7-PROXY-TOKEN-SWAP: 关键 swap.
        # P3.5.44 (鸿波 6/20 catch '不一次性解决留尾巴'): 老逻辑 os.environ.get 直接读,
        # token 30 天过期员工没设 cron 续 → 所有 /api/* cascade 401 (录屏/advisory/
        # proactive/fetchMe). 现在走 hermes_token_renewal.get_fresh_service_token,
        # 转发前自动检测剩余 < 5 天就调 catfish-identity mint 新, 写盘 + os.environ.
        # mint 失败 (没配 CLIENT_SECRET / IdP 挂) 仍 fallback 返当前 token, 跟老
        # 行为持平 (gateway 401 让员工看到错误信息, 不静默退化).
        from . import hermes_token_renewal  # noqa: PLC0415
        svc_token = await hermes_token_renewal.get_fresh_service_token()
        if svc_token:
            headers["Authorization"] = f"Bearer {svc_token}"
        else:
            logger.warning(
                "P7 proxy: HERMES_SERVICE_TOKEN env 没配, Authorization 透传 "
                "client Bearer. gateway 大概率 401. 跑 scripts/setup-catfish-edge.sh."
            )

        async with aiohttp.ClientSession() as session:
            async with session.request(
                method=request.method,
                url=target_url,
                headers=headers,
                data=body,
                allow_redirects=False,
            ) as upstream:
                resp = web.StreamResponse(
                    status=upstream.status,
                    headers={k: v for k, v in upstream.headers.items()
                             if k.lower() not in ("content-encoding", "transfer-encoding", "connection")},
                )
                await resp.prepare(request)
                async for chunk in upstream.content.iter_chunked(8192):
                    await resp.write(chunk)
                await resp.write_eof()
                return resp

    APIServerAdapter._handle_companion_proxy = _handle_companion_proxy

    # P7 改实现: aiohttp UrlDispatcher 不允许 catch-all (`/{proxy_path:.*}`) 跟
    # 已注册具体 routes 共存 (raises "method GET is already registered"). 改用
    # **404 fallback middleware** — 走完 routing dispatch, 看到 HTTPNotFound
    # 就 proxy 到 catfish-gateway. 等价效果, aiohttp 不抱怨.
    #
    # middleware 在 connect() wrap 里 append 到 _app.middlewares (跟 P11 stash
    # middleware 同位置注册).
    pass  # 真注册逻辑在 _patch_p7_companion_proxy_middleware (下面定义)


def _proxy_404_middleware_factory(handler_method_name="_handle_companion_proxy"):
    """生成 404 fallback middleware. handler_method_name = APIServerAdapter 上的
    proxy handler 方法名. 用 factory 是因为 middleware 需要绑 self 才能调
    self._handle_companion_proxy.
    """
    from aiohttp import web as _w

    @_w.middleware
    async def proxy_404_middleware(request, handler):
        try:
            return await handler(request)
        except _w.HTTPNotFound:
            # 路由没匹配 → 走 proxy
            adapter = request.app.get("_catfish_apiserver_adapter")
            if adapter is None or not hasattr(adapter, handler_method_name):
                raise  # 没 adapter 引用, 退回 404
            proxy_handler = getattr(adapter, handler_method_name)
            return await proxy_handler(request)

    return proxy_404_middleware


_proxy_404_middleware = _proxy_404_middleware_factory()


# ── P8 / P9 ──────────────────────────────────────────────────────────────

_TAURI_ORIGINS = (
    # packaged Tauri app 真用 origin
    "tauri://localhost",
    "http://tauri.localhost",
    # 6/2 晚 BL-CORS-DEV-ORIGIN: dev mode 真用 origin — Companion vite dev server.
    # 鸿波 6/2 晚生产事故 audit: webview console "[vite] connecting..." 真证.
    #
    # 6/2 晚 BL-CORS-DEV-ORIGIN-1420 (鸿波 21:15 真 paste vite log 抓的): vite 真启动
    # log "Local: http://localhost:1420/" — Tauri 模板默认 vite 端口是 1420 (Tauri
    # 文档推荐, 跟 vite 标准 5173 不同). 我下午盲加 5173 是错的, 加 1420 才真生效.
    "http://localhost:1420",
    "http://127.0.0.1:1420",
    # 保留 5173 作 fallback (有些员工自己改了 vite.config.ts 用 5173)
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)
_CATFISH_EXTRA_CORS_HEADERS = (
    "X-Catfish-User",
    "X-Catfish-Internal",
    # 6/2 晚 BL-CORS-PREV-MODEL: 真生产事故 — Companion chat.ts 真发 X-Catfish-Prev-Model
    # (跟 internal_models follow-user model 联动), hermes CORS preflight 拒, 整个
    # /v1/chat/completions 死. 浏览器 console:
    #   "Request header field X-Catfish-Prev-Model is not allowed by
    #    Access-Control-Allow-Headers"
    # 单条不在 allowlist 整 preflight 失败, 顺带把 X-Catfish-User 也报错. 加上.
    "X-Catfish-Prev-Model",
    "X-Catfish-Source",  # 顺手 — BL-RBAC-DAY4-HARDENING audit header, Companion 可能发
    "X-Hermes-Session-Id",
    "X-Hermes-Session-Key",
)


def _patch_p8_p9_cors() -> None:
    """
    P8: Tauri origin (Companion 桌面 app) 加进允许列表.
    P9: Allow-Headers 加 X-Catfish-* / X-Hermes-Session-* (Companion 发的自定义 header).
    P11: 顺便把 picker middleware 注册到 app.
    """
    from gateway.platforms.api_server import APIServerAdapter

    # P9: 扩 Allow-Headers.
    # hermes 0.15: _CORS_HEADERS 是 **module-level 常量** (api_server.py:502), 不
    # 是 class attribute. 直接 mutate module dict. 同时给 class 兼容性写一份 (有
    # 老版本可能在 class 上).
    try:
        from gateway.platforms import api_server as _api_server_mod
        if hasattr(_api_server_mod, "_CORS_HEADERS"):
            existing = _api_server_mod._CORS_HEADERS.get("Access-Control-Allow-Headers", "")
            existing_set = {h.strip() for h in existing.split(",") if h.strip()}
            for h in _CATFISH_EXTRA_CORS_HEADERS:
                existing_set.add(h)
            _api_server_mod._CORS_HEADERS["Access-Control-Allow-Headers"] = ", ".join(sorted(existing_set))
            logger.info("P9 module-level _CORS_HEADERS Allow-Headers 扩 ✓")
    except Exception as _e:
        logger.warning("P9 module-level _CORS_HEADERS 修改失败: %s", _e)

    # 老版本可能 class attribute (兼容)
    if hasattr(APIServerAdapter, "_CORS_HEADERS"):
        existing = APIServerAdapter._CORS_HEADERS.get("Access-Control-Allow-Headers", "")
        existing_set = {h.strip() for h in existing.split(",") if h.strip()}
        for h in _CATFISH_EXTRA_CORS_HEADERS:
            existing_set.add(h)
        APIServerAdapter._CORS_HEADERS["Access-Control-Allow-Headers"] = ", ".join(sorted(existing_set))

    # P8: 给 APIServerAdapter 装 _is_tauri_origin 方法 + wrap origin 检查
    def _is_tauri_origin(self, origin: str) -> bool:
        if not origin:
            return False
        return origin in _TAURI_ORIGINS

    APIServerAdapter._is_tauri_origin = _is_tauri_origin

    # 找 origin 校验函数 — 跨版本名字可能漂. 候选:
    for candidate in ("_origin_allowed", "_is_origin_allowed", "_cors_origin_allowed"):
        if hasattr(APIServerAdapter, candidate):
            _orig_origin_check = getattr(APIServerAdapter, candidate)

            def patched_origin_check(self, origin: str, _orig=_orig_origin_check) -> bool:
                if self._is_tauri_origin(origin):
                    return True
                return _orig(self, origin)

            setattr(APIServerAdapter, candidate, patched_origin_check)
            break
    else:
        logger.warning("P8: cannot find origin check method, Tauri origin NOT allowed via CORS")

    # 6/2 晚 BL-CORS-RETURN-HEADERS: wrap `_cors_headers_for_origin` 真返 CORS headers
    # 给 tauri/dev origin. 真生产事故续: 上一 commit 加 dev origin 到 _TAURI_ORIGINS,
    # `_origin_allowed` 真过, 但 hermes cors_middleware 之后调 `_cors_headers_for_origin`,
    # 这函数**没被 patch**, 看 self._cors_origins (空, 因 hermes config 没设) 返 None.
    # OPTIONS + cors_headers is None → 403.
    # 加 patch: tauri origin 时构造 headers (Allow-Origin = origin, Allow-Headers 来自
    # module-level _CORS_HEADERS 含 P9 加的 X-Catfish-* 全套).
    if hasattr(APIServerAdapter, "_cors_headers_for_origin"):
        _orig_cors_headers = APIServerAdapter._cors_headers_for_origin

        def patched_cors_headers(self, origin: str, _orig=_orig_cors_headers):
            # Tauri / dev origin: 自己构造 headers, 不走原函数 (它要求 _cors_origins
            # 非空才返). 直接用 module-level _CORS_HEADERS (含 P9 扩的 Allow-Headers).
            if self._is_tauri_origin(origin):
                from gateway.platforms import api_server as _api_server_mod
                headers = dict(_api_server_mod._CORS_HEADERS)
                headers["Access-Control-Allow-Origin"] = origin
                headers["Vary"] = "Origin"
                headers["Access-Control-Max-Age"] = "600"
                return headers
            # 非 tauri/dev origin: 走原 hermes 逻辑 (按 self._cors_origins 配)
            return _orig(self, origin)

        APIServerAdapter._cors_headers_for_origin = patched_cors_headers
        logger.info("P8 _cors_headers_for_origin patched ✓ (tauri/dev origin 真返 CORS headers)")
    else:
        logger.warning("P8: APIServerAdapter._cors_headers_for_origin 不存在, dev origin 仍会 403")

    # P11: 注册 stash middleware 到 self._app.
    # hermes 0.15: self._app 在 async connect() 里建 (api_server.py:4653
    # `self._app = web.Application(middlewares=mws, ...)`), 不是 __init__.
    # patch __init__ 时 _app 还没存在, 会 'NoneType.middlewares' 错.
    # 改 patch connect() — 在 orig connect 跑完后 (此时 _app 已建) append.
    # aiohttp web.Application 在 AppRunner.setup() 之后 freeze middlewares,
    # connect() wrap 已经晚了. 用 web.Application monkey-patch — 让构造时就把
    # 我们的 middleware 加进 middlewares 参数. 跨整个 process 影响每个新建的
    # Application, 但我们用 fence (检查构造参数有没有 cors_middleware /
    # security_headers_middleware) 只对 hermes api_server 的 Application 生效.
    import aiohttp.web as _aw

    if not getattr(_aw.Application.__init__, "_catfish_patched", False):
        _orig_app_init = _aw.Application.__init__

        def _patched_app_init(self, *args, middlewares=(), **kwargs):
            mws_list = list(middlewares) if middlewares else []
            is_hermes_app_local = False
            try:
                from gateway.platforms.api_server import (
                    cors_middleware,
                    security_headers_middleware,
                )
                # fence: 只对 hermes api_server App 注入. 别的 aiohttp Application
                # (Companion 本地 server / 别的 plugin) 不动.
                is_hermes_app_local = (
                    cors_middleware in mws_list
                    or security_headers_middleware in mws_list
                )
                if is_hermes_app_local:
                    if _request_stash_middleware not in mws_list:
                        mws_list.append(_request_stash_middleware)
                    if _proxy_404_middleware not in mws_list:
                        mws_list.append(_proxy_404_middleware)
                    # P15.2 (6/6): chat_approval middleware. 必须 register 阶段同步跑
                    # (__init__.py:Step 2.7), delayed install 太晚 — Application.__init__
                    # 在 connect() 立即 trigger, 早于 delayed install 3 分钟.
                    if _chat_approval_middleware is not None and _chat_approval_middleware not in mws_list:
                        mws_list.append(_chat_approval_middleware)
                    logger.info(
                        "P7/P11/P15.2 middlewares injected via Application.__init__ fence ✓"
                    )
            except Exception as _e:
                logger.debug("middleware inject fence check failed: %s", _e)
            _orig_app_init(self, *args, middlewares=tuple(mws_list), **kwargs)
            # P18 (P3.5.18 6/17 鸿波) — post-init add_post router 未 freeze 前.
            #
            # 问题 (6/17 22:16 鸿波本机 catch): 之前 P18 wrap connect post
            # _orig_connect runner.setup() → app.freeze() 已跑, add_post too late
            # 撞 'Cannot register a resource into frozen router'. 真fix**: Application
            # 真创建时 add_post (此刻 router 未 freeze, hermes 自己 connect add_post
            # 真同时机**). handler runtime call `adapter._handle_compress_session_stream`
            # via P7 stashed `request.app["_catfish_apiserver_adapter"]` (line 1241).
            if is_hermes_app_local:
                try:
                    async def _p18_compress_handler(request):
                        adapter = request.app.get("_catfish_apiserver_adapter")
                        if adapter is None:
                            return _aw.json_response(
                                {"error": "P18 adapter not ready (P7 stash missing)"},
                                status=503,
                            )
                        return await adapter._handle_compress_session_stream(request)
                    self.router.add_post(
                        "/api/sessions/{session_id}/compress/stream",
                        _p18_compress_handler,
                    )
                    logger.info(
                        "P18 route POST /api/sessions/{id}/compress/stream registered "
                        "(via Application.__init__) ✓"
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "P18 add_post 失败 (via Application.__init__): %s", e,
                    )
                # ── P26 (P3.5.105 6/25 鸿波): cron RESTful endpoints ──
                #
                # 跟 P18 同时机注册 (router 未 freeze), handler 走 P7 stashed adapter.
                # 3 个 endpoint: pause / resume / delete.
                try:
                    async def _p26_cron_pause_handler(request):
                        adapter = request.app.get("_catfish_apiserver_adapter")
                        if adapter is None:
                            return _aw.json_response(
                                {"error": "P26 adapter not ready (P7 stash missing)"},
                                status=503,
                            )
                        return await adapter._handle_cron_pause(request)

                    async def _p26_cron_resume_handler(request):
                        adapter = request.app.get("_catfish_apiserver_adapter")
                        if adapter is None:
                            return _aw.json_response(
                                {"error": "P26 adapter not ready (P7 stash missing)"},
                                status=503,
                            )
                        return await adapter._handle_cron_resume(request)

                    async def _p26_cron_delete_handler(request):
                        adapter = request.app.get("_catfish_apiserver_adapter")
                        if adapter is None:
                            return _aw.json_response(
                                {"error": "P26 adapter not ready (P7 stash missing)"},
                                status=503,
                            )
                        return await adapter._handle_cron_delete(request)

                    self.router.add_post(
                        "/api/cron/jobs/{job_id}/pause", _p26_cron_pause_handler,
                    )
                    self.router.add_post(
                        "/api/cron/jobs/{job_id}/resume", _p26_cron_resume_handler,
                    )
                    self.router.add_delete(
                        "/api/cron/jobs/{job_id}", _p26_cron_delete_handler,
                    )
                    logger.info(
                        "P26 cron routes registered: POST /api/cron/jobs/{id}/pause"
                        " + /resume + DELETE /api/cron/jobs/{id} ✓"
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "P26 cron add_post/delete 失败 (via Application.__init__): %s",
                        e,
                    )
                # ── P30 (P3.5.198 7/8 鸿波): wechat qr_login start/poll ──
                #
                # 跟 P26 同时机注册 (router 未 freeze), handler 走 P7 stashed
                # adapter. 2 个 endpoint:
                #   POST /api/platforms/wechat/qr_login/start (无 body)
                #   GET  /api/platforms/wechat/qr_login/poll?qrcode=<hex>
                # 前端 wechat_qr.ts v3 契约, adapter._handle_wechat_qr_*
                # 自己走 _check_auth.
                try:
                    async def _p30_wechat_qr_start_handler(request):
                        adapter = request.app.get("_catfish_apiserver_adapter")
                        if adapter is None:
                            return _aw.json_response(
                                {"error": "P30 adapter not ready (P7 stash missing)"},
                                status=503,
                            )
                        return await adapter._handle_wechat_qr_start(request)

                    async def _p30_wechat_qr_poll_handler(request):
                        adapter = request.app.get("_catfish_apiserver_adapter")
                        if adapter is None:
                            return _aw.json_response(
                                {"error": "P30 adapter not ready (P7 stash missing)"},
                                status=503,
                            )
                        return await adapter._handle_wechat_qr_poll(request)

                    self.router.add_post(
                        "/api/platforms/wechat/qr_login/start",
                        _p30_wechat_qr_start_handler,
                    )
                    self.router.add_get(
                        "/api/platforms/wechat/qr_login/poll",
                        _p30_wechat_qr_poll_handler,
                    )
                    logger.info(
                        "P30 wechat qr routes registered: "
                        "POST /api/platforms/wechat/qr_login/start + "
                        "GET /api/platforms/wechat/qr_login/poll ✓"
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "P30 wechat qr add_post/get 失败 (via Application.__init__): %s",
                        e,
                    )
            return None

        _patched_app_init._catfish_patched = True  # type: ignore[attr-defined]
        _aw.Application.__init__ = _patched_app_init

    # connect() wrap: 装 adapter 引用 (proxy_404_middleware 要它调
    # self._handle_companion_proxy). 不再 append middleware (上面构造时已加).
    _orig_connect = APIServerAdapter.connect

    async def patched_connect(self, *args, **kwargs):
        result = await _orig_connect(self, *args, **kwargs)
        try:
            if getattr(self, "_app", None) is not None:
                self._app["_catfish_apiserver_adapter"] = self
        except Exception as e:
            logger.warning("P7 adapter ref set failed: %s", e)
        return result

    APIServerAdapter.connect = patched_connect


# ── P10 ──────────────────────────────────────────────────────────────────

def _patch_p10_apply_client_headers_localhost() -> None:
    """AIAgent._apply_client_headers_for_base_url 加 localhost:8999 分支.

    wrap: 先 check 是不是 catfish-gateway, 是就跑我们 5 步链 set X-Catfish-User,
    return. 不是就调 orig (其它 provider OpenRouter/NIM/Codex etc.).
    """
    from run_agent import AIAgent

    _orig = AIAgent._apply_client_headers_for_base_url

    def patched(self, base_url: str) -> None:
        # P29 (6/5): env-aware gateway 检测, 不再硬编码 localhost:8999.
        if _is_catfish_gateway_base_url(base_url or ""):
            cf_user = resolver.resolve_for_agent(self)
            headers: dict[str, str] = {}
            if cf_user:
                headers["X-Catfish-User"] = cf_user
            # P41 (8/8): 来源标记跟着走。中间件在 inbound 请求上 set 的 CV,
            # 经 _patch_asyncio_executor_for_contextvars 的 copy_context 一路
            # 带到 executor 线程, 这里读得到。
            #
            # **跟 X-Catfish-User 解耦**: 有 source 没 user 的场景 (CLI / cron
            # 调 hermes 时不带 user) 也要标得住, 所以不写在 if cf_user 里面。
            try:
                cf_source = (CV_CF_SOURCE.get() or "").strip()
            except Exception:  # noqa: BLE001  CV 不该抛, 抛了也不能拖垮发请求
                cf_source = ""
            if cf_source:
                headers["X-Catfish-Source"] = cf_source

            if headers:
                self._client_kwargs["default_headers"] = headers
            else:
                # 两个都没有时 explicit clear (防别地方继承上一轮)
                self._client_kwargs.pop("default_headers", None)
            return
        return _orig(self, base_url)

    AIAgent._apply_client_headers_for_base_url = patched


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


# ─────────────────────────────────────────────────────────────────────────
# Step 3: plugin entry point
# ─────────────────────────────────────────────────────────────────────────

_PATCHED = False


def install() -> None:
    """plugin 入口. hermes 加载时调一次.

    幂等: 重复调不会重 patch (避免 double-wrap 导致 5 步链跑 5 次).
    """
    global _PATCHED, _INSTALLED
    if _PATCHED:
        logger.debug("catfish-xcatfish-user already installed, skip")
        return

    _verify_patch_targets()
    _apply_patches()
    _PATCHED = True
    _INSTALLED = True  # 6/1 BL-PLUGIN-HERMES-015-LAZY-INSTALL: pre_tool_call hook 看这个

    logger.info("catfish-xcatfish-user plugin installed ✓ (15 patches applied)")


# 6/1 BL-PLUGIN-HERMES-015-LAZY-INSTALL — pre_tool_call hook 兜底 fail-loud.
# 真正的 "避免 silent 跨员工串数据 P0 漏洞" 安全检查: 第一次 LLM tool call 之前,
# 看 _INSTALLED. 没装 plugin 等于跨员工 header 没注入 — 立刻 raise, hermes 拒服务.
# 比 plugin 加载时 fail 更精准: 真用到时检, 不在加载时.
def pre_tool_call_safety_check(*args, **kwargs):
    """hermes pre_tool_call hook.

    Hermes protocol (hermes_cli/plugins.py:1666 get_pre_tool_call_block_message):
      - 返 dict {"action": "block", "message": "..."} → hermes 真 block tool call, message 真给 LLM
      - raise / 真返其它 → silently ignored, tool call 真继续

    本 hook 真职责: plugin 装载 invariant — 没装 raise (跨员工 P0 风险).

    BL-LLM-NO-TERMINAL-BYPASS-V2 (2026-06-03) 历史:
      v1 在这里 block terminal 真 LLM 直调路径, 撤回真因 hermes hook 真无
      parent_tool 字段, 真无法区分 LLM 直调 vs catfish_run_skill / skill 内部用
      terminal. 一刀切 block 真误伤员工合法 skill 路径.
      v2 改成: catfish-memory plugin prefetch 真加 _render_safety_redline 真注入
      user message 末尾, LLM 真每轮看红线 prompt 自查不绕 sandbox.
    """
    if not _INSTALLED:
        raise RuntimeError(
            "catfish-xcatfish-user 未装载: hermes 主流程 ready 后后台 install 没完成. "
            "可能 hermes 0.15+ 内部变化, 看 ~/.hermes/logs/mcp-stderr.log. "
            "跨员工数据 P0 风险, 拒服务."
        )

    return None  # 让 tool call 继续


# ── P14 ──────────────────────────────────────────────────────────────────
#
# P14 (6/5 鸿波) — 中文 "批准" / "拒绝" → hermes /approve / /deny slash command
#
# 背景: hermes execute_code guard (approval.py:1455) 在 gateway/ask context 走
# notify_cb 等用户决定. Companion 没注册 notify_cb (P22 BL), fallback 走 message
# field "Asking the user for approval" + 等 user `/approve` 文字命令解除. LLM
# 看到 message 翻译成中文 "请批准", 鸿波打 "批准" — hermes 不识别中文 alias →
# 不 dispatch 到 _handle_approve_command → 死循环.
#
# 本 patch: GatewayRunner._handle_message 真入口前**预处理 event.text, 中文
# alias → 改成 /approve / /deny 真 slash command `字面, 走原 hermes
# dispatch flow. 不动 hermes approval 逻辑 (松耦合).
#
# 别名设计 (鸿波语义习惯, 6/5 拍):
#   "批准"/"同意"/"通过"/"确认"/"审批" → /approve  (单次)
#   "总是批准"/"始终批准"           → /approve always (永久)
#   "本会话批准"/"会话批准"        → /approve session (本 session)
#   "拒绝"/"驳回"/"不同意"/"取消"   → /deny

_APPROVE_ALIASES = {
    # ── 无 / 版本 (员工口语) ─────────────────────────
    "批准": "/approve",
    "同意": "/approve",
    "通过": "/approve",
    "确认": "/approve",
    "审批": "/approve",
    "ok": "/approve",
    "好的": "/approve",
    "可以": "/approve",
    "总是批准": "/approve always",
    "始终批准": "/approve always",
    "总是允许": "/approve always",
    "本会话批准": "/approve session",
    "会话批准": "/approve session",
    "本次会话": "/approve session",
    "本次会话批准": "/approve session",
    "拒绝": "/deny",
    "驳回": "/deny",
    "不同意": "/deny",
    "取消": "/deny",
    "no": "/deny",
    # ── 带 / 版本 (Bot 提示词里出现的 · 员工按提示复制) ─────────
    # BL-P14-SLASH-ALIAS (7/19 鸿波 catch): Bot 提示 `/批准 本次会话` · 但老 P14 patch
    # `if not raw.startswith("/")` 直接跳过 · 员工按提示发 · plugin 认不出 · 又弹审批.
    # 且 · 老死代码 _P28_CMD_ALIASES 定义未使用 · 应 dedupe 到这里.
    "/批准": "/approve",
    "/批准 本次会话": "/approve session",
    "/批准本次会话": "/approve session",
    "/批准 执行": "/approve",  # Bot 提示词里"回复 /批准 执行 (单次)"
    "/拒绝": "/deny",
}


def _patch_p14_approve_chinese_alias() -> None:
    """GatewayRunner._handle_message 入口前预处理 event.text 中文 → slash.

    只在 session 有 blocking approval 时触发别名 (has_blocking_approval),
    避免误改正常 chat (员工说 "可以" / "好的" 当聊天话不该被吞)真.
    """
    try:
        from gateway.run import GatewayRunner
        from tools.approval import has_blocking_approval
    except ImportError as e:
        logger.warning("P14: GatewayRunner / has_blocking_approval import 失败 (%s), skip", e)
        return

    _orig_handle = GatewayRunner._handle_message

    async def patched(self, event):
        try:
            raw = (event.text or "").strip()
            # BL-P14-SLASH-ALIAS (7/19 鸿波 catch WeChat "/批准 本次会话" 又弹审批):
            # 老逻辑 `if raw and not raw.startswith("/")` 让**带 / 前缀直接跳过** ·
            # 但 Bot outbound 提示词 (P28 line 3418) 就是 `/批准 本次会话` · 员工按
            # 提示复制发 · 全被跳过 · hermes 不认 `/批准` slash · 当新 msg 触发 LLM ·
            # LLM 想帮员工又调 execute_code · 又弹审批. 死循环.
            #
            # BL-P14-SLASH-UNCONDITIONAL (7/19 二次 catch): 更细分 · 带 / 是员工
            # **明确意图** · 无论 pending 有没都翻译 (hermes 收 /approve 无 pending 会
            # silent no-op · 不会像 /批准 那样 Unknown command). 不带 / 是中文口语 ·
            # 需 has_blocking_approval gate 避免误吞正常聊天 ("好的"/"可以").
            if raw:
                alias = _APPROVE_ALIASES.get(raw.lower())
                if alias:
                    if raw.startswith("/"):
                        # 明确 slash · 无条件翻译 · pending timeout 也能续命
                        logger.info(
                            "P14 slash alias (unconditional): '%s' → '%s'", raw, alias
                        )
                        event.text = alias

                        # BL-P25-SESSION-SCOPE-SUPPLEMENT (7/19 Task #25 深追):
                        # `/approve session` hermes 内部 _handle_approve_command 只
                        # resolve 当前 pending · **可能**没调 approve_session(session_key,
                        # pattern_key). 补 · WeChat 场景 /批准 本次会话 · 我们**主动**
                        # 调 approve_session · 把 "execute_code" pattern_key 加进
                        # _session_approved · 后续同 pattern execute_code auto-approve ·
                        # 不再弹.
                        if alias == "/approve session":
                            try:
                                from tools.approval import approve_session
                                sess = self._session_key_for_source(event.source)
                                if sess:
                                    approve_session(sess, "execute_code")
                                    logger.info(
                                        "P25 supplement: approve_session(%s, 'execute_code') "
                                        "手工加进 _session_approved · 后续同 pattern 免批",
                                        sess[:12],
                                    )
                            except Exception as e:  # noqa: BLE001
                                logger.warning(
                                    "P25 supplement: approve_session 手工调挂 (%s) · "
                                    "hermes 内部 _handle_approve_command 应仍生效", e
                                )
                    else:
                        # 不带 / 中文口语 · 走原 pending gate 避免误吞 chat
                        try:
                            session_key = self._session_key_for_source(event.source)
                        except Exception:
                            session_key = ""
                        if session_key and has_blocking_approval(session_key):
                            logger.info(
                                "P14 casual alias (gated): '%s' → '%s' (session=%s)",
                                raw, alias, session_key[:12],
                            )
                            event.text = alias
                elif raw.startswith("/") and raw.lower().startswith(("/批准", "/拒绝")):
                    # 带 / 中文 · 但字典没这 key · 打 log 找漏 · Bot 提示词与字典必须对齐
                    logger.warning(
                        "P14 unknown chinese slash: '%s' — 加进 _APPROVE_ALIASES 字典",
                        raw[:60],
                    )
        except Exception as e:  # noqa: BLE001
            logger.debug("P14 alias preprocess 异常 (ignored): %s", e)
        return await _orig_handle(self, event)

    GatewayRunner._handle_message = patched
    logger.info("P14 chinese approval alias patched (GatewayRunner._handle_message)")


# ── P15 ──────────────────────────────────────────────────────────────────
#
# P15 (6/5 鸿波 marathon audit) — Companion chat/completions 真 approval 闭环.
#
# 背景 (audit api_server.py:1892-1987 + tools/approval.py:1454-1574):
#   1. Companion 走 /v1/chat/completions (chat.ts:190). 这条 path 不 register
#      _gateway_notify_cbs (跟 /v1/runs path 不同, 后者 line 3802 注册).
#   2. execute_code 触发 check_execute_code_guard → 走 approval.py:1554 fallback
#      (notify_cb is None) → 立即 return pending dict, agent 不阻塞继续下一轮.
#   3. 用户点 button 发 "/approve" user message → chat completions 没 slash command
#      hook → LLM 直接看 "/approve" 编释义. 永远不解 block.
#   4. _gateway_queues vs _pending 是两个独立 dict — fallback 写 _pending,
#      resolve_gateway_approval 操作 _gateway_queues. 没人 resolve fallback.
#
# 修法 (不 fork _handle_chat_completions, 用 Python 闭包反射):
#   - hermes 闭包 _on_delta 持有 local 变量 _stream_q (api_server.py:1896).
#   - patched _run_agent 通过 stream_delta_callback.__closure__ 拿 _stream_q ref.
#   - 注册 _approval_notify 到 _gateway_notify_cbs[session_key], notify 时把
#     approval data 走现有 ("__tool_progress__", dict) tuple pattern push 进
#     _stream_q. _write_sse_chat_completion._emit (line 2156) 自动写出
#     `event: hermes.tool.progress` SSE event 给 Companion (复用现有协议, 不动
#     hermes 一行代码).
#   - Companion 端 chat.ts 解析 SSE event 行 (新增), 检测 status==approval_pending
#     时 ChatToolCall 弹 button, onClick → tool-bridge RPC `chat_approval`
#     (plugin 加的 RPC method) → resolve_gateway_approval(sid, choice) 解 block.
#
# 风险评估:
#   - 闭包反射 _on_delta.__closure__ 依赖 hermes 内部变量名 `_stream_q`. hermes
#     0.16+ 改名 / 改实现 → patch 失效 (silent fail, 只是 button 不工作,
#     不会 crash). 加 verify_target check 用 grep 检测变量名仍存.
#   - patch _run_agent (公开 method) 比 fork streaming branch 风险低 1 个数量级.

_APPROVE_REQUEST_TOOL_MARKER = "_approval_request"  # Companion 检测这字符串


def _patch_p15_chat_completions_approval() -> None:
    """patch APIServerAdapter._run_agent — chat/completions 注入 _approval_notify."""
    try:
        from gateway.platforms.api_server import APIServerAdapter
        from gateway.session_context import (
            clear_session_vars,
            set_session_vars,
        )
        from tools.approval import (
            register_gateway_notify,
            reset_current_session_key,
            set_current_session_key,
            unregister_gateway_notify,
        )
    except ImportError as e:
        logger.warning("P15: api_server / approval import 失败 (%s), skip patch", e)
        return

    _orig = APIServerAdapter._run_agent

    async def patched_run_agent(self, *args, **kwargs):
        cb = kwargs.get("stream_delta_callback")
        sid = (
            kwargs.get("gateway_session_key")
            or kwargs.get("session_id")
        )

        # P15.2 (6/22 鸿波 catch "按钮还没弹"): 用户报 P15.1 ship 后按钮仍没弹.
        # patched_run_agent 真跑没跑 / sid 拿到没 / stream_q 反射成功没 — 加
        # INFO log 让 hermes daemon 日志说话. 一次性确定真因, 不瞎猜.
        logger.info(
            "P15 patched_run_agent: 入口 sid=%r has_cb=%s kwargs_keys=%s",
            sid, cb is not None, sorted(list(kwargs.keys())),
        )

        # 反射拿 _stream_q (api_server.py:1896 _on_delta 闭包持有这个 local 变量)
        stream_q = None
        if cb is not None and getattr(cb, "__closure__", None) is not None:
            try:
                freevars = cb.__code__.co_freevars
                for i, name in enumerate(freevars):
                    if name == "_stream_q":
                        stream_q = cb.__closure__[i].cell_contents
                        break
            except Exception as e:  # noqa: BLE001
                logger.debug("P15: closure 反射失败 (%s), 跳过 approval 注入", e)
        logger.info(
            "P15 patched_run_agent: stream_q 反射结果 = %s (cb.freevars=%s)",
            "GOT" if stream_q is not None else "NONE",
            (cb.__code__.co_freevars if cb is not None and hasattr(cb, "__code__") else None),
        )

        notify_cb = None
        if stream_q is not None and sid:
            def _approval_notify(approval_data):
                """Push approval event 到 chat completion SSE stream.

                复用 hermes 现有 ("__tool_progress__", dict) tuple pattern —
                _write_sse_chat_completion._emit (api_server.py:2156) 把这种
                tuple 写为 `event: hermes.tool.progress\\ndata: {...}` SSE event,
                Companion chat.ts 解析检测 status==approval_pending.
                """
                event = {
                    "tool": _APPROVE_REQUEST_TOOL_MARKER,
                    "status": "approval_pending",
                    "approval_session_key": sid,
                    "command": approval_data.get("command", ""),
                    "pattern_key": approval_data.get("pattern_key", ""),
                    "description": approval_data.get("description", ""),
                    "choices": ["once", "session", "always", "deny"],
                }
                try:
                    stream_q.put(("__tool_progress__", event))
                except Exception as e:  # noqa: BLE001
                    logger.debug("P15: stream_q.put 失败 (%s)", e)

            notify_cb = _approval_notify
            try:
                register_gateway_notify(sid, notify_cb)
                logger.info("P15 patched_run_agent: register_gateway_notify(%r) ✓", sid)
            except Exception as e:  # noqa: BLE001
                logger.warning("P15: register_gateway_notify 失败 (%s)", e)
                notify_cb = None
        else:
            logger.info(
                "P15 patched_run_agent: 不注册 notify_cb (stream_q=%s sid=%r) — "
                "按钮不会弹, approval 走 hermes fallback (auto-approve 或 pending)",
                stream_q is not None, sid,
            )

        # P15 真根因 fix (00:30 audit): approval.py:1521 `session_key =
        # get_current_session_key()` 拿 _approval_session_key contextvar. chat
        # completions path 没 set 这个 contextvar, 默认 fallback "default" 字符串.
        # plugin register_gateway_notify 用的 sid (e.g. session_id) ≠ "default",
        # 所以 _gateway_notify_cbs.get("default") = None, notify_cb 没调 → 走 fallback.
        # set_current_session_key(sid) 跟 /v1/runs path (api_server.py:3797) 一样,
        # 让 approval.py 内 get 拿到匹配的 sid → notify_cb 命中. P0 patch_asyncio
        # _executor_for_contextvars 保证 contextvar 跨 run_in_executor 透传.
        approval_token = None
        if sid:
            try:
                approval_token = set_current_session_key(sid)
            except Exception as e:  # noqa: BLE001
                logger.debug("P15: set_current_session_key 失败 (%s)", e)

        # P15.1 (6/22 鸿波 catch "审批按钮没了"): hermes v0.17 升级新加
        # _is_gateway_approval_context() gate (approval.py:134-152) 检查
        # HERMES_SESSION_PLATFORM contextvar. check_execute_code_guard:1710
        # 拿 is_gateway, line 1738 `if not is_gateway: return {"approved": True}`
        # — 没 set platform → silent auto-approve, **按钮永远不弹**.
        #
        # hermes /v1/runs path (api_server.py:3870) 走 set_session_vars(
        #   platform="api_server", session_key=...). 这步是把 _SESSION_PLATFORM
        # contextvar set 成 "api_server" → _is_gateway_approval_context() 返 True.
        #
        # P15 当初写时 hermes 没这检查, v0.17 升级 silent break. 跟 /v1/runs 对齐.
        session_tokens: list = []
        if sid:
            try:
                session_tokens = set_session_vars(
                    platform="api_server",
                    session_key=sid,
                )
                logger.info("P15.1 patched_run_agent: set_session_vars(platform=api_server, session_key=%r) ✓", sid)
            except Exception as e:  # noqa: BLE001
                logger.warning("P15.1: set_session_vars 失败 (%s)", e)

        try:
            return await _orig(self, *args, **kwargs)
        finally:
            if session_tokens:
                try:
                    clear_session_vars(session_tokens)
                except Exception:  # noqa: BLE001
                    pass
            if approval_token is not None:
                try:
                    reset_current_session_key(approval_token)
                except Exception:  # noqa: BLE001
                    pass
            if notify_cb is not None:
                try:
                    unregister_gateway_notify(sid)
                except Exception:  # noqa: BLE001
                    pass

    APIServerAdapter._run_agent = patched_run_agent
    logger.info(
        "P15 chat/completions approval patched (_run_agent wrapped, "
        "approval flows through hermes.tool.progress SSE event)"
    )


# ── P15.2 ──────────────────────────────────────────────────────────────
#
# P15.2 (6/6 鸿波 audit 真根因): 跨进程 dict 问题.
# tool-bridge 进程 vs hermes daemon 进程独立, _gateway_queues 不共享.
# P15 register_gateway_notify 在 hermes daemon 进程 (_run_agent patch),
# entry 入队 _gateway_queues 在 hermes daemon 内存. tool-bridge 进程的
# _gateway_queues 是空 dict. resolve_gateway_approval 在 tool-bridge
# 进程 lookup 拿不到 entry → 返 0 → block 不解.
#
# 修法: P15.2 改成 patch hermes API server 加新 HTTP route POST
# /v1/sessions/{session_id}/approval, Companion fetch 这条 endpoint
# (走 hermes proxy 8642), 这调用走 hermes daemon 进程, resolve 真起效.

def _patch_p15_2_chat_approval_route() -> None:
    """注册 chat_approval middleware 到 hermes api_server.

    aiohttp Application 在 AppRunner.setup() 后 frozen, 不能加 route. 跟 P7 一样
    用 middleware 拦截 path. _patched_app_init (在 _patch_p8_p9_cors 里) 创建
    hermes _app 时 inject middlewares, 这里把 chat_approval_middleware 也 inject.
    middleware 检测 POST /v1/sessions/{sid}/approval, 调 resolve_gateway_approval.

    跑在 hermes daemon 进程 (因为这是 hermes APIServerAdapter), _gateway_queues
    跟 P15 register 的 entry 共享.
    """
    try:
        import aiohttp.web as _aw
        from tools.approval import resolve_gateway_approval
    except ImportError as e:
        logger.warning("P15.2: aiohttp / approval import 失败 (%s), skip", e)
        return

    @_aw.middleware
    async def chat_approval_middleware(request, handler):
        # path match: /v1/sessions/<sid>/approval
        if request.method == "POST":
            path = request.path
            if path.startswith("/v1/sessions/") and path.endswith("/approval"):
                parts = path.strip("/").split("/")
                if len(parts) == 4 and parts[3] == "approval":
                    session_id = parts[2]
                    try:
                        body = await request.json()
                    except Exception:
                        return _aw.json_response(
                            {"error": "JSON body required"}, status=400
                        )
                    choice = str(body.get("choice", "")).strip().lower()
                    resolve_all = bool(body.get("all", False))
                    if choice not in {"once", "session", "always", "deny"}:
                        return _aw.json_response(
                            {"error": f"choice ∈ once/session/always/deny, got: {choice!r}"},
                            status=400,
                        )
                    try:
                        resolved = resolve_gateway_approval(
                            session_id, choice, resolve_all=resolve_all,
                        )
                        logger.info(
                            "P15.2 chat_approval resolved=%d choice=%s sid=%s",
                            resolved, choice, session_id[:24],
                        )
                        return _aw.json_response(
                            {"resolved": resolved, "choice": choice}
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.exception("P15.2: chat_approval resolve 异常")
                        return _aw.json_response({"error": str(e)}, status=500)
        return await handler(request)

    # 注册到 module global, _patched_app_init 会读这个 list (跟 P7 一样的 pattern).
    # _patch_p8_p9_cors 里 inject middlewares. 这里 expose 给那边 import.
    global _chat_approval_middleware  # noqa: PLW0603
    _chat_approval_middleware = chat_approval_middleware
    logger.info("P15.2 chat_approval middleware registered (waiting for app init)")


# module-level reference for _patched_app_init in P7 path
_chat_approval_middleware = None  # noqa: PLW0603


# ── P18 (P3.5.18 6/17 鸿波: 主动压缩 + SSE 进度弹窗) ────────────────────
#
# # 真因背景 (鸿波 6/17 verbatim)
#
# > "为什么还是提示, 直接压缩, 压缩过程可以弹窗显示压缩进度"
#
# P3.5.17.c banner 信息流 (下次发消息时 hermes 自动压缩), 鸿波要的是
# Companion 检测 80%+ ctx 时**主动 trigger hermes 压缩** + **弹窗显进度**
# (类似 mac 系统更新).
#
# # 抄什么
#
# hermes 全 工具 现成:
#   - compress_context(agent, messages, system_message, *, approx_tokens, focus_topic, force)
#     → ~/.hermes/hermes-agent/agent/conversation_compression.py:271
#   - SessionDB.{get_session, get_messages, replace_messages}
#     → ~/.hermes/hermes-agent/hermes_state.py:1358/2112/2026
#   - summarize_manual_compression(before_messages, after_messages, before_tokens, after_tokens)
#     → ~/.hermes/hermes-agent/agent/manual_compression_feedback.py:8
#   - estimate_request_tokens_rough(messages, *, system_prompt, tools)
#     → ~/.hermes/hermes-agent/agent/model_metadata.py:1887
#   - AIAgent(model=, ephemeral_system_prompt=, session_id=, status_callback=, session_db=)
#     → ~/.hermes/hermes-agent/run_agent.py:336 (init)
#     注意: model= (不是 model_name=), ephemeral_system_prompt= (不是
#     system_prompt=) — design doc bug, 6/17 audit catch.
#   - status_callback(kind: str, message: str)
#     → ~/.hermes/hermes-agent/run_agent.py:761 (_emit_status / _emit_warning)
#     kind "lifecycle" / "warn".
#   - SSE 模板 _handle_session_chat_stream
#     → ~/.hermes/hermes-agent/gateway/platforms/api_server.py:1679

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


# ── P21 (P3.5.74, 6/22 鸿波 catch "cron 不是用 picker 吗") ──────────────
#
# hermes cron/scheduler.py run_job 读 config.yaml model.default → cron job
# 用 catfish-public-deepseek-flash, 跟 chat picker 解耦. P3.5.28/42/42.1 把
# picker 联动到 chat / advisor / email scheduler / vision / catfish-memory
# summarize, 这里补 cron job — 最后一个 picker sprint gap.
#
# 实施: monkey-patch hermes cron.scheduler.run_job. wrap 老 run_job, 在调
# 用前检查 picker_state.json — 若 picker set 了 chat_model, 把 job 字段 +
# 环境 + config 字段都 override 让 hermes 原代码读到 picker model.
#
# 优先级 (跟 catfish-memory _get_summarize_model 一致):
#   picker_state.json > job.model (user 显式指定) > config.yaml.model.default > env
#
# 安全: try/except 包死, picker 读失败 fallback 老路径不影响 cron 跑.
# fail-silent fallback (跟 P16 / catfish-memory 风格一致).

def _read_catfish_picker_model() -> str:
    """读 ~/.catfish/picker_state.json 拿 chat_model. 复用 catfish-memory 同款 ABI.

    catfish-memory plugin 也有 _read_picker_state_model helper (catfish_memory_helpers.py).
    本 plugin 没依赖 catfish-memory (两个 plugin 独立装载), 不能 cross import. 抄个
    最简版本 — 文件不存在 / parse 错 / chat_model 缺 → 空字符串.
    """
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    catfish_home = Path.home() / ".catfish"
    path = catfish_home / "picker_state.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            model = data.get("chat_model", "")
            if isinstance(model, str) and model.strip():
                return model.strip()
    except (json.JSONDecodeError, OSError, ValueError):
        pass
    return ""


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
            picker_model = _read_catfish_picker_model()
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


# ── P24 (P3.5.89, 6/23 鸿波): hermes API server CORS allowlist 扩 catfish header ─
#
# 真因 audit (今天 8 次瞎猜后真审 me.ts:630 + chat.ts:71 + api_server.py:545):
#
#   ① 用户点 🎓 教学按钮 → chat.ts:307-309 加 header X-Catfish-Teaching-Mode: 1
#   ② Companion (WKWebView) 发请求前自动 OPTIONS preflight, 把这 header 列入
#      Access-Control-Request-Headers
#   ③ hermes api_server.py:543 _CORS_HEADERS["Access-Control-Allow-Headers"] 写死:
#        "Authorization, Content-Type, Idempotency-Key"   ← 只 3 个 header
#   ④ X-Catfish-Teaching-Mode 不在 allowlist → preflight 非 200 → 浏览器抛
#      TypeError: Load failed → Companion 弹 "无法连接 hermes API"
#
# 实证 (今天):
#   - curl 直打 8642 用 hermes_key 返 200 ✓ (curl 不走 preflight, 直接 POST)
#   - Companion 走 preflight 触发 CORS block → Load failed
#   - me.ts:630 早注释过 "hermes proxy CORS allowlist 不含" (5/26 BL-PROACTIVE-DECOUPLE
#     当时把 X-Catfish-Journal-Tail-B64 等移到 body 绕过, 但 X-Catfish-Teaching-Mode 仍 header)
#
# Companion 发的全部 X-Catfish-* header (grep 实证 6/23):
#   X-Catfish-Agent-Name, X-Catfish-Agent-Personality,
#   X-Catfish-Journal-Tail-B64, X-Catfish-Last-Model,
#   X-Catfish-Prev-Model, X-Catfish-Source,
#   X-Catfish-Teaching-Mode, X-Catfish-User, X-Catfish-User-Dept
#
# 修法: in-place mutate _CORS_HEADERS["Access-Control-Allow-Headers"], append
# 所有 X-Catfish-* header. 跟 P22 同款 in-place 修 hermes module-level constant.
#
# fail-safe: api_server module 没导 / _CORS_HEADERS 不存在 / 非 dict → silent skip.

_CATFISH_CORS_HEADERS = [
    "X-Catfish-Agent-Name",
    "X-Catfish-Agent-Personality",
    "X-Catfish-Journal-Tail-B64",
    "X-Catfish-Last-Model",
    "X-Catfish-Prev-Model",
    "X-Catfish-Source",
    "X-Catfish-Teaching-Mode",
    "X-Catfish-User",
    "X-Catfish-User-Dept",
]


def _patch_p24_cors_allowlist() -> None:
    """扩 hermes api_server._CORS_HEADERS Access-Control-Allow-Headers 含 X-Catfish-*.

    hermes 默认只 allow 3 header (Authorization / Content-Type / Idempotency-Key).
    catfish 加 9 个 X-Catfish-* header 没同步进 allowlist, 浏览器 preflight block,
    Companion fetch throw TypeError: Load failed.

    修法: in-place append 所有 X-Catfish-* 到 allowlist string.
    """
    try:
        from gateway.platforms import api_server as _api  # noqa: PLC0415
    except ImportError as e:
        logger.warning("P24: hermes api_server module 没导, skip patch (%s)", e)
        return

    cors = getattr(_api, "_CORS_HEADERS", None)
    if cors is None or not isinstance(cors, dict):
        logger.warning(
            "P24: api_server._CORS_HEADERS 不存在 / 非 dict (got %s), skip. "
            "Companion 浏览器 preflight 会 block 教学按钮 + 其它 X-Catfish-* header.",
            type(cors).__name__,
        )
        return

    current = cors.get("Access-Control-Allow-Headers", "")
    existing = {h.strip() for h in current.split(",") if h.strip()}
    to_add = [h for h in _CATFISH_CORS_HEADERS if h not in existing]

    if not to_add:
        logger.info("P24 CORS allowlist: 已含全部 %d X-Catfish-* header (idempotent skip)",
                    len(_CATFISH_CORS_HEADERS))
        return

    merged = current + ", " + ", ".join(to_add) if current else ", ".join(to_add)
    cors["Access-Control-Allow-Headers"] = merged
    logger.info(
        "P24 CORS allowlist patched — 加 %d X-Catfish-* header 到 Access-Control-Allow-Headers ✓ "
        "(修浏览器 preflight block 教学按钮 + 自定义 header 导致 TypeError: Load failed)",
        len(to_add),
    )


# ── P23 (P3.5.79, 6/23 鸿波): inbound message 路径 picker 联动 ─────────
#
# 真因 audit (P3.5.77 audit-完整 + 6/22 23:50 微信 ClawBot 真聊 fail):
#
#   ① 微信 inbound message 走 gateway.run.handle_inbound → AIAgent(model=...)
#   ② model 来自 turn_route["model"], 由 _resolve_session_agent_runtime 算
#   ③ _resolve_session_agent_runtime 第一行调 _resolve_gateway_model(config)
#   ④ _resolve_gateway_model 直接读 cfg["model"]["default"] 返
#      = config.yaml.model.default (当时 = catfish-public-deepseek-flash,
#        7/22 校: **现值 = catfish-auto** — 会走 gateway roles.yaml chat_default resolve.
#        老注释这行只作历史 bug 复现现场读)
#   ⑤ 微信 path 完全不读 ~/.catfish/picker_state.json, 跟桌面 chat 行为不一致
#      (桌面 chat 走 P5/P6/P11 _RUNTIME_MAIN_MODEL override, 真桥到 picker)
#   ⑥ 实证: 23:46:57 inbound msg='19号花了300块钱，加油' platform=weixin
#           model=catfish-public-deepseek-flash    ← deepseek 而不是 picker 选的 qwen
#
# 修法 (跟 P21 cron picker 同款 monkey-patch pattern): wrap _resolve_gateway_model,
# 在原结果之前优先读 picker_state.json. 一处 hook 覆盖**所有 platform** (微信 /
# Discord / Slack / Telegram / CLI inbound), 跟 P3.5.74 cron 同一架构.
#
# 优先级 (新): picker_state.json > config.yaml.model.default
#  (跟 cron P21 优先级一致, 让员工 picker 一切真"主权" — 选啥所有 platform 用啥)
#
# fail-safe: picker 读失败 / hermes gateway.run 模块没导 / patch attach 失败 →
# silent fallback 老路径 (config.yaml.model.default). _PATCH_TARGETS 加
# ("gateway.run", "_resolve_gateway_model", "func") fail-loud verify.

def _patch_p23_inbound_picker_integration() -> None:
    """patch gateway.run._resolve_gateway_model — inbound message model 跟 picker 联动.

    hermes _resolve_gateway_model 代码 (gateway/run.py:2070):
        def _resolve_gateway_model(config=None) -> str:
            cfg = config if config is not None else _load_gateway_config()
            model_cfg = cfg.get("model", {})
            if isinstance(model_cfg, str): return model_cfg
            elif isinstance(model_cfg, dict):
                return model_cfg.get("default") or model_cfg.get("model") or ""
            return ""

    patch 思路: wrap, 在 _orig 调用前先读 picker_state.json. 若 picker 有值, 跳过
    _orig 直接返 picker model. picker 空 → fallback 老路径 (config.yaml).

    fail-safe: picker 读失败 → 走老路径 (silent, 不抛). hermes gateway.run 模块没导
    → silent skip patch (旧 hermes 版本不支持).
    """
    try:
        from gateway import run as _gateway_run  # noqa: PLC0415
    except ImportError as e:
        logger.warning("P23: hermes gateway.run module 没导, skip patch (%s)", e)
        return

    _orig_resolve = getattr(_gateway_run, "_resolve_gateway_model", None)
    if _orig_resolve is None:
        logger.warning(
            "P23: gateway.run._resolve_gateway_model 不存在 (hermes 重构?), skip patch."
        )
        return

    def _patched_resolve_gateway_model(config=None):
        # picker override (优先级最高). picker 读失败 → fallback 老路径.
        try:
            picker_model = _read_catfish_picker_model()
            if picker_model:
                logger.info(
                    "P23 inbound picker integration: gateway model → %r "
                    "(picker_state.json override, was config.yaml fallback)",
                    picker_model,
                )
                return picker_model
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "P23 inbound: picker_state 读失败 (%s), fallback config.yaml",
                e,
            )

        # picker 空 / 读失败 → 走 hermes 老路径 (config.yaml.model.default)
        return _orig_resolve(config)

    _gateway_run._resolve_gateway_model = _patched_resolve_gateway_model
    logger.info(
        "P23 inbound picker integration patched — inbound message (微信/Discord/Slack/"
        "Telegram/CLI) model 跟 picker_state.json 联动 (优先级: picker > yaml.default) ✓"
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
        #   2. `_read_catfish_picker_model()` 在 json 解析失败时返回 ""，
        #      `if picker_model:` 于是整段跳过, 同样无声。
        #
        # 现在两条都记日志。注意**不能**改成"取不到就拒绝请求"——
        # picker 文件不存在是全新装机的正常状态, 那样会让新员工一条都发不出去。
        # 能做的是: 保险失效时必须在日志里留下痕迹, 让排查的人找得到。
        picker_model = _read_catfish_picker_model()
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
                logger.debug(
                    "P25 check_execute_code_guard 检测到污染 env 真清掉 "
                    "(caller 非 cron 线程, env 是 hermes cron scheduler 残留)"
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
_P28_REPLACEMENTS = [
    # 长真 reason — first 防被短真前缀打断
    (
        "execute_code script execution. The script can spawn subprocesses or "
        "mutate files without passing through terminal command approval; "
        "approval is one-shot for this run.",
        "execute_code 脚本执行 — 可能调子进程 / 改文件, 绕过终端命令审批. 本次审批仅 1 次有效.",
    ),
    # reply 段 (鸿波铁律: 砍 `/approve always`)
    # ⚠ 7/22 鸿波 catch (P3.5.79+): hermes v0.19 Quicksilver (7/20) 改了原文
    # `to execute,` → `to execute this one operation,` (gateway/run.py:370).
    # 老 v0.18 pattern silent miss → 英文全条泄漏到微信. 加 v0.19 pattern first
    # (长 first 匹配), 保留 v0.18 pattern 兜底 (客户装老版 hermes 时用).
    #
    # v0.19 pattern
    (
        "Reply `/approve` to execute this one operation, `/approve session` to approve this pattern "
        "for the session, `/approve always` to approve permanently, or `/deny` to cancel.",
        "回复 `/批准` 执行 (单次), 或 `/批准 本次会话` 本会话内同款命令免审批, 或 `/拒绝` 取消.\n"
        "（安全提示：永久免批已禁用，危险命令必须每次或每会话审批）",
    ),
    # v0.20 新增的两个变体 (8/8 升级 v2026.8.3 时补).
    #
    # 上面那条只盖住"四选项"这一种。看 v0.20 `gateway/run.py:508`
    # `_format_exec_approval_fallback` —— choices 是**按开关拼出来的**:
    #
    #     choices = ["Reply `/approve` to execute this one operation"]
    #     if not smart_denied and allow_session:
    #         choices.append("`/approve session` ...")
    #         if allow_permanent:
    #             choices.append("`/approve always` ...")
    #     choices.append("`/deny` to cancel")
    #
    # 所以一共 3 种成品, 我们原来只翻了 allow_session ∧ allow_permanent 那一种。
    # 另外两种会整条英文泄到微信 —— 正是 P28 要防的事。
    #
    # 三条互不为子串 (中间 "always" / "session" 段不同), 顺序不影响 str.replace,
    # 但仍按长→短排, 保持本表的既有约定。
    #
    # allow_permanent=False (铁律场景: 本来就该砍掉永久免批)
    (
        "Reply `/approve` to execute this one operation, `/approve session` to approve this pattern "
        "for the session, or `/deny` to cancel.",
        "回复 `/批准` 执行 (单次), 或 `/批准 本次会话` 本会话内同款命令免审批, 或 `/拒绝` 取消.",
    ),
    # allow_session=False 或 smart_denied=True — **只剩单次**, 不能提"本次会话",
    # 提了就是骗员工: hermes 那边根本不接受 `/approve session`。
    (
        "Reply `/approve` to execute this one operation, or `/deny` to cancel.",
        "回复 `/批准` 执行 (仅此一次), 或 `/拒绝` 取消.",
    ),
    # v0.18 及之前 pattern (兜底 · 客户老 hermes 装)
    (
        "Reply `/approve` to execute, `/approve session` to approve this pattern "
        "for the session, `/approve always` to approve permanently, or `/deny` to cancel.",
        "回复 `/批准` 执行 (单次), 或 `/批准 本次会话` 本会话内同款命令免审批, 或 `/拒绝` 取消.\n"
        "（安全提示：永久免批已禁用，危险命令必须每次或每会话审批）",
    ),
    # 真短单句
    ("⚠️ **Dangerous command requires approval:**", "⚠️ **危险命令需要审批:**"),
    # v0.20 新 heading (smart_denied 分支, gateway/run.py:505)
    (
        "⚠️ **Smart DENY — owner override for one operation:**",
        "⚠️ **智能拦截已拒绝 — 仅本次由管理员放行:**",
    ),
    ("⚡ Interrupting current task", "⚡ 中断当前任务"),
    (". I'll respond to your message shortly.", ", 马上回复你."),
    (". I'll respond once the current task finishes.", ", 当前任务完成后回复你."),
    ("⏳ Queued for the next turn", "⏳ 已排队下个回合"),
    ("Reason: ", "原因: "),
]

# BL-P14-P28-DEDUPE (7/19 鸿波 catch): _P28_CMD_ALIASES 老死代码 — **定义但从未使用**.
# grep 全项目 zero use. 老 P28 只做 outbound (WeixinAdapter.send 英文→中文), inbound
# 命令翻译 (`/批准`→`/approve`) 事实上没接. 员工按 Bot 提示 `/批准 本次会话` 发, 走
# hermes 原 slash dispatcher, 不认 `/批准` → 当 message 触发 LLM → LLM 又调
# execute_code → 又弹审批. 死循环.
#
# 修法 · 死代码删 + 中文 slash alias dedupe 到 **_APPROVE_ALIASES** (line 1833) ·
# 由 P14 patch 统一处理 (P14 wrap GatewayRunner._handle_message · 覆盖所有平台
# inbound · 不只 WeChat). P14 patched 逻辑放宽 · 支持带 / 前缀. 见 line 1870+ 修.
# _P28_CMD_ALIASES = {} — 死代码删净.


def _translate_hermes_zh(text):
    """str.replace 英文 → 中文 — P28 outbound 中文化main entry真.

    0 raise — : input 异常 → 返原文 (不阻塞 send).

    ⚠ 7/22 军规 fail-loud (P3.5.79+): 翻译完仍含英文 slash prompt (`/approve`,
    `/deny`) → warn log. hermes 升级会改原文 (v0.18→v0.19 就改过), 老 pattern
    silent miss = 员工看到英文丑. warn 让下次一发现就修 _P28_REPLACEMENTS, 别
    等员工投诉.
    """
    if not text or not isinstance(text, str):
        return text
    try:
        for en, zh in _P28_REPLACEMENTS:
            text = text.replace(en, zh)
    except Exception as e:  # noqa: BLE001
        logger.warning("P28 translate fail (return original): %s", e)
        return text

    # fail-loud 检测: 只在明显 approval prompt (含 `/approve`) 场景下检查 · 且
    # 中文替换未生效 (没 "回复" / "批准" 关键字). 防误报 (员工正常聊到 /approve).
    if isinstance(text, str) and "/approve" in text and "批准" not in text and "回复" not in text:
        logger.warning(
            "P28 miss: outbound 含未翻译 `/approve` — hermes 上游可能改了原文, "
            "需 update _P28_REPLACEMENTS. text preview: %r",
            text[:200],
        )
    return text


def _patch_p28_weixin_zh() -> None:
    """wrap WeixinAdapter.send — : 真:** : 真:** : : : : : : : : : : :

    真: : : : : : : : : : : : : : : : : : : : : : : : : : : : :

    真:** : : : : : : : : : : : : : : : : : : : : : : :
    """
    try:
        from gateway.platforms import weixin as _wx_mod  # noqa: PLC0415
    except ImportError as e:
        logger.warning("P28: hermes gateway.platforms.weixin 没导, skip (%s)", e)
        return

    _WxCls = getattr(_wx_mod, "WeixinAdapter", None)
    if _WxCls is None:
        logger.warning("P28: WeixinAdapter 没真 attr (hermes 改名?), skip")
        return

    _orig_send = getattr(_WxCls, "send", None)
    if _orig_send is None:
        logger.warning("P28: WeixinAdapter.send 没真 method, skip")
        return
    if getattr(_orig_send, "_p28_patched", False):
        logger.info("P28 already patched, skip (dev hot-reload)")
        return

    @functools.wraps(_orig_send)
    async def patched_send(self, chat_id, content, reply_to=None, metadata=None):
        content = _translate_hermes_zh(content)
        return await _orig_send(
            self, chat_id, content, reply_to=reply_to, metadata=metadata
        )

    patched_send._p28_patched = True  # type: ignore[attr-defined]
    _WxCls.send = patched_send  # type: ignore[method-assign]
    logger.info(
        "P28 wrap WeixinAdapter.send 完成 — 中文化 hermes 英文 outbound "
        "(approval / 中断提示 / /approve 命令说明), 鸿波铁律: 砍永久免批入口"
    )


# ── P29 (P3.5.168, 7/3 鸿波): /learn slash command 前置翻译 ────────────────
#
# 真因 (P3.5.164 严格 audit):
#   hermes v0.18 /learn 只在 GatewayRunner._handle_message 处理
#   (gateway/run.py:9263-9289): 检测 canonical == "learn" → 调
#   agent.learn_prompt.build_learn_prompt → 替换 event.text → fall through.
#   /v1/chat/completions (APIServerAdapter._handle_chat_completions api_server.py:1833+)
#   严格不过 slash command dispatcher — 提取 messages → 直接 _run_agent, 无 canonical
#   command 检测. Companion 员工输 "/learn xxx" → LLM 只当 prompt 释义.
#   catfish P15 patch comment 明确 confirm (plugin.py:1807-1808):
#     "chat completions 没 slash command hook → LLM 直接看 '/approve' 编释义"
#
# 修法 (不 fork hermes, 不改 Companion 前端):
#   wrap APIServerAdapter._run_agent (跟 P15 同挂点, 但 P29 wrap 是外层).
#   前置检测 message.startswith("/learn") → 调 hermes agent/learn_prompt.
#   build_learn_prompt(arg) 翻译 → 替换 args[0] 或 kwargs["message"] →
#   继续 P15 approval 闭包 → hermes original _run_agent.
#
# wrap 顺序: 注册顺序 P1..P15..P28..P29, runtime call chain: P29 (外, 先跑翻译)
# → P15 (中, approval 闭包) → hermes original. P29 前置翻译不影响 P15 approval.
#
# 风险评估:
#   - hermes v0.18 _run_agent signature 第 1 位置参 = message (P3.5.159 Phase A audit
#     confirm): _run_agent(self, message, context_prompt, history, source, session_id, ...).
#     P29 拿 args[0] 或 kwargs["message"], 兼容 caller.
#   - build_learn_prompt 抛异常 → fall through as normal message (原行为), warn log.
#   - hermes v0.19 若改 _run_agent 参数顺序或 build_learn_prompt module 位置 →
#     P29 fail-safe: import 失败 skip patch, runtime 反射失败 warn + fall through.
#   - 只处理 /learn, 其他 slash (/goal /journey /steer /fast /verbose /memory /skills)
#     未来员工反馈驱动再加 P30+.
#
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

_wechat_qr_sessions: dict = {}  # qrcode(str) → {"base_url": str, "created_at": float}
_WECHAT_QR_SESSION_TTL = 600.0  # 10 min. 二维码本身 35s 过期, 给 UI 留缓冲.


def _wechat_qr_sweep_expired() -> None:
    """清掉超过 TTL 的 in-memory session. start 时 call 一次."""
    import time as _t
    now = _t.time()
    expired = [k for k, v in _wechat_qr_sessions.items()
               if (now - v.get("created_at", 0.0)) > _WECHAT_QR_SESSION_TTL]
    for k in expired:
        _wechat_qr_sessions.pop(k, None)


async def _handle_wechat_qr_start(self, request):
    """POST /api/platforms/wechat/qr_login/start

    Body: none
    Response: {qrcode, qrcode_url, scan_data}  |  502 (ilink 挂)
    """
    import time as _t
    auth_err = self._check_auth(request)
    if auth_err:
        return auth_err
    try:
        import aiohttp  # noqa: PLC0415
        from gateway.platforms.weixin import (  # noqa: PLC0415
            ILINK_BASE_URL, EP_GET_BOT_QR, QR_TIMEOUT_MS,
            _api_get, _make_ssl_connector,
        )
    except ImportError as e:
        return web.json_response(
            {"error": f"hermes weixin 模块 import 失败: {e}"}, status=500,
        )

    _wechat_qr_sweep_expired()

    try:
        async with aiohttp.ClientSession(
            trust_env=True, connector=_make_ssl_connector(),
        ) as http_sess:
            qr_resp = await _api_get(
                http_sess,
                base_url=ILINK_BASE_URL,
                endpoint=f"{EP_GET_BOT_QR}?bot_type=3",
                timeout_ms=QR_TIMEOUT_MS,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("P30 wechat qr start: ilink get_bot_qrcode 失败: %s", e)
        return web.json_response(
            {"error": f"ilink 获取二维码失败: {e}"}, status=502,
        )

    qrcode_value = str(qr_resp.get("qrcode") or "")
    qrcode_url = str(qr_resp.get("qrcode_img_content") or "")
    if not qrcode_value:
        return web.json_response(
            {"error": "ilink 返回未含 qrcode 字段"}, status=502,
        )

    _wechat_qr_sessions[qrcode_value] = {
        "base_url": ILINK_BASE_URL,
        "created_at": _t.time(),
    }
    scan_data = qrcode_url if qrcode_url else qrcode_value
    return web.json_response({
        "qrcode": qrcode_value,
        "qrcode_url": qrcode_url,
        "scan_data": scan_data,
    })


async def _handle_wechat_qr_poll(self, request):
    """GET /api/platforms/wechat/qr_login/poll?qrcode=<hex>

    Response:
      {status: "wait" | "scaned" | "confirmed" | "expired",
       account_id?, user_id?, _warning?}
      404 (session not found — hermes 重启; UI 会自己重跑 start)

    scaned_but_redirect 内部升 base_url 后返给前端 "scaned" (前端语义只有
    wait/scaned/confirmed/expired 4 档).
    """
    auth_err = self._check_auth(request)
    if auth_err:
        return auth_err
    qrcode_value = request.query.get("qrcode", "")
    if not qrcode_value:
        return web.json_response(
            {"error": "缺 qrcode query 参数"}, status=400,
        )
    sess_state = _wechat_qr_sessions.get(qrcode_value)
    if sess_state is None:
        return web.json_response(
            {"error": "session not found (可能 hermes 重启, 请重新扫码)"},
            status=404,
        )

    try:
        import aiohttp  # noqa: PLC0415
        from gateway.platforms.weixin import (  # noqa: PLC0415
            EP_GET_QR_STATUS, QR_TIMEOUT_MS,
            _api_get, _make_ssl_connector,
            save_weixin_account,
        )
        from hermes_constants import get_hermes_home  # noqa: PLC0415
    except ImportError as e:
        return web.json_response(
            {"error": f"hermes weixin 模块 import 失败: {e}"}, status=500,
        )

    base_url = sess_state.get("base_url") or ""
    try:
        async with aiohttp.ClientSession(
            trust_env=True, connector=_make_ssl_connector(),
        ) as http_sess:
            status_resp = await _api_get(
                http_sess,
                base_url=base_url,
                endpoint=f"{EP_GET_QR_STATUS}?qrcode={qrcode_value}",
                timeout_ms=QR_TIMEOUT_MS,
            )
    except Exception as e:  # noqa: BLE001
        # 前端 wechat_qr.ts:22 契约: poll 502/_warning 字段 → 临时网络抖动
        # UI 不动让它下次再 poll (等到 wait/expired). 这里 200 + _warning.
        logger.info("P30 wechat qr poll: ilink get_qrcode_status 抖动: %s", e)
        return web.json_response({"status": "wait", "_warning": str(e)})

    status = str(status_resp.get("status") or "wait")
    # P3.5.198.d (7/8 鸿波): 加 raw status_resp key 日志便于 audit 已绑用户扫码卡
    # scaned 场景 (ilink 侧不给 confirm). 记 keys 不记 value 防 token 泄漏.
    logger.info(
        "P30 wechat qr poll: qrcode=%s...%s status=%r keys=%s",
        qrcode_value[:6], qrcode_value[-4:],
        status,
        sorted(status_resp.keys()) if isinstance(status_resp, dict) else "not-dict",
    )

    if status == "wait":
        return web.json_response({"status": "wait"})

    if status == "scaned":
        return web.json_response({"status": "scaned"})

    if status == "scaned_but_redirect":
        redirect_host = str(status_resp.get("redirect_host") or "")
        if redirect_host:
            sess_state["base_url"] = f"https://{redirect_host}"
        # 前端语义把它归到 scaned (等下次 poll 用新 base_url 拿最终 confirmed).
        return web.json_response({"status": "scaned"})

    if status == "expired":
        _wechat_qr_sessions.pop(qrcode_value, None)
        return web.json_response({"status": "expired"})

    if status == "confirmed":
        account_id = str(status_resp.get("ilink_bot_id") or "")
        token = str(status_resp.get("bot_token") or "")
        acct_base_url = str(status_resp.get("baseurl") or base_url)
        user_id = str(status_resp.get("ilink_user_id") or "")
        if not account_id or not token:
            logger.warning(
                "P30 wechat qr poll: confirmed 但 payload 缺 ilink_bot_id/bot_token "
                "(account_id=%r len_token=%d)", account_id, len(token),
            )
            return web.json_response({
                "status": "wait",
                "_warning": "confirmed but credential payload incomplete",
            })
        try:
            save_weixin_account(
                str(get_hermes_home()),
                account_id=account_id,
                token=token,
                base_url=acct_base_url,
                user_id=user_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("P30 wechat qr poll: save_weixin_account 失败")
            return web.json_response(
                {"error": f"保存 credential 失败: {e}"}, status=500,
            )
        # P31 (P3.5.198.f 7/8 鸿波): save 完 credential 立刻同步 ~/.hermes/.env
        # 的 WEIXIN_* 变量. 老流程要员工扫完码开 terminal 手动 vim .env, 荒唐;
        # P31 后员工只需 restart hermes (Companion 服务重启按钮或 launchd) 即通.
        # 失败不阻塞主流程 — credential 已经 save 到 accounts/, 员工大不了手动
        # 编 env (回退到荒唐路径). fail-safe.
        try:
            _sync_hermes_env_weixin(
                account_id=account_id, bot_token=token, user_id=user_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("P31 sync .env WEIXIN_* 失败 (不阻塞): %s", e)
        _wechat_qr_sessions.pop(qrcode_value, None)
        logger.info(
            "P30 wechat qr confirmed: account_id=%s user_id=%s → 已 save_weixin_account",
            account_id, user_id,
        )
        # P38 (P3.5.201 7/8 鸿波军规审判 — 撤 P32-P37 中间层):
        #
        # audit 出 hermes 里原生已有 SIGUSR1 graceful restart:
        #   - gateway/run.py:19362-19364 gateway 注册 SIGUSR1 handler
        #   - gateway/run.py:5973 request_restart(via_service=True) drain in-flight
        #   - launchd `<KeepAlive>true</>` (plutil verify) 自动拉起新 process
        #   - hermes_cli/gateway.py:239 _graceful_restart_via_sigusr1 官方 recipe
        #
        # 实测 (7/8 21:xx 鸿波本机): kill -USR1 hermes → 3 秒 launchd 起新 PID.
        #
        # 老路径 (P32-P37) 是我们在 Companion 侧搭 "kill -9 + sh -lc start + poll
        # /health 45s" 中间层 — 复杂 + 无 drain 断 in-flight chat/SSE + 装机依
        # 赖 sh -lc PATH / launchd 501 recovery / hermes_kill Tauri command
        # 权限. 全部推翻用 hermes 原生 SIGUSR1 收敛到一行 plugin 代码.
        #
        # # 边界
        #
        # 1. asyncio.get_event_loop().call_later(3s, ...) 延迟 3 秒 — 给当前
        #    response (return web.json_response 下面那句) 时间 flush 到网络,
        #    Modal 收到 confirmed 后再 hermes 才 drain+exit.
        # 2. 用 signal.SIGUSR1 (POSIX 通用, mac+linux 都有). Windows 走
        #    hasattr(signal, "SIGUSR1") = False 分支跳过 — 员工用 Companion
        #    在 mac/linux, 忽略 Windows.
        # 3. 失败不阻塞主流程 — credential 已 save 到 accounts/, env 已 sync,
        #    员工手动 hermes gateway restart 也能起来 (fallback 路径).
        try:
            import signal as _signal, asyncio as _asyncio
            if hasattr(_signal, "SIGUSR1"):
                _loop = _asyncio.get_event_loop()
                _hermes_pid = os.getpid()
                def _fire_sigusr1() -> None:
                    try:
                        _signal.raise_signal(_signal.SIGUSR1)  # type: ignore[attr-defined]
                    except AttributeError:
                        # Python < 3.8 or Windows fallback (should not fire here)
                        os.kill(_hermes_pid, _signal.SIGUSR1)
                    except Exception as _e:  # noqa: BLE001
                        logger.warning("P38 SIGUSR1 raise 失败: %s", _e)
                _loop.call_later(3.0, _fire_sigusr1)
                logger.info(
                    "P38 已排 SIGUSR1 3s 后自 restart (pid=%s) — hermes 原生 "
                    "graceful drain + launchd KeepAlive 拉起, WeixinAdapter "
                    "读新 .env credential. Modal 侧看 /health 通就 auto-close.",
                    _hermes_pid,
                )
            else:
                logger.info(
                    "P38: signal.SIGUSR1 不存在 (Windows?), 员工需手动 "
                    "'hermes gateway restart' 让新 credential 生效.",
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("P38 SIGUSR1 排程失败 (不阻塞, 员工需手动 restart): %s", e)
        return web.json_response({
            "status": "confirmed",
            "account_id": account_id,
            "user_id": user_id,
        })

    # 未知 status — 归到 wait + warning, 别让前端崩
    logger.info("P30 wechat qr poll: 未知 status=%r, 归 wait", status)
    return web.json_response({
        "status": "wait",
        "_warning": f"ilink 未知 status: {status}",
    })


# ── P31 (P3.5.198.f 7/8 鸿波): .env WEIXIN_* 自动同步 ─────────────────
#
# # 真因 (7/8 05:xx 员工反馈"手动命令行同步，很荒唐")
#
# hermes WeixinAdapter (weixin.py:1162) init 时 self._account_id 从 config
# extra.account_id 或 env WEIXIN_ACCOUNT_ID 读死一个, 启动后不 hot-reload.
# 员工扫码 P30 只把新 credential 落到 ~/.hermes/weixin/accounts/{id}.json,
# 但 WeixinAdapter 手里的 account_id 还是老的. 结合 ilink 后端"一个 wechat
# user 一个 active bot" policy (员工每次扫码, ilink 侧生成新 bot_id → 老 bot
# 立刻废), 结果 WeixinAdapter 手里那个直接 errcode=-14 session timeout.
#
# 老流程要员工 open terminal 编 ~/.hermes/.env 换 WEIXIN_ACCOUNT_ID + WEIXIN_TOKEN,
# restart hermes. 这跟 "扫码即绑定" 承诺矛盾 — 员工反馈"荒唐".
#
# # P31 修法
#
# P30 confirmed 分支 save_weixin_account 之后立刻 call _sync_hermes_env_weixin
# 更新 .env 里的:
#   - WEIXIN_ACCOUNT_ID: 换新 account_id (格式 xxx@im.bot)
#   - WEIXIN_TOKEN: 换新 bot_token — hermes qr_login helper 拿的 ilink bot_token
#     就是完整 "{account_id}:{hex}" 格式 (JSON 里也是), 直接放入不再拼 prefix.
#   - WEIXIN_HOME_CHANNEL: 如果原来为空且新 user_id 有值就补上; 有值就保留
#     (员工可能显式 pin 某个 home channel, 不覆盖)
#
# 员工扫码后剩下的动作只有 "restart hermes" 一步 (Companion Dashboard 上的
# 服务重启按钮 / 或者 launchd kickstart / 或者敲一次 hermes gateway stop+start),
# 完全不用 vim .env.
#
# # 边界
#
# - 只改这 3 个 key, 其他 WEIXIN_* / 别的 env 完全不动
# - 原子写 (tmp + rename) 防 partial write 撞 half-updated .env 让 hermes 起不来
# - chmod 0600 保持权限 (env 里含 bot_token 是敏感)
# - .env 里从没这些 key 就 append (少数首次绑定场景)
# - 失败不阻塞 P30 主流程 — credential 已经落到 accounts/, 员工最坏回退到
#   老"手动 vim .env" 路径 (荒唐但至少可用)

def _sync_hermes_env_weixin(
    *, account_id: str, bot_token: str, user_id: str = "",
) -> None:
    """P31: 把新 WEIXIN_* 同步到 ~/.hermes/.env.

    bot_token 是 ilink 返回的 "bot_token" 原始值, 格式已经是
    "{account_id}:{token_hex}" — 直接写入 WEIXIN_TOKEN, 不再拼前缀.

    fail-safe: 抛错让 caller 记 warning 不阻塞主流程.
    """
    import os as _os
    from pathlib import Path as _Path

    env_path = _Path.home() / ".hermes" / ".env"
    if not env_path.exists():
        # 无 .env 直接创建 — 员工首次装 catfish, hermes 端可能还没 init env
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.touch(mode=0o600)

    try:
        content = env_path.read_text(encoding="utf-8")
    except Exception as e:
        raise RuntimeError(f"读 .env 失败: {e}") from e

    seen = {"WEIXIN_ACCOUNT_ID": False, "WEIXIN_TOKEN": False, "WEIXIN_HOME_CHANNEL": False}
    new_lines: list[str] = []
    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("WEIXIN_ACCOUNT_ID="):
            new_lines.append(f"WEIXIN_ACCOUNT_ID={account_id}\n")
            seen["WEIXIN_ACCOUNT_ID"] = True
        elif stripped.startswith("WEIXIN_TOKEN="):
            new_lines.append(f"WEIXIN_TOKEN={bot_token}\n")
            seen["WEIXIN_TOKEN"] = True
        elif stripped.startswith("WEIXIN_HOME_CHANNEL="):
            existing_val = stripped.split("=", 1)[1].strip() if "=" in stripped else ""
            # 已有值就保留 (员工可能显式 pin 别的 channel); 空才补 user_id
            if not existing_val and user_id:
                new_lines.append(f"WEIXIN_HOME_CHANNEL={user_id}\n")
            else:
                new_lines.append(line)
            seen["WEIXIN_HOME_CHANNEL"] = True
        else:
            new_lines.append(line)

    # 缺哪 key 追加 (首次绑定 .env 里可能没这几行)
    # 确保结尾有换行防拼进上一行
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] = new_lines[-1] + "\n"
    if not seen["WEIXIN_ACCOUNT_ID"]:
        new_lines.append(f"WEIXIN_ACCOUNT_ID={account_id}\n")
    if not seen["WEIXIN_TOKEN"]:
        new_lines.append(f"WEIXIN_TOKEN={bot_token}\n")
    if not seen["WEIXIN_HOME_CHANNEL"] and user_id:
        new_lines.append(f"WEIXIN_HOME_CHANNEL={user_id}\n")

    # atomic write + chmod 600
    tmp = env_path.with_name(f".env.p31.tmp.{_os.getpid()}")
    try:
        tmp.write_text("".join(new_lines), encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass  # Windows / 特殊 FS 没 chmod 概念, 不致命
        tmp.replace(env_path)
    except Exception as e:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise RuntimeError(f"原子写 .env 失败: {e}") from e

    logger.info(
        "P31 .env WEIXIN_* 已同步: WEIXIN_ACCOUNT_ID=%s (bot_token 长度=%d) "
        "user_id=%s. Restart hermes gateway 让 WeixinAdapter 拿新 credential.",
        account_id, len(bot_token), user_id or "(未变)",
    )


def _patch_p30_wechat_qr_endpoints() -> None:
    """P30 (P3.5.198 7/8 鸿波): wechat qr_login start/poll RESTful endpoint.

    跟 P26 同模式: attach handler 到 APIServerAdapter class, route 由
    _patched_app_init 在 Application 创建时真注册 (router 未 freeze 时机).

    fail-safe: import 失败 / attach 失败 → silent skip 不阻塞 hermes 启动.
    """
    try:
        from gateway.platforms.api_server import APIServerAdapter  # noqa: PLC0415
    except ImportError as e:
        logger.warning(
            "P30: api_server 没导, skip wechat qr endpoint patch (%s)", e,
        )
        return
    APIServerAdapter._handle_wechat_qr_start = _handle_wechat_qr_start
    APIServerAdapter._handle_wechat_qr_poll = _handle_wechat_qr_poll
    logger.info(
        "P30 APIServerAdapter._handle_wechat_qr_start/poll 已挂 ✓ "
        "(route 由 Application.__init__ patch 真注册, 跟 P26 同时机)"
    )


# ── P36 (P3.5.199 7/8 鸿波): execute_code / terminal / file_tools 默认 cwd ──
#
# # 真因 (7/8 chat sandbox 找不到 .catfish/uploads 深审 8 项)
#
# hermes 由 launchd 起 gateway daemon (~/Library/LaunchAgents/ai.hermes.gateway.plist),
# ProgramArguments 里没设 WorkingDirectory → process cwd = "/". LLM 里
# execute_code 用相对路径 (`.catfish/uploads/x.csv`, `notes.md`) 就从 `/` 找
# → FileNotFoundError.
#
# hermes source grep 出 5 处 os.getcwd() 会返 "/":
#   - tools/code_execution_tool.py:_resolve_child_cwd → execute_code subprocess cwd
#   - tools/terminal_tool.py:_safe_getcwd → _get_env_config 用
#   - tools/file_tools.py:_resolve_base_dir 兜底
#   - tools/file_operations.py 链式 fallback 兜底
#   - tools/environments/local.py 兜底
#
# 前 3 处**都优先读 TERMINAL_CWD env** (hermes upstream 公开 API, 稳定, 有
# unit test 覆盖). setdefault 兜底一次覆盖三条路径, 不 monkey-patch.
#
# # 边界
#
# 1. 员工/装机脚本已 export TERMINAL_CWD → 尊重, 不覆盖 (员工主权)
# 2. $HOME 不是有效目录 → skip (edge case)
# 3. 顶层 try/except 挂了不阻塞 hermes 启动 (跟 P29-P35 同 pattern)
#
# # 不 fail-loud 的原因
#
# 不列入 _PATCH_TARGETS. P36 不依赖任何 hermes 内部 attr / func, 只 setenv 一
# 个公开 env var. hermes 未来重构 _resolve_child_cwd / _get_env_config 内部
# 实现, TERMINAL_CWD env 语义都不会变 (hermes 自己 docstring 里明说 "session's
# TERMINAL_CWD (same as the terminal tool)").
#
# # 影响面 (审 P36 audit 8 项后严格分类)
#
# 全部正向:
#   ✓ execute_code 里 open(".catfish/uploads/x") 找到 $HOME/.catfish/uploads/x
#   ✓ terminal 里 `ls .catfish` 找到 $HOME/.catfish
#   ✓ file_tools read_file("notes.md") 找到 $HOME/notes.md
#   ✓ cron scheduled 里 execute_code 也从 $HOME 起 (员工 cron script 更需要)
#   ✓ LLM 用 terminal `cd /somewhere` 后 overrides.cwd 机制照旧生效, 不 touch
#   ✓ SSH / Docker / Modal / Daytona backend 完全不 touch (它们本身有合理 default)

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


# hermes 0.14+ plugin discovery 自动调 __init__.py 里的 install() 或类似 hook.
# 实际接入方式跟 catfish-memory 一样, 看 catfish/memory/plugin/__init__.py 模仿.
