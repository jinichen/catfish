"""patch 落点自检 —— 从 plugin.py 拆出 (8/15)。

装 patch 之前先确认 hermes 那边的类/方法/属性还在。hermes 一升级把名字改了,
这里能在启动日志里说清楚是哪一处没了, 而不是等到运行时静默失效。

`_PATCH_TARGETS` 那张表 (77 行) 是唯一权威清单, 加 patch 就得往里加一行。
"""
from __future__ import annotations

import logging

# 跟 plugin.py 同名 —— logging.getLogger 同名返回同一对象
logger = logging.getLogger("catfish.xcatfish_user.plugin")

import os


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
    # P42 (8/8): 后台调用不进记忆 — 见 plugin_memory_gate.py
    ("agent.memory_manager", "MemoryManager", "attr"),
    # P43 (8/13): 提升 catfish 工具为核心 — 见 plugin_core_tools.py.
    # hermes 哪天把 _HERMES_CORE_TOOLS 改名/换结构, 这里 fail-loud, 不静默失效。
    ("toolsets", "_HERMES_CORE_TOOLS", "attr"),
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
