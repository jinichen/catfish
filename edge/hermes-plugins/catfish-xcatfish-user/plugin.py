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

# 8/15 第 2 趟: 会话 / 运行时 / 零散 三组再搬走 1464 行。
plugin_session = _import_sibling("plugin_session")
_patch_p2_current_main_runtime = plugin_session._patch_p2_current_main_runtime  # noqa: F401
_patch_p4_auto_title_session = plugin_session._patch_p4_auto_title_session  # noqa: F401
_patch_p16_session_search = plugin_session._patch_p16_session_search  # noqa: F401
_patch_p17_bg_review_inject = plugin_session._patch_p17_bg_review_inject  # noqa: F401
_patch_p40_companion_user_message_dedup = plugin_session._patch_p40_companion_user_message_dedup  # noqa: F401

plugin_runtime = _import_sibling("plugin_runtime")
_get_catfish_gateway_host_port = plugin_runtime._get_catfish_gateway_host_port  # noqa: F401
_is_catfish_gateway_base_url = plugin_runtime._is_catfish_gateway_base_url  # noqa: F401
_read_catfish_picker_model = plugin_runtime._read_catfish_picker_model  # noqa: F401
_patch_p1_agent_init = plugin_runtime._patch_p1_agent_init  # noqa: F401
_patch_p3_auxiliary_client = plugin_runtime._patch_p3_auxiliary_client  # noqa: F401
_patch_p5_p6_p11_api_server_create_agent_and_picker = plugin_runtime._patch_p5_p6_p11_api_server_create_agent_and_picker  # noqa: F401
_patch_p10_apply_client_headers_localhost = plugin_runtime._patch_p10_apply_client_headers_localhost  # noqa: F401
_patch_p23_inbound_picker_integration = plugin_runtime._patch_p23_inbound_picker_integration  # noqa: F401

plugin_misc = _import_sibling("plugin_misc")
_EXEC_CODE_PATTERN_KEYS_BLOCKED = plugin_misc._EXEC_CODE_PATTERN_KEYS_BLOCKED  # noqa: F401
_patch_p12_update_system_prompt_safe = plugin_misc._patch_p12_update_system_prompt_safe  # noqa: F401
_patch_p13_dump_naming_type_tag = plugin_misc._patch_p13_dump_naming_type_tag  # noqa: F401
_patch_p19_status_callback_bridge = plugin_misc._patch_p19_status_callback_bridge  # noqa: F401
_patch_p20_block_execute_code_permanent = plugin_misc._patch_p20_block_execute_code_permanent  # noqa: F401
_patch_p29_learn_slash_translate = plugin_misc._patch_p29_learn_slash_translate  # noqa: F401
_patch_p36_terminal_cwd_home = plugin_misc._patch_p36_terminal_cwd_home  # noqa: F401
_patch_p7_companion_proxy_route = plugin_cors._patch_p7_companion_proxy_route  # noqa: F401
_patch_p8_p9_cors = plugin_cors._patch_p8_p9_cors  # noqa: F401
_patch_p24_cors_allowlist = plugin_cors._patch_p24_cors_allowlist  # noqa: F401
_patch_p44_service_call_lean = plugin_service_lean._patch_p44_service_call_lean  # noqa: F401  (re-export · 见 plugin_service_lean.py)
plugin_core_tools = _import_sibling("plugin_core_tools")
_patch_p43_promote_catfish_core_tools = plugin_core_tools._patch_p43_promote_catfish_core_tools  # noqa: F401  (re-export · 见 plugin_core_tools.py)
plugin_deferred_tool_guard = _import_sibling("plugin_deferred_tool_guard")
_install_p45_deferred_tool_guard = plugin_deferred_tool_guard.install  # noqa: F401  (re-export · 见 plugin_deferred_tool_guard.py)
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

    # P45 (8/19 鸿波"固化 eis-login SKILL"连转六轮): P43 只提升了 11 个, 剩下
    # 67 个 catfish 工具仍然被 defer —— 那是**对的**。错的是它们被叫到时,
    # repair_tool_call 会在**可见**列表里模糊匹配 (cutoff=0.7), 而 catfish 工具名
    # 共享 28 字符前缀, 于是必定命中另一个工具:
    #     catfish_freeze_skill → catfish_browser_fill  0.800
    #     catfish_teach_start  → catfish_search_docs   0.872
    # 改名是就地改, 落库前发生, 所以事后看记录像"模型自己调错了"。
    # 详见 plugin_deferred_tool_guard.py 模块 docstring。
    _try_patch(_install_p45_deferred_tool_guard, "P45: 被 defer 工具的改名守卫装载失败 (跳过, 不阻塞 hermes 启动): %s")

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


# ── P5 / P6 / P11 ────────────────────────────────────────────────────────

# Companion 端 picker model_override 走 aiohttp middleware: 拦截 body.model 暂存
# 到 request, _create_agent wrap 时取出来覆盖 agent.model. 不动 hermes 内部 caller.

from aiohttp import web
import contextvars as _cv









# 保留旧名让别处 (P8/P9 注册) 引用兼容.
_picker_model_stash_middleware = _request_stash_middleware


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


# hermes 0.14+ plugin discovery 自动调 __init__.py 里的 install() 或类似 hook.
# 实际接入方式跟 catfish-memory 一样, 看 catfish/memory/plugin/__init__.py 模仿.
