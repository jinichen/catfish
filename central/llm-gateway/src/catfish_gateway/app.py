"""鲶鱼 LLM 网关的 FastAPI 入口。

做四件事：鉴权来访请求（P0 阶段用 dev token）、把 OpenAI 兼容调用路由到
配置好的上游模型、对员工侧暴露 /v1/catalog 用于选模型、以及记录请求
元数据（token 数、延迟、错误码等；对话内容永不入库）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any


# --- Load .env BEFORE anything else reads env vars ---
def _load_dotenv() -> Path | None:
    try:
        # Intentional lazy import: dotenv is optional; we fall back gracefully.
        from dotenv import load_dotenv  # noqa: PLC0415
    except ImportError:
        return None
    # Try cwd first, then project root (relative to this file)
    for p in [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent.parent / ".env",
    ]:
        if p.exists():
            load_dotenv(p, override=False)
            return p
    return None


_ENV_FILE_LOADED = _load_dotenv()

# 关键：在 import litellm 之前先做网络层屏蔽。
# litellm 的 aiohttp client 在 import 时就读 HTTPS_PROXY 等 env 缓存到内部 client，
# 之后 unset os.environ 也没用了。所以 precheck_and_setup 必须在 litellm 导入之前。
from .network import precheck_and_setup  # noqa: E402, PLC0415

_NETWORK_STATUS = precheck_and_setup()

import httpx  # noqa: E402
import litellm  # noqa: E402
from fastapi import Depends, FastAPI, Header, HTTPException, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402

from . import quota as _quota_module  # noqa: E402  五一 sprint 5/2 收尾: chat 后写 quota_events
# 5/26 BL-SESSION-META-PLUGIN-TAKEOVER: session_meta.tick() 砍, plugin 接管. import 删.
from .auth import (  # noqa: E402
    User,
    X_CATFISH_USER_HEADER,
    get_current_user,
    get_current_user_optional,
    is_service_principal,
    resolve_effective_user_email,
)
from .catalog import build_catalog  # noqa: E402
from . import model_store, provider_store  # noqa: E402
from .config import (  # noqa: E402
    Config,
    get_config,
    invalidate_config,
    load_raw_models,
)
# 5/23 BL-GATEWAY-DROP-LEGACY-SUMMARIZE: inject_employee_journal 5/20 BL-GATEWAY-
# MEMORY-REGISTRY-DELETE 时已 disable (registry.providers 永远空), 实际无 caller.
# 函数体也从 employee_journal.py 删, 该模块剩下 read_journal / append_to_journal
# 给 a2a_journal_hook (写 bob 本机 journal) 用. 老 import 移除.
from .fallback import with_fallback  # noqa: E402
from .gemini_guard import harden_for_gemini  # noqa: E402
from .identity_inject import (  # noqa: E402
    header_agent_prefs,
    request_skips_identity,
    inject_identity_if_needed,
)
# 5/26 砍: inject_session_history + session_facts 真死代码 (grep 验 0 处真 caller),
# catfish-memory hermes plugin 接管 memory 注入. import 删除.
from .metrics import log_request_metadata  # noqa: E402
from .multimodal_guard import route_to_vision_if_needed  # noqa: E402
from .multimodal_tool_unwrap import unwrap_tool_images  # noqa: E402
# 8/15: chat_completions 那条流水线里最大的三段搬到 chat_prepare.py。
# 拆之前用 AST 算过读写集 + 确认三块里一条 return 都没有 (有的话外提就是
# 静默改控制流)。详见那个文件的模块 docstring。
# 8/15: LLM 参数构造 / 响应解析那一层 (457 行, 零 yield) 搬到 llm_params.py。
# 全部 re-export —— tests/test_auto_sentinel.py 直接
# `from catfish_gateway.app import AUTO_MODEL_SENTINEL, _resolve_auto_sentinel`,
# 保住这类路径是 app.py:621 定的规矩。
from .llm_params import (  # noqa: E402,F401
    AUTO_MODEL_SENTINEL,
    _apply_max_tokens,
    _apply_prompt_cache_markers,
    _build_litellm_params,
    _check_context_usage,
    _compute_max_allowed_output_tokens,
    _extract_nested_usage,
    _http_code_for_upstream,
    _model_info_payload,
    _provider_supports_cache_marker,
    _raise_upstream_error,
    _resolve_auto_sentinel,
    _safe_float_env,
    _safe_int_env,
)
# _seed_and_migrate_models 8/15 搬去 gateway_startup (它唯一的调用方在那)。
# re-export 保住 tests/test_admin_models_api.py 的 A._seed_and_migrate_models()。
from .gateway_startup import (  # noqa: E402,F401
    _seed_and_migrate_models,
    run_startup,
)
from .chat_prepare import (  # noqa: E402
    enforce_quota,
    maybe_compress,
    prepare_messages,
)
# 5/23 BL-GATEWAY-DROP-LEGACY-SUMMARIZE: session_summarizer 整文件已删, plugin 接管
# (catfish-memory on_session_end 写 employee_journal). 老 import 移除.
# 5/26 P0 砍 skills_inject (中央扫员工本机 SKILL.md → 上游 LLM, 隐私违规).
# 详见 skills_loader.py 顶部 DEPRECATED 说明.
# 8/13 砍 skill_guard / stats_guard / feedback_inject / tool_capability_guard
# 四套 —— 见下方 chat_completions 里 reroute 段的说明。
from .model_handoff import apply_soft_handoff  # noqa: E402
from .tools_sanitizer import sanitize_tools  # noqa: E402

# Global setup

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("catfish.gateway")

# 5/5 鸿波报"日志文件不存在": 之前 gateway log 只到 stdout, 关掉 terminal 就丢了.
# 加 file handler 写 ~/Library/Logs/catfish/gateway.log (跟 macOS 习惯一致).
#: 文件日志的 handler, _setup_file_logging 建好后存这里。
#: _attach_file_handler_to_uvicorn() 要用它 —— uvicorn 的 logger 得等它自己
#: 配置完之后才能挂, 见那个函数。
_FILE_HANDLER = None


def _attach_file_handler_to_uvicorn() -> None:
    """把文件 handler 挂到 uvicorn 的三个 logger 上。**必须在 uvicorn 配置日志之后调。**

    # 病 (8/13 实测)

    `_setup_file_logging` 的注释一直写着「加到 root logger, 所有 catfish.* /
    uvicorn / litellm 日志都进文件」, 而 gateway.log 里 `HTTP/1.1` **0 条** ——
    访问日志从来没落过盘。

    两层原因, 第一层我先查到、修了, 结果没生效, 第二层才是真的:

      1. uvicorn 的 logger **不冒泡** (`uvicorn` / `uvicorn.access` 的
         `propagate: false`), 挂 root 收不到 → 得单独挂。
      2. **挂早了也没用**: `uvicorn.config.Config.configure_logging()` 调
         `logging.config.dictConfig(LOGGING_CONFIG)`, 而那份配置给
         `uvicorn.access` 指定了 `handlers: ["access"]` ——
         **dictConfig 会替换整个 handler 列表, 把先挂上的抹掉**。

    模块导入期挂 (`_setup_file_logging` 里那次) 发生在 uvicorn 配置之前,
    所以会被抹。真正生效的是 lifespan 启动时那次 —— 那时 dictConfig 已经跑完。

    两处都调是有意的:
      · 模块导入期那次 → 覆盖不经 uvicorn 的用法 (测试 / 直接 import app)
      · lifespan 那次   → 覆盖 uvicorn 跑起来的真实路径

    # 为什么不改 propagate / 不传 log_config=None

    改 `propagate = True` 会让每条访问日志同时走 uvicorn 自己的 stdout handler
    和 root handler, stdout 里打两遍 —— 6/30 P3.5.149 刚修过一次"log 每条写
    两遍"。传 `log_config=None` 则会连 uvicorn 的彩色 stdout 格式一起丢掉。
    只加 handler 是副作用最小的做法。

    # 代价 (为什么值得修)

    8/13 当天撞了两次:
      · gpt-5.6-luna 打到 8999 拿 404, 想事后查是哪个组件在调 —— 没有落盘的
        访问日志, 只能靠人贴终端输出
      · 想统计"哪些端点从没被调过"(P18 / P30 那类死路由), 数出来 39/39 全零,
        差点报成 39 个死端点 —— 实际是访问日志压根不在文件里

    网关一重启终端输出就没了, HTTP 层的所有证据都是易失的。
    """
    if _FILE_HANDLER is None:
        return
    from logging.handlers import RotatingFileHandler  # noqa: PLC0415
    target = os.path.abspath(_FILE_HANDLER.baseFilename)
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(name)
        # 防重 —— 这个函数会被调两次 (模块导入 + lifespan), 累加就是双写
        if any(
            isinstance(x, RotatingFileHandler)
            and os.path.abspath(x.baseFilename) == target
            for x in lg.handlers
        ):
            continue
        lg.addHandler(_FILE_HANDLER)


# 用 RotatingFileHandler 防无限增长 — 单文件 10MB, 保留 5 个轮替.
# CATFISH_LOG_FILE env 可换路径; CATFISH_LOG_FILE=- 表示禁用文件日志 (CI 用).
def _setup_file_logging() -> None:
    global _FILE_HANDLER
    log_file = os.environ.get("CATFISH_LOG_FILE")
    if log_file == "-":
        return  # 显式禁用
    if not log_file:
        # 合规说明: 这里的 home 是**网关进程自己**的 home, 写的是网关自己的
        # 日志 (~/Library/Logs/catfish/gateway.log)。中央端不许读的是"员工的"
        # ~/.catfish / ~/.hermes 数据; 自己的进程往自己的 home 写日志不在此列。
        # 异机部署时这会落到服务器的 home, 正是想要的行为。
        home = os.environ.get("HOME") or os.path.expanduser("~")  # noqa: BOUNDARY
        log_file = os.path.join(home, "Library", "Logs", "catfish", "gateway.log")
    try:
        from logging.handlers import RotatingFileHandler  # noqa: PLC0415
        # P3.5.149 (6/30 鸿波 catch "log 每条写两遍"): 双 import 导致 addHandler 累加.
        # 启动序列: `python -m catfish_gateway.app` 把模块当 __main__ 执行触发顶层
        # _setup_file_logging() 第一次, 然后 uvicorn.run("catfish_gateway.app:app", ...)
        # 用 import string 再 import 模块 (Python 把 __main__ 和 catfish_gateway.app
        # 视为两个不同 module 对象), 触发顶层 _setup_file_logging() 第二次, 两个
        # RotatingFileHandler 都被 addHandler 到 root logger → 每条 log 写两遍.
        # 修法: idempotent guard, 检查 root logger handlers 已经有同款 file path
        # 真 RotatingFileHandler 就 skip.
        root_logger = logging.getLogger()
        for existing in root_logger.handlers:
            if isinstance(existing, RotatingFileHandler) and \
                    os.path.abspath(existing.baseFilename) == os.path.abspath(log_file):
                # 已经装过 → **不重复 addHandler**, 但绝不能空手 return。
                #
                # ⚠ 8/13: 空手 return 正是访问日志第二次没修好的原因。
                #
                # 上面那段注释已经把机制写清楚了 —— 模块被 import 两次, 是**两个
                # 不同的 module 对象**, 各有一套模块级变量。而 uvicorn 真正拿去
                # 跑的 `app` / `lifespan` 属于**第二个**对象:
                #
                #     __main__          _setup() 走完整路径, _FILE_HANDLER = h  ✓
                #     catfish_gateway.app   走到这里 return, _FILE_HANDLER 还是 None ✗
                #                           ↑ uvicorn 用的是这个
                #
                # 于是 lifespan 里那句 _attach_file_handler_to_uvicorn() 一进门就
                # 撞上 `if _FILE_HANDLER is None: return`, **什么都没干**,
                # gateway.log 里 HTTP/1.1 依旧 0 条。
                #
                # 修法: 把已存在的那个 handler 认下来, 再挂 uvicorn。
                # 复现脚本验过: 空手 return → 0 条; 认下来 → 1 条且不双写。
                _FILE_HANDLER = existing
                _attach_file_handler_to_uvicorn()
                return
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        h = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5)
        h.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        # 加到 root logger — catfish.* / litellm 这些都会冒泡上来。
        root_logger.addHandler(h)

        # 记下来给 _attach_file_handler_to_uvicorn() 用 —— 见那个函数的注释,
        # uvicorn 的 logger 必须**等它配置完之后**再挂。
        _FILE_HANDLER = h
        _attach_file_handler_to_uvicorn()

        logger.info("file logging → %s (10MB × 5 rotation, 含 uvicorn access)", log_file)
    except Exception as e:
        logger.warning("file logging 启用失败 (继续仅 stdout): %s", e)


_setup_file_logging()

# Silence LiteLLM's verbose mode -- we don't want it printing prompts.
litellm.set_verbose = False
litellm.drop_params = True




@asynccontextmanager
async def lifespan(app: FastAPI):
    # 8/15: 216 行启动接线搬到 gateway_startup.run_startup。
    # 关闭段留在这里 —— 只有 47 行, 且要 await 启动起的那两个后台任务;
    # 那两个 task 就是这两半之间**唯一**的接口 (拆前扫过: 启动段无 global、
    # 无属性写, 关闭段只用得上这两个局部)。
    refresh_task, archive_summary_task = await run_startup(app, _ENV_FILE_LOADED)

    # BL-RECMODE-NO-AUTO-DELETE (5/25 鸿波): 撤掉 cleanup daemon.
    #
    # 旧设计 (V2 #67, 5/15): 启动时清 14 天前 recordings + 后台 24h daemon
    # 自动删. 当时理由"截图含业务数据不能永久留".
    #
    # 5/25 哲学修正 — 录屏 100% 在员工本机 ~/.catfish/recordings/, 中央 0  # noqa: BOUNDARY
    # 字节. 既然中央不管, catfish 后台代码也不该后台自动删用户本机文件.
    # 这跟 catfish 给员工的 talking point "你电脑你做主, 中央不存不管"
    # 一致, 不再有"嘴上不上传 + 暗中删你硬盘" 的撕裂感.
    #
    # cleanup_old_recordings 函数留作 utility — 员工通过 Dashboard "我的录屏"
    # 区点"清理" 显式触发, 或 admin 跑 catfish-recordings prune CLI.
    # 详见 docs/EMPLOYEE-PRIVACY-COMMITMENT.md (TODO).
    #
    # 老的 _is_kept_forever 标志保留, 但语义反转: 默认 = 永久留 (不删),
    # _keep_forever 字段成为 cosmetic 兼容老元数据.

    yield

    if archive_summary_task is not None:
        archive_summary_task.cancel()
        try:
            await archive_summary_task
        except asyncio.CancelledError:
            pass

    refresh_task.cancel()
    try:
        await refresh_task
    except asyncio.CancelledError:
        pass

    # BL-D3 (5/9) Phase 1 收尾: 关 mcp-registry httpx client
    try:
        await app.state.mcp_registry_client.aclose()
    except Exception as e:
        logger.debug("mcp_registry_client aclose: %s", e)

    # BL-D2 (5/10): 关 skills-hub httpx client
    try:
        await app.state.skills_hub_client.aclose()
    except Exception as e:
        logger.debug("skills_hub_client aclose: %s", e)

    # P3.3.18 (6/10): 关 wiki-hub httpx client
    try:
        await app.state.wiki_hub_client.aclose()
    except Exception as e:
        logger.debug("wiki_hub_client aclose: %s", e)

    # BL-F13 (5/4): 清 LiteLLM 内部 aiohttp / httpx client, 减少 "Unclosed client session"
    # warning. LiteLLM 1.50+ 用 httpx 主路径但仍持有少量 aiohttp module-level client,
    # uvicorn ctrl+c 时这些没 close, asyncio 报 ERROR. 不致命但污染 log.
    # best-effort: 多个属性名兼容 LiteLLM 版本.
    try:
        for attr in ("module_level_aclient", "module_level_client",
                     "module_level_async_client", "in_memory_llm_clients_cache"):
            client = getattr(litellm, attr, None)
            if client is None:
                continue
            close = getattr(client, "aclose", None) or getattr(client, "close", None)
            if close is not None:
                try:
                    result = close()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    pass  # 个别 client cleanup 失败不影响其他
            # cache-like 对象
            clear = getattr(client, "clear", None)
            if clear is not None:
                try:
                    clear()
                except Exception:
                    pass
    except Exception as e:
        logger.debug("LiteLLM client cleanup (best-effort): %s", e)

    logger.info("catfish-gateway shutting down")


app = FastAPI(
    title="Catfish LLM Gateway",
    description="Company LLM routing with SSO -- part of the Catfish platform",
    version="0.1.0",
    lifespan=lifespan,
)

# 8/1 按 CLAUDE.md 军规 §1: 模型配置管理那一组接口 (约 400 行) 搬去
# admin_models_router.py —— app.py 是 3838 行, 红线的 4.8 倍, 而这一轮
# 又往里加了 141 行。app.py 本身仍远超红线, 需要一轮专门的拆分。
#
# 军规 §3 (re-export): 路由要挂在同一个 app 上, 没法用 `from X import *`,
# 所以改用显式 register。同时在下面 re-export 那几个 helper, 保住
# `from catfish_gateway.app import _check_api_key_env` 这类 monkeypatch 路径。
from .admin_models_router import (  # noqa: E402
    register_model_admin_routes,
)
from .admin_providers_router import (  # noqa: E402
    register_provider_admin_routes,
)

from .audit_router import register_audit_routes  # noqa: E402
from .quota_router import register_quota_routes  # noqa: E402
from .misc_routes import register_misc_routes  # noqa: E402

register_model_admin_routes(app)
register_provider_admin_routes(app)
# 8/15: 审计 (6 个查询路由 + _require_admin) 和配额 (13 个管理路由 +
# /api/inflight + _require_sysadmin + 5 个 body 模型) 搬到各自的 router,
# 兑现 8/1 那段注释里说的"需要一轮专门的拆分"。
register_audit_routes(app)
register_quota_routes(app)
# 8/15: 目录/模型/embeddings/身份/边缘工具 等零散路由 (346 行) 搬到 misc_routes。
register_misc_routes(app)

# CORS：Tauri webview / Companion App 跨域调 gateway 必须放过 OPTIONS preflight。
# Dev 阶段开放所有源；P1 上线后改成白名单：tauri://localhost / companion 域名 / 公司 SaaS 域名。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # 不带 cookie，dev token 走 Authorization 头
    allow_methods=["*"],      # 含 OPTIONS / POST / GET 等
    allow_headers=["*"],      # 含 Authorization / Content-Type / X-Catfish-* 等
)


# A2A /a2a/ask SSE endpoint 5/26 砍 — Plan D Federation 整套停 (0 真客户).
# 详见 docs/HERMES-013-ALIGN.md A2A 段 + git history (恢复用 git log --follow a2a_server.py).

# BL-D3 (5/9) Phase 1 收尾: mcp-registry 反向代理 router.
# Companion 走单一 origin (gateway) 调 /v1/mcp/*, gateway 透传到 mcp-registry
# 上游 (yaml mcp_registry.upstream_url, 默认 :8996, 跟 skills-hub 8997 错开).
# 注入 X-Catfish-User-Sub /
# Dept / Role header 让上游做部门权限过滤. dept 由 gateway 从 JWT 抽出, 不让
# Companion 自己改 (防绕权限).
try:
    from .mcp_registry_proxy import router as mcp_registry_router  # noqa: PLC0415
    app.include_router(mcp_registry_router)
    logger.info("mcp_registry_proxy: /v1/mcp/* 反代已挂载")
except Exception as e:
    logger.warning("mcp_registry_proxy 挂载失败 (BL-D3 反代不可用): %s", e)

# BL-D2 (5/10): skills-hub 反代, /v1/hub/* → :8997. 注入 X-Catfish-User-* header.
try:
    from .skills_hub_proxy import router as skills_hub_router  # noqa: PLC0415
    app.include_router(skills_hub_router)
    logger.info("skills_hub_proxy: /v1/hub/* 反代已挂载")
except Exception as e:
    logger.warning("skills_hub_proxy 挂载失败 (BL-D2 反代不可用): %s", e)

# P3.3.18 (6/10): wiki-hub 反代, /v1/wiki/* → :8994. 部门 wiki publish.
# manifesto 公理 2 例外 (员工主动 push) — 详见 CATFISH-CENTRAL-MANIFESTO.md.
try:
    from .wiki_hub_proxy import router as wiki_hub_router  # noqa: PLC0415
    app.include_router(wiki_hub_router)
    logger.info("wiki_hub_proxy: /v1/wiki/* 反代已挂载 (P3.3.18)")
except Exception as e:
    logger.warning("wiki_hub_proxy 挂载失败 (P3.3.18 反代不可用): %s", e)

# BL-ARCH1 P1 (5/10): identity-server admin 反代, /api/admin/* → :8998
try:
    from .admin_proxy import router as admin_router  # noqa: PLC0415
    app.include_router(admin_router)
    logger.info("admin_proxy: /api/admin/* 反代已挂载")
except Exception as e:
    logger.warning("admin_proxy 挂载失败: %s", e)

# BL-Q3-FACT P0 MVP (5/10): 事实补丁系统 — 政策变更 → diff → 找受影响 skill → 生成 patch
# 数据落 ~/.catfish/facts/<id>/ (jsonl 临时, Q3 P1 迁 PG).  # noqa: BOUNDARY
try:
    from .facts_router import router as facts_router  # noqa: PLC0415
    app.include_router(facts_router)
    logger.info("facts_router: /api/facts/* 已挂载 (BL-Q3-FACT P0 MVP)")
except Exception as e:
    logger.warning("facts_router 挂载失败: %s", e)

# 6/7 BL-MANIFESTO-ADVISORY-PHASE1: advisory feed (pull-based, manifesto 公理 4).
# Server publish + 客户端自己 pull 决定怎么处理, 不 push, 不查员工设备状态.
# Spec: docs/ADVISORY-FEED-SPEC.md
try:
    from .advisory_router import router as advisory_router  # noqa: PLC0415
    app.include_router(advisory_router)
    logger.info("advisory_router: /advisory/feed.json 已挂载 (manifesto Phase 1)")
except Exception as e:
    logger.warning("advisory_router 挂载失败: %s", e)

# 6/7 BL-CATFISH-MANIFESTO clean-up: /api/tool-archives/* router 整套撤.
# 历史:
#   - 5/11 BL-Q3-ARCHIVE v1: gateway 端 PG archive HTTP API
#   - 5/22 BL-CENTRAL-EDGE-TOOL-ARCHIVE: edge 端接管, catfish_read_tool_archive
#     从 HTTP gateway 改 tool_archive_local.py 本机读 sqlite + jsonl
#   - 5/26 BL-BOUNDARY: db.py PG 路径砍, content 全员工本机
#   - 6/7 (现在): HTTP router 死代码 (没员工 caller), 整套不再 mount
#
# 跟 catfish-central-manifesto 公理 4 "API surface 物理无能" 一致 — 中央服务
# 物理不提供"读员工 tool 调用结果"的 API. 哪怕 admin 也调不到.
#
# 死代码 file 留 git history (router.py / db.py / reader.py / summary_worker.py
# / archiver.py PG fallback), 后续 BL-MANIFESTO-CLEAN-DEAD 整套 rm.


# /a2a/internal/ask endpoint 5/26 砍 — Plan D Federation 整套停, tool-bridge
# 的 catfish_a2a_ask tool 同批砍.


# Health






# Quota — 五一 sprint 5/3, BL-D9
#
# 给 Companion Dashboard 的 QuotaCard 用. 返当前用户三维 quota:
#   - per-user 1 minute
#   - per-user 1 day
#   - per-department 1 day (没设部门或无部门 quota → limit=0=不限)
#
# limit=0 在 Companion 侧渲染成 "不限".




# /api/me — 当前用户信息 (Companion 用来按角色 conditional render Dashboard)
#
# 五一 sprint 5/2 RBAC.




# BL-CENTRAL-WEB-PURGE-USERDATA (5/17 鸿波): 砍 /api/sessions/me/* 3 个端点.
#
# 老逻辑: 中央 web /sessions 页面调这些端点 → gateway 读员工本机
# ~/.hermes/state.db → 返完整 chat messages 给 web. 违反 BL-CENTRAL-EDGE-BOUNDARY  # noqa: BOUNDARY
# (中央端不碰用户数据). Companion 自己有这功能.
#
# 砍 3 个: GET /api/sessions/me, /api/sessions/me/search, /api/sessions/me/{id}
# 配套 src/catfish_gateway/sessions_browse.py 不再被任何 endpoint 调用, 可独立
# 移除 (BL ticket follow-up). 现在保留 module 但 dead code, lint allowlist 可
# 把 sessions_browse.py 标 dead.


# ── /api/tasks/me — Multi-Agent Kanban 单员工任务看板 (BL-HERMES013-RED-2 5/13) ──
#
# scope 1 (鸿波 5/13 22:35 拍板): 单员工本地任务聚合, 跨员工跨设备等 BL-RBAC sprint
# 后做 task_manager 中心 DB 持久化再扩.
#
# 数据源:
#  - ~/.catfish/tasks.jsonl              tool-bridge task_manager (catfish_run_task)  # noqa: BOUNDARY
#  - ~/.catfish/a2a_notifications.jsonl  a2a 收件 (BL-FED2.6, 5/26 砍)  # noqa: BOUNDARY
#
# 跟 hermes 0.13 自带 Multi-Agent Kanban API 不冲突 — 5/15-5/18 接 hermes Kanban
# 的话当一个 tile 嵌进来 (scope 2 补).

# ── /api/learn/* — BL-LEARN-RECMODE 录屏+语音教学引擎 (5/14 v0 骨架) ──
#
# 设计文档: docs/LEARN-RECMODE-DESIGN.md
# 流程: Companion 点 🎙 RecMode → POST /start → 用户操作 Catfish Chrome (CDP
# listener 后台抓 events + 截图) → 用户点 ✅ 完成 → POST /stop → 后端综合
# (events + 语音 + 截图 → catfish-private-main) → SKILL.md + main.py 落档.
#
# v0 骨架: start/stop/list 通, 真 CDP 连接 + 综合 aggregator 等 5/26 sprint
# 真做时填. v0 已能让 Companion 端走通 UI 状态机.


# P3.5.45 (鸿波 6/20 拍 'A: 砍录屏 HTTP path'): 11 个 /api/learn/* endpoint
# 全砍 (619-1113). 老链路 Companion → HTTP → hermes 8642 P7 catch-all → HTTP →
# gateway → unix sock → tool-bridge, 4 跳 + 2 HTTP 中转 + token 验签 cascade fail.
# 新链路 Companion (Tauri) → unix sock → tool-bridge 直调, 1 跳 0 token.
# 跟 chat 路径彻底解耦, 录屏永不再撞 SERVICE_TOKEN 过期.
# 前端 src/lib/recmode.ts 改 invoke("recmode_rpc"), Rust commands/recmode.rs
# 复用 services/tool_bridge_rpc.rs 公共 helper 调 unix sock.


# BL-CENTRAL-WEB-PURGE-USERDATA (5/17 鸿波): 砍 /api/tasks/me 端点.
#
# 老逻辑: 中央 web /看板 页面调此端点 → gateway 读员工本机
# ~/.catfish/tasks.jsonl → 返任务标题/状态给 web. 违反 BL-CENTRAL-EDGE-BOUNDARY.  # noqa: BOUNDARY
# Companion 自己有任务看板. 配套 src/catfish_gateway/tasks_browse.py 不再被调用,
# 但模块保留待 follow-up ticket 清理.


# /api/quota/department/{dept} — manager / admin 看本部门 quota 聚合
#
# 包含: 部门日 quota 用量 + 限额 + top N 员工 token 用量.
# RBAC: admin 全权, manager 限 managed_departments.




# ── BL-EDGE-TOOL-KEY (5/24 鸿波): hermes 边缘工具中央派发 backend key ──
#
# 让 catfish-cli refresh-hermes 拉这个 endpoint, 把 Tavily/Firecrawl 等
# 第三方 key 从中央 .env 同步到员工 ~/.hermes/.env, admin 改 key 50 台机器  # noqa: BOUNDARY
# 下次 refresh 自动拿新值, 不用 ssh 全跑一遍.
#
# 详见 catfish_gateway/edge_tool_config.py docstring.








# /api/audit/me — 员工自查 "中央到底存了我啥".
#
# BL-EMPLOYEE-PRIVACY-VERIFICATION (#79, 5/25): Companion 隐私 tab + privacy-audit
# CLI 都接这条. 员工不需要任何 RBAC 角色, 谁登录返谁的数据.
#
# Privacy contract (写死, 永远不回退):
#   1. 返的字段全是 metadata (count / token / model / 时间戳), 没有 prompt / response
#   2. 员工只能拿到自己 sub 的数据 (filter 在 quota.audit_summary_user_since 里, 不走 query 参数 — 防 IDOR)
#   3. first_seen_ts / last_seen_ts 不局限 cutoff_ms, 让员工知道"中央存了我多久"






# /api/audit/department/{dept} — manager / admin 看本部门 audit 聚合
#
# 包含: 总请求数 / 总 token / 模型分布 / top 员工 (匿名化看部门级).
# RBAC: admin 全权, manager 限 managed_departments.




# /api/quota/department/{dept} PUT — manager / admin 改本部门 quota
#
# 五一 sprint 5/2 收尾 RBAC: 写 quotas.yaml 的 overrides.departments.<dept>.tokens_per_day
# Body: { "tokens_per_day": int }   (0 表示不限)
# 改完 gateway 下一次请求自动加载新 yaml (load_quota_config 每次重读, 无 cache).


from pydantic import BaseModel as _BaseModel  # 局部 import 防顶层污染






# /api/quota/global + /api/audit/global — admin 全员 / 全部门 / 全模型聚合
#
# RBAC: 严格 admin only. manager 看不到全局, 只看 managed_departments.




# ── P3.5.93 (6/23 鸿波): /admin/quota web 编辑 UI 后端 ────
#
# 10 个 endpoint, sysadmin only. 改的全是 quotas.yaml, gateway hot reload
# (load_quota_config 无 cache, 每请求重读). RBAC: sysadmin, 比 advisory 一致 —
# 改全员 quota 比改部门 RBAC 影响更大, 只有 sysadmin 能动.
#
# 跟老 /api/quota/department/{dept} (BL-D9 5/2) 关系:
#   - 老 endpoint 仍保留 (内部 update_department_quota → put_dept_override 等价)
#   - 新 endpoint 命名空间 /api/admin/quota/* 跟其他 admin 一致 (/api/admin/advisory)
#   - AccessPage 的部门 quota 编辑已 6 周来不生效 (写 identity-server 但 gateway
#     不读), P3.5.93 一并砍掉 — 部门 quota 收口到 /admin/quota 走 yaml.








































# /api/dev/users — 列出 dev 测试账号 (Companion 切换器用)
#
# 五一 sprint 5/2. 仅 dev 模式 (CATFISH_ENV != prod) 启用. 生产环境 404.
# 不需要 auth — 列表本身就是为了让没登录的人选账号. 包含 token 字段, dev 模式安全.




# /api/proactive/starter — 主动闲聊 BL-E13 C-MVP (五一 sprint 5/2 收尾)
#
# Companion 调这个拿一句 LLM 生成的 starter, 显示在 Dashboard 卡 / macOS 通知里.


# 5/26 BL-PROACTIVE-DECOUPLE v2 (CORS 修):
# 老 5/26 早版本用 header (X-Catfish-Journal-Tail-B64 + X-Catfish-Last-Model)
# 透传, 但 Companion 走 hermes proxy CORS allowlist 不含这俩自定义 header,
# 浏览器层 preflight 直接 block (TypeError: Load failed). 改 body 字段透传:
#   - /api/proactive/starter: GET → POST, body 含 journal_tail + last_model
#   - /api/proactive/contextual: 仍 POST, 加 last_model body 字段
# body 走 Content-Type: application/json, 标准 CORS allowlist 无问题.
# 老 GET caller (5/26 早版本 Companion) 走 405, 不致命 — caller 立即 fallback.






# Capability-probe stubs
#
# Clients like Hermes probe well-known paths to figure out what kind of
# server they're talking to (Ollama, llama.cpp, OpenAI, …). We're
# OpenAI-compatible only -- returning minimal 200 payloads stops the probe
# noise without misleading the client into trying Ollama-native endpoints.








# Model listing
















# Helpers


def _resolve_model(config: Config, name: str):
    model = config.get_model(name)
    if not model:
        raise HTTPException(status_code=404, detail=f"model not found: {name}")
    if not model.upstream.is_available:
        raise HTTPException(
            status_code=503,
            detail=(
                f"model '{name}' is configured but unavailable: "
                f"env variable {model.upstream.api_key_env} is not set"
            ),
        )
    return model


















def _pick_cache_read_from_streaming_usage(usage: dict, current: int) -> int:
    """6/2 BL-CACHE-AUDIT-PROVIDER-FIELDS: streaming chunk 里抓 cache_read.

    优先级:
      1. cache_read_input_tokens (Anthropic 顶层, 旧字段)
      2. prompt_tokens_details.cached_tokens (OpenAI/DeepSeek/DashScope/Gemini LiteLLM 统一)
    任一拿到非 0 就用, 否则保留 current.
    """
    v = usage.get("cache_read_input_tokens")
    if v:
        return int(v)
    details = usage.get("prompt_tokens_details") or {}
    if isinstance(details, dict):
        v2 = details.get("cached_tokens")
        if v2:
            return int(v2)
    return current






# BL-FIX23-L8-overflow-fix (5/13 鸿波"谎报生成"): context > window 时上游 silent
# truncate 输入, LLM 看不到完整 tool result, 反复"谎报生成成功". retry 让 prompt
# 越涨越多形成死循环. 超阈值时强制 break + 推 SSE 友好错误.
_CONTEXT_OVERFLOW_RETRY_THRESHOLD = 0.95  # ≥95% 视作即将/已超, 不再 retry


def _is_context_overflowed(model, prompt_tokens: int) -> bool:
    """prompt_tokens / context_window ≥ 95% → True (上游可能 silent truncate).

    没 model.context_window 或 prompt_tokens=0 → False (退化为不判, 走原 retry 逻辑).
    """
    cw = getattr(model, "context_window", 0) or 0
    if cw <= 0 or prompt_tokens <= 0:
        return False
    return (prompt_tokens / cw) >= _CONTEXT_OVERFLOW_RETRY_THRESHOLD


def _context_overflow_friendly_error(model, prompt_tokens: int) -> str:
    """给员工的 SSE error 提示 — 告诉他"会话太长, 怎么解."""
    cw = getattr(model, "context_window", 0) or 0
    pct = int((prompt_tokens / cw) * 100) if cw > 0 else 0
    return (
        f"⚠️ 会话上下文已用 {pct}% (>{int(_CONTEXT_OVERFLOW_RETRY_THRESHOLD*100)}% 阈值, "
        f"{prompt_tokens:,} / {cw:,} tokens). 上游 LLM 可能截断输入, 看不到完整工具结果. "
        f"鲶鱼之前的 execute_code 可能真做了 (文件已写盘), 但它看不到 tool 结果就反复重做.\n\n"
        f"建议:\n"
        f"  - **新建会话** (Cmd+N / 左侧 + 新对话): 上下文重置, 已写文件不丢\n"
        f"  - 切大上下文模型: /model catfish-public-gemini-pro (2M tokens)\n"
        f"  - /compress 压缩当前会话保留主题"
    )




# BL-ADVISOR-UPSTREAM-ERROR-AS-CONTENT (6/1 鸿波): 已知上游错误关键词
# (catfish-private-main / LiteLLM 私有 LLM streaming fail 时返 200 + content=这些).
# Source: ~/.hermes/logs/gateway.log 实测 "API call failed after 3 retries: An error  # noqa: BOUNDARY
# occurred during streaming" 是 LiteLLM 标准 retry exhausted 错误.
_UPSTREAM_ERROR_AS_CONTENT_PATTERNS = (
    "API call failed after",
    "after 3 retries",
    "retries exhausted",
    "An error occurred during streaming",
    "Max retries (3) exhausted",
)


def _extract_content_text(response_dict: dict) -> str:
    """从 chat completion response dict 抽 message.content 文本. 失败返空串."""
    try:
        choices = response_dict.get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        return content if isinstance(content, str) else ""
    except Exception:
        return ""


def _looks_like_upstream_error_as_content(response_dict: dict) -> bool:
    """detect 上游 LiteLLM-based LLM 服务把内部 retry 失败 message 当作 LLM 输出返回.

    保守判定: content 长度 < 500 字符 + 匹配已知错误关键词. 真 LLM 输出长篇复读
    错误词概率极低, 但短 + 关键词 = 上游真错的高置信号.
    """
    content = _extract_content_text(response_dict)
    if not content or len(content) > 500:
        return False
    return any(pat in content for pat in _UPSTREAM_ERROR_AS_CONTENT_PATTERNS)




# Chat completions

#: 上游 chunk 间隔超过这个秒数时, 发 SSE keepalive comment 防客户端 / 中间代理 timeout.
#: 私有 LLM tool calling 思考阶段经常 30-60s 没 chunk, 不发心跳前端会断开.
#: SSE comment 行 (": keepalive\n\n") 任何 SSE 解析器都忽略, 不影响数据语义.
_KEEPALIVE_INTERVAL_SECS = 30


async def _stream_with_keepalive(iterator, interval_secs: float = _KEEPALIVE_INTERVAL_SECS):
    """Wrap async iterator: chunk 间隔 > interval_secs 时 yield keepalive marker.

    Yields:
        - 原 chunk 对象 (上游来的 ChatCompletionChunk)
        - 字符串 "__keepalive__" (上游慢, 该发心跳了; caller 自己翻译成 SSE comment)

    用 asyncio.wait_for 给每次 __anext__ 加超时, 不影响最终拿到的总数据.
    """
    while True:
        try:
            chunk = await asyncio.wait_for(
                iterator.__anext__(), timeout=interval_secs
            )
            yield chunk
        except TimeoutError:
            yield "__keepalive__"
        except StopAsyncIteration:
            return


# ─── BL-FIX23 plan-only retry 系列 全部 DELETED (5/13 鸿波"乱七八糟") ────
# 历史 L5 (5/9) → L9 (5/13) 在 gateway 层堆 retry+灌 hint+物理强迫 tool_choice
# guard, 副作用 > 收益 (overflow v1 误杀 tool_calls / hint 让 context 越涨越多).
# 鸿波 5/13 拍板删干净 — gateway 只干净转发, LLM stop 就 stop, 客户端自己跟它说"继续".
# LLM 不调工具属模型层/prompt 层问题, 不是 gateway 该治.
#
# 已删: _PLAN_ONLY_*_KEYWORDS, _TASK_COMPLETE_KEYWORDS, _PLAN_ONLY_HARD_HINT,
# _MAX_PLAN_ONLY_RETRIES_*, _REPETITIVE_JACCARD_THRESHOLD, _jaccard_bigram,
# _assistant_history_too_repetitive, _last_role_is_tool_result,
# _is_plan_only_content, _has_completion_claim, _has_future_intent,
# _is_task_complete_claim, _last_user_message_is_feedback.
# 保留 _is_context_overflowed/_context_overflow_friendly_error (仅 metric/告警用,
# 不再驱动 break).


# 8/13 删掉 `_fake_sse_response` (44 行)。
#
# 它是 5/11 BL-HERMES013-3 给 client-side slash 命令 (/goal 这种) 用的 ——
# 不走 LLM, gateway 直接拦截并伪造一条 SSE 流。5/26 `ea5fb35` 砍掉 session_goals
# 整套 (hermes 0.14 原生 /goal + /subgoal 替代) 之后**唯一的调用点跟着没了**,
# 函数本体留了下来, 死了 79 天。
#
# 删之前查证过: 全仓只有它自己的定义一处, 无 getattr / globals() 动态取名,
# docs/HERMES-013-ALIGN.md 里那行本来就已经划掉 (~~...~~)。
# 要恢复看 ea5fb35 之前的版本。


async def _stream_chat_completion(
    body: dict,
    *,
    user_sub: str,
    user_dept: str,  # 五一 sprint 5/2 RBAC: quota.record_usage 需要部门
    model_name: str,
    model,
    security_concern: str | None = None,
    is_internal: bool = False,  # BL-F17 (5/5): internal 调用跳 record_usage
    source_hint: str = "unknown",  # BL-RBAC-DAY4-HARDENING (5/17): X-Catfish-Source audit
    # BL-ABORT-PROPAGATE (7/23 达华 POC): 传 request 进来 · 循环里定期 check
    # request.is_disconnected() · 客户 abort 时主动 aclose upstream iterator ·
    # 省 token / 释 vLLM slot. 老行为 · client abort 后 gateway 仍继续烧到本轮
    # finish · 只在下次 SSE write 时 broken pipe 才感知. None = 兼容老 caller.
    request: Request | None = None,
    # BL-LEAN-SESSION teaching_mode 参数 已 DELETED (5/13 鸿波"全部清干净"):
    # 之前给 BL-FIX23 retry 的 _lean gate 用. retry 删了它就 dead arg.
    # _lean 控制 SOUL inject pipeline 那部分仍在 chat_completions 里 (1651), 不影响.
) -> AsyncIterator[str]:
    """SSE async generator for streaming chat completions, with fallback chain.

    Fallback 时机:
        在 "acompletion + 首 chunk" 阶段失败 → 切下一个模型重试
        已开始流之后挂掉 → 没法切, 直接转 SSE error 返回 (中途换模型会乱掉客户端解析)

    BL-FIX23 L5 (5/9): plan-only retry — finish_reason=stop + 没 tool_call +
    plan-only content + 上一条 user 是反馈 → 内部起新 acompletion 强制重发,
    最多 2 次. 客户端无感, 看着像鲶鱼自己续写.
    """
    start = time.time()
    ttft_ms: float | None = None  # 首 token / 首 chunk 延迟, fallback 后会被覆盖成实际值
    prompt_tokens = 0
    completion_tokens = 0
    # BL-CACHE-AUDIT (5/17): streaming Anthropic prompt cache 字段, 末尾 chunk
    # 的 usage 里抓. 不存在或非 Anthropic 时永远 0.
    _stream_cache_creation = 0
    _stream_cache_read = 0
    status_str = "ok"
    err = ""
    used_model = model
    config: Config = get_config()
    attempts_log: list[str] = []

    # BL-ABORT-PROPAGATE (7/23 达华 POC): pre-declare · 若 fallback 阶段 raise ·
    # finally 里 iterator 未 assign 会 UnboundLocalError. None 兜底.
    iterator: Any = None

    # BL-HERMES013-4 (5/12): in-flight tracking — 流开始 mark, 结束 chain
    # 跑 InflightCleanupTransform unlink. gateway 真崩 (SIGKILL) 文件留下,
    # 启动时 reap_interrupted 写一条 'interrupted_resumed' audit 替补.
    import uuid as _uuid  # noqa: PLC0415

    from . import inflight_streams  # noqa: PLC0415
    request_id = _uuid.uuid4().hex
    inflight_streams.mark_started(
        request_id,
        user=user_sub,
        model=model_name,
        message_count=len(body.get("messages") or []),
        extra={"is_internal": is_internal},
    )

    try:
        # 用 fallback 链找一个能拿到首 chunk 的模型
        async def _start_stream(candidate_model):
            params = _build_litellm_params(body, candidate_model)
            try:
                response = await litellm.acompletion(**params)
            except Exception as _e:
                # 8/10: 上游返"什么都没说的 400"时把请求**形状**存一份 (不含正文)。
                # 内网 Qwen3-VL 的错误是
                #   error: code = 400 reason =  message =  metadata = map[] cause = <nil>
                # 两轮八个探针全没复现, 继续猜变量的成本已经高过抓真身。
                # 有话说的错误 (余额不足 / 超上下文) 不会触发, 见 request_shape_dump。
                from .request_shape_dump import dump_on_opaque_error  # noqa: PLC0415

                dump_on_opaque_error(params, candidate_model.name, _e)
                raise
            iterator = response.__aiter__()
            # 拉首 chunk —— 这是 429 / 503 最容易抛错的地方
            try:
                first = await iterator.__anext__()
            except StopAsyncIteration:
                first = None
            return iterator, first

        # BL-FALLBACK-PROMPT-CAP (5/14): 算 prompt 估算传给 with_fallback, 大 prompt
        # 失败时跳过公网 candidate (公网更慢更贵, 不该兜底).
        # BL-TOKEN-COUNTER-LITELLM (5/15): 传 model 名让 estimate 用真 tokenizer.
        # BL-TOOLS-IN-ESTIMATE (5/15 22:00): 加 tools schema 估算, 上游也算 tools.
        from .fallback import estimate_prompt_tokens  # noqa: PLC0415
        prompt_estimate = estimate_prompt_tokens(
            body.get("messages") or [],
            model=model.upstream.model,
            tools=body.get("tools"),
        )
        # 6/7 BL-GROQ-TPM-PREFLIGHT: 检查上游 rate_limits.tpm, 超就 raise 413 +
        # 友好中文 + 替代 model. 拦下来不让员工看 stack trace.
        from .rate_limit_preflight import preflight_check_rate_limits  # noqa: PLC0415
        preflight_check_rate_limits(model, prompt_estimate, config=config)

        # 8/10: 压完还是装不下, 就别发了 —— 发出去只会换回一个 reason/message
        # 全空的 400, 员工看到「未知错误」, 谁都查不出原因 (今天为这个 400 做了
        # 两轮八个探针才靠 shape dump 找到)。dyn ≤ 0 已经说明装不下, 见
        # context_preflight。**位置必须在压缩之后** —— 压缩能省 89%, 压之前拦
        # 会把本来救得回来的对话也拒掉。
        from .context_preflight import check_context_fits  # noqa: PLC0415

        _too_long = check_context_fits(prompt_estimate, model)
        if _too_long:
            raise HTTPException(status_code=413, detail=_too_long)
        # ── 8/15: 等首 chunk 期间每 30s 出一次声 ──────────────────────
        #
        # 下面那句 await 会一直阻塞到上游吐出第一个 chunk。这段时间里**什么日志
        # 都没有**, 而它可能很长: 8/15 现场一条 deepseek 流卡在这里, 日志上表现
        # 为"这条请求没有收尾行", 要靠人比对前后才能看出来。
        #
        # 下面 2580 行那条 TTFT 告警帮不上忙 —— 它在首 chunk **到了之后**才执行,
        # 永远不来就永远不打。
        #
        # 心跳 (_stream_with_keepalive) 也帮不上 —— 它包的是 iterator, 而 iterator
        # 正是这句 await 的产物。中途卡住有保护, 开头卡住没有。
        #
        # 这里只加日志, 不动超时: 真正的超时是 model.upstream.timeout 传给
        # litellm 的那个, 改它是另一件事 (会影响所有慢模型)。
        async def _warn_while_waiting() -> None:
            waited = 0.0
            while True:
                await asyncio.sleep(_KEEPALIVE_INTERVAL_SECS)
                waited += _KEEPALIVE_INTERVAL_SECS
                logger.warning(
                    "等上游首 chunk 已 %.0fs: model=%s request_id=%s user=%s "
                    "(上游 timeout=%ss). 这段时间客户端收不到任何字节 —— "
                    "SSE 心跳要等首 chunk 之后才开始。",
                    waited, model.name, request_id, user_sub,
                    getattr(model.upstream, "timeout", "?"),
                )

        _first_chunk_watch = asyncio.create_task(_warn_while_waiting())
        try:
            # attempts_out=attempts_log: 让 with_fallback 原地填, 这样**抛异常时**
            # 下面 except 里也拿得到试过谁 (返回值那条路在 raise 时走不到)。
            (iterator, first_chunk), used_model, attempts_log = await with_fallback(
                config, model, _start_stream, prompt_estimate=prompt_estimate,
                attempts_out=attempts_log,
            )
        finally:
            _first_chunk_watch.cancel()
        # 首 chunk 拿到 = 上游开始往外吐数据. 这就是 TTFT (time-to-first-token).
        # 注: 如果走了 fallback, 这里记的是"最终成功那个模型的 TTFT", 不算前面失败模型的等待.
        ttft_ms = (time.time() - start) * 1000
        if ttft_ms > 30_000:
            logger.warning(
                "TTFT 异常: model=%s ttft=%.0fms (>30s, 上游可能拥堵, 看是否需要切 flash)",
                used_model.name, ttft_ms,
            )

        # 重新对齐 model_name 到实际用的 (给 metrics + 客户端 [DONE] 之前的元信息)
        if used_model is not model:
            logger.info(
                "stream served by fallback: requested=%s used=%s",
                model.name, used_model.name,
            )

        # ── 干净转发 stream (5/13 鸿波"乱七八糟"反馈, 删掉 BL-FIX23 retry loop) ──
        # 单轮 acompletion 跑完 → 转发所有 chunk → [DONE] 收尾. 不再判 plan-only,
        # 不再 retry 灌 hint, 不再物理强迫 tool_choice. LLM stop 就 stop, 客户端
        # 自己跟它说"继续". gateway 干净转发, 不猜 LLM 心思.
        chunk_stats = {"total": 0, "content": 0, "reasoning": 0, "tool_calls": 0, "empty": 0}
        last_finish_reason: str | None = None
        cumulative_content = ""
        cumulative_has_tool_call = False

        # 写出首 chunk (上面 with_fallback 拉到的那一个)
        if first_chunk is not None:
            data = first_chunk.model_dump() if hasattr(first_chunk, "model_dump") else first_chunk
            if isinstance(data, dict):
                usage = data.get("usage") or {}
                prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                completion_tokens = usage.get("completion_tokens", completion_tokens)
                # BL-CACHE-AUDIT (5/17 + 6/2 BL-CACHE-AUDIT-PROVIDER-FIELDS):
                # Anthropic 顶层字段 + LiteLLM 统一字段 (deepseek/dashscope/gemini/openai).
                _stream_cache_creation = (
                    usage.get("cache_creation_input_tokens", _stream_cache_creation) or _stream_cache_creation
                )
                _stream_cache_read = _pick_cache_read_from_streaming_usage(
                    usage, _stream_cache_read,
                )
                choices = data.get("choices") or []
                if choices:
                    choice0 = choices[0]
                    delta = choice0.get("delta") or {}
                    chunk_stats["total"] += 1
                    has_any = False
                    if delta.get("content"):
                        chunk_stats["content"] += 1
                        cumulative_content += delta["content"]
                        has_any = True
                    if delta.get("reasoning_content"):
                        chunk_stats["reasoning"] += 1
                        has_any = True
                    if delta.get("tool_calls"):
                        chunk_stats["tool_calls"] += 1
                        cumulative_has_tool_call = True
                        has_any = True
                    if not has_any:
                        chunk_stats["empty"] += 1
                    if choice0.get("finish_reason"):
                        last_finish_reason = choice0["finish_reason"]
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

        # 后续 chunks. _stream_with_keepalive 包装: chunk 间隔 > 30s 时插 SSE
        # comment 防客户端 / 中间代理 timeout 断开.
        async for chunk in _stream_with_keepalive(iterator):
            # BL-ABORT-PROPAGATE (7/23 达华 POC): 每 chunk 前检 client 是否断连.
            # 断了就 return · 触发 finally · aclose upstream · 停 token 消耗.
            # 老行为 · client 断了 gateway 继续烧到本轮 finish · 浪费 token / vLLM slot.
            if request is not None and await request.is_disconnected():
                logger.info(
                    "[abort] client disconnected mid-stream · req=%s user=%s "
                    "model=%s chunks_forwarded=%d",
                    request_id, user_sub, used_model.name, chunk_stats["total"],
                )
                status_str = "aborted"
                inflight_streams.mark_aborted(request_id, reason="client_disconnect")
                return  # generator return · finally 会跑 (aclose + output_transforms)
            if chunk == "__keepalive__":
                yield ": keepalive\n\n"
                continue
            data = chunk.model_dump() if hasattr(chunk, "model_dump") else chunk
            if isinstance(data, dict):
                usage = data.get("usage") or {}
                prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                completion_tokens = usage.get("completion_tokens", completion_tokens)
                # BL-CACHE-AUDIT (5/17 + 6/2 cache_read 兼容统一字段): final chunk usage
                _stream_cache_creation = (
                    usage.get("cache_creation_input_tokens", _stream_cache_creation) or _stream_cache_creation
                )
                _stream_cache_read = _pick_cache_read_from_streaming_usage(
                    usage, _stream_cache_read,
                )
                choices = data.get("choices") or []
                if choices:
                    choice0 = choices[0]
                    delta = choice0.get("delta") or {}
                    chunk_stats["total"] += 1
                    has_any = False
                    if delta.get("content"):
                        chunk_stats["content"] += 1
                        cumulative_content += delta["content"]
                        has_any = True
                    if delta.get("reasoning_content"):
                        chunk_stats["reasoning"] += 1
                        has_any = True
                    if delta.get("tool_calls"):
                        chunk_stats["tool_calls"] += 1
                        cumulative_has_tool_call = True
                        has_any = True
                    if not has_any:
                        chunk_stats["empty"] += 1
                    if choice0.get("finish_reason"):
                        last_finish_reason = choice0["finish_reason"]
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

        # 流末尾 stat 日志 (debug 用 — 看 chunk 形态 + finish_reason)
        if chunk_stats["total"] > 0:
            logger.info(
                "chunk stats: model=%s total=%d content=%d reasoning=%d "
                "tool_calls=%d empty=%d finish_reason=%s (cum_content=%d cum_tc=%s)",
                used_model.name,
                chunk_stats["total"],
                chunk_stats["content"],
                chunk_stats["reasoning"],
                chunk_stats["tool_calls"],
                chunk_stats["empty"],
                last_finish_reason,
                len(cumulative_content),
                cumulative_has_tool_call,
            )
            if last_finish_reason == "length":
                logger.warning(
                    "finish_reason=length: model=%s max_tokens 截了 (L4a 兜底 4096 "
                    "还撞 → 调大 / 真做 BL-A1.2 streaming auto-continue).",
                    used_model.name,
                )

        # ── 8/13 删: BL-TASK-ASSESS-1-GATEWAY (5/15) 的 task_assessment 事件 ──
        #
        # 它原本在 [DONE] 前多发一条 `object="task_assessment"` 的 SSE, 给
        # Companion 做 promise-vs-reality 检测 (模型说"已生成 x.docx"但没真调
        # 工具 → UI 打 ⚠)。删掉的理由有两层, 第二层才是决定性的:
        #
        # 1. **它从来没到过客户端。** 5/19 切 hermes 后 Companion 连的是
        #    hermes:8642, 这条事件产生在 hermes → gateway 这一段, hermes 不转发
        #    (hermes 侧全仓 0 处提及 task_assessment)。所以 ⚠ badge 一次都没显示过。
        #
        #    ⚠ 同一天我还先修过一个更里层的 bug: 判据字段 `skill_guard_fired`
        #      被写死成 False (5/26 砍 skills_loader 时留下的), 于是就算走老的
        #      直连路径也永不触发。修完才发现主路径压根收不到这条事件 ——
        #      **先修后查, 顺序反了。**
        #
        # 2. **判据在错的层, 转发也救不回来。** hermes 一次用户回合 = agent loop
        #    里多次 gateway 请求, 而 gateway 只看得见其中一轮。成功任务的最后
        #    一轮恰恰没有 tool_call (它是总结轮), 文字还常写"已生成 xxx.md" ——
        #    因为前面几轮真的生成了。照搬转发会在**几乎每个成功的文件生成任务**
        #    上误报。
        #
        # 结论: 这个判断只有 hermes 做得对 —— 只有它知道"这一整回合总共调了
        # 几次工具"。要重做就在 hermes 侧做, 不要再从 gateway 这层发信号。
        #
        # 前端同批删干净: promiseCheck.ts / PromiseCheckBadge.tsx / _promise_check
        # / TaskAssessment / onNudge 链路。恢复看本提交之前。
        yield "data: [DONE]\n\n"
    except asyncio.CancelledError:
        # BL-ABORT-PROPAGATE (7/23 达华 POC): fastapi/starlette 感知客户端 TCP 断连时 ·
        # 会 cancel 当前 generator task · raise CancelledError 到这里. 军规 · 不吞 ·
        # re-raise 让上层框架清理. finally 会跑 aclose upstream + output_transforms audit.
        # 注 · disconnect check 已在循环里覆盖大多数情况 · 这里是 check 与断连的窗口期兜底.
        logger.info(
            "[abort] CancelledError · req=%s user=%s model=%s",
            request_id, user_sub,
            used_model.name if used_model is not None else model_name,
        )
        status_str = "aborted"
        inflight_streams.mark_aborted(request_id, reason="cancelled")
        raise
    except Exception as e:  # noqa: BLE001
        status_str = "error"
        err = str(e)
        # 8/15: 上游报的恢复时间是 UTC, 而这行日志打的是本地时间。两个时区并排
        # 放着 (日期还可能差一天), 排查的人会以为早就该恢复了。换算一份出来。
        _reset = _localize_reset_hint(err)
        # 8/15: 原来这里是 `if attempts_log else "single"`, 而 attempts_log 在失败
        # 路径上恒为空 (见上面 attempts_out) —— 于是**每一次失败**都打 "single",
        # 不管实际试过几个。现在有真数据了; 还空就说明连主模型都没跑起来。
        _attempts = " -> ".join(attempts_log) if attempts_log else "无记录(主模型都没跑起来)"
        if _is_quota_or_rate_limit(err):
            # 配额 / 限流**不打 traceback**。
            #
            # 那四十行栈全是 litellm / openai 内部调用链, 对这类错零价值 —— 它不是
            # 我们的 bug, 是上游的账务状态。代价却很实在: 每个请求刷一屏, 把真正
            # 要看的三样 (哪个模型 / 上游原话 / 什么时候恢复) 挤出屏幕。
            #
            # 最要命的一条: 上游那句 UTC 恢复时间原样躺在栈的**最后一行**, 而我们
            # 换算成本地的那份在四十行之上 —— 人的眼睛落在栈底, 看到的永远是没
            # 换算的那个。8/15 就这么把 "08-14 23:54 UTC" 读成了 "早该恢复了"
            # (实际是本地 08-15 07:54)。信息在不在日志里, 和人能不能看见, 是两回事。
            logger.error(
                "streaming chat completion failed (attempts=%s) · model=%s · 上游原话: %s%s",
                _attempts,
                model.name,
                err.replace("\n", " ")[:300],
                f" · {_reset}" if _reset else "",
            )
        else:
            logger.exception(
                "streaming chat completion failed (attempts=%s)%s",
                _attempts,
                f" · {_reset}" if _reset else "",
            )
        # 给客户端一个 friendly 错误 —— 把内部 trace 简化成人话
        friendly = _friendly_upstream_error(err)
        # BL-FIX-TIMEOUT-OUTPUTS (5/13 鸿波"做不出文档"): 上游 LLM 卡 / timeout
        # 时, 副手实际可能已经写过文件 (execute_code 早跑了), 列最近 ~/.catfish/output/  # noqa: BOUNDARY
        # 文件给员工看, 别让他以为"做不出来"实际"已经做了".
        err_low = err.lower()
        is_timeout_or_overload = (
            "timeout" in err_low or "timed out" in err_low
            or "overloaded" in err_low or "503" in err
            or "broken pipe" in err_low or "connection error" in err_low
        )
        if is_timeout_or_overload:
            # 5/26 BL-RECENT-OUTPUTS-DECOUPLE: gateway 不再读员工本机 ~/.catfish/output/.  # noqa: BOUNDARY
            # 老 BL-FIX-TIMEOUT-OUTPUTS 在 timeout 错误段追加"过去 24h 已写文件"
            # 列表, 现砍 — Companion 端在 timeout 时自己显示 toast 列本地 outputs
            # (BL-X 后续做; recent_outputs.py 改 stub).
            friendly += (
                "\n💡 上游模型可能拥堵, 试试:\n"
                "  - **切大模型**: 输入 `/model catfish-public-gemini-pro` (2M 上下文, 公网快)\n"
                "  - **新建会话** (Cmd+N): 减小 prompt 让上游推理更快\n"
                "  - 上游 catfish-private-main 是内网 122B Qwen, 长 prompt + 高负载下推理 5+ 分钟"
            )
        # 8/9: error 必须是**对象**, 不能是裸字符串。
        #
        # 老写法 `{'error': friendly}` 里 friendly 是 str。OpenAI 官方客户端
        # (hermes 走的就是它) 的判据是 openai/_streaming.py:88-99:
        #
        #     if is_mapping(data) and data.get("error"):
        #         error = data.get("error")
        #         if is_mapping(error):            ← 字符串不是 mapping
        #             message = error.get("message")
        #         if not message or not isinstance(message, str):
        #             message = "An error occurred during streaming"   ← 落这里
        #         raise APIError(message=message, ...)
        #
        # 也就是说: **我们精心写的 friendly 文案被整个丢掉**, 客户端只拿到一句
        # 无信息量的 "An error occurred during streaming"。
        #
        # 8/9 实测代价 (P44 进度探针刚上线就照出来的第一个问题): hermes 拿着这句
        # 空话重试 3 次 (每次退避 2s / 6s), 白烧 ~22K token 的 prompt 三遍, 最后
        # 返 200 但正文是 "API call failed after 3 retries..."。员工看到的是
        # "等了很久然后没结果", 而真实原因 (friendly 里写着的) 一路都没传出去。
        #
        # 新形状跟 OpenAI 一致: {"error": {"message": ..., "type": ...}}。
        # Companion 那边 chat.ts 老代码只认字符串, 已同步改成两种都认 ——
        # 新旧网关 / 新旧 Companion 交叉组合都不会瞎。
        yield "data: {}\n\n".format(
            json.dumps(
                {"error": {"message": friendly, "type": "upstream_error"}},
                ensure_ascii=False,
            )
        )
    finally:
        # BL-ABORT-PROPAGATE (7/23 达华 POC): 显式关 upstream iterator · 停 token.
        # 正常完成 · iterator 已 exhausted · aclose 是 no-op. Abort 时 (client_disconnect
        # 走 return / CancelledError raise / Exception raise) 才实际发 close 到 litellm ·
        # 传到 vLLM/DashScope · 上游停生成. 军规 · 无 iterator/aclose 时 silent skip (预热
        # 阶段 iterator=None · 或某些 litellm 版本没 aclose 都 OK · 不该阻塞 audit).
        if iterator is not None:
            _aclose = getattr(iterator, "aclose", None)
            if _aclose is not None:
                try:
                    await _aclose()
                except Exception as _e:  # noqa: BLE001
                    logger.debug("upstream iterator aclose 失败 (可忽略): %s", _e)

        # BL-HERMES013-5 (5/12): 散点 audit/quota/context inline 调用 重构成
        # output_transforms ABC plugin chain (借鉴 Hermes 0.13 transform_llm_output).
        # 默认 chain: ContextUsageTransform → AuditTransform → QuotaTransform.
        # 后续加新 hook (in-flight 持久化 / 客户定制脱敏) 只改 build_default_chain.
        from . import output_transforms  # noqa: PLC0415
        actual_model_name = used_model.name if used_model is not None else model_name
        ctx = output_transforms.OutputCtx(
            user=user_sub,
            department=user_dept,
            model=actual_model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=(time.time() - start) * 1000,
            ttft_ms=ttft_ms,
            status=status_str,
            error=err,
            security_concern=security_concern,
            is_internal=is_internal,
            used_model=used_model,
            request_id=request_id,
            # BL-CACHE-AUDIT (5/17): streaming 路径同抓 cache tokens (final chunk
            # usage 里). 抓不到 (非 Anthropic / fallback model) 默认 0.
            cache_creation_tokens=_stream_cache_creation,
            cache_read_tokens=_stream_cache_read,
            # BL-RBAC-DAY4-HARDENING (5/17): X-Catfish-Source audit
            source=source_hint,
        )
        output_transforms.DEFAULT_CHAIN.run(ctx)


# _friendly_upstream_error 抽到 errors.py (无 litellm 依赖, 测试可独立 import).
# 在 app.py 里给一个 alias 别名, 兼容历史 import 路径.
from .errors import friendly_upstream_error as _friendly_upstream_error
from .errors import is_quota_or_rate_limit as _is_quota_or_rate_limit
from .errors import localize_reset_hint as _localize_reset_hint


async def _invoke_chat_completion(
    body: dict,
    *,
    user_sub: str,
    user_dept: str = "",  # 五一 sprint 5/2 RBAC: quota.record_usage 用
    model_name: str,
    model,
    security_concern: str | None = None,
    is_internal: bool = False,  # BL-F17 (5/5): internal 调用跳 record_usage
    source_hint: str = "unknown",  # BL-RBAC-DAY4-HARDENING (5/17): X-Catfish-Source audit
) -> dict[str, Any]:
    """Non-streaming chat completion path, with fallback chain support.

    BL-A1.1 (5/8): 加 auto-continue on finish_reason=length. LLM 输出被
    max_tokens 截时自动续写, 直到 stop / tool_calls / 5 次上限. 让员工不需要
    手动说"继续", 真 Agent 行为.
    """
    from . import auto_continue  # noqa: PLC0415  lazy import 防循环

    start = time.time()
    config: Config = get_config()

    # 给 with_fallback 用的 inner caller — 一次 LLM 调用 (含 fallback chain)
    used_model_holder: list = [model]  # 用 list 当 mutable 容器, 让闭包能写

    async def _invoker_with_fallback(call_body: dict):
        async def _call(candidate_model):
            params = _build_litellm_params(call_body, candidate_model)
            return await litellm.acompletion(**params)
        # BL-FALLBACK-PROMPT-CAP (5/14): 大 prompt 失败时跳过公网
        # BL-TOKEN-COUNTER-LITELLM (5/15): 真 tokenizer 估算
        # BL-TOOLS-IN-ESTIMATE (5/15 22:00): tools schema 也算
        from .fallback import estimate_prompt_tokens  # noqa: PLC0415
        pe = estimate_prompt_tokens(
            call_body.get("messages") or [],
            model=model.upstream.model,
            tools=call_body.get("tools"),
        )
        # 6/7 BL-GROQ-TPM-PREFLIGHT: 拦超 TPM 请求 (友好 413 替 stack trace)
        from .rate_limit_preflight import preflight_check_rate_limits  # noqa: PLC0415
        preflight_check_rate_limits(model, pe, config=config)
        resp, used, _attempts = await with_fallback(
            config, model, _call, prompt_estimate=pe,
        )
        used_model_holder[0] = used  # 续写跨次都记最新 used_model
        return resp

    try:
        response, continuation_count = await auto_continue.call_with_auto_continue(
            body,
            invoker=_invoker_with_fallback,
            # internal 调用 (summarizer / proactive / a2a 辅助) 关 auto-continue:
            # 它们 max_tokens 是有意设短的 (600/120/80), 续写没意义 + 浪费 quota.
            enable=not is_internal,
        )
    except Exception as e:
        _raise_upstream_error(
            e,
            user_sub=user_sub,
            model_name=model_name,
            latency_ms=(time.time() - start) * 1000,
            log_context="chat completion failed",
        )
        raise  # unreachable; satisfies type checker

    used_model = used_model_holder[0]

    if continuation_count > 0:
        logger.info(
            "auto-continue: user=%s model=%s 续写 %d 次完成 (latency_ms=%.0f)",
            user_sub, used_model.name, continuation_count,
            (time.time() - start) * 1000,
        )

    if used_model is not model:
        logger.info(
            "non-stream served by fallback: requested=%s used=%s",
            model.name, used_model.name,
        )

    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
    completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
    # BL-CACHE-AUDIT (5/17): Anthropic prompt cache hit metrics.
    # Anthropic 在 usage 里返 cache_creation_input_tokens (首次写 cache 的 tokens)
    # + cache_read_input_tokens (命中 cache 复用的 tokens).
    #
    # 6/2 BL-CACHE-AUDIT-PROVIDER-FIELDS (鸿波 6/2 晚抓的真问题): 5/17 只抓 Anthropic
    # 字段, 但 catfish 真用的 deepseek/dashscope/gemini/openai 走 LiteLLM 统一字段
    # `usage.prompt_tokens_details.cached_tokens` (OpenAI 标准). 抓不到导致鸿波重启
    # 后 cache_read=0 误以为 cache 没生效. 加 cached_tokens fallback.
    #
    # LiteLLM 1.86 真实测 (llms/dashscope/cost_calculator.py:28-30 真证):
    #   - DashScope qwen-max:    usage.prompt_tokens_details.cached_tokens
    #   - DeepSeek API:           usage.prompt_tokens_details.cached_tokens
    #   - Gemini (cached_content):usage.prompt_tokens_details.cached_tokens
    #   - OpenAI gpt-4o:          usage.prompt_tokens_details.cached_tokens
    #   - Anthropic Claude:      usage.cache_read_input_tokens (顶层, 旧字段)
    cache_creation = (
        getattr(usage, "cache_creation_input_tokens", 0)
        or _extract_nested_usage(usage, "cache_creation_input_tokens")
        or 0
    ) if usage else 0
    cache_read = (
        getattr(usage, "cache_read_input_tokens", 0)
        or _extract_nested_usage(usage, "cache_read_input_tokens")
        or _extract_nested_usage(usage, "cached_tokens")  # 6/2 LiteLLM 统一字段
        or 0
    ) if usage else 0
    if cache_creation or cache_read:
        # 命中率 = cache_read / (cache_read + non-cached prompt_tokens)
        # 但 prompt_tokens 是总数 (含 cache_read), Anthropic 文档讲法不一, 都 log
        logger.info(
            "BL-CACHE-AUDIT: model=%s prompt=%d cache_create=%d cache_read=%d "
            "(cache_read/prompt=%.0f%%) user=%s",
            used_model.name, prompt_tokens, cache_creation, cache_read,
            (cache_read / prompt_tokens * 100) if prompt_tokens else 0,
            user_sub,
        )
    _check_context_usage(used_model, prompt_tokens, user_sub)
    log_request_metadata(
        user=user_sub,
        model=used_model.name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=(time.time() - start) * 1000,
        status="ok",
        security_concern=security_concern,
        cache_creation_tokens=cache_creation,
        cache_read_tokens=cache_read,
        # BL-RBAC-DAY4-HARDENING (5/17): X-Catfish-Source audit
        source=source_hint,
    )
    # 五一 sprint 5/2 收尾: 同步写 quota_events. ok 才记 (error 时 tokens=0).
    # BL-F17 (5/5): internal 调用跳 record_usage (audit log 仍写, 只 quota 跳).
    if (prompt_tokens > 0 or completion_tokens > 0) and not is_internal:
        _quota_module.record_usage(
            user_email=user_sub,
            department=user_dept,
            model=used_model.name,
            tokens_in=prompt_tokens,
            tokens_out=completion_tokens,
        )

    # BL-ADVISOR-UPSTREAM-ERROR-AS-CONTENT (6/1 鸿波): 上游某些 LiteLLM-based 私有 LLM
    # 服务 (e.g. catfish-private-main) streaming fail 重试用尽后, 把 error 当作
    # completion content 返 200 OK, gateway 透传 → 客户端 (advisor / chat) 看 200
    # 走 JSON.parse 失败, 误判 "LLM 返非 JSON", 真因 (上游挂) 完全隐藏.
    #
    # 这里 detect 已知错误关键词 → 转 502 让客户端正确知道上游问题, 不污染 quota.
    # 真 LLM 输出含这些词 (e.g. 用户问"What's API call failed?" LLM 复读) 概率极低,
    # 但避免误杀: 只在 content 是**纯错误文本** (不超 500 字) 时识别.
    result = response.model_dump() if hasattr(response, "model_dump") else response
    if _looks_like_upstream_error_as_content(result):
        upstream_msg = _extract_content_text(result)[:300]
        logger.warning(
            "BL-ADVISOR-UPSTREAM-ERROR-AS-CONTENT: 上游 LLM 返 200 但 content 是错误文本, "
            "转 502 给客户端. model=%s user=%s content=%r",
            used_model.name, user_sub, upstream_msg[:200],
        )
        raise HTTPException(
            status_code=502,
            detail={
                "error": "upstream_error_as_content",
                "error_type": "UpstreamErrorAsContent",
                "message": upstream_msg,
                "friendly": "上游 LLM 服务暂时不可用 (重试用尽), 稍后再试.",
                "model": used_model.name,
            },
        )
    return result


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    user: User = Depends(get_current_user),
):
    body = await request.json()
    model_name = body.get("model")
    if not model_name:
        raise HTTPException(status_code=400, detail="model parameter required")

    # BL-CATFISH-AUTO-ROUTE (7/18 鸿波 catch WeChat model 硬编码 · Task #16):
    # 加 catfish-auto 特殊 model 支持 · hermes config.yaml model.name=catfish-auto
    # 永久静态. WeChat 走 hermes → gateway · hermes 不感知 Companion picker · 若
    # 硬编码 catfish-public-deepseek-flash · 员工 role 换 gateway 用错 model 挂.
    # Fix: gateway 收 catfish-auto · 从 roles.yaml chat_default 动态 resolve 真
    # model. 员工 role 换 gateway auto 换. hermes 无需 restart.
    #
    # 注: Companion picker 只影响 Companion chat.ts (传真 model 名). WeChat 通过
    # hermes 用 auto · gateway 用员工 role chat_default. 两路 clean 分.
    #
    # 8/13: 解析本身挪进 `_resolve_auto_sentinel`, 因为 GET /v1/models/{id} 也要
    # 认这个名字 (hermes 从那里拿 context_length)。原来只有这一处认, 两个端点
    # 对同一个名字给出不同答案 —— 见那个函数的注释。
    resolved = _resolve_auto_sentinel(model_name)
    if resolved != model_name:
        logger.info(
            "BL-CATFISH-AUTO-ROUTE: model=%s user=%s role=%s → resolved=%s",
            model_name, user.sub, getattr(user, "role", "?"), resolved,
        )
        model_name = resolved
        # 让下游 metrics/audit/logs 拿到真 model 名 · 不是 auto
        body["model"] = resolved

    config: Config = get_config()
    model = _resolve_model(config, model_name)

    # BL-F17 (5/5): 早早判定 internal call, 让后面所有 inject 阶段 (session_meta tick /
    # prompt_security detector / 等) 都能跳过 internal 调用. 这条**必须**在 line 1074
    # session_meta tick 之前赋值, 否则 UnboundLocalError. (5/5 17:13 鸿波报 P0,
    # 之前不知谁不小心注释了, 导致 chat_completions 全 500.)
    #
    # 5/21 BL-CORS-DEBT-FIX: hermes proxy 8642 CORS allow-list 没配 X-Catfish-*, 撞 preflight.
    # 加 query param fallback (?catfish_internal=1) 兼容. header 仍优先, 老 caller 不破.
    #
    # BL-PLUGIN-AUTH-FIX (7/27 鸿波): 加**授权校验** — 老逻辑只看 header/query 值,
    # 任何拿到员工 JWT 的人加个 X-Catfish-Internal 就免 quota + 跳 prompt_security.
    # 现在双条件: "想要" (header/query) AND "有权" (身份带 background.tasks scope).
    #
    # 有权的两类:
    #   ① auth_method=internal_loopback — gateway 调自己 (proactive / conversation_compressor)
    #   ② service token 带 background.tasks scope — hermes 进程内 plugin
    #      (catfish-memory distill/summarize/wiki · catfish-xcatfish-user memory_enforce)
    #      scope 由 identity client_credentials 白名单校验过 (routes_token.py:463-479) 可信
    #
    # 无权时: 照常扣 quota + 走 prompt_security (功能不断, 只是不给免费额度) + warning log.
    _wants_internal = (
        request.headers.get("x-catfish-internal", "").lower() in ("true", "1", "yes")
        or request.query_params.get("catfish_internal", "").lower() in ("true", "1", "yes")
    )
    is_internal_call = _wants_internal and user.has_scope("background.tasks")
    if _wants_internal and not is_internal_call:
        # 文案只提中央侧动作 — 中央端不该知道边缘 fs 布局 (BL-CENTRAL-EDGE-BOUNDARY,
        # 5/26 起中央 6 处边缘路径引用已全清, 不往回走). 边缘怎么拿 token 是边缘的事,
        # plugin 侧 _gateway_dev_token() 自己的 fail-loud log 会写清查哪几个来源.
        logger.warning(
            "X-Catfish-Internal 请求被拒 (缺 background.tasks scope): user=%s "
            "auth_method=%s scopes=%s → 照常扣 quota. "
            "若是 hermes 侧后台任务 (distill / summarize / memory_enforce): "
            "该 caller 的 service token 需带 background.tasks scope — "
            "确认 identity clients.yaml 的 hermes-cli allowed_scopes 已含它, "
            "然后重新走 client_credentials 换 token (见 refresh-jwt-hermes-env.sh)",
            user.sub, user.auth_method, user.scopes,
        )

    # BL-RBAC-DAY4-HARDENING (5/17, hermes 0.14 #23194 ctx.llm bypass 防御):
    # Companion 显式标 X-Catfish-Source=companion. hermes plugin 内部 ctx.llm
    # 调用如果没改 default 会留 unknown — audit 看 unknown 比例就知道部署里有
    # 多少 plugin 在绕开. 真要堵需要客户 IT firewall 把 plugin 出口锁回 catfish.
    # 字段进 audit log, 不阻塞 (绕 catfish 的 plugin 根本到不了我们这).
    #
    # 5/21 BL-CORS-DEBT-FIX: 加 ?catfish_source= query param fallback (hermes proxy CORS).
    source_hint = (
        request.headers.get("x-catfish-source", "").strip()
        or request.query_params.get("catfish_source", "").strip()
        or "unknown"
    )

    # ────────────────────────────────────────────────────────────────────
    # BL-AUTH-DECOUPLE-A1 (5/19): service token + X-Catfish-User → on-behalf-of.
    #
    # 普通 user JWT (sub=email): X-Catfish-User 完全忽略, effective = user.sub.
    # service token (sub=client:hermes-cli) + 白名单: 读 X-Catfish-User 当 effective.
    # quota / RBAC / audit / metrics / inject_ctx 全用 effective_user_email,
    # 不直接用 user.sub. 但 RBAC dataclass 仍是 user (service token 的 role/
    # allowed_models 是 service 维度, 不是 on-behalf user 的 — 那 cross-project
    # 解析能力 A1 不引入, 留 A4+).
    #
    # 跨小时 401 race 治本: hermes 用长寿 service token 调 gateway, 不再被 user
    # token 1h refresh 卡.
    # ────────────────────────────────────────────────────────────────────
    _x_user_header_raw = request.headers.get(X_CATFISH_USER_HEADER)
    effective_user_email = resolve_effective_user_email(user, _x_user_header_raw)
    _is_service_call = is_service_principal(user)
    if _is_service_call and effective_user_email != user.sub:
        logger.info(
            "BL-AUTH-DECOUPLE-A1: service token sub=%s acting on behalf of "
            "user=%s (X-Catfish-User)",
            user.sub, effective_user_email,
        )

    if not user.can_access(model):
        raise HTTPException(status_code=403, detail=f"access denied to model: {model_name}")
    if model.mode != "chat":
        raise HTTPException(status_code=400, detail=f"model {model_name} is not a chat model")

    # BL-HERMES013-3 (5/11) /goal Ralph loop **5/26 砍** — hermes 0.14 原生
    # /goal + /subgoal (#25449) 替代. Companion → hermes → gateway 链路里 hermes
    # 自己拦 /goal, catfish gateway 永远收不到这命令, detect 拦截是死代码.
    # 详见 session_goals.py 顶部 DEPRECATED 说明.

    # BL-GATEWAY-SOFT-HANDOFF (5/18 鸿波拍板): 跨 model 切换中间件.
    # client 自报上轮 model (X-Catfish-Prev-Model header), 跟本轮 body["model"] 不同 +
    # 新 model 不支持 tools → 把历史 tool_calls / tool messages 转 inline 文本摘要,
    # 不让旧的 tool history 直接送给新 model 而炸. 客户端不传 header → no-op.
    # 不处理 context window / persona / memory rebind (3 件 hermes 自家做 / 我们不需要).
    _prev_model_name = request.headers.get("x-catfish-prev-model", "").strip()
    if _prev_model_name:
        body, _handoff_hint = apply_soft_handoff(
            body,
            prev_model_name=_prev_model_name,
            new_model=model,
            config=config,
        )
        if _handoff_hint:
            request.state.handoff_hint = _handoff_hint
            logger.info(
                "soft handoff applied: user=%s hint=%s", user.sub, _handoff_hint
            )

    # 鲶鱼身份注入：客户端没传 system message 就自动加 SOUL + memory
    # Hermes 这种已自带 system 的不动；客户端可加 X-Catfish-Skip-Identity: true 强制跳过
    # BL-E11 命名权: header X-Catfish-Agent-Name / -Personality 让员工改名 + 选人设
    skip = request_skips_identity(request)  # 5/21: header 或 ?catfish_skip_identity=1 都生效
    # BL-LEAN-CHAT (5/15 凌晨): 服务 token (token_use=service, e.g. hermes-cli, cron, a2a)
    # 自动 skip identity. 服务调用不需要"鲶鱼人格", 它跑批 / 跑 skill, 拿原始 LLM 答即可.
    # 鸿波 5/14 端到端测时单 chat "1+1=?" 撞 35713 input tokens, 80% 来自 SOUL/identity.
    # 这条让服务 token 自动 ultra-lean, 用户身份 (web Companion / employee SSO) 不动.
    if user.role == "service":
        skip = True
        logger.info("BL-LEAN-CHAT: service token (sub=%s) auto-skip identity", user.sub)
    agent_name, agent_personality = header_agent_prefs(request.headers)
    # BL-IDENTITY-INJECT-DECOUPLE (5/26): Companion 在员工 mac 读好 SOUL/USER/memories,
    # body 里塞 _catfish_identity_bundle 字段传过来. pop 后不再 forward 给 upstream LLM
    # (这是 gateway 内部消费的字段, 不该出现在 OpenAI-compat 请求里).
    # bundle 缺 → fs 兜底 (向后兼容 dev / 老 Companion). SaaS 后 fs 兜底永远命中空.
    identity_bundle = body.pop("_catfish_identity_bundle", None)
    if identity_bundle is not None and not isinstance(identity_bundle, dict):
        identity_bundle = None  # 防御: 客户端格式错就当没传
    body["messages"] = inject_identity_if_needed(
        body.get("messages", []),
        skip=skip,
        agent_name=agent_name,
        agent_personality=agent_personality,
        # BL-SOUL-SCENARIO P2 (5/13): 透传 tools 让 inject 按 tool 候选注入
        # SOUL_BROWSER.md / SOUL_EXECUTE_CODE.md 等场景段, 不再永远全量灌.
        tools=body.get("tools"),
        bundle=identity_bundle,
    )

    # ────────────────────────────────────────────────────────────────────
    # BL-MM9-FREEZE (5/12 鸿波拍板): CATFISH_LEAN_INJECT=1 模式总开关.
    #
    # 教学场景下, 鲶鱼应该是"专注学一个系统怎么操作", 不需要看员工画像 / 历史
    # session / 反馈 / 周报偏好 / stats_guard / 各种 retry-hint. 这些 inject
    # 互相打架, prompt 30K+ 字符让 LLM 行为不可预测.
    #
    # 一个 env 开关把所有非核心 inject 关掉. **保留**: identity (身份)
    # + skills_catalog (LLM 看 skill 列表) + session_goal (员工锁定目标)
    # + session_meta (时间感) + prompt_security (安全 detector).
    # 上下文压缩 BL-Q3-ARCHIVE 在更前面, 跟 lean mode 配合, 不动.
    #
    # 默认 LEAN_INJECT=0 = 老行为, 不破坏现有部署.
    # BL-LEAN-SESSION (5/13 鸿波拍板 "客户无法跑命令行"): 优先看请求 header
    # X-Catfish-Teaching-Mode: 1 (Companion 教学模式 toggle 传), env 仍兼容.
    # 跨 session 隔离, 教学完关 toggle 立刻回常态, 不需要重启 gateway.
    # ────────────────────────────────────────────────────────────────────
    _teaching_mode = request.headers.get("X-Catfish-Teaching-Mode") == "1"
    # BL-LEAN-CHAT (5/15): 服务 token (hermes-cli / cron / a2a) 也走 lean 模式 —
    # 不需要 session_facts / journal / feedback / hints (这些都是给"用户"看的元数据,
    # 服务调用没有"用户"). 跟上面 skip identity 一起把服务 chat 从 35K 砍到 ~3K.
    _service_lean = (user.role == "service")
    _lean = (
        _teaching_mode
        or os.environ.get("CATFISH_LEAN_INJECT", "0") == "1"
        or _service_lean
    )
    if _teaching_mode:
        logger.info("BL-LEAN-SESSION: header 触发 teaching mode (lean inject ON)")
    if _service_lean and not _teaching_mode:
        logger.info("BL-LEAN-CHAT: service token (sub=%s) auto-lean inject", user.sub)

    # BL-MEMORY-MIGRATE-STEP1C (5/16): 8 个 inject_X() 合并成 1 个 Registry 调用.
    #
    # 替代历史: inject_session_facts / inject_stats_guard / inject_skills_catalog /
    #          inject_skill_guard / inject_session_history / inject_employee_journal /
    #          inject_feedback / build_meta_block 8 处.
    # BL-GATEWAY-MEMORY-REGISTRY-DELETE (5/20): memory_registry inject 整套删除.
    # 5/19 BL-MEMORY-OWNERSHIP-FIX Phase 2-3 已 disable provider 注册 → inject_unified
    # / inject_subset 实际是 no-op (registry.providers 永远空). 5/20 spike audit
    # 确认死代码 + 删 caller. catfish-memory plugin (hermes 侧 prefetch 钩子) 接管
    # 5 个 catfish 边缘 provider 的 system prompt 注入路径.
    #
    # 保留: identity (创建 system) / session_goal / hints / session_meta tick / hermes builtin

    # BL-GATEWAY-CLEANUP-POST-HERMES (5/20 删): compound_intent.py 已删,
    # hermes-agent 自管 plan-execute (run_agent.py:12614 agent loop).

    # 档 4 (BL-HERMES013-3 5/11) /goal 注入 **5/26 砍** — hermes 0.14 原生 /goal
    # 替代. catfish 自己注入的是 ~/.catfish/session_goal.txt (跟 hermes 内部 goal  # noqa: BOUNDARY
    # 状态独立), 双 inject 互不知道, 是状态分裂源. hermes 已统一管理 goal 注入.
    # 详见 session_goals.py 顶部 DEPRECATED 说明.

    # BL-A1.2 / A1.3 / FIX24 — lean 模式全关.
    # 教学场景里 LLM 正常会"重复调"(再 snapshot 看 DOM 变化) / 中间停顿想一下 /
    # 多步任务做完才说. 这些 retry-hint 系列误伤 → 教学卡死.
    # BL-A1.2/A1.3 软 hint 注入. 跟 BL-FIX24 软 hint 同性质 — 只往 messages 加
    # system msg, LLM 看见就听看不见就算, 不阻断流, 副作用小. 5/13 鸿波"全部清干净"
    # 决策: 这两个保留 (跟 BL-FIX23 retry / BL-FIX24 hard-block 不同, 不会误杀
    # 成功流). 加总开关 CATFISH_DISABLE_GATEWAY_HINTS=1 一键关 — 万一未来再撞坑.
    _hints_disabled = os.environ.get("CATFISH_DISABLE_GATEWAY_HINTS", "0") == "1"
    if not is_internal_call and not _lean and not _hints_disabled:
        # BL-A1.2: 连续多次同 tool 失败时换思路
        from . import tool_retry_hint  # noqa: PLC0415  lazy import
        # 5/18 BL-HERMES-AUTO-CONTINUE-LIMIT: hard cap 优先于 soft hint.
        # 鸿波实盘 hermes 不听 hint 闷头重试 89 次. 撞 5 次连续同 tool 失败 →
        # gateway 合成 assistant abort 直接返客户端, 跳过 LLM 调用 (省 token +
        # 强制退 agent loop). 跟 inject_hint 互补 (hint 给思路, hard cap 兜底).
        hit, tool_name, error_summary = tool_retry_hint.should_hard_cap(body["messages"])
        if hit:
            count, _, _ = tool_retry_hint._detect_consecutive_failures(body["messages"])
            synthetic = tool_retry_hint.build_hard_cap_abort_response(
                model=body.get("model", "unknown"),
                tool_name=tool_name,
                error_summary=error_summary,
                count=count,
            )
            logger.warning(
                "BL-HERMES-AUTO-CONTINUE-LIMIT: 跳过 LLM 调用, 直接返合成 abort "
                "(tool=%s consecutive=%d)", tool_name, count,
            )
            # 走跟 LLM 返同样的下游路径 (audit / response shape), 但不烧 token
            return JSONResponse(synthetic)
        body["messages"] = tool_retry_hint.inject_tool_retry_hint(body["messages"])

        # BL-GATEWAY-CLEANUP-POST-HERMES (5/20 删): self_critique.py 已删,
        # 完成承诺 / plan-then-stop 检测由 hermes-agent agent loop 兜底
        # (hermes 自己跑 max_iterations=90 + tool_retry, 不再需要 gateway 注入 hint).

        # ─── BL-FIX24 duplicate-tool-call guard 全部 DELETED (5/13 鸿波"乱七八糟") ──
        # 历史: 软 hint (inject_duplicate_guard_hint) + 物理 hard-block (detect_hard_block_duplicate
        # ≥3 次直接 SSE 推 error + break) 都删. 副作用: 长任务正常重复调 (例如批量
        # 写多份文件) 会被误拦, 把正常流打成失败. duplicate_tool_call_guard.py 模块
        # 本身保留, 不再被 chat_completions 入口调用.

    # 5/26 BL-SESSION-META-PLUGIN-TAKEOVER: 老 session_meta.tick() 砍 (gateway 不
    # 再写员工本机 session_meta.json). catfish-memory hermes plugin sync_turn hook
    # 接管 _tick_session_meta(), 在 plugin 进程里写自己 fs (合规). 这里删 caller
    # — session_meta module 改 fail-loud stub, 防回归.
    # (BL-F17 5/5 internal-call skip 逻辑也跟着不需要了 — plugin 只在真 chat
    #  turn 同步, internal loopback 调用走 service token 不触发 plugin sync_turn.)

    # 5/23 BL-GATEWAY-DROP-LEGACY-SUMMARIZE (鸿波 task #5 Stage 1): 删 gateway 端
    # session_summarizer (trigger_background_summary) + memory_distill (maybe_run_llm_
    # distillation) 两条后台路径. 5/22 切 CATFISH_GATEWAY_LEGACY_SUMMARIZE=0 后,
    # catfish-memory plugin 的 sync_turn / on_session_end 接管: 写 employee_journal +
    # 蒸馏 distilled_facts.md 全在 hermes 侧 plugin (Companion 端) 跑. gateway 不再
    # 读写员工本机 journal / distilled_facts → 中央边缘分离真落实, gateway 物理上可
    # 搬到中央服务器跑.
    #
    # 删的: src/memory_distill.py (747 行) + src/session_summarizer.py (527 行) +
    # 3 个测试 (test_memory_distill / _live / test_memory_features_baseline +
    # test_session_summarizer), 共净删 ~2500 行.
    #
    # env CATFISH_GATEWAY_LEGACY_SUMMARIZE 同步从 .env 删, 不再有 fallback.

    # Prompt 安全检测: 扫 user messages 看是否含明文密码 / 凭据.
    # 不拦截 (员工知道在干嘛), 只 log warn + audit 标记, 让员工 IT 事后能查谁在何时
    # 把密码写进 prompt — 推荐他们用 secret_ref 替代.
    #
    # BL-F18 (5/5): internal call (summarizer/proactive/a2a) 跳 detector.
    # 这些是后台 housekeeping, 拼的 prompt 是已经存进系统的内容 (journal / facts),
    # 不是员工**这次**说的, 不该计入"今日安全事件". 跟 BL-F17 quota skip 同思路.
    if not is_internal_call:
        from .prompt_security import detect_credentials_in_messages  # noqa: PLC0415
        credential_hits = detect_credentials_in_messages(body.get("messages", []))
        if credential_hits:
            logger.warning(
                "user=%s 在 prompt 里检测到密码 / 凭据明文 (%d 处). "
                "建议员工用 secret_ref (keychain:// 或 env://) 替代. user_sub=%s",
                effective_user_email, len(credential_hits), effective_user_email,
            )
            # 把 hits 暂存到 request state, 让后面 audit log 能拿到
            request.state.credential_hits = credential_hits

    # 5/8 BL-FIX2: 把 role=tool 含 image 重组成 user multipart message.
    # 详见 multimodal_tool_unwrap.py.
    # 8/15: 消息数组解包/规整搬到 chat_prepare.prepare_messages (66 行)。
    body = prepare_messages(body, model, model_name, effective_user_email)
    # 含图自动 reroute 到 vision 模型: 防止主力模型 (非 vision) 收到 image_url
    # 直接被上游 protobuf 解析炸 BadRequest 400. in-place 改 body["model"].
    rerouted_model, vision_hint = route_to_vision_if_needed(
        body, config, model
    )
    if rerouted_model is not None:
        model = rerouted_model
        model_name = rerouted_model.name
        request.state.vision_reroute_hint = vision_hint
        logger.info(
            "auto-route to vision: orig_user=%s new_model=%s",
            user.sub, model_name,
        )

    # 8/13 删掉 tool_capability_guard 的 reroute 段 (整套模块一并删)。
    #
    # 它原本管的是: 已知 tool 调用不稳的模型 (qwen 122b a10b) 碰到 skill 意图时
    # 自动切到 flash 系列, 防止"文字幻觉'已生成 X.docx'但磁盘上没文件"
    # (4/29 demo 反复翻车的真因)。
    #
    # 为什么删而不是修:
    #
    #   1. **它自 5/26 起就从没触发过**。判据 `_has_tool_intent` → `has_skill_intent`,
    #      而后者在砍 skills_loader (中央扫员工本机 SKILL.md, P0 隐私违规) 时被改成
    #      `return False` 恒定。整个 guard 空转 79 天。
    #   2. `request.state.tool_capability_hint` **只写不读** —— 就算触发了, 那句
    #      给员工的提示也没有任何消费方。
    #   3. 上游已经接管: hermes 0.14 把每个 skill 当独立 tool 暴露, LLM 看 tool list
    #      自然会调, 不再依赖 gateway 侧的意图检测 + 强制切换。
    #   4. ⚠ 而且它带一个**潜伏的越界口**: pick_tool_capable_alternative 同 tier
    #      找不到备选就跨 tier 兜底 (且有测试钉住 private→public 这条)。
    #      `ModelConfig.supports_tool_use` 默认 False, 所以往 models.yaml 加一个
    #      内网 chat 模型、漏写这一行, 就足以让内网 prompt 被自动改路到公网上游。
    #      现网目录暂时踩不到 (5 个 chat 模型全标了 true), 但挡住这扇门的恰恰是
    #      第 1 条那个 bug —— 靠 bug 守边界不能算守住。
    #
    # 恢复看本提交之前的 tool_capability_guard.py / skill_guard.py。

    # 防御性清洗 tools 数组 —— 畸形 tool (例如缺 function.name) 直接丢, 不让
    # LiteLLM 转 Gemini functionDeclarations 时 KeyError 把整个请求挂掉。
    # BL-RBAC-DAY4 (5/17): 传 user, sanitizer 按 user.effective_allowed_tools
    # 过滤 LLM tool 列表. sysadmin / 空 list / ALWAYS_ON 永远放行.
    # 5/22 BL-TOOL-PROFILE 鸿波: 传 source_hint, sanitizer 按 source 砍 catfish_*
    # 到 profile 白名单 (companion-advisor / companion-profile / etc).
    body = sanitize_tools(body, user=user, source_hint=source_hint)

    # Gemini 防退化: 在 system 末尾加禁用 native tool_code 的指令
    # 没用 Gemini 模型 / 客户端不传 system 都会跳过, 无副作用
    body = harden_for_gemini(body, model)

    # BL-COMPRESSION-GATEWAY (5/15 早): 长 session messages 历史压缩.
    # 触发条件: estimate_tokens(messages) > model.context * 50%
    # 保留: 头 2 条 (system/首条 user) + 尾 8 条 (最近上下文), 中间压成 1 句.
    # 跳过:
    #   - is_internal_call (gateway-loopback summarizer/proactive 等, 它们本来就短)
    #   - X-Catfish-Compression-Internal (本模块自调 LLM 时设, 防自递归)
    #   - service token **且不是代表某个员工** (见下面 8/8 那段)
    # 5/15 早鸿波看 audit 撞 chenhongbo@ffcs.cn 单 chat 95K input — 70K 历史 +
    # 25K inject. lean 已省 inject (#77), 历史这块就是 BL-COMPRESSION-GATEWAY 解.
    #
    # ── 8/8: service token 的豁免开一个口子 ────────────────────────────
    #
    # 原条件是一句光秃秃的 `user.role != "service"`, 理由写在 5/15 的注释里:
    # 「service token (sub=client:xxx, 也是 1-shot 没历史)」。**当时是对的。**
    #
    # 5/19 BL-AUTH-DECOUPLE-A1 把 hermes-cli 改成了「service token + X-Catfish-User
    # 代表员工」—— 从那天起, 员工的主力对话带着几百条历史从 service token 进来,
    # 而这条豁免把它整个挡在门外。**压缩钩子对主路径一次都没跑过。**
    #
    # 铁证就在下面 8 行: 「压缩按 effective_user_email 归账 (service on-behalf-of
    # 时 = X-Catfish-User 指定的员工)」—— 那行注释只有在 service token 能走到
    # 这里时才有意义, 它是 5/19 那次改动留下的、从写下就没被执行过的意图。
    # 8/8 鸿波实盘: 单请求 prompt 361,405 token, 405 条 messages, 压缩零触发。
    #
    # 现在的判据是 **effective_user_email != user.sub** —— 也就是
    # resolve_effective_user_email 认定的「这次是替某个真员工跑」。为什么用它:
    #   · hermes chat 路径一定带 X-Catfish-User (Companion 走 sub_email 必传)
    #     → effective 是员工邮箱 → 该压
    #   · hermes 自己的 auxiliary_client (后台压缩 / summary) **不带**这个 header,
    #     auth/__init__.py:145 那段会 fallback 到 sub → effective == user.sub
    #     → 仍然豁免, 跟 5/15 的原意一致 (它们确实是 1-shot)
    #   · cron / a2a 这类不在 SERVICE_CLIENTS_ALLOWING_USER_OVERRIDE 白名单的
    #     service, resolve 直接返 sub → 也仍然豁免
    # 两个死循环护栏 (is_internal_call / _compression_internal) 一个都没动。
    # 8/15: 上下文压缩搬到 chat_prepare.maybe_compress (66 行)。
    # 它顺带写 request.state.compression_stats —— 那是**属性写**, 传同一个
    # request 对象进去照样生效。
    body = await maybe_compress(
        body, request, model, model_name, user,
        is_internal_call, _is_service_call, effective_user_email,
    )
    # 8/15: 配额阻断搬到 chat_prepare.enforce_quota (52 行)。
    # 纯守卫 —— 超了 raise 429, 不产出任何后面要用的值 (读写集算过)。
    enforce_quota(body, user, model_name, effective_user_email, is_internal_call)
    is_stream = bool(body.get("stream", False))

    # 把 prompt_security 检测结果转成 security_concern 字符串 (audit log 字段),
    # 不含真密码值, 只标记 'prompt_credential_detected' 类型.
    security_concern = (
        "prompt_credential_detected"
        if getattr(request.state, "credential_hits", None)
        else None
    )

    if is_stream:
        return StreamingResponse(
            _stream_chat_completion(
                body, user_sub=effective_user_email, user_dept=user.department,
                model_name=model_name, model=model,
                security_concern=security_concern,
                is_internal=is_internal_call,  # BL-F17: 透传, 跳 record_usage
                source_hint=source_hint,  # BL-RBAC-DAY4-HARDENING (5/17)
                # BL-AUTH-DECOUPLE-A1 (5/19): user_sub 透传 effective_user_email,
                # audit / record_usage 都按 X-Catfish-User 归账 (service token on-behalf-of).
                # BL-ABORT-PROPAGATE (7/23 达华 POC): 传 request · 生成器里定期
                # check is_disconnected · 客户 abort 主动关上游 · 停 token 消耗.
                request=request,
            ),
            media_type="text/event-stream",
        )
    return await _invoke_chat_completion(
        body, user_sub=effective_user_email, user_dept=user.department,
        model_name=model_name, model=model,
        security_concern=security_concern,
        is_internal=is_internal_call,  # BL-F17: 透传, 跳 record_usage
        source_hint=source_hint,  # BL-RBAC-DAY4-HARDENING (5/17)
    )


# Embeddings




# Entry point


def run():
    # 网络层屏蔽已经在 module 顶部做过了（必须在 import litellm 之前）。
    # 这里只打印之前缓存的 status banner。
    from .network import print_banner  # noqa: PLC0415
    print_banner(_NETWORK_STATUS)

    # Intentional lazy import: uvicorn only needed when launching as a script.
    import uvicorn  # noqa: PLC0415

    # 5/6 安全 P0 G1: 默认仅本机 (127.0.0.1). 私有部署服务器才显式 HOST=0.0.0.0,
    # 防员工电脑跑 gateway 时同公司局域网扫端口蹭 quota.
    host = os.environ.get("HOST", "127.0.0.1")
    port_str = os.environ.get("PORT", "8000")
    port = int(port_str)
    port_source = "env PORT" if "PORT" in os.environ else "default"
    host_source = "env HOST" if "HOST" in os.environ else "default(127.0.0.1)"
    if host == "0.0.0.0":
        print(
            "[catfish] ⚠️ HOST=0.0.0.0 — gateway 暴露到所有网卡 (局域网可访问). "
            "仅服务器部署用. 员工电脑应改回 127.0.0.1.",
            flush=True,
        )
    # 6/9 鸿波修: 之前 uvicorn.run 没传 workers, UVICORN_WORKERS env 完全被忽略,
    # 永远跑 1 个 worker. BL-F10 bench 1000 user 时单 worker GIL 卡死 (P50 31s).
    # 加 workers env 支持 — 默认 1 保持向后兼容, 部署文档建议 prod 用 4+ worker.
    # 注意: uvicorn workers > 1 必须用 import string 调 app (不是 app 对象), 已是.
    workers = int(os.environ.get("UVICORN_WORKERS", "1"))
    print(
        f"[catfish] starting uvicorn on {host}:{port} workers={workers} "
        f"(HOST source={host_source}, PORT={port_str} source={port_source})",
        flush=True,
    )
    uvicorn.run(
        "catfish_gateway.app:app",
        host=host,
        port=port,
        workers=workers,
        reload=False,
    )


if __name__ == "__main__":
    run()
