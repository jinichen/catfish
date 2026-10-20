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


def _hermes_home():
    """hermes 的数据目录。**不是**永远 ~/.hermes —— Windows 上是 %LOCALAPPDATA%\\hermes。

    9/23: 这个插件里原来有 6 处写死 `Path.home() / ".hermes"`, Windows 上全部指向
    一个不存在的目录 (state.db / memories / .env / skills 都读不到, 且静默)。
    优先级跟 hermes 自己一致: HERMES_HOME > hermes_constants.get_hermes_home()
    > 平台默认。每个文件内联一份而不是抽模块: 这个包名带 dash, 兄弟模块互相
    import 要绕 _import_sibling 三段 fallback (见 plugin.py:100), 为 8 行不值。
    """
    import os as _os
    import sys as _sys
    from pathlib import Path as _P

    env = _os.environ.get("HERMES_HOME", "").strip()
    if env:
        return _P(env).expanduser()
    try:
        from hermes_constants import get_hermes_home  # noqa: PLC0415

        return _P(get_hermes_home())
    except Exception:  # noqa: BLE001  — 插件加载早期 hermes_constants 可能还不在 sys.path
        pass
    if _sys.platform == "win32" and _os.environ.get("LOCALAPPDATA"):
        return _P(_os.environ["LOCALAPPDATA"]) / "hermes"
    return _P.home() / ".hermes"


# ─────────────────────────────────────────────────────────────────────────
# Step 1: import-time verify — patch 目标 attribute 必须存在, 否则 fail loud
# ─────────────────────────────────────────────────────────────────────────

_PATCH_TARGETS = [
    # (module, attribute, kind) — kind ∈ {"func", "method", "attr"}
    ("agent.agent_init", "init_agent", "func"),
    ("run_agent", "AIAgent", "attr"),
    # P3 (v0.22 起): 辅助调用的身份头 —— 包 _plan_aux_call (同步 / 异步都经过它),
    # 用 _normalize_main_runtime 判断这次是不是走 catfish 网关 (见 plugin_runtime.py)。
    ("agent.auxiliary_client", "_plan_aux_call", "func"),
    # 原来的 _MAIN_RUNTIME_FIELDS / _resolve_auto 两个目标 v0.22 已删。
    ("agent.auxiliary_client", "_normalize_main_runtime", "func"),
    # P47: private-model auxiliary calls must fail closed. If Hermes renames
    # a fallback hook, the guard could silently stop blocking public spend.
    ("agent.auxiliary_client", "_call_llm_impl", "func"),
    ("agent.auxiliary_client", "_async_call_llm_impl", "func"),
    ("agent.auxiliary_client", "_try_configured_fallback_chain", "func"),
    ("agent.auxiliary_client", "_try_main_fallback_chain", "func"),
    ("agent.auxiliary_client", "_try_payment_fallback", "func"),
    ("agent.auxiliary_client", "_try_main_agent_model_fallback", "func"),
    (
        "agent.auxiliary_client",
        "_try_configured_fallback_for_unavailable_client",
        "func",
    ),
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
    ("gateway.run", "_load_gateway_config", "func"),
    ("agent.codex_runtime", "run_codex_app_server_turn", "func"),
    # v0.22 起 Codex 的流式输出 / 工具进度由上游这个事件桥负责, P39 会话池复用时要换绑
    ("agent.codex_runtime", "make_codex_app_server_event_bridge", "func"),
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
    # P49 (9/10): 横向协同 RoomLink 三个补丁的目标 — 见 plugin_room_link.py。
    # 任一改名 → 那一个 patch 静默失效 → 对应的红线漏洞 (A 拿到 approve 权限 /
    # B 的回复未经审批出端 / B 的工具审批推给 A)。三个都必须 fail-loud。
    # run_conversation 是 AIAgent 方法, 走下面 _AIAGENT_METHOD_TARGETS。
    ("gateway.hosted_room_peer", "issue_room_grant", "func"),
    ("gateway.hosted_room_execution_policy", "current_room_execution_policy", "func"),
    ("tools.approval", "register_gateway_notify", "func"),
    # P20: 删除类命令不许进永久白名单 —— v0.22 起按 profile 懒加载直接读 _read_permanent_allowlist
    ("tools.approval", "_read_permanent_allowlist", "func"),
    ("tools.approval", "save_permanent_allowlist", "func"),
    ("tools.approval", "_permanent_set", "func"),
    # P59: 始终允许放行 / 删除类强制问人 / 定时任务判定 (plugin_approval_always.py)
    ("tools.approval", "is_approved", "func"),
    ("tools.approval", "_human_decision", "func"),
    ("tools.approval", "_is_permanently_approved", "func"),
    ("tools.approval", "_unattended_contexts", "func"),
    ("tools.approval_detection", "detect_dangerous_command", "func"),
    # P60: 微信里找专家 —— 按聊天改派 profile + 主 Bot 收「找X / 回小鲶」命令 (plugin_wechat_expert.py)
    ("gateway.run", "_multiplex_profile_homes", "func"),
    # P61: 空闲后台压缩 —— 包 API server 的 _run_agent / 网关的 _handle_message, 照会话卫生建临时 agent 压
    ("gateway.platforms.api_server", "APIServerAdapter", "attr"),
    ("gateway.run", "_seed_hygiene_system_prompt", "func"),
    ("gateway.run", "_resolve_runtime_agent_kwargs", "func"),
    ("gateway.run", "_resolve_gateway_model", "func"),
    ("gateway.run", "_profile_runtime_scope", "func"),
    ("agent.conversation_compression", "CompressionCommitFence", "attr"),
    # P61b: 摘要篇幅上限 —— 包 ContextCompressor._build_summary_prompt
    ("agent.context_compressor", "ContextCompressor", "attr"),
    ("gateway.platforms.base", "BasePlatformAdapter", "attr"),
    # P53: tool_call 桥参数解析 (v0.22 起支持 {"calls": [...]})
    ("tools.tool_search", "resolve_underlying_call", "func"),
    ("tools.tool_search", "scoped_deferrable_names", "func"),
    ("tools.tool_search_validation", "normalize_tool_call_entries", "func"),
]


_AIAGENT_METHOD_TARGETS = [
    "_current_main_runtime",
    "_apply_client_headers_for_base_url",
    # P49.2: room run 的 final_response 在这里截走 — 见 plugin_room_link.py。
    # 它是 forwarder (run_agent.py:8690 → conversation_loop.run_conversation),
    # patch 方法而不是模块函数, 因为 api_server_runs.py:820 调的是
    # agent.run_conversation(...)。
    "run_conversation",
]


_APISERVER_METHOD_TARGETS = [
    "_create_agent",
    # _check_auth (8/20): P15.2 的 middleware 是短路的, handler 里那行鉴权跑不到,
    # 所以它自己调 adapter._check_auth 补上 (见 plugin_approval._require_api_auth).
    # 上游改名 → 运行时 fail-closed = 审批按钮直接失效. 钉在这里, 让它在**插件
    # 装载期**就炸, 而不是等员工点不动按钮再来查。
    "_check_auth",
    # _run_agent: P61 闲时压缩包它 (P29 /learn 翻译 10/20 退役)
    "_run_agent",
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
    hermes_root = os.environ.get("HERMES_ROOT") or str(_hermes_home() / "hermes-agent")
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


# P52 微信审批回复: 包 _handle_active_session_busy_message, 发提示要找回收消息的那个 adapter
_GATEWAYRUNNER_METHOD_TARGETS = [
    # P60: 微信里找专家 —— 包这个按聊天改派 profile; _transport_owner 用来认出是不是主 Bot 收的
    "_profile_name_for_source",
    "_transport_owner",
    # P61
    "_handle_message", "_session_key_for_source", "_is_session_running", "_resolve_session_agent_runtime",
    "_evict_cached_agent", "_resolve_profile_home_for_source",
    "_handle_active_session_busy_message",
    "_delivery_adapter_for",
    "_reply_anchor_for_event",
    "_thread_metadata_for_source",
]


def _check_class_method_in_source(module_path: str, class_name: str, method: str,
                                  _depth: int = 0) -> bool:
    """静态文件检查 class (连同它的父类 / mixin) 是否含某 method (不 import)。

    v0.22 起 AIAgent / APIServerAdapter / GatewayRunner 的方法大量挪进了 mixin
    (agent/client_lifecycle.py、gateway/run_busy.py …), 只看 class 本体会误报缺失。
    这里用 ast 解析: 本体没有就顺着 ``from x import Base`` 找到父类所在文件接着查。
    """
    import ast
    import os
    hermes_root = os.environ.get("HERMES_ROOT") or str(_hermes_home() / "hermes-agent")
    file_path = os.path.join(hermes_root, module_path.replace(".", "/") + ".py")
    if _depth > 6 or not os.path.exists(file_path):
        return False
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=file_path)
    except Exception:
        return False
    imported: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                imported[alias.asname or alias.name] = (node.module, alias.name)
    for node in tree.body:
        if not (isinstance(node, ast.ClassDef) and node.name == class_name):
            continue
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == method:
                return True
            if isinstance(item, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == method for t in item.targets):
                return True
        for base in node.bases:
            name = base.id if isinstance(base, ast.Name) else (
                base.attr if isinstance(base, ast.Attribute) else None)
            if not name:
                continue
            src_mod, src_name = imported.get(name, (module_path, name))
            if _check_class_method_in_source(src_mod, src_name, method, _depth + 1):
                return True
        return False
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

    for method in _GATEWAYRUNNER_METHOD_TARGETS:
        if not _check_class_method_in_source("gateway.run", "GatewayRunner", method):
            missing.append(f"gateway.run.GatewayRunner.{method}")

    if missing:
        raise ImportError(
            "catfish-xcatfish-user plugin: hermes refactor 破坏了 patch targets. "
            "缺失 attributes (静态文件检查): " + ", ".join(missing) + ". "
            "不允许半加载 (会导致跨员工串数据). 升级 plugin 或回滚 hermes."
        )

    logger.info(
        "catfish-xcatfish-user: patch target verify ✓ "
        "(静态文件检查 %d module + %d method)",
        len(_PATCH_TARGETS),
        len(_AIAGENT_METHOD_TARGETS) + len(_APISERVER_METHOD_TARGETS)
        + len(_GATEWAYRUNNER_METHOD_TARGETS),
    )
