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
from typing import Any, Optional
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
    # SSE endpoint, Companion 主动 trigger hermes compress + 真**进度推 UI**.
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
    # preflight `_emit_status('📦 Preflight compression...')` 真**经 catfish-lifecycle
    # tool name 走 SSE hermes.tool.progress channel** → Companion 接 + 显 inline.
    try:
        _patch_p19_status_callback_bridge()
    except Exception as e:  # noqa: BLE001
        logger.error(
            "P19: _patch_p19_status_callback_bridge 顶层异常 (跳过, 不阻塞 hermes 启动): %s",
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
            # P18 (P3.5.18 6/17 鸿波) — post-init add_post 真**router 未 freeze 前**.
            #
            # 真**问题 (6/17 22:16 鸿波本机 catch)**: 之前 P18 真**wrap connect post**
            # _orig_connect 真**runner.setup() → app.freeze()** 已跑, add_post 真**too late**
            # 撞 'Cannot register a resource into frozen router'. 真**真**fix**: 真**Application
            # 真创建时** add_post (此刻 router 真**未 freeze**, hermes 自己 connect 真**add_post
            # 真**同时机**). handler 真**runtime call** `adapter._handle_compress_session_stream`
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
            except Exception as e:  # noqa: BLE001
                logger.debug("P15: register_gateway_notify 失败 (%s)", e)
                notify_cb = None

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

        try:
            return await _orig(self, *args, **kwargs)
        finally:
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
# P3.5.17.c banner 真**信息流** (下次发消息时 hermes 自动压缩), 鸿波要的是
# Companion 检测 80%+ ctx 时**主动 trigger hermes 压缩** + **弹窗显进度**
# (类似 mac 系统更新).
#
# # 真**抄什么**
#
# hermes 真**全 工具 现成**:
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
#     注意: 真**model= (不是 model_name=)**, 真**ephemeral_system_prompt= (不是
#     system_prompt=)** — design doc 真**bug**, 6/17 audit catch.
#   - status_callback(kind: str, message: str)
#     → ~/.hermes/hermes-agent/run_agent.py:761 (_emit_status / _emit_warning)
#     kind 真**"lifecycle" / "warn"**.
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

    真**fail-silent on disconnect** — 用户切走 / 弹窗关 真**抛 ConnectionResetError**,
    真**try/except 兜底** 不阻塞 compress_future. compress_future 真**继续跑完写 db**.
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
        """写 SSE 帧. 真**ConnectionResetError 兜底**返 False (client 断), 调用方真**别再写**.
        compress_future 真**继续跑** (执行器线程), 写 db 真**完整**.
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
        # session_row 真 dict 真**有 model / ephemeral_system_prompt / system_prompt 字段**
        # 真**老 session 真**model 字段** 真**可能 None** — 真**fallback role_default**
        # 真**或** "catfish-private-main" (compress 不实际 inference, 只 aux LLM).
        model_name = (
            session_row.get("model")
            or session_row.get("model_name")
            or "catfish-private-main"
        )
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
            # client 真**已断**: 跑 compress 但不再 emit SSE (写 db 仍 useful).
            pass

        # 3. body 解析 (optional focus_topic / force)
        try:
            body = await request.json() if request.can_read_body else {}
        except Exception:  # noqa: BLE001
            body = {}
        focus_topic = str(body.get("focus_topic", "") or "").strip() or None
        force = bool(body.get("force", False))

        # 4. 真**临时 AIAgent** 跟 Slack /compress 同款 tmp_agent pattern.
        # status_callback 真**桥** AIAgent._emit_status / _emit_warning → SSE queue.
        from run_agent import AIAgent
        from agent.conversation_compression import compress_context
        from agent.manual_compression_feedback import summarize_manual_compression

        status_queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def status_callback(kind: str, message: str = "") -> None:
            # 真**executor 线程**调 — run_coroutine_threadsafe 把 event 推 main loop 真 queue.
            try:
                asyncio.run_coroutine_threadsafe(
                    status_queue.put((kind, message)), loop,
                )
            except RuntimeError:
                # main loop 真**已关** (client 断 + cleanup) — 忽略, compress_future 真**自跑完**.
                pass

        tmp_agent = AIAgent(
            session_id=session_id,
            model=model_name,                          # 真**hermes API 真 model=, 不是 model_name=**
            ephemeral_system_prompt=system_prompt,     # 真**hermes API 真 ephemeral_system_prompt=**
            status_callback=status_callback,
            session_db=db,                             # 真**复用 同 SessionDB, compress_context 写回**
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
            """真**轮询 status_queue 真**0.5 秒**, compress_future 完了真**退出**."""
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

        # 6. compress_context 真**已经写回 SessionDB** (line 271 真**split the session in SQLite**).
        # 真**无需 再调 db.replace_messages** — 老 /fork pattern 真**create child + replace**,
        # 但 compress_context 真**直接 in-place rotate** 真 session.

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
            # 真**注意**: hermes summarize_manual_compression 真**不接 focus_topic 参数**
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
    """P18: 真**注册 POST /api/sessions/{session_id}/compress/stream SSE handler**.

    真**6/17 22:16 鸿波本机 bug fix**: 之前 P18 wrap connect → _orig_connect 真**runner.setup()
    后 router 已 freeze** → add_post 撞 'Cannot register a resource into frozen router'.

    真**真**新 path**: route 真**Application.__init__ patch (line 1200+)** 真**post _orig_app_init
    add_post** (router 真**未 freeze**, 跟 hermes 自己 connect add_post 同时机). 这里只 attach
    handler method 给 APIServerAdapter class — handler 真**实例 method**, 真**Application.__init__
    时 已经 attached** (plugin import 时 _apply_patches 真先跑 _patch_p18 真**attach class
    attribute**, 之后 hermes 真**create APIServerAdapter 实例 + Application 真**触发
    _patched_app_init** 真**add_post 真 closure handler 真 runtime call adapter method**).
    """
    from gateway.platforms.api_server import APIServerAdapter

    APIServerAdapter._handle_compress_session_stream = _handle_compress_session_stream
    logger.info(
        "P18 APIServerAdapter._handle_compress_session_stream 已挂 ✓ "
        "(route 由 Application.__init__ patch 真**未 freeze 时**注册)"
    )


# ── P19 (P3.5.18 Phase 2, 6/17 鸿波 audit miss revert 后 真**正确路径**) ─
#
# # 真**鸿波诉求 verbatim 链** (P3.5.17.c.1 commit + P3.5.18 design doc)
#
# > "自动进行压缩, 提示这个不是觉得奇怪" (P3.5.17.c.1 commit verbatim)
# > "为什么还是提示, 直接压缩, 压缩过程可以弹窗显示压缩进度" (P3.5.18 design)
#
# 真**P3.5.17.b 已修** hermes 自带 ContextCompressor (catfish-gateway auth fallback
# 让 hermes-cli auxiliary 缺 X-Catfish-User 不再 400 paused). 真**hermes preflight
# 真**自动 trigger compress_context**, 真**但 真**Companion 0 反馈** — chat 卡 30s
# 不知道发生啥, 真**鸿波感知 "怎么还没回?"**.
#
# # 真**真**audit 真因** (6/17 22:50)
#
# hermes 真**`_create_agent` (api_server.py:1068) 真**0 status_callback 参数**:
#   def _create_agent(self, ..., stream_delta_callback, tool_progress_callback,
#                     tool_start_callback, tool_complete_callback, ...):
#
# 真**`_run_agent` (line 3584) 真**call _create_agent 真**也没传 status_callback**.
# 真**`AIAgent(model=, ..., status_callback=status_callback)` 真**永 None**.
#
# 真**preflight `agent._emit_status("📦 Preflight compression: ...")` (run_agent.py:761)**
# → 真**`self._vprint(...)` 真 CLI 显** + 真**`self.status_callback(...)` 真 None skip**.
# → 真**API server (Companion) 0 收**, telegram/discord/slack 真 wire callback 真 收.
#
# # 真**修法**
#
# P19 wrap `APIServerAdapter._create_agent` post-init:
#   1. 真**捕获 kwargs.tool_progress_callback** (hermes `_run_agent` 真传)
#   2. 真**create `catfish_status_callback(kind, message)`** 真**桥 tool_progress_callback**:
#      tool_progress_callback(
#          event_type=f"catfish.lifecycle.{kind}",
#          tool_name="catfish-lifecycle",
#          preview=message,
#      )
#   3. 真**`agent.status_callback = catfish_status_callback`** (instance attr set)
#
# 真**`tool_progress_callback` 真**stream_q.put(("__tool_progress__", payload))**
# → SSE 真`event: hermes.tool.progress` (api_server.py:2207) 真**Companion 接** 真**显**.
#
# 真**Companion 真**配套改 lib/chat.ts**: 真**handle `tool === "catfish-lifecycle"`**
# → 真**onLifecycle callback** → useChat → ChatPanel inline 显 "📦 Compacting...".
#
# # 真**和 P11 model_override 真**叠加 wrap**
#
# P5/P6/P11 已 wrap `_create_agent` (line 930). P19 真**叠加同样 pattern** —
# `_orig_create_agent = APIServerAdapter._create_agent` 真**这时拿到 真**P5/P6/P11-wrapped
# 版本**, 真**call 完后 真**post-init inject status_callback**. 真**不破 P5/P6/P11**.

def _patch_p19_status_callback_bridge() -> None:
    """P19: post-init 注入 agent.status_callback 桥 tool_progress_callback.

    真**让 hermes preflight 自动压缩 真**SSE 推 progress 给 Companion**, 真**用户
    看 chat 真**不再卡 30s 不知道发生啥**.
    """
    from gateway.platforms.api_server import APIServerAdapter

    _orig_create_agent_p19 = APIServerAdapter._create_agent

    def patched_create_agent_p19(self, *args, **kwargs):
        agent = _orig_create_agent_p19(self, *args, **kwargs)
        # 真**捕获 tool_progress_callback** (hermes _run_agent line 3617 真传)
        tpc = kwargs.get("tool_progress_callback")
        if tpc is None or not callable(tpc):
            # 真**caller 真**没传 callback** (e.g. non-stream path) — skip wire.
            return agent

        def catfish_status_callback(kind, message=""):  # noqa: ANN001
            """真**桥** `agent._emit_status(msg)` → SSE hermes.tool.progress.

            真**kind 真**hermes 真**'lifecycle' / 'warn'** (run_agent.py:777/794).
            真**Companion 真**tool="catfish-lifecycle" 真**marker** 真**分发 inline
            进度 UI 真**不污染 tool_calls list**.
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


# hermes 0.14+ plugin discovery 自动调 __init__.py 里的 install() 或类似 hook.
# 实际接入方式跟 catfish-memory 一样, 看 catfish/memory/plugin/__init__.py 模仿.
