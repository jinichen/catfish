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

# P46 的"picker 是唯一真源"判据 + picker 文件读取。8/13 提到模块层:
# `_read_catfish_picker_model` 现在转调它, 而模块层不能用函数体里那种
# `from . import model_authority` —— 包名带 dash, 得走 _import_sibling 的三段
# fallback。(函数体里那处 8/9 起一直是通的, 日志里 P46 真定夺过两次、
# "P6/P11 post-init failed" 一次没有; 顺手改成同一条路, 少一种写法。)
model_authority = _import_sibling("model_authority")

# ── 拆出去的两块, 按军规的 re-export 协议原样吐回来 ──────────────────
#
# 用 _import_sibling 而不是 `from .x import y`: 这个包名带 dash, hermes 的
# spec_from_file_location 加载下 relative import 不稳 —— 上面那个函数的
# 三段 fallback 就是为这个写的, 新模块没理由绕开它自己发明一套。
#
# re-export 是为了不破老 caller: 外部只引用了每块的 _patch_* 入口, 但把块内
# 定义的符号都吐回来, 免得下次谁 `from plugin import _translate_hermes_zh`
# 又踩空。
plugin_weixin_zh = _import_sibling("plugin_weixin_zh")
_P28_REPLACEMENTS = plugin_weixin_zh._P28_REPLACEMENTS  # noqa: F401  (re-export · 见 plugin_weixin_zh.py)
_translate_hermes_zh = plugin_weixin_zh._translate_hermes_zh  # noqa: F401  (re-export · 见 plugin_weixin_zh.py)
_patch_p28_weixin_zh = plugin_weixin_zh._patch_p28_weixin_zh  # noqa: F401  (re-export · 见 plugin_weixin_zh.py)
plugin_memory_gate = _import_sibling("plugin_memory_gate")
_patch_p42_memory_skip_background = plugin_memory_gate._patch_p42_memory_skip_background  # noqa: F401  (re-export · 见 plugin_memory_gate.py)
plugin_service_lean = _import_sibling("plugin_service_lean")
# 8/15 拆分: CORS / 审批 / patch 自检 / 请求上下文 四组搬到兄弟模块。
# 全部 re-export 回来 —— _apply_patches 里的调用点名字一个都不能变
# (tests/test_apply_patches_call_equivalence.py 拿 git 历史逐个对)。
plugin_ctx = _import_sibling("plugin_ctx")
CV_CF_USER = plugin_ctx.CV_CF_USER            # noqa: F401
CV_PICKER_MODEL = plugin_ctx.CV_PICKER_MODEL  # noqa: F401
CV_CF_SOURCE = plugin_ctx.CV_CF_SOURCE        # noqa: F401
_PICKER_PLACEHOLDERS = plugin_ctx._PICKER_PLACEHOLDERS  # noqa: F401
_request_stash_middleware = plugin_ctx._request_stash_middleware  # noqa: F401
_patch_asyncio_executor_for_contextvars = plugin_ctx._patch_asyncio_executor_for_contextvars  # noqa: F401

plugin_verify = _import_sibling("plugin_verify")
_PATCH_TARGETS = plugin_verify._PATCH_TARGETS  # noqa: F401
_verify_patch_targets = plugin_verify._verify_patch_targets  # noqa: F401

plugin_approval = _import_sibling("plugin_approval")
_patch_p14_approve_chinese_alias = plugin_approval._patch_p14_approve_chinese_alias  # noqa: F401
_patch_p15_chat_completions_approval = plugin_approval._patch_p15_chat_completions_approval  # noqa: F401
_patch_p15_2_chat_approval_route = plugin_approval._patch_p15_2_chat_approval_route  # noqa: F401

plugin_cors = _import_sibling("plugin_cors")
_patch_p7_companion_proxy_route = plugin_cors._patch_p7_companion_proxy_route  # noqa: F401
_patch_p8_p9_cors = plugin_cors._patch_p8_p9_cors  # noqa: F401
_patch_p24_cors_allowlist = plugin_cors._patch_p24_cors_allowlist  # noqa: F401
_patch_p44_service_call_lean = plugin_service_lean._patch_p44_service_call_lean  # noqa: F401  (re-export · 见 plugin_service_lean.py)
plugin_core_tools = _import_sibling("plugin_core_tools")
_patch_p43_promote_catfish_core_tools = plugin_core_tools._patch_p43_promote_catfish_core_tools  # noqa: F401  (re-export · 见 plugin_core_tools.py)
plugin_wechat_qr = _import_sibling("plugin_wechat_qr")
_WECHAT_QR_SESSION_TTL = plugin_wechat_qr._WECHAT_QR_SESSION_TTL  # noqa: F401  (re-export · 见 plugin_wechat_qr.py)
_wechat_qr_sessions = plugin_wechat_qr._wechat_qr_sessions  # noqa: F401  (re-export · 8/13 从这边搬过去, 注意是同一个 dict 对象)
_wechat_qr_sweep_expired = plugin_wechat_qr._wechat_qr_sweep_expired  # noqa: F401  (re-export · 见 plugin_wechat_qr.py)
_sync_hermes_env_weixin = plugin_wechat_qr._sync_hermes_env_weixin  # noqa: F401  (re-export · 见 plugin_wechat_qr.py)
_patch_p30_wechat_qr_endpoints = plugin_wechat_qr._patch_p30_wechat_qr_endpoints  # noqa: F401  (re-export · 见 plugin_wechat_qr.py)

# P39 · Codex App Server 会话池 (8/13 拆出, 420 行)。
#
# 它是第一个**需要兄弟模块**的拆出块 (要 resolver 和 model_authority), 而兄弟之间
# 不能互相 import —— 包名带 dash, 得走上面那个三段 fallback 的 _import_sibling,
# 而 import 它就成了循环。所以由这里装载后写进去。
#
# 换个说法: _import_sibling 只能由 plugin.py 调, 拆出去的模块靠注入拿依赖。
# 这是本仓第一次出现这种需求, 后面再拆到需要兄弟的块时按同一套来。
plugin_codex_session = _import_sibling("plugin_codex_session")
plugin_codex_session.resolver = resolver
plugin_codex_session.model_authority = model_authority
# 在**加载期**验, 不是等到 patch 跑的时候 —— P39 的 patch 要到第一次
# _resolve_runtime_agent_kwargs 才执行, 那时候报错已经在请求路径上了。
# 这跟文件头的 fail-loud 原则一致: 不允许 plugin 半推半就加载。
plugin_codex_session._require_wiring()
_patch_p39_codex_app_server_auth_bypass = plugin_codex_session._patch_p39_codex_app_server_auth_bypass  # noqa: F401  (re-export · 见 plugin_codex_session.py)
_p39_close_all_codex_sessions = plugin_codex_session._p39_close_all_codex_sessions  # noqa: F401  (re-export)
_p39_prune_codex_cache = plugin_codex_session._p39_prune_codex_cache  # noqa: F401  (re-export)
_P39_CODEX_CACHE = plugin_codex_session._P39_CODEX_CACHE  # noqa: F401  (re-export · 注意是同一个 dict 对象)


# cron 一族 (8/13 拆出, 456 行): P21 picker / P25 线程隔离 / P26 REST / P27 重试。
# 跟 plugin_codex_session 同一套注入协议 —— 只要一个 model_authority。
plugin_cron = _import_sibling("plugin_cron")
plugin_cron.model_authority = model_authority
plugin_cron._require_wiring()          # 加载期验, 不等 cron 真触发
_CATFISH_CRON_THREAD_LOCAL = plugin_cron._CATFISH_CRON_THREAD_LOCAL  # noqa: F401  (re-export · 注意是同一个对象)
_handle_cron_pause = plugin_cron._handle_cron_pause  # noqa: F401  (re-export)
_handle_cron_resume = plugin_cron._handle_cron_resume  # noqa: F401  (re-export)
_handle_cron_delete = plugin_cron._handle_cron_delete  # noqa: F401  (re-export)
_patch_p21_cron_picker_integration = plugin_cron._patch_p21_cron_picker_integration  # noqa: F401  (re-export)
_patch_p25_cron_env_isolation = plugin_cron._patch_p25_cron_env_isolation  # noqa: F401  (re-export)
_patch_p26_cron_rest_endpoints = plugin_cron._patch_p26_cron_rest_endpoints  # noqa: F401  (re-export)
_patch_p27_cron_auto_retry = plugin_cron._patch_p27_cron_auto_retry  # noqa: F401  (re-export)





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










# ─────────────────────────────────────────────────────────────────────────
# Step 2: 应用 11 处 monkey-patch
# ─────────────────────────────────────────────────────────────────────────

#: 本轮 install 中失败的可选 patch 名字。install() 的收尾日志读它。
_PATCH_FAILURES: list[str] = []


def _try_patch(fn, err_msg: str, *args) -> bool:
    """跑一个**可失败**的 patch: 挂了记一条 error + 记名字, 不打断后面的。

    `*args` 转发给 fn —— P42 要收 `CV_CF_SOURCE`。**这个参数是补出来的**,
    第一版没有, 直接把 P42 的实参吞了 (见下面"翻过的车")。

    # 为什么有这个函数

    8/13 发现收尾那行日志在撒谎:

        logger.info("catfish-xcatfish-user plugin installed ✓ (15 patches applied)")

    `15` 是写死的字符串, 而 `_apply_patches` 实际调 33 个 patch 入口。它从 15 涨到
    33 的整个过程一动不动 —— 更要命的是, 19 个包在 try 里的 patch **全部失败**时
    这行照样打 `✓ (15 patches applied)`。一个不会变的状态指示灯, 而它正是判断
    "插件装好了没"用的那个信号。

    收掉 19 个一模一样的 try 块 (形状逐个比对过: try 体单调用 + 单 handler +
    `except Exception as e` + `logger.error(<文案>, e)`), 文案**原样传参**不改一个字
    —— 那些文案是历次事故留下的, 不该在"修计数器"这件事里被顺手改掉。

    # 两类 patch 的区别

    裸调的 14 个失败会往上抛 (fail-loud, install 整个失败, 收尾日志根本到不了);
    走这里的 19 个失败只降级。这个区分是原来就有的, 这里只是把后一类的结果记下来。

    # 翻过的车 (8/13, 同一天先后两次)

    第一版的 AST 转换只取了 `call.func.id`, 于是:

      1. **吞掉了 P42 的实参** `_patch_p42_memory_skip_background(CV_CF_SOURCE)`
         → memory 来源闸整个没装上。那道闸挡的是"员工邮件正文进个人知识库",
         是 8/8 专门修过的隐私红线。
      2. **吞掉了 19 个 handler 的 `exc_info=True`** → patch 失败不再有堆栈。

    而当时"文案逐字一致"的验证之所以全绿 —— 我只比了 `args[0]`, 也就是我**自己
    选择要保留**的那个东西。验证范围等于设计范围, 所以它抓不到没想到的部分。
    形状检查同理: 验了 `len(handler.args)==2`, 没看 `handler.keywords`;
    验了 try 体是单个 `_patch_*` 调用, 没看那个调用有没有实参。

    钉住这两条的测试在 tests/test_apply_patches_call_equivalence.py —— 它拿
    重构**之前**的 git 版本逐个调用点对拍, 而不是拿我的设计意图对拍。
    """
    try:
        fn(*args)
        return True
    except Exception as e:  # noqa: BLE001
        # exc_info=True 是原来 19 个 handler 都有的, 第一版转换给丢了。
        # 没有堆栈时, "name 'functools' is not defined" 这种错根本不知道在哪一行。
        logger.error(err_msg, e, exc_info=True)
        _PATCH_FAILURES.append(getattr(fn, "__name__", str(fn)))
        return False


def _count_patch_entry_points() -> Optional[int]:
    """数 `_apply_patches` 里到底挂了多少个 patch 入口 —— **从源码数, 不写死**。

    写死一个 33 就是把 8/13 修掉的那个 bug 换个数字再犯一遍: 下次加 patch 时
    没人会想起来同步它, 而它错了不报错。

    数两类:
      · 裸调 `_patch_pNN_xxx()`   —— 失败会往上抛 (fail-loud)
      · `_try_patch(_patch_..., ...)` —— 失败只降级

    数不出来返 None (源码读不到 —— 比如被打包成 pyc 分发), 调用方照实说"未知",
    不编一个数字出来。
    """
    import ast as _ast  # noqa: PLC0415
    import inspect as _inspect  # noqa: PLC0415

    try:
        tree = _ast.parse(_inspect.getsource(_apply_patches))
    except (OSError, TypeError, SyntaxError, IndentationError):
        return None
    n = 0
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.Call) or not isinstance(node.func, _ast.Name):
            continue
        # `_try_patch(_patch_x, ...)` 里的 _patch_x 是 Name 不是 Call, 不会重复计数
        if node.func.id == "_try_patch" or node.func.id.startswith("_patch_"):
            n += 1
    return n


def _apply_patches() -> None:
    """应用所有 monkey-patch. 顺序无关 (各自独立)."""
    _PATCH_FAILURES.clear()
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
    _try_patch(_patch_p16_session_search, "P16: _patch_p16_session_search 顶层异常 (跳过, 不阻塞 hermes 启动): %s")
    # P3.4.D 6/15 鸿波: bg-review HTTP 400 X-Catfish-User missing — patch AIAgent.__init__
    #   post-init, bg-review review_agent 从 session_registry 拿 parent cf_user 注入.
    _try_patch(_patch_p17_bg_review_inject, "P17: _patch_p17_bg_review_inject 顶层异常 (跳过, 不阻塞 hermes 启动): %s")
    # P3.5.18 Phase 2 (6/17 鸿波"自动进行压缩, 提示这个不是觉得奇怪"): hermes preflight
    # 自动压缩时**Companion 0 反馈** — chat 卡 30 秒不知道发生啥. 修法:
    # _create_agent post-init 注入 status_callback 桥 tool_progress_callback,
    # preflight `_emit_status('📦 Preflight compression...')` 经 catfish-lifecycle
    # tool name 走 SSE hermes.tool.progress channel → Companion 接 + 显 inline.
    _try_patch(_patch_p19_status_callback_bridge, "P19: _patch_p19_status_callback_bridge 顶层异常 (跳过, 不阻塞 hermes 启动): %s")
    # P3.5.65 P20 (6/22 鸿波 catch "审批按钮一直不弹"): wrap approve_permanent /
    # load_permanent, 拦截 execute_code 进 _permanent_approved.
    # 真因 (read-only diagnostic 实证): 用户某次 audit UI 点 "always", 把
    # "execute_code" 字面字符串加进 _permanent_approved + config.yaml
    # command_allowlist. 之后每次 execute_code 调走 check_execute_code_guard:1749
    # is_approved("execute_code") → True → silent auto-approve 永远不弹按钮.
    # execute_code 是给 LLM **任意 Python 沙箱权限**的危险 pattern, 一旦 always-
    # approved = 给 LLM 完全 shell 权限. 红线: 永远不允许永久 approve.
    _try_patch(_patch_p20_block_execute_code_permanent, "P20: _patch_p20_block_execute_code_permanent 顶层异常 (跳过, 不阻塞 hermes 启动): %s")
    # P3.5.74 P21 (6/22 鸿波 catch "cron 不是用 picker 吗"): hermes cron/scheduler.py
    # run_job 读 config.yaml model.default 跟 picker_state.json 解耦, 员工切 picker
    # 后 cron job 仍走老 model. P3.5.28/42/42.1 把 picker 联动到 chat / advisor /
    # email scheduler / vision / catfish-memory summarize, 这里补 cron job — 最后
    # 一个 sprint gap.
    #
    # 复用 catfish-memory plugin 已有的 _read_picker_state_model helper (同款架构),
    # 优先级 picker_state.json > job.model > config.yaml.model.default > env (跟
    # _get_summarize_model 优先级一致).
    _try_patch(_patch_p21_cron_picker_integration, "P21: _patch_p21_cron_picker_integration 顶层异常 (跳过, 不阻塞 hermes 启动): %s")
    # P22 reverted in P3.5.78 (6/22 鸿波): bookkeep 重构进 catfish-memory.expense,
    # 不再需要 pin _HERMES_CORE_TOOLS — memory tool 本来就 core, kind=expense 自然命中.

    # P43 (8/13 鸿波"为什么一直不会去查知识库"): catfish 工具全走 MCP 进 hermes,
    # 被 tool_search 的 progressive disclosure 整族 defer 掉 —— shape dump 实测
    # 下发给模型的 32 个工具里 catfish 一个都没有。把几个高频的提升为核心。
    # 详见 plugin_core_tools.py 模块 docstring。
    _try_patch(_patch_p43_promote_catfish_core_tools, "P43: _patch_p43_promote_catfish_core_tools 顶层异常 (跳过, 不阻塞 hermes 启动): %s")

    # P23 (P3.5.79 6/23 鸿波): inbound message 路径 (微信/Discord/Slack/Telegram)
    # picker 联动 — 修 catfish picker 联动 sprint 漏 cover 的最后一个 platform.
    _try_patch(_patch_p23_inbound_picker_integration, "P23: _patch_p23_inbound_picker_integration 顶层异常 (跳过, 不阻塞 hermes 启动): %s")

    # P24 (P3.5.89 6/23 鸿波): CORS allowlist 扩 X-Catfish-* — 修教学按钮 / 任何
    # 自定义 header 浏览器 preflight block 触发 TypeError: Load failed 真因.
    _try_patch(_patch_p24_cors_allowlist, "P24: _patch_p24_cors_allowlist 顶层异常 (跳过, 不阻塞 hermes 启动): %s")

    # P25 (P3.5.104 6/24 鸿波 catch "execute_code 不弹审批一直被拦"): cron env
    # 隔离, 治 hermes cron/scheduler.py:1558 设 HERMES_CRON_SESSION 后不清污染
    # 全 daemon 进程的 bug. P25 用 threadlocal 精准判定 cron 线程, 不依赖被污染
    # 的全进程 env. 必须在 P21 (wrap run_job) 之后调用 — P25 内部也 wrap run_job
    # set threadlocal, 顺序保证 P25 包 P21 包 orig, finally pop env 在最外层.
    _try_patch(_patch_p25_cron_env_isolation, "P25: _patch_p25_cron_env_isolation 顶层异常 (跳过, 不阻塞 hermes 启动): %s")

    # P26 (P3.5.105 6/25 鸿波 catch "定时任务跑没跑结果如何都看不到"): cron 监控
    # 操作 RESTful endpoint. attach handler 给 APIServerAdapter class; 真 route
    # 注册在 _patched_app_init 块 (Application 创建时, router 未 freeze), 跟 P18
    # add_post 同时机.
    _try_patch(_patch_p26_cron_rest_endpoints, "P26: _patch_p26_cron_rest_endpoints 顶层异常 (跳过, 不阻塞 hermes 启动): %s")

    # P27 (P3.5.106 6/25 鸿波 catch "失败不重试"): cron 任务失败 5/10/15 分钟 三档
    # 自动重试. wrap cron.jobs.mark_job_run, success=False 时改 next_run_at = now +
    # backoff, 累计 3 次后让 hermes 真按 schedule 跑下次 (退出 retry). delivery_error
    # 不触发 retry (agent 跑出来了, 发不出去是渠道问题). 跟 P21/P25 真独立 — 它们
    # wrap run_job, P27 wrap mark_job_run, 0 嵌套冲突.
    _try_patch(_patch_p27_cron_auto_retry, "P27: _patch_p27_cron_auto_retry 顶层异常 (跳过, 不阻塞 hermes 启动): %s")

    # P28 (P3.5.123 6/25 鸿波 catch "微信里 ClawBot 英文不合适"): :
    # wrap WeixinAdapter.send 真str.replace 英文 → 中文** (approval / 中断提示 /
    # /approve 命令说明). : 只 wrap weixin, slack / matrix 保英文.
    # 鸿波铁律: 中文 reply 段砍 /approve always (永久免批) :.
    _try_patch(_patch_p28_weixin_zh, "P28: _patch_p28_weixin_zh 顶层异常 (跳过, 不阻塞 hermes 启动): %s")

    # P29 (P3.5.168 7/3 鸿波 catch v0.18 upgrade backlog):
    # Companion 走 hermes 8642 /v1/chat/completions 时 /learn slash command 前置翻译.
    # 真因 (P3.5.164 严格 audit): hermes v0.18 /learn 只在 GatewayRunner._handle_message
    # 处理 (gateway/run.py:9263-9289), /v1/chat/completions (APIServerAdapter) 严格
    # 不过 slash command dispatcher. Companion 员工输 "/learn xxx" → LLM 只当 prompt
    # 释义. P29 wrap _run_agent 前置检测 → 调 hermes agent.learn_prompt.build_learn_prompt
    # 翻译 → 替换 message → P15 approval 闭包 → hermes original _run_agent.
    _try_patch(_patch_p29_learn_slash_translate, "P29: _patch_p29_learn_slash_translate 顶层异常 (跳过, 不阻塞 hermes 启动): %s")

    # P30 (P3.5.198 7/8 鸿波 catch "微信扫码绑定 Error: start 失败 404"):
    # 前端 wechat_qr.ts v3 (5/26) 契约缺 backend impl — hermes v0.17→v0.18
    # 升级弄丢 or 从来没落地. hermes weixin.qr_login helper (weixin.py:1003)
    # 是 CLI 阻塞打印 ASCII 二维码到 stdout 的单 flow 函数, 不能直接暴露 REST.
    # P30 拆成 stateless start/poll 双 endpoint, 复用 hermes ILINK_BASE_URL /
    # EP_GET_BOT_QR / EP_GET_QR_STATUS / _api_get / _make_ssl_connector /
    # save_weixin_account, 前端 wechat_qr.ts 零改动. 跟 P26 同模式: attach
    # handler 到 APIServerAdapter, route 在 _patched_app_init 注册.
    _try_patch(_patch_p30_wechat_qr_endpoints, "P30: _patch_p30_wechat_qr_endpoints 顶层异常 (跳过, 不阻塞 hermes 启动): %s")

    # P36 (P3.5.199 7/8 鸿波 catch "chat sandbox 相对路径找不到 uploads"):
    # hermes launchd 起 gateway → process cwd="/". LLM execute_code 里
    # `.catfish/uploads/x.csv` 相对路径 → subprocess.Popen(cwd="/") → 404.
    # 员工从来没期望 subprocess 从 `/` 起 (mac 员工机日常在 $HOME 干活).
    # hermes upstream 公开 TERMINAL_CWD env API: execute_code / terminal /
    # file_tools 都读它. setdefault 一次覆盖三条路径, 零 monkey-patch.
    _try_patch(_patch_p36_terminal_cwd_home, "P36: _patch_p36_terminal_cwd_home 顶层异常 (跳过, 不阻塞 hermes 启动): %s")

    # P39 (7/31): Codex App Server 模式在 provider credential resolver 之前
    # short-circuit。登录继续只由 Codex CLI 管，不复制 OAuth token 到 Hermes。
    _try_patch(_patch_p39_codex_app_server_auth_bypass, "P39: Codex App Server auth bypass patch 失败: %s")

    # P40 (7/31): Companion 与 Hermes/Codex 共用 state.db 时，某些 runtime 会在
    # 请求开始和 turn 完成各写一次相同 user message。把防重放在 SessionDB 公共
    # 写入层，覆盖 App、API server、Codex adapter 的所有组合。
    _try_patch(_patch_p40_companion_user_message_dedup, "P40: Companion user message dedup patch 失败: %s")

    # P42 (8/8 鸿波"员工邮件正文进个人知识库不合理"): 后台调用不写记忆。
    # email_scheduler 的评级调用走 agent loop, 于是邮件标题/发件人被写进
    # employee_journal.md, 再被蒸成 wiki 条目。判据和失效方式见
    # plugin_memory_gate.py 的模块 docstring。
    # CV_CF_SOURCE 必须传进去 —— 8/13 第一版重构把这个实参吞了, memory 来源闸
    # 整个没装上 (那道闸挡的是"员工邮件正文进个人知识库")。
    _try_patch(_patch_p42_memory_skip_background, "P42: memory 来源闸 patch 失败: %s", CV_CF_SOURCE)

    # P44 (8/15): 后台分类调用不背 agent 上下文。跟 P42 共用 CV_CF_SOURCE ——
    # P42 挡记忆**写**(邮件正文进知识库), P44 挡工具 schema + 记忆**读**(一次
    # 评级 42K token 换 185 token)。
    #
    # 判据不一样, 这点要紧: P42 是"有 source 就跳", P44 是**只含两项的白名单**。
    # 早安 (companion-briefing-card / companion-advisor) 和知识库
    # (companion-wiki-suggest) 都是有 source 的, 沿用 P42 那条宽判据会误伤。
    # 见 plugin_service_lean.py 的模块 docstring。
    _try_patch(_patch_p44_service_call_lean, "P44: 服务式调用瘦身 patch 失败: %s", CV_CF_SOURCE)


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

            # P11 + P46: picker 是**唯一真源**.
            #
            # 老 P11 只有两段: kwargs.model_override / CV_PICKER_MODEL (都来自
            # 请求体的 model)。请求体没带 model 时就"不动", 于是 hermes 自己那套
            # 优先级说了算 —— 而它里面有一条**会话级持久化 override**
            # (state.db sessions.model), 一旦写进去就永久生效, 员工换 picker 也
            # 不解除。
            #
            # 8/9 实撞: 工作台选的是 deepseek, 但会话 api-b6bcbf8a419068fa 的
            # sessions.model 钉着 catfish-public-qwen-flash (周配额已耗尽),
            # 于是同一次早安页刷新里一部分请求 deepseek 成功、一部分 qwen 429。
            # session_key 是 sha256(system_prompt + 首条 user message)[:16],
            # 员工看不到也清不掉。
            #
            # 鸿波 8/9 拍板: **在要求确定性的环境里这不可接受, 直接砍掉。**
            # 加第三段 —— 请求体没带 model 时读 picker_state.json 兜底, 让那个
            # 持久化的会话模型变成惰性的 (我们不拦它的写入, 只是不再听它的)。
            #
            # 判据本身在 model_authority.decide_model, 那是纯函数有单测;
            # 这里只负责"什么时候调"。
            #
            # 8/13: 原来这里是 `from . import model_authority` (函数体内相对导入)。
            # 它一直是通的 —— 日志里 P46 真定夺过、"P6/P11 post-init failed" 一次
            # 没有。但它落在一个 except 只打 warning 的 try 里, 万一哪天 hermes 换
            # 加载方式导致相对导入失效, 表现就是「模型只能 picker 模型」这条硬规矩
            # 静默失效, 只留一行 warning。改用模块层已经装好的那份, 少一个失败面。
            model_override = model_authority.decide_model(
                request_model=kwargs.get("model_override") or CV_PICKER_MODEL.get(),
            )
            if model_override and agent.model != model_override:
                logger.info(
                    "P46 picker 定夺: agent.model %s → %s "
                    "(会话持久化的模型不参与, 见 model_authority.py)",
                    agent.model, model_override,
                )
                agent.model = model_override
        except Exception as e:
            logger.warning("P6/P11 _create_agent post-init failed: %s", e)
        return agent

    APIServerAdapter._create_agent = patched_create_agent












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

    # 8/13: 原来是写死的 "(15 patches applied)" —— 实际 33 个入口, 而且 19 个可选
    # patch 全挂了它也照样打 ✓。见 _try_patch 的注释。
    _n = _count_patch_entry_points()
    _attempted = str(_n) if _n is not None else "未知数量的"
    if _PATCH_FAILURES:
        logger.warning(
            "catfish-xcatfish-user plugin installed ⚠ (%s 个 patch 入口, "
            "%d 个可选 patch 失败: %s —— 原因见上面各自的 error)",
            _attempted, len(_PATCH_FAILURES), ", ".join(_PATCH_FAILURES),
        )
    else:
        logger.info(
            "catfish-xcatfish-user plugin installed ✓ (%s 个 patch 入口全部生效)",
            _attempted,
        )


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
    """读 ~/.catfish/picker_state.json 拿 chat_model。

    # 8/13: 从"本地抄一份"改成转调 model_authority.read_picker_model

    这个函数原来是 catfish-memory 那份 `_read_picker_state_model` 的手抄副本
    (注释写着"两个 plugin 独立装载, 不能 cross import, 抄个最简版本")。那个理由
    对 **catfish-memory** 成立, 但对同一个包里的 `model_authority` 不成立 ——
    P46 早就把同一段逻辑放在那里了, 而且它才是"picker 是唯一真源"这条规矩的归属
    模块。

    两份并存的风险很具体: 「模型只能 picker 模型」是硬规矩, 而 P21 / P23 / P39
    走这份、model_authority.decide_model 走那份。两边哪天飘了, 表现是"有的路径听
    picker、有的不听", 跟 8/9 那次会话级 model override 一模一样 —— 同一个员工、
    同一个界面, 不同请求用不同模型, 而且没有任何地方显示这件事。

    改之前把两份实现在 11 种输入上对拍过 (文件不存在 / 空文件 / 坏 json /
    不是 dict / 缺键 / 空串 / 全空格 / 非字符串 / null / 前后带空格 / 正常),
    输出逐个相同。测试在 tests/test_picker_reader_single_source.py。
    """
    return model_authority.read_picker_model()








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


# hermes 0.14+ plugin discovery 自动调 __init__.py 里的 install() 或类似 hook.
# 实际接入方式跟 catfish-memory 一样, 看 catfish/memory/plugin/__init__.py 模仿.
