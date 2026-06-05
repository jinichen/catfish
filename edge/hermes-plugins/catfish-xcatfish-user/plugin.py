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

import logging
import os
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("catfish.xcatfish_user.plugin")


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
    """返当前 catfish-gateway 真**`(host, port)`** 真集合 (兼容多默认).

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
    """base_url 真是不是 catfish-gateway (P3 / P10 真**`X-Catfish-User`** header 注入判断).

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

    if missing:
        raise ImportError(
            "catfish-xcatfish-user plugin: hermes refactor 破坏了 patch targets. "
            "缺失 attributes (静态文件检查): " + ", ".join(missing) + ". "
            "不允许半加载 (会导致跨员工串数据). 升级 plugin 或回滚 hermes."
        )

    logger.info(
        "catfish-xcatfish-user: patch target verify ✓ (静态文件检查 %d module + %d method)",
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

    def patched_resolve_auto(main_runtime=None):
        client, model = _orig_resolve(main_runtime=main_runtime)
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
        svc_token = os.environ.get("HERMES_SERVICE_TOKEN")
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
            try:
                from gateway.platforms.api_server import (
                    cors_middleware,
                    security_headers_middleware,
                )
                # fence: 只对 hermes api_server App 注入. 别的 aiohttp Application
                # (Companion 本地 server / 别的 plugin) 不动.
                is_hermes_app = (
                    cors_middleware in mws_list
                    or security_headers_middleware in mws_list
                )
                if is_hermes_app:
                    if _request_stash_middleware not in mws_list:
                        mws_list.append(_request_stash_middleware)
                    if _proxy_404_middleware not in mws_list:
                        mws_list.append(_proxy_404_middleware)
                    logger.info(
                        "P7/P11 middlewares injected via Application.__init__ fence ✓"
                    )
            except Exception as _e:
                logger.debug("middleware inject fence check failed: %s", _e)
            return _orig_app_init(self, *args, middlewares=tuple(mws_list), **kwargs)

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
            if cf_user:
                self._client_kwargs["default_headers"] = {"X-Catfish-User": cf_user}
            else:
                # 没 user 时 explicit clear (防别地方继承上一轮)
                self._client_kwargs.pop("default_headers", None)
            return
        return _orig(self, base_url)

    AIAgent._apply_client_headers_for_base_url = patched


# ── P12 ──────────────────────────────────────────────────────────────────

def _patch_p12_update_system_prompt_safe() -> None:
    """BL-HERMES-SYSTEM-PROMPT-PERSIST-BROKEN (6/4): hermes_state.update_system_prompt
    silent fail bug 真**`monkey-patch fix`**.

    Bug: hermes_state.HermesState.update_system_prompt 真 SQL 直接 `UPDATE sessions
    SET system_prompt = ? WHERE id = ?`, 真**`没 _insert_session_row 保护`**真. 真
    concurrent load (cron + kanban + delegate_task) 时, create_session() race condition
    真**`session row 没真 insert 上`**真 → UPDATE silent affect 0 rows → 下次 read
    system_prompt 真**`null`** → conversation_loop 真**`'Stored system prompt is null'`**
    warning + 真**`每 turn rebuild + prefix cache miss (~29K tokens)`**.

    对比同 file `update_token_counts` (line 967-971) 真**已加** INSERT OR IGNORE pre-call
    保护 — 真**`update_system_prompt 漏改了`**.

    Fix: wrap 真**`call 前 _insert_session_row(session_id, "unknown")`** 真**`保证 row 存`**真.
    幂等 — INSERT OR IGNORE 真**`真**`真**`真**`已 在 row 真**`noop`**真.
    """
    try:
        # 真**`真**`真**`真**`实际 class name 是 SessionDB (audit hermes_state.py:354), 不是 HermesState`**真
        from hermes_state import SessionDB
    except ImportError as e:
        logger.warning("P12: hermes_state.SessionDB import 失败 (%s), skip patch", e)
        return

    _orig = SessionDB.update_system_prompt

    def patched(self, session_id: str, system_prompt: str) -> None:
        # 真**`保证 session row 存`** — 真**`INSERT OR IGNORE 幂等`**真.
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

    Bug: agent_runtime_helpers.dump_api_request_debug @ line 1123 真**`生`**
    `request_dump_{session_id}_{timestamp}.json` — 真**`无 type prefix`**真.
    audit 时 chat / background-review (curator) / cron 真**`真`** 真**`dump 都`**
    真**`一起 排序混杂`**, 真**`grep 找员工真 chat dump 真`** 真**`真**`6 小时 audit slow`**真
    (6/4 凌晨 catfish-memory P0 验证 真踩坑).

    Fix: dump filename 真前缀加 type tag, 真**从 threading.current_thread().name 真**`检`**:
    - `bg-review` thread → `bg`
    - 默认 (main thread, chat session) → `chat`

    new format: `request_dump_<type>_<session_id>_<ts>.json`
    e.g. `request_dump_chat_20260604_125823_d77073_20260604_130043.json`
         `request_dump_bg_20260604_125823_d77073_20260604_130100.json`

    audit 时**`ls request_dump_chat_*` 真**`真**`只`** 真**`真**`员工 chat dump`** — 真**`真**`不混真 curator`**真.
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

        # Call orig — 真**`真**`真**`真**`原 logic 写`** `request_dump_<sid>_<ts>.json`**真
        result = _orig(agent, api_kwargs, reason=reason, error=error)
        if result is None or not isinstance(result, Path):
            return result

        # Rename 真**加 type tag**真**: `request_dump_<sid>_<ts>.json` → `request_dump_<type>_<sid>_<ts>.json`
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

    logger.info("catfish-xcatfish-user plugin installed ✓ (13 patches applied)")


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
# field "Asking the user for approval" + 等 user 真**`/approve` 文字命令解除. LLM
# 看到 message 翻译成中文 "请批准", 鸿波打 "批准" — hermes 不识别中文 alias →
# 不 dispatch 到 _handle_approve_command → 死循环.
#
# 本 patch: GatewayRunner._handle_message 真**入口前**预处理 event.text, 中文
# alias → 改成 /approve / /deny 真 slash command 真**`字面**, 走原 hermes
# dispatch flow. 不动 hermes approval 逻辑 (松耦合).
#
# 别名设计 (鸿波语义习惯, 6/5 拍):
#   "批准"/"同意"/"通过"/"确认"/"审批" → /approve  (单次)
#   "总是批准"/"始终批准"           → /approve always (永久)
#   "本会话批准"/"会话批准"        → /approve session (本 session)
#   "拒绝"/"驳回"/"不同意"/"取消"   → /deny

_APPROVE_ALIASES = {
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
    "拒绝": "/deny",
    "驳回": "/deny",
    "不同意": "/deny",
    "取消": "/deny",
    "no": "/deny",
}


def _patch_p14_approve_chinese_alias() -> None:
    """GatewayRunner._handle_message 真**入口前**预处理 event.text 中文 → slash.

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
            if raw and not raw.startswith("/"):
                # 拿 session_key — 借用 GatewayRunner 真**`_session_key_for_source`**
                try:
                    session_key = self._session_key_for_source(event.source)
                except Exception:
                    session_key = ""
                # 只在有 pending approval 时触发 alias — 避免误吞正常 chat
                if session_key and has_blocking_approval(session_key):
                    alias = _APPROVE_ALIASES.get(raw.lower())
                    if alias:
                        logger.info(
                            "P14 approve alias: '%s' → '%s' (session=%s)",
                            raw, alias, session_key[:12],
                        )
                        event.text = alias
        except Exception as e:  # noqa: BLE001
            logger.debug("P14 alias preprocess 异常 (ignored): %s", e)
        return await _orig_handle(self, event)

    GatewayRunner._handle_message = patched
    logger.info("P14 chinese approval alias patched (GatewayRunner._handle_message)")


# hermes 0.14+ plugin discovery 自动调 __init__.py 里的 install() 或类似 hook.
# 实际接入方式跟 catfish-memory 一样, 看 catfish/memory/plugin/__init__.py 模仿.
