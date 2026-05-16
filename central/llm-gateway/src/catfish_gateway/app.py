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
from fastapi import Depends, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402

from . import quota as _quota_module  # noqa: E402  五一 sprint 5/2 收尾: chat 后写 quota_events
from . import session_meta  # noqa: E402  BL-E16 关系建立: tick + inject 时间元
from .auth import User, get_current_user, get_current_user_optional  # noqa: E402
from .catalog import build_catalog  # noqa: E402
from .config import Config, load_config  # noqa: E402
from .employee_journal import inject_employee_journal  # noqa: E402
from .fallback import with_fallback  # noqa: E402
from .feedback_inject import inject_feedback  # noqa: E402  BL-MM6
from .gemini_guard import harden_for_gemini  # noqa: E402
from .identity_inject import (  # noqa: E402
    header_agent_prefs,
    header_skips_identity,
    inject_identity_if_needed,
)
from .inject_session_history import inject_session_history  # noqa: E402
from .metrics import log_request_metadata  # noqa: E402
from .multimodal_guard import route_to_vision_if_needed  # noqa: E402
from .multimodal_tool_unwrap import unwrap_tool_images  # noqa: E402
from .session_facts import inject_session_facts  # noqa: E402
from .session_summarizer import trigger_background_summary  # noqa: E402
from .skill_guard import inject_skill_guard  # noqa: E402
from .skills_inject import inject_skills_catalog  # noqa: E402
from .stats_guard import inject_stats_guard  # noqa: E402
from .tool_capability_guard import route_to_tool_capable_if_needed  # noqa: E402
from .tools_sanitizer import sanitize_tools  # noqa: E402

# Global setup

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("catfish.gateway")

# 5/5 鸿波报"日志文件不存在": 之前 gateway log 只到 stdout, 关掉 terminal 就丢了.
# 加 file handler 写 ~/Library/Logs/catfish/gateway.log (跟 macOS 习惯一致).
# 用 RotatingFileHandler 防无限增长 — 单文件 10MB, 保留 5 个轮替.
# CATFISH_LOG_FILE env 可换路径; CATFISH_LOG_FILE=- 表示禁用文件日志 (CI 用).
def _setup_file_logging() -> None:
    log_file = os.environ.get("CATFISH_LOG_FILE")
    if log_file == "-":
        return  # 显式禁用
    if not log_file:
        home = os.environ.get("HOME") or os.path.expanduser("~")
        log_file = os.path.join(home, "Library", "Logs", "catfish", "gateway.log")
    try:
        from logging.handlers import RotatingFileHandler  # noqa: PLC0415
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        h = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5)
        h.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        # 加到 root logger, 所有 catfish.* / uvicorn / litellm 日志都进文件
        logging.getLogger().addHandler(h)
        logger.info("file logging → %s (10MB × 5 rotation)", log_file)
    except Exception as e:
        logger.warning("file logging 启用失败 (继续仅 stdout): %s", e)


_setup_file_logging()

# Silence LiteLLM's verbose mode -- we don't want it printing prompts.
litellm.set_verbose = False
litellm.drop_params = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    app.state.config = config
    if _ENV_FILE_LOADED:
        logger.info("loaded .env from: %s", _ENV_FILE_LOADED)
    else:
        logger.info(".env not found -- using process env vars only")

    env_mode = os.environ.get("CATFISH_ENV", "dev")
    dev_token = os.environ.get("CATFISH_DEV_TOKEN", "dev-token-local")
    # Only show a fingerprint of the token, not the value itself
    token_preview = dev_token[:4] + "..." + dev_token[-4:] if len(dev_token) > 8 else "<short>"
    logger.info("mode=%s  dev_token_preview=%s", env_mode, token_preview)

    # BL-FIX37 (5/10): gateway 内部 loopback (proactive_starter / session_summarizer)
    # 用 internal-only token 调自己 /v1/chat/completions, 修 BL-FIX29 关掉员工
    # dev_token 后内部调用 401 的副作用. 没显式配 → 自动生成 32B random.
    from .auth.dev_token import ensure_internal_dev_token  # noqa: PLC0415
    ensure_internal_dev_token()

    # BL-MEMORY-MIGRATE-STEP1C (5/16): 注册 8 个内置 MemoryProvider 到全局 Registry.
    # chat_completions middleware 改成调 registry.inject_subset(), 替代 8 个分散
    # inject_X() 调用. identity 不在 Registry (它**创建** system, Registry 是
    # **追加**, 留 app.py 早期跑作 Registry 前置).
    from .memory.bootstrap import bootstrap_registry  # noqa: PLC0415
    bootstrap_registry()

    logger.info("catfish-gateway starting with %d model(s):", len(config.models))
    for m in config.models:
        logger.info(
            "  - %s [%s/%s] -> %s",
            m.name,
            m.tier,
            m.mode,
            m.upstream.api_base,
        )

    # 上游可达性自检：员工启动后立刻知道现在能调哪些 LLM，
    # 不必等到第一次对话时被 60s 超时教育。
    # 顺便把结果缓存到 app.state，/v1/catalog 直接读，避免每次列模型都探活。
    from .network import report_upstream_reachability  # noqa: PLC0415
    app.state.upstream_status = report_upstream_reachability(config.models)

    # 后台定期重探(60s 间隔), 让 catalog 反映 VPN 起停 / Clash 起停 / 服务器恢复等。
    # silent=True 不打 banner 免得日志被刷屏, 只用 logger.info 记一行变化。
    async def _refresh_loop() -> None:
        prev_reachable = {
            n: bool(s.get("reachable"))
            for n, s in app.state.upstream_status.items()
        }
        while True:
            try:
                await asyncio.sleep(60)
                new_status = report_upstream_reachability(config.models, silent=True)
                app.state.upstream_status = new_status

                # 只有"可达性变化"时打日志,避免每分钟刷一行噪音
                changes = []
                for name, s in new_status.items():
                    now_reach = bool(s.get("reachable"))
                    if prev_reachable.get(name) != now_reach:
                        arrow = "✓→" if now_reach else "✗→"
                        changes.append(f"{name}: {arrow} {s.get('reason')}")
                        prev_reachable[name] = now_reach
                if changes:
                    logger.info(
                        "upstream reachability changed: %s",
                        " | ".join(changes),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("upstream refresh loop error (continuing)")

    refresh_task = asyncio.create_task(_refresh_loop())

    # BL-D3 (5/9) Phase 1 收尾: mcp-registry 反向代理用的 httpx client.
    # 长 lifecycle 单例, 复用连接池. lifespan close 时 aclose. 跑在 gateway
    # 同 process 不另起服务 — Companion 走 gateway 单 origin /v1/mcp/*.
    app.state.mcp_registry_client = httpx.AsyncClient(
        timeout=config.mcp_registry.timeout,
    )

    # BL-D2 (5/10): skills-hub 反代 httpx client, 同模式
    app.state.skills_hub_client = httpx.AsyncClient(
        timeout=config.skills_hub.timeout,
    )

    # 五一 sprint Day 5 (B 方案): 启动时自动 register 到 catfish-identity registry.
    # 这样别的 catfish 实例 (Plan D Federation) 能通过 lookup 找到本机.
    # 失败不阻塞启动 (a2a 不可用, 其他功能正常).
    try:
        from .a2a_self_register import self_register  # noqa: PLC0415
        await self_register()
    except Exception as e:
        logger.warning("a2a_self_register 失败 (Plan D A2A 不可用): %s", e)

    # BL-HERMES013-4 (5/12 鸿波拍板): 启动时 reap 上次崩前没流完的 in-flight stream.
    # 写一条 'interrupted_resumed' audit 留痕迹, 然后 unlink 文件 (防累积).
    # 失败不阻塞启动 (audit 写不成也只是少一条记录, 文件总会被清).
    try:
        from . import inflight_streams  # noqa: PLC0415
        reaped = inflight_streams.reap_interrupted()
        if reaped > 0:
            logger.info(
                "BL-HERMES013-4: 启动 reap 清掉 %d 个 interrupted in-flight stream",
                reaped,
            )
    except Exception as e:
        logger.warning("BL-HERMES013-4 reap_interrupted 失败 (静默): %s", e)

    # BL-Q3-ARCHIVE (5/11): tool message archive 后台 haiku 摘要 worker.
    # 异步扫 tool_archives 表 (summary IS NULL), 调 gateway loopback chat 走
    # tool_summarizer use_case (haiku tier=private). 失败 silent, 不阻塞 chat.
    archive_summary_task = None
    try:
        from .tool_archive.summary_worker import start_summary_worker  # noqa: PLC0415
        archive_summary_task = start_summary_worker()
        if archive_summary_task is not None:
            logger.info("BL-Q3-ARCHIVE summary_worker 已启动")
    except Exception as e:
        logger.warning("BL-Q3-ARCHIVE summary_worker 启动失败 (archive 仍能写, 只是不摘要): %s", e)

    # BL-LEARN-RECMODE V2 #67 (5/15): 隐私 — 启动时清 14 天前 recordings,
    # 后台 task 每 24h 重跑一次. 截图含业务数据不能永久留, 跟 macOS / iCloud
    # 14 天回收同模式. opt-in '保留作 ground truth' 跳过.
    recmode_cleanup_task = None
    try:
        from .recmode import cleanup as _recmode_cleanup  # noqa: PLC0415
        # 启动时同步跑一次
        stats0 = _recmode_cleanup.cleanup_old_recordings()
        if stats0["scanned"] > 0:
            logger.info(
                "RecMode cleanup [startup]: scanned=%d deleted=%d kept_forever=%d freed=%.1f MB",
                stats0["scanned"], stats0["deleted"], stats0["kept_forever"],
                stats0["freed_bytes"] / 1024 / 1024,
            )
        # 起后台 daemon
        recmode_cleanup_task = asyncio.create_task(_recmode_cleanup.cleanup_daemon())
    except Exception as e:
        logger.warning("RecMode cleanup 启动失败 (recordings 不会自动清): %s", e)

    yield

    if recmode_cleanup_task is not None:
        recmode_cleanup_task.cancel()
        try:
            await recmode_cleanup_task
        except asyncio.CancelledError:
            pass

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

# CORS：Tauri webview / Companion App 跨域调 gateway 必须放过 OPTIONS preflight。
# Dev 阶段开放所有源；P1 上线后改成白名单：tauri://localhost / companion 域名 / 公司 SaaS 域名。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # 不带 cookie，dev token 走 Authorization 头
    allow_methods=["*"],      # 含 OPTIONS / POST / GET 等
    allow_headers=["*"],      # 含 Authorization / Content-Type / X-Catfish-* 等
)


# 五一 sprint Day 4 (BL-M4.3): Plan D · A2A SSE endpoint (B 端).
# /a2a/ask 接收其他 catfish 实例的问询, 验 JWT + 隐私拦截 + 转 LLM 流式返流.
try:
    from .a2a_server import build_a2a_router  # noqa: PLC0415
    app.include_router(build_a2a_router())
    logger.info("a2a_server: /a2a/ask SSE endpoint 已挂载")
except Exception as e:
    logger.warning("a2a_server 挂载失败 (Plan D 不可用): %s", e)

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

# BL-ARCH1 P1 (5/10): identity-server admin 反代, /api/admin/* → :8998
try:
    from .admin_proxy import router as admin_router  # noqa: PLC0415
    app.include_router(admin_router)
    logger.info("admin_proxy: /api/admin/* 反代已挂载")
except Exception as e:
    logger.warning("admin_proxy 挂载失败: %s", e)

# BL-Q3-FACT P0 MVP (5/10): 事实补丁系统 — 政策变更 → diff → 找受影响 skill → 生成 patch
# 数据落 ~/.catfish/facts/<id>/ (jsonl 临时, Q3 P1 迁 PG).
try:
    from .facts_router import router as facts_router  # noqa: PLC0415
    app.include_router(facts_router)
    logger.info("facts_router: /api/facts/* 已挂载 (BL-Q3-FACT P0 MVP)")
except Exception as e:
    logger.warning("facts_router 挂载失败: %s", e)

# BL-Q3-ARCHIVE (5/11): tool message archive 路由.
# /api/tool-archives/read  — LLM 调 catfish_read_tool_archive 工具走这条
# /api/tool-archives/{ref} — admin 自查 / debug 用
# /api/tool-archives/gc    — 手动 GC (sysadmin)
try:
    from .tool_archive.router import router as tool_archive_router  # noqa: PLC0415
    app.include_router(tool_archive_router)
    logger.info("tool_archive_router: /api/tool-archives/* 已挂载 (BL-Q3-ARCHIVE)")
except Exception as e:
    logger.warning("tool_archive_router 挂载失败: %s", e)


# Plan D · A 端内部 endpoint — tool-bridge 通过 HTTP 调这个触发 A2A.
# 简化版: 收完整 SSE 流, 一次返给 tool-bridge (不流式 UX, Phase 2 升级 Companion 直连 SSE).
@app.post("/a2a/internal/ask")
async def a2a_internal_ask(req: dict) -> dict:
    """tool-bridge → gateway: 帮我问 to_sub 这个问题.

    body: {from_sub, to_sub, question, purpose?, context_hint?, max_tokens?}
    返: {ok, answer, audit_id_remote, error?}
    """
    from .a2a_client import ask_remote_agent  # noqa: PLC0415

    from_sub = (req.get("from_sub") or "").strip()
    to_sub = (req.get("to_sub") or "").strip()
    question = (req.get("question") or "").strip()
    if not from_sub or not to_sub or not question:
        return {"ok": False, "error": "from_sub / to_sub / question 必填"}

    chunks: list[str] = []
    try:
        async for chunk in ask_remote_agent(
            from_sub=from_sub,
            to_sub=to_sub,
            question=question,
            purpose=req.get("purpose", ""),
            context_hint=req.get("context_hint", ""),
            max_tokens=int(req.get("max_tokens") or 500),
        ):
            chunks.append(chunk)
    except PermissionError as e:
        return {"ok": False, "error": str(e), "error_type": "denied"}
    except ConnectionError as e:
        return {"ok": False, "error": str(e), "error_type": "connection"}
    except Exception as e:
        return {"ok": False, "error": str(e), "error_type": "internal"}

    return {
        "ok": True,
        "answer": "".join(chunks),
        "chunks_count": len(chunks),
    }


# Health


@app.get("/health")
@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "catfish-gateway"}


# Quota — 五一 sprint 5/3, BL-D9
#
# 给 Companion Dashboard 的 QuotaCard 用. 返当前用户三维 quota:
#   - per-user 1 minute
#   - per-user 1 day
#   - per-department 1 day (没设部门或无部门 quota → limit=0=不限)
#
# limit=0 在 Companion 侧渲染成 "不限".


@app.get("/api/quota/me")
async def quota_me(user: User = Depends(get_current_user)) -> dict[str, Any]:
    from . import quota  # 懒 import 避免顶层循环

    config = quota.load_quota_config()
    user_q = config.per_user_for(user.sub)

    now_ms = int(time.time() * 1000)
    minute_cutoff = now_ms - 60_000
    day_cutoff = now_ms - 86_400_000

    used_minute = quota.sum_tokens_user_since(user.sub, minute_cutoff)
    used_day = quota.sum_tokens_user_since(user.sub, day_cutoff)

    dept_used_day = 0
    dept_limit_day = 0
    if user.department:
        dept_used_day = quota.sum_tokens_dept_since(user.department, day_cutoff)
        dept_q = config.department_quotas.get(user.department)
        if dept_q is not None:
            dept_limit_day = dept_q.tokens_per_day

    return {
        "user_email": user.sub,
        "department": user.department,
        "minute": {
            "used": used_minute,
            "limit": user_q.tokens_per_minute,  # 0 = 不限
        },
        "day": {
            "used": used_day,
            "limit": user_q.tokens_per_day,
        },
        "department_day": {
            "used": dept_used_day,
            "limit": dept_limit_day,
        },
    }


# /api/me — 当前用户信息 (Companion 用来按角色 conditional render Dashboard)
#
# 五一 sprint 5/2 RBAC.


@app.get("/api/me")
async def api_me(user: User = Depends(get_current_user)) -> dict[str, Any]:
    """返当前 user 元信息. Companion useMe() 调."""
    return {
        "email": user.sub,
        "department": user.department,
        "role": user.role,
        "managed_departments": user.managed_departments or [],
        "auth_method": user.auth_method,
    }


# ── /api/sessions/me/* — Sessions 浏览 + 跨日搜索 (5/12 借鉴 hermes-desktop A) ──
#
# 数据源: 员工自己 mac ~/.hermes/state.db (read-only sqlite, 跟 inject_session_history 同源).
# 不需要 RBAC 二次校验 — db 本来就只有自己的 (gateway 跑在员工 mac, 物理隔离).
# catfish-web 浏览器调本地 gateway (vite proxy / nginx 反代到 localhost:8999).


@app.get("/api/sessions/me")
async def api_sessions_list(
    user: User = Depends(get_current_user),
    days_back: int = 30,
    q: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """列员工自己的 hermes session 历史 + 跨日搜索 + 分页.

    Args:
        days_back: 看过去多少天的 session, 默认 30
        q: 关键字 (任意 message content 含 q 命中, 大小写不敏感)
        limit: 1-200 默认 50
        offset: ≥0 默认 0
    """
    from . import sessions_browse  # noqa: PLC0415
    return {
        "sessions": sessions_browse.list_sessions(
            days_back=days_back,
            search_q=q,
            limit=limit,
            offset=offset,
        ),
        "total": sessions_browse.count_sessions(days_back=days_back, search_q=q),
        "limit": max(1, min(200, int(limit))),
        "offset": max(0, int(offset)),
        "days_back": max(0, int(days_back)),
        "q": q,
        "viewer": user.sub,
    }


@app.get("/api/sessions/me/search")
async def api_sessions_search(
    q: str,
    user: User = Depends(get_current_user),
    days_back: int = 30,
    limit: int = 50,
) -> dict[str, Any]:
    """跨 session 全文搜索 — 命中行级别返 (跳到对应 session 用).

    跟 /api/sessions/me?q=X 区别:
      - list 是 session 级 (整个 session 含 q 即命中, 显示 session 卡片)
      - search 是 message 级 (含 q 的具体行 + ±50 字符上下文)
    """
    from . import sessions_browse  # noqa: PLC0415
    return {
        "matches": sessions_browse.search_messages(
            q=q, days_back=days_back, limit=limit,
        ),
        "q": q,
        "viewer": user.sub,
    }


@app.get("/api/sessions/me/{session_id}")
async def api_sessions_detail(
    session_id: str,
    user: User = Depends(get_current_user),
    max_messages: int = 500,
) -> dict[str, Any]:
    """单个 session 详情 + messages 数组. 不存在返 404.

    max_messages: 1-2000, 默认 500.
    """
    from . import sessions_browse  # noqa: PLC0415
    detail = sessions_browse.get_session(session_id, max_messages=max_messages)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail=f"session {session_id} 不存在 (已被 hermes 清理 / id 错)",
        )
    detail["viewer"] = user.sub
    return detail


# ── /api/tasks/me — Multi-Agent Kanban 单员工任务看板 (BL-HERMES013-RED-2 5/13) ──
#
# scope 1 (鸿波 5/13 22:35 拍板): 单员工本地任务聚合, 跨员工跨设备等 BL-RBAC sprint
# 后做 task_manager 中心 DB 持久化再扩.
#
# 数据源:
#  - ~/.catfish/tasks.jsonl              tool-bridge task_manager (catfish_run_task)
#  - ~/.catfish/a2a_notifications.jsonl  a2a 收件 (BL-FED2.6, 别人来求助)
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


@app.post("/api/learn/start_recording")
async def api_learn_start_recording(
    body: dict,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """开 RecMode session — 连 Catfish Chrome CDP + 起后台 listener.

    Body: {"session_id": str, "chrome_ws": str (可选, 默认 ws://localhost:9222)}

    Returns: {session_id, started_at, output_dir}
    """
    from .recmode import cdp_listener  # noqa: PLC0415
    session_id = (body.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id 不能空")
    chrome_ws = (body.get("chrome_ws") or "ws://localhost:9222").strip()
    # connect_ws 可由 caller 关 (test / dev 不真连 Chrome 时), 默认真连
    connect_ws = bool(body.get("connect_ws", True))
    try:
        info = await cdp_listener.start_recording(
            session_id, chrome_ws=chrome_ws, connect_ws=connect_ws,
        )
        info["viewer"] = user.sub
        return info
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except RuntimeError as e:
        # ws 连接失败 / websockets 没装 → 503
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.post("/api/learn/stop_recording")
async def api_learn_stop_recording(
    body: dict,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """停 RecMode session — flush events.jsonl + meta.json.

    Body: {"session_id": str}

    Returns: meta dict (events_count, keyframes_count, duration_s, output_dir)
    """
    from .recmode import cdp_listener  # noqa: PLC0415
    session_id = (body.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id 不能空")
    try:
        summary = await cdp_listener.stop_recording(session_id)
        summary["viewer"] = user.sub
        return summary
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.post("/api/learn/record_transcript")
async def api_learn_record_transcript(
    body: dict,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """RecMode A (5/14 ship): Companion 把 whisper.cpp 转写出来的字符串落到
    `~/.catfish/recordings/<session_id>/transcripts.jsonl`, 给 aggregator 用.

    BL-VOICE3 现状返字符串没写文件, 这是中间桥接 endpoint.

    Body: {"session_id": str, "text": str, "ts_offset"?: float (默认 0,
        相对 RecMode session 起始时间偏移), "duration"?: float}

    Returns: {ok: True, transcripts_path: str, lines_count: int}
    """
    session_id = (body.get("session_id") or "").strip()
    text = (body.get("text") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id 不能空")
    if not text:
        return {"ok": True, "transcripts_path": "", "lines_count": 0, "skipped": "empty text"}
    ts_offset = float(body.get("ts_offset") or 0.0)
    duration = float(body.get("duration") or 0.0)

    catfish_home = os.environ.get("CATFISH_HOME", "").strip()
    rec_root = (
        Path(catfish_home).expanduser() / "recordings" if catfish_home
        else Path.home() / ".catfish" / "recordings"
    )
    sd = rec_root / session_id
    if not sd.exists():
        raise HTTPException(status_code=404, detail=f"session_dir {sd} 不存在")

    path = sd / "transcripts.jsonl"
    record = {"ts": ts_offset, "duration": duration, "text": text}
    import json as _json
    with path.open("a", encoding="utf-8") as f:
        f.write(_json.dumps(record, ensure_ascii=False) + "\n")
    lines_count = sum(1 for _ in path.open("r", encoding="utf-8"))
    return {
        "ok": True,
        "transcripts_path": str(path),
        "lines_count": lines_count,
        "viewer": user.sub,
    }


@app.get("/api/learn/skill_content")
async def api_learn_skill_content(
    skill_dir: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """RecMode B (5/14 ship): preview UI 读 SKILL.md + main.py 文件内容显示.

    Companion Tauri webview 沙箱读不了任意路径, 需要 endpoint 代理.
    skill_dir 必须在 ~/.catfish/skills/ 或 ~/.catfish/recordings/ 下,
    防 path traversal.

    Query: ?skill_dir=/Users/.../personal/skill_x

    Returns: {skill_md: str, main_py: str, recmode_meta: dict | None}
    """
    sd = Path(skill_dir).expanduser().resolve()
    # path traversal 防御 — 必须在受信路径下
    home = Path.home().resolve()
    allowed_roots = [
        home / ".catfish" / "skills",
        home / ".catfish" / "recordings",
    ]
    catfish_home = os.environ.get("CATFISH_HOME", "").strip()
    if catfish_home:
        cf = Path(catfish_home).expanduser().resolve()
        allowed_roots += [cf / "skills", cf / "recordings"]
    if not any(str(sd).startswith(str(r)) for r in allowed_roots):
        raise HTTPException(
            status_code=403,
            detail=f"skill_dir {sd} 不在受信路径下 (~/.catfish/skills/ 或 recordings/)",
        )
    if not sd.exists():
        raise HTTPException(status_code=404, detail=f"skill_dir {sd} 不存在")

    def _read_or_empty(p: Path) -> str:
        try:
            return p.read_text(encoding="utf-8") if p.exists() else ""
        except OSError:
            return ""

    import json as _json
    meta = None
    meta_path = sd / "recmode_meta.json"
    if meta_path.exists():
        try:
            meta = _json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = None

    return {
        "skill_dir": str(sd),
        "skill_md": _read_or_empty(sd / "SKILL.md"),
        "main_py": _read_or_empty(sd / "main.py"),
        "recmode_meta": meta,
        "viewer": user.sub,
    }


@app.post("/api/learn/save_skill")
async def api_learn_save_skill(
    body: dict,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """RecMode C (5/14 ship): 用户 review preview 后点'保存', 把 draft skill
    从 recordings/<sid>/skill_draft/ 移到 ~/.catfish/skills/<namespace>/<name>/.

    Body: {"draft_dir": str, "namespace"?: str (覆盖 SKILL.md 默认),
           "name"?: str (覆盖 SKILL.md 默认)}

    Returns: {final_dir: str, moved: bool}
    """
    import shutil
    draft_dir_str = (body.get("draft_dir") or "").strip()
    if not draft_dir_str:
        raise HTTPException(status_code=400, detail="draft_dir 不能空")
    draft_dir = Path(draft_dir_str).expanduser().resolve()
    home = Path.home().resolve()
    catfish_home = os.environ.get("CATFISH_HOME", "").strip()
    rec_root = (
        Path(catfish_home).expanduser().resolve() / "recordings" if catfish_home
        else home / ".catfish" / "recordings"
    )
    skills_root = (
        Path(catfish_home).expanduser().resolve() / "skills" if catfish_home
        else home / ".catfish" / "skills"
    )
    if not str(draft_dir).startswith(str(rec_root)):
        raise HTTPException(status_code=403, detail=f"draft_dir 必须在 {rec_root} 下")
    if not draft_dir.exists():
        raise HTTPException(status_code=404, detail=f"draft_dir {draft_dir} 不存在")

    # 从 SKILL.md / recmode_meta.json 读 namespace + name (caller 可覆盖)
    meta_path = draft_dir / "recmode_meta.json"
    namespace = (body.get("namespace") or "").strip()
    name = (body.get("name") or "").strip()
    if (not namespace or not name) and meta_path.exists():
        import json as _json
        try:
            meta = _json.loads(meta_path.read_text(encoding="utf-8"))
            raw = meta.get("raw_llm_json") or {}
            namespace = namespace or raw.get("namespace", "personal")
            name = name or raw.get("skill_name", "")
        except Exception:
            pass
    if not namespace or not name:
        raise HTTPException(
            status_code=422,
            detail="缺 namespace / name (recmode_meta.json 也没): 请显式传",
        )

    final_dir = skills_root / namespace / name
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    if final_dir.exists():
        # 已存在 (重名), 备份老的然后覆盖
        backup = final_dir.with_name(f"{name}.bak.{int(time.time())}")
        final_dir.rename(backup)
    shutil.copytree(draft_dir, final_dir)

    # V2 #67: opt-in '保留作 ground truth' — 用户勾了 keep_forever, 写 flag
    # 到 session_dir 的 .keep_forever (cleanup 跳过整个 session_dir)
    # + 写 _keep_forever: true 到 recmode_meta.json (双重保险)
    keep_forever = bool(body.get("keep_forever", False))
    if keep_forever:
        # 找 draft_dir 上面的 session_dir (recordings/<sid>/skill_draft/<ns>/<name>/)
        try:
            session_dir = draft_dir.parent.parent.parent  # <ns> → skill_draft → <sid>
            (session_dir / ".keep_forever").touch()
            # 同时写到 recmode_meta.json
            meta_path = final_dir / "recmode_meta.json"
            if meta_path.exists():
                import json as _json
                meta = _json.loads(meta_path.read_text(encoding="utf-8"))
                meta["_keep_forever"] = True
                meta_path.write_text(
                    _json.dumps(meta, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
        except Exception:  # noqa: BLE001
            logger.warning("save_skill: keep_forever flag 写失败 (不致命)", exc_info=True)

    return {
        "ok": True,
        "final_dir": str(final_dir),
        "namespace": namespace,
        "name": name,
        "moved": True,
        "keep_forever": keep_forever,
        "viewer": user.sub,
    }


@app.post("/api/learn/repair_selector")
async def api_learn_repair_selector(
    body: dict,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """V2 #68 selector 漂移修复: skill 跑时 find_by_text(hint) 找不到 →
    catfish_browser_runtime POST 这里, gateway 调 main vision 看截图找新 selector.

    Body: {
        "hint": {"text": "应用", "near_text": "通讯录", "role": "tab"},
        "screenshot_b64": str,
        "context": str (录制时语音转写, 可空)
    }

    Returns: {found, text, near_text, role, confidence, reason}
    """
    from .recmode import selector_repair  # noqa: PLC0415
    hint = body.get("hint") or {}
    screenshot = (body.get("screenshot_b64") or "").strip()
    context = (body.get("context") or "").strip()
    if not hint:
        raise HTTPException(status_code=400, detail="hint 不能空")
    if not screenshot:
        raise HTTPException(status_code=400, detail="screenshot_b64 不能空 (vision 必须看图)")

    auth_token = os.environ.get("CATFISH_DEV_TOKEN", "")
    try:
        out = await selector_repair.repair_selector(
            hint=hint,
            screenshot_b64=screenshot,
            context=context,
            auth_token=auth_token,
        )
        out["viewer"] = user.sub
        return out
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.post("/api/learn/cleanup")
async def api_learn_cleanup(
    body: dict,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """V2 #67: 手动触发 cleanup (admin / 测试用 — 不等 24h daemon).

    Body: {"dry_run"?: bool (默认 false 真删), "ttl_days"?: int (默认 14)}
    Returns: cleanup_old_recordings stats
    """
    from .recmode import cleanup as _recmode_cleanup  # noqa: PLC0415
    dry_run = bool(body.get("dry_run", False))
    ttl_days = int(body.get("ttl_days") or 14)
    stats = _recmode_cleanup.cleanup_old_recordings(
        ttl_seconds=ttl_days * 24 * 3600,
        dry_run=dry_run,
    )
    stats["viewer"] = user.sub
    return stats


@app.post("/api/learn/test_skill")
async def api_learn_test_skill(
    body: dict,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """RecMode E (5/14 ship): preview 'pen 跑一次试' 触发 — 走 hermes runtime
    跑这个 skill, 拿结果回. 因 catfish_browser_* 等 tool 必须 hermes adapter
    dispatch_native, 这里 spawn 一个 catfish-cli sub-process 跑.

    简化 v1 策略: subprocess.run 跑 `catfish` CLI 一次, 喂 prompt 让它调
    catfish_run_skill. 拿 stdout 返. 30s 超时.

    Body: {"skill_dir": str, "params"?: dict}

    Returns: {ok, output, duration_s}
    """
    import subprocess
    skill_dir_str = (body.get("skill_dir") or "").strip()
    params = body.get("params") or {}
    if not skill_dir_str:
        raise HTTPException(status_code=400, detail="skill_dir 不能空")
    sd = Path(skill_dir_str).expanduser().resolve()
    if not sd.exists():
        raise HTTPException(status_code=404, detail=f"skill_dir {sd} 不存在")

    # 从 skill_dir 推 namespace/name (路径形如 ~/.catfish/skills/personal/skill_x)
    parts = sd.parts
    if len(parts) < 2:
        raise HTTPException(status_code=422, detail="skill_dir 路径格式不对")
    name = parts[-1]
    namespace = parts[-2]
    skill_path_arg = f"{namespace}/{name}"

    import json as _json
    params_json = _json.dumps(params, ensure_ascii=False)
    prompt = f"调 catfish_run_skill(skill_path='{skill_path_arg}', params={params_json}). 跑完总结结果."

    start = time.time()
    try:
        proc = subprocess.run(
            ["catfish", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=120.0,
        )
        duration = time.time() - start
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "")[:8000],
            "stderr": (proc.stderr or "")[:2000],
            "duration_s": round(duration, 1),
            "skill_path": skill_path_arg,
            "viewer": user.sub,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": "skill 跑超 120s (改 prompt 加'你只跑一次 不要等' 减时间)",
            "duration_s": 120.0,
            "skill_path": skill_path_arg,
            "viewer": user.sub,
        }
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail="catfish CLI 不在 PATH. 安装 catfish-cli 后再试 (or skill_dir 直接 python main.py 见 docs)",
        )


@app.post("/api/learn/analyze")
async def api_learn_analyze(
    body: dict,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """触发 RecMode aggregator: 读 session_dir → 调 catfish-private-main →
    解析 JSON → 落 SKILL.md + main.py 到 ~/.catfish/skills/<namespace>/<name>/.

    Body: {"session_id": str, "skills_root": str (可选, 默认 ~/.catfish/skills)}

    Returns: {skill_name, namespace, skill_dir, steps_count, confidence,
              questions_for_user}

    Caller (Companion): 录屏完点 ✅ → 先 POST /stop_recording → 再 POST /analyze
    → 拿 skill_dir → 读 SKILL.md / main.py 显 preview UI 给用户 review.
    """
    from .recmode import aggregator  # noqa: PLC0415
    session_id = (body.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id 不能空")

    # 找 session_dir — caller 可显式传, 否则按 cdp_listener 默认路径推
    skills_root_str = (body.get("skills_root") or "").strip()
    skills_root = Path(skills_root_str).expanduser() if skills_root_str else None

    catfish_home = os.environ.get("CATFISH_HOME", "").strip()
    rec_root = (
        Path(catfish_home).expanduser() / "recordings" if catfish_home
        else Path.home() / ".catfish" / "recordings"
    )
    session_dir = rec_root / session_id
    if not session_dir.exists():
        raise HTTPException(
            status_code=404,
            detail=f"session_dir {session_dir} 不存在. 先调 /start_recording 录一段.",
        )

    # auth_token 从 caller 透传 — 让 RecMode 走 caller 自己的 quota / RBAC.
    # dev token 路径直接复用; OIDC 路径 5/26 加.
    auth_token = os.environ.get("CATFISH_DEV_TOKEN", "")
    # RecMode C: 默认 draft_only — 落 session_dir/skill_draft/, 用户 review
    # 后调 /api/learn/save_skill 才正式. caller 显式传 false 跳过 draft 流程.
    draft_only = bool(body.get("draft_only", True))
    try:
        out = await aggregator.aggregate_session(
            session_dir,
            skills_root=skills_root,
            auth_token=auth_token,
            draft_only=draft_only,
        )
        out["viewer"] = user.sub
        return out
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@app.get("/api/learn/active")
async def api_learn_active(
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """列当前在录的 RecMode session (调试 / 监控用)."""
    from .recmode import cdp_listener  # noqa: PLC0415
    return {
        "active_session_ids": cdp_listener.list_active(),
        "viewer": user.sub,
    }


@app.get("/api/learn/status/{session_id}")
async def api_learn_status(
    session_id: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """RecMode F (5/14): 单 session 实时状态 — Companion 录制浮层 polling.

    返 elapsed_s + events_count + keyframes_count + ws_connected.
    没在录中 → 404 (Companion 应停 polling).
    """
    from .recmode import cdp_listener  # noqa: PLC0415
    s = cdp_listener.session_status(session_id)
    if s is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} 没在录")
    s["viewer"] = user.sub
    return s


@app.get("/api/tasks/me")
async def api_tasks_list(
    user: User = Depends(get_current_user),
    hours_back: int = 48,
    limit: int = 200,
    source: str = "",
) -> dict[str, Any]:
    """列员工自己本地的任务 (background + a2a_inbox 聚合).

    Args:
        hours_back: 看过去几小时, 默认 48 (周一看周五跨天).
        limit: 最多返几张卡, 默认 200.
        source: 过滤 source 逗号分隔 (空 = 全要), 例 "background" / "a2a_inbox".

    Returns:
        {
          "cards": [TaskCard, ...],   # 倒序 (最新在前)
          "summary": {pending: N, running: N, waiting: N, completed: N, failed: N},
          "viewer": "<sub>",
          ...
        }
    """
    from . import tasks_browse  # noqa: PLC0415
    sources_filter: list[str] | None = None
    if source:
        sources_filter = [s.strip() for s in source.split(",") if s.strip()]
    cards = tasks_browse.list_my_tasks(
        hours_back=max(1, int(hours_back)) if hours_back > 0 else None,
        limit=max(1, min(1000, int(limit))),
        sources=sources_filter,
    )
    return {
        "cards": cards,
        "summary": tasks_browse.status_summary(cards),
        "total": len(cards),
        "limit": max(1, min(1000, int(limit))),
        "hours_back": int(hours_back),
        "viewer": user.sub,
    }


# /api/quota/department/{dept} — manager / admin 看本部门 quota 聚合
#
# 包含: 部门日 quota 用量 + 限额 + top N 员工 token 用量.
# RBAC: admin 全权, manager 限 managed_departments.


@app.get("/api/quota/department/{department}")
async def api_quota_department(
    department: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """部门级 quota 聚合 — manager 改 / 看本部门."""
    from . import quota  # 懒 import

    if not user.can_manage_department(department):
        raise HTTPException(
            status_code=403,
            detail=(
                f"role={user.role} 无权访问部门 {department} 的 quota. "
                f"managed_departments={user.managed_departments}"
            ),
        )

    config = quota.load_quota_config()
    now_ms = int(time.time() * 1000)
    day_cutoff = now_ms - 86_400_000

    dept_used_day = quota.sum_tokens_dept_since(department, day_cutoff)
    dept_q = config.department_quotas.get(department)
    dept_limit_day = dept_q.tokens_per_day if dept_q else 0

    # Top 员工 (按今日用量)
    top_users = quota.top_users_in_department(department, day_cutoff, limit=10)

    return {
        "department": department,
        "day": {
            "used": dept_used_day,
            "limit": dept_limit_day,  # 0 = 不限
        },
        "top_users": top_users,  # [{user_email, tokens_used}]
        "viewer_role": user.role,
    }


# /api/audit/department/{dept} — manager / admin 看本部门 audit 聚合
#
# 包含: 总请求数 / 总 token / 模型分布 / top 员工 (匿名化看部门级).
# RBAC: admin 全权, manager 限 managed_departments.


@app.get("/api/audit/department/{department}")
async def api_audit_department(
    department: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """部门级 audit 聚合 — manager 看本部门员工总用量分布."""
    from . import quota  # 懒 import (audit 数据从 quota_events sqlite 也能算)

    if not user.can_manage_department(department):
        raise HTTPException(
            status_code=403,
            detail=(
                f"role={user.role} 无权访问部门 {department} 的 audit. "
                f"managed_departments={user.managed_departments}"
            ),
        )

    now_ms = int(time.time() * 1000)
    day_cutoff = now_ms - 86_400_000

    summary = quota.audit_summary_dept_since(department, day_cutoff)
    return {
        "department": department,
        "since_ms": day_cutoff,
        **summary,  # request_count / total_tokens / by_model / by_user
        "viewer_role": user.role,
    }


# /api/quota/department/{dept} PUT — manager / admin 改本部门 quota
#
# 五一 sprint 5/2 收尾 RBAC: 写 quotas.yaml 的 overrides.departments.<dept>.tokens_per_day
# Body: { "tokens_per_day": int }   (0 表示不限)
# 改完 gateway 下一次请求自动加载新 yaml (load_quota_config 每次重读, 无 cache).


from pydantic import BaseModel as _BaseModel  # 局部 import 防顶层污染


class _DeptQuotaUpdate(_BaseModel):
    tokens_per_day: int


@app.put("/api/quota/department/{department}")
async def api_quota_department_update(
    department: str,
    body: _DeptQuotaUpdate,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """改部门日 quota. RBAC: admin 全权 / manager 限 managed_departments."""
    from . import quota

    if not user.can_manage_department(department):
        raise HTTPException(
            status_code=403,
            detail=(
                f"role={user.role} 无权改部门 {department} quota. "
                f"managed_departments={user.managed_departments}"
            ),
        )

    if body.tokens_per_day < 0:
        raise HTTPException(status_code=400, detail="tokens_per_day 不能负")

    ok = quota.update_department_quota(department, body.tokens_per_day)
    if not ok:
        raise HTTPException(status_code=500, detail="写 quotas.yaml 失败, 看 gateway log")

    return {
        "department": department,
        "tokens_per_day": body.tokens_per_day,
        "updated_by": user.sub,
        "ok": True,
    }


# /api/quota/global + /api/audit/global — admin 全员 / 全部门 / 全模型聚合
#
# RBAC: 严格 admin only. manager 看不到全局, 只看 managed_departments.


def _require_admin(user: User) -> None:
    if not user.is_admin():
        raise HTTPException(
            status_code=403,
            detail=f"role={user.role} 不能访问全局聚合 (admin only)",
        )


@app.get("/api/quota/global")
async def api_quota_global(
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """全员 quota 聚合 — admin 看 top 部门 / top 用户 / 总用量."""
    from . import quota
    _require_admin(user)

    now_ms = int(time.time() * 1000)
    day_cutoff = now_ms - 86_400_000

    return {
        "since_ms": day_cutoff,
        "top_departments": quota.top_departments(day_cutoff, limit=10),
        "viewer_role": user.role,
    }


@app.get("/api/audit/global")
async def api_audit_global(
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """全员 audit 聚合 — admin 看请求总数 / 模型分布 / 部门分布 / top 员工."""
    from . import quota
    _require_admin(user)

    now_ms = int(time.time() * 1000)
    day_cutoff = now_ms - 86_400_000

    summary = quota.audit_summary_global_since(day_cutoff)
    return {
        "since_ms": day_cutoff,
        **summary,
        "viewer_role": user.role,
    }


@app.get("/api/audit/events")
async def api_audit_events(
    user: User = Depends(get_current_user),
    since_ms: int | None = None,
    dept: str | None = None,
    user_filter: str | None = None,
    model: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """BL-ADMIN-AUDIT (5/12 鸿波): 全员 audit 逐条历史 + 4 维度筛选 + 分页.

    给 catfish-web /admin/quota/events 页用. RBAC 严格 admin only (sysadmin 走
    User.is_admin() 通过).

    Args:
        since_ms: 只看 ts_ms >= 这个的 (默认 24h 前)
        dept: department 过滤
        user_filter: user_email 过滤 (param 名 user_filter 避开跟 user dependency 撞)
        model: model 过滤
        status: 'ok' / 'error' / 'interrupted_resumed' (BL-HERMES013-4)
        limit: 1-200, 默认 50
        offset: ≥0, 默认 0

    Returns:
        {events: [...], total: int, limit, offset, since_ms, viewer_role}
    """
    from . import metrics as _metrics
    _require_admin(user)

    # since_ms 默认 24h 前
    if since_ms is None:
        since_ms = int(time.time() * 1000) - 86_400_000
    since_unix = since_ms // 1000

    # 防御 limit / offset 边界
    limit = max(1, min(200, int(limit)))
    offset = max(0, int(offset))

    events = _metrics.read_events(
        since_unix=since_unix,
        user_filter=user_filter or None,
        model_filter=model or None,
        status_filter=status or None,
        dept_filter=dept or None,
        limit=limit,
        offset=offset,
    )
    total = _metrics.count_events(
        since_unix=since_unix,
        user_filter=user_filter or None,
        model_filter=model or None,
        status_filter=status or None,
        dept_filter=dept or None,
    )

    return {
        "events": events,
        "total": total,
        "limit": limit,
        "offset": offset,
        "since_ms": since_ms,
        "filters": {
            "dept": dept or "",
            "user": user_filter or "",
            "model": model or "",
            "status": status or "",
        },
        "viewer_role": user.role,
    }


# /api/dev/users — 列出 dev 测试账号 (Companion 切换器用)
#
# 五一 sprint 5/2. 仅 dev 模式 (CATFISH_ENV != prod) 启用. 生产环境 404.
# 不需要 auth — 列表本身就是为了让没登录的人选账号. 包含 token 字段, dev 模式安全.


@app.get("/api/dev/users")
async def api_dev_users() -> dict[str, Any]:
    """返 dev_users.yaml 配置的所有测试账号.

    5/5 鸿波: 之前 prod 返 404, gateway log 每次 Companion 启动刷一条 404.
    改 prod 返 200 + 空列表 — 语义更对 ('prod 无 dev users' 而不是 'endpoint 不存在'),
    log 也干净. Companion DevUserSwitcher 拿空 list 自己 hide.
    """
    if os.environ.get("CATFISH_ENV", "dev").lower() == "prod":
        return {"users": []}
    from .auth.dev_token import list_dev_users
    return {"users": list_dev_users()}


# /api/proactive/starter — 主动闲聊 BL-E13 C-MVP (五一 sprint 5/2 收尾)
#
# Companion 调这个拿一句 LLM 生成的 starter, 显示在 Dashboard 卡 / macOS 通知里.


@app.get("/api/proactive/starter")
async def api_proactive_starter(
    user: User = Depends(get_current_user),  # noqa: ARG001  鉴权但不用 user 字段
) -> dict[str, Any]:
    """返一个上下文感知的 starter (引用员工 journal + 时段). 失败返 fallback 模板."""
    from . import proactive
    return await proactive.generate_starter()


# 5/6 BL-E13.5 真主动 Phase B: 信号触发的针对性 starter
@app.post("/api/proactive/contextual")
async def api_proactive_contextual(
    body: dict[str, Any],
    user: User = Depends(get_current_user),  # noqa: ARG001
) -> dict[str, Any]:
    """信号触发的 starter. body = {signal_kind: str, context: dict}.

    signal_kind: 'silence' | 'deadline' | 'focus'
    context: 各 kind 不同, 见 proactive.py _SIGNAL_KIND_PROMPTS

    失败返 source='fallback', frontend 用本地模板兜底.
    """
    from . import proactive
    signal_kind = (body.get("signal_kind") or "").strip()
    context = body.get("context") or {}
    if not signal_kind or not isinstance(context, dict):
        return {
            "starter": "",
            "context_hint": "missing signal_kind or context",
            "source": "fallback",
        }
    return await proactive.generate_contextual_starter(signal_kind, context)


# Capability-probe stubs
#
# Clients like Hermes probe well-known paths to figure out what kind of
# server they're talking to (Ollama, llama.cpp, OpenAI, …). We're
# OpenAI-compatible only -- returning minimal 200 payloads stops the probe
# noise without misleading the client into trying Ollama-native endpoints.


@app.get("/api/tags")
@app.get("/api/v1/models")
async def ollama_stub() -> dict[str, Any]:
    return {"models": []}


@app.get("/v1/props")
@app.get("/props")
async def llamacpp_stub() -> dict[str, Any]:
    return {}


@app.get("/version")
async def version_stub() -> dict[str, str]:
    return {"version": "catfish-gateway/0.1.0"}


# Model listing


def _model_info_payload(m) -> dict[str, Any]:
    """OpenAI-compatible model metadata.

    Clients like Hermes use it to decide context compression, tool support, etc.
    """
    return {
        "id": m.name,
        "object": "model",
        "owned_by": "catfish",
        "created": 1700000000,  # static is fine -- OpenAI itself rarely moves this
        # Extended fields many OpenAI-compatible clients inspect:
        "context_window": m.context_window,
        "max_context_length": m.context_window,
        "supports_tool_use": m.supports_tool_use,
        "supports_vision": m.supports_vision,
        "supports_streaming": m.supports_streaming,
        "tier": m.tier,
    }


@app.get("/v1/models")
async def list_models(user: User = Depends(get_current_user)) -> dict[str, Any]:
    """OpenAI-compatible model list. Hides models whose API key is not configured."""
    config: Config = app.state.config
    return {
        "object": "list",
        "data": [
            _model_info_payload(m)
            for m in config.models
            if user.can_access(m) and m.mode != "embedding" and m.upstream.is_available
        ],
    }


@app.get("/v1/models/{model_id}")
async def get_model(
    model_id: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """OpenAI-compatible single-model metadata endpoint."""
    config: Config = app.state.config
    m = config.get_model(model_id)
    if not m or not user.can_access(m) or not m.upstream.is_available:
        raise HTTPException(status_code=404, detail=f"model not found: {model_id}")
    return _model_info_payload(m)


@app.get("/v1/catalog")
async def get_catalog(
    user: User | None = Depends(get_current_user_optional),
) -> dict[str, Any]:
    """Hermes 启动时的模型选择清单。

    匿名也能调：字段全是公开信息（display_name / tier / 能力开关），
    不暴露 api_base 或 UUID。响应里有 `authenticated` 标志位让客户端
    知道当前是匿名视图还是个性化视图。
    """
    config: Config = app.state.config
    upstream_status = getattr(app.state, "upstream_status", None)
    return build_catalog(config, user, upstream_status)


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


def _safe_int_env(key: str, default: int) -> int:
    """env 读 int, 解析失败回 default. 防 yaml/.env 里值写歪了崩进程."""
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default


def _safe_float_env(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except ValueError:
        return default


def _compute_max_allowed_output_tokens(
    messages: list, tools: list | None, model
) -> int | None:
    """算当前 model 真实剩余 output 空间. 返 None 表示算不出 (cw=0).

    BL-MAX-TOKENS-DYNAMIC (5/15): cw - prompt_est*buffer - safety. 比硬编码 32K 优:
      - 短 prompt (5-10K) 输出空间 ~120K, 不再被 32K 上限卡住
      - 长 prompt (>96K) 自动留够 prompt 空间, 不会跑 OOM
    BL-TOKEN-COUNTER-LITELLM (5/15 22:00): estimator 走 LiteLLM token_counter, 认
      Llama/Qwen/Gemini/DeepSeek 各家 tokenizer, 准估 ±5%. 准估后 dyn 自然在剩余
      空间内, **不需要硬编码 HARD CAP** (那是偷懒, 鸿波拍过).
    BL-TOOLS-IN-ESTIMATE (5/15 22:00): tools schema 也算 prompt 一部分 (50 tools *
      200 token = 10K, 漏算会撞 ContextWindowExceeded).
    BL-ESTIMATE-ERROR-MARGIN (5/15 22:10): tokenizer 仍有 10-25% 误差 (系统 inject
      markdown / 特殊 token / chat format 差异), 用 buffer_factor (默认 1.3, env
      CATFISH_PROMPT_BUFFER_FACTOR 可调) 给 prompt_est 加成比例 buffer.
    BL-MAX-OUTPUT-TOKENS (5/15 22:31): context_window 跟 max_output_tokens 是俩字段:
      cw = prompt+output 总上限 (DeepSeek 1M); max_out = 单次 output 上限 (DeepSeek
      393K). dyn 必须 clip 到 min(cw, max_out), 否则撞 400 [1, 393216].
    """
    try:
        cw = int(getattr(model, "context_window", 0) or 0)
    except (TypeError, ValueError):
        cw = 0
    if cw <= 0:
        return None
    try:
        from .fallback import estimate_prompt_tokens  # noqa: PLC0415

        prompt_est = estimate_prompt_tokens(
            messages, model=model.upstream.model, tools=tools
        )
    except Exception:  # noqa: BLE001
        prompt_est = 0
    safety = _safe_int_env("CATFISH_MAX_TOKENS_SAFETY", 2048)
    buffer_factor = _safe_float_env("CATFISH_PROMPT_BUFFER_FACTOR", 1.3)
    dyn = cw - int(prompt_est * buffer_factor) - safety
    try:
        max_out = int(getattr(model, "max_output_tokens", 0) or 0)
    except (TypeError, ValueError):
        max_out = 0
    upper = min(cw, max_out) if max_out > 0 else cw
    return max(4096, min(dyn, upper))  # 下限 4K (防压成 0/负数), 上限取真实输出 cap


def _apply_max_tokens(params: dict, model) -> None:
    """In-place 决定 params['max_tokens']:

    - client 没传 → 用 _compute_max_allowed_output_tokens (dyn), 算不出走 32K 兜底
    - client 传了 → 仍 clip 到 dyn (BL-MAX-TOKENS-CLIP 防 fallback 切小 ctx 撞 400)
    """
    allowed = _compute_max_allowed_output_tokens(
        params.get("messages") or [], params.get("tools"), model
    )
    client_val = params.get("max_tokens")
    if "max_tokens" not in params or client_val is None:
        if allowed is not None:
            params["max_tokens"] = allowed
        else:
            params["max_tokens"] = _safe_int_env("CATFISH_MAX_TOKENS_FALLBACK", 32768)
        return
    # client 显式传了 — clip 到 model 真实容量 (BL-MAX-TOKENS-CLIP)
    if allowed is not None and isinstance(client_val, int) and client_val > allowed:
        cw = getattr(model, "context_window", 0) or 0
        logger.warning(
            "BL-MAX-TOKENS-CLIP: client 传 max_tokens=%d 超 model=%s 剩余空间 %d, "
            "clip 到 %d (context_window=%d, prompt 估算占用过大)",
            client_val, model.name, allowed, allowed, cw,
        )
        params["max_tokens"] = allowed


def _build_litellm_params(body: dict, model) -> dict:
    """Map gateway request -> litellm call params.

    `model.upstream.model` must already contain the LiteLLM provider prefix,
    e.g. 'openai/qwen_v3_5_122b_a10b' or 'gemini/gemini-2.5-pro'.
    """
    params = {k: v for k, v in body.items() if k != "model"}
    params.update(
        {
            "model": model.upstream.model,
            "api_key": model.upstream.api_key,
            # 网络错误快速失败，不走 LiteLLM 默认的重试，避免掩盖真实问题
            "num_retries": 0,
            "timeout": model.upstream.timeout,
        }
    )
    _apply_max_tokens(params, model)
    if model.upstream.api_base:
        params["api_base"] = model.upstream.api_base

    # BL-FIX34 (5/10 鸿波诊断): streaming 默认上游不送 usage chunk, gateway
    # 抽 prompt_tokens / completion_tokens 永远 0, audit 写 status=ok tokens=0,
    # quota_events 因 token=0 不记 (record_usage 的 if 条件: tokens>0). 改:
    # streaming 时自动注入 stream_options.include_usage=True, 让 OpenAI 兼容
    # 上游 (deepseek / vLLM v0.5+ Qwen) 在 [DONE] 前送一个 usage chunk.
    # Gemini / Anthropic 的 litellm 包装器 silently ignore 这字段, 无副作用.
    if params.get("stream"):
        so = params.get("stream_options")
        if not isinstance(so, dict):
            so = {}
        if "include_usage" not in so:
            so["include_usage"] = True
        params["stream_options"] = so

    # Apply per-model forced overrides (e.g. Gemini 3 requires temperature=1.0)
    if model.upstream.param_overrides:
        before = {k: params.get(k) for k in model.upstream.param_overrides}
        params.update(model.upstream.param_overrides)
        changed = {
            k: {"from": before[k], "to": v}
            for k, v in model.upstream.param_overrides.items()
            if before[k] != v
        }
        if changed:
            logger.info("applied param_overrides for %s: %s", model.name, changed)

    return params


def _check_context_usage(model, prompt_tokens: int, user_sub: str) -> None:
    """看本次请求的 prompt_tokens 相对 context_window 的占用。

    两档告警：
        >= 80%  -> WARNING（员工该注意了，建议 /compress 或切 Gemini Pro）
        >= 100% -> ERROR（已经超配，模型可能在跑"侥幸能吃"的路径，下次可能崩）

    不阻断请求，只记日志。实际拒绝交给上游模型自己报 413 / context_length_exceeded。

    建议员工动作（Hermes 0.10 已内置）：
        /compress [focus topic]   手动压缩当前会话，保留指定主题
        /sb                       实时看 ctx 利用率状态栏
        /model <模型名>            切其他模型（Gemini Pro 2M 更适合长 context）
        /new                      开新会话（彻底清空，memory 保留）
    """
    cw = getattr(model, "context_window", 0) or 0
    if cw <= 0 or prompt_tokens <= 0:
        return
    pct = prompt_tokens / cw
    if pct >= 1.0:
        logger.error(
            "context overflow: model=%s prompt_tokens=%d context_window=%d (%.0f%%) "
            "user=%s — 上游可能拒绝。建议员工立刻在 Hermes 里敲 /compress 压缩，"
            "或 /model catfish-public-gemini-pro 切到 2M 上下文模型",
            model.name, prompt_tokens, cw, pct * 100, user_sub,
        )
    elif pct >= 0.8:
        logger.warning(
            "context near limit: model=%s prompt_tokens=%d context_window=%d (%.0f%%) "
            "user=%s — 建议 /compress [当前任务主题] 压缩会话，或 /model 切长上下文模型",
            model.name, prompt_tokens, cw, pct * 100, user_sub,
        )


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


def _http_code_for_upstream(err_msg: str) -> int:
    """Map an upstream error message to a sensible HTTP status code."""
    low = err_msg.lower()
    if "429" in low or "rate limit" in low or "quota" in low:
        return 429
    if "401" in low or "unauthorized" in low or "invalid api key" in low:
        return 401
    if "timeout" in low or "timed out" in low:
        return 504
    return 502


def _raise_upstream_error(
    exc: Exception,
    *,
    user_sub: str,
    model_name: str,
    latency_ms: float,
    log_context: str,
) -> None:
    """Log metrics + logger.exception + raise HTTPException for upstream errors.

    Factored out because both chat completions (non-stream) and embeddings share
    the exact same error-handling path.

    给客户端的 detail 里同时返回 raw `message` (诊断用) + 翻译过的 `friendly`
    (员工 UI 直接显示给员工看的话), 让 Companion 前端可以二选一.
    """
    err_type = type(exc).__name__
    err_msg = str(exc)[:400]

    # BL-FALLBACK-PROMPT-CAP (5/14): 大 prompt + 内网失败 + 公网被 cap 拦截 →
    # 这是设计行为, 不是上游 bug. 转 503 + 友好消息, 不写 status=error 避免误算.
    from .fallback import LargePromptFallbackBlocked  # noqa: PLC0415
    if isinstance(exc, LargePromptFallbackBlocked):
        log_request_metadata(
            user=user_sub,
            model=model_name,
            latency_ms=latency_ms,
            status="fallback_capped",  # 区别于 'error' 让 audit 看清是 cap 拦的
            error=f"LargePromptFallbackBlocked: prompt={exc.prompt_estimate} cap={exc.cap}",
        )
        logger.warning(
            "BL-FALLBACK-PROMPT-CAP triggered: %s prompt=%d cap=%d primary_err=%s",
            model_name, exc.prompt_estimate, exc.cap, type(exc.last_exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "large_prompt_fallback_blocked",
                "error_type": "LargePromptFallbackBlocked",
                "message": exc.friendly_message(),
                "friendly": exc.friendly_message(),
                "model": model_name,
                "prompt_estimate": exc.prompt_estimate,
                "cap": exc.cap,
                "blocked_chain": exc.blocked_chain,
                "latency_ms": round(latency_ms, 1),
            },
        ) from exc

    log_request_metadata(
        user=user_sub,
        model=model_name,
        latency_ms=latency_ms,
        status="error",
        error=f"{err_type}: {err_msg[:200]}",
    )
    logger.exception("%s [%s @ %.0fms]", log_context, model_name, latency_ms)
    raise HTTPException(
        status_code=_http_code_for_upstream(err_msg),
        detail={
            "error": "upstream error",
            "error_type": err_type,
            "message": err_msg[:300],
            "friendly": _friendly_upstream_error(err_msg),
            "model": model_name,
            "latency_ms": round(latency_ms, 1),
        },
    ) from exc


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


async def _fake_sse_response(
    text: str,
    *,
    model_name: str = "catfish-client-cmd",
) -> AsyncIterator[str]:
    """模拟一条 SSE 流式 response, 不调 LLM. 给 /goal 等 client-side 命令用.

    BL-HERMES013-3 (5/11): 借鉴 Hermes 0.13 client-side slash commands —
    /goal 这种命令不该走 LLM (耗 quota / 引入 LLM 解析歧义), gateway 直接
    拦截 + 返 fake SSE response 模拟 LLM 输出. Companion 端协议无感.

    OpenAI streaming chat completion SSE 格式:
      data: {"choices": [{"delta": {"role": "assistant"}}], ...}
      data: {"choices": [{"delta": {"content": "..."}}], ...}
      ...
      data: {"choices": [{"finish_reason": "stop", "delta": {}}], ...}
      data: [DONE]
    """
    import uuid  # noqa: PLC0415

    chat_id = f"chatcmpl-cmd-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    def _chunk(delta: dict, finish: str | None = None) -> str:
        payload = {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "choices": [{
                "index": 0,
                "delta": delta,
                "finish_reason": finish,
            }],
        }
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    # role chunk
    yield _chunk({"role": "assistant"})
    # 整段 content (一次发, 不切片 — slash 命令响应都短)
    yield _chunk({"content": text})
    # finish
    yield _chunk({}, finish="stop")
    yield "data: [DONE]\n\n"


async def _stream_chat_completion(
    body: dict,
    *,
    user_sub: str,
    user_dept: str,  # 五一 sprint 5/2 RBAC: quota.record_usage 需要部门
    model_name: str,
    model,
    security_concern: str | None = None,
    is_internal: bool = False,  # BL-F17 (5/5): internal 调用跳 record_usage
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
    status_str = "ok"
    err = ""
    used_model = model
    config: Config = app.state.config
    attempts_log: list[str] = []

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
            response = await litellm.acompletion(**params)
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
        (iterator, first_chunk), used_model, attempts_log = await with_fallback(
            config, model, _start_stream, prompt_estimate=prompt_estimate,
        )
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
            if chunk == "__keepalive__":
                yield ": keepalive\n\n"
                continue
            data = chunk.model_dump() if hasattr(chunk, "model_dump") else chunk
            if isinstance(data, dict):
                usage = data.get("usage") or {}
                prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                completion_tokens = usage.get("completion_tokens", completion_tokens)
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

        # ── BL-TASK-ASSESS-1-GATEWAY (5/15 鸿波"客户端要评估完成情况") ──
        # 在 [DONE] 之前多发一条 task_assessment 事件, Companion 接到后做
        # promise-vs-reality 检测 (assistant 文字说了"已生成"但 cum_tc=false +
        # 文件路径不存在 → ⚠ 嘴炮). 不重复算 — gateway 一次性把状态给客户端.
        #
        # 格式: 一条普通 data: 行, object="task_assessment". OpenAI 兼容客户端
        # 看到 unknown object 会忽略 (extra-field 容忍). Companion 嗅 object
        # 字段拿 metadata.
        try:
            from .skill_guard import has_skill_intent  # noqa: PLC0415
            from .skills_loader import discover_skills  # noqa: PLC0415

            sg_fired = has_skill_intent(
                body.get("messages") or [],
                skills=discover_skills(),
            )

            # 历史里 assistant 调过 catfish_run_skill 没?
            ever_called_skill = False
            for _m in (body.get("messages") or []):
                if _m.get("role") != "assistant":
                    continue
                for _tc in (_m.get("tool_calls") or []):
                    _fn = (_tc.get("function") or {}) if isinstance(_tc, dict) else {}
                    if _fn.get("name") == "catfish_run_skill":
                        ever_called_skill = True
                        break
                if ever_called_skill:
                    break

            task_assessment = {
                "object": "task_assessment",
                "model": used_model.name,
                "finish_reason": last_finish_reason,
                "tool_call_count": chunk_stats["tool_calls"],
                "content_chars": len(cumulative_content),
                "cum_has_tool_call": cumulative_has_tool_call,
                "skill_guard_fired": sg_fired,
                "ever_called_catfish_run_skill_in_session": ever_called_skill,
            }
            yield f"data: {json.dumps(task_assessment, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            logger.debug("task_assessment 事件构造失败 (%s), 不阻塞主流程", e)

        yield "data: [DONE]\n\n"
    except Exception as e:  # noqa: BLE001
        status_str = "error"
        err = str(e)
        logger.exception(
            "streaming chat completion failed (attempts=%s)",
            " -> ".join(attempts_log) if attempts_log else "single",
        )
        # 给客户端一个 friendly 错误 —— 把内部 trace 简化成人话
        friendly = _friendly_upstream_error(err)
        # BL-FIX-TIMEOUT-OUTPUTS (5/13 鸿波"做不出文档"): 上游 LLM 卡 / timeout
        # 时, 副手实际可能已经写过文件 (execute_code 早跑了), 列最近 ~/.catfish/output/
        # 文件给员工看, 别让他以为"做不出来"实际"已经做了".
        err_low = err.lower()
        is_timeout_or_overload = (
            "timeout" in err_low or "timed out" in err_low
            or "overloaded" in err_low or "503" in err
            or "broken pipe" in err_low or "connection error" in err_low
        )
        if is_timeout_or_overload:
            try:
                from . import recent_outputs  # noqa: PLC0415
                outputs = recent_outputs.list_recent(hours_back=24, limit=5)
                if outputs:
                    friendly += "\n\n📁 **过去 24h 鲶鱼已写文件** (上游卡时她可能已经做过, 直接 open 看):\n"
                    for o in outputs:
                        friendly += f"  - `{o['path']}` ({o['size_human']}, {o['mtime_iso'][:16]})\n"
                friendly += (
                    "\n💡 上游模型可能拥堵, 试试:\n"
                    "  - **切大模型**: 输入 `/model catfish-public-gemini-pro` (2M 上下文, 公网快)\n"
                    "  - **新建会话** (Cmd+N): 减小 prompt 让上游推理更快\n"
                    "  - 上游 catfish-private-main 是内网 122B Qwen, 长 prompt + 高负载下推理 5+ 分钟"
                )
            except Exception as _e:  # noqa: BLE001
                logger.warning("BL-FIX-TIMEOUT-OUTPUTS: recent_outputs 失败 (静默): %s", _e)
        yield f"data: {json.dumps({'error': friendly})}\n\n"
    finally:
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
        )
        output_transforms.DEFAULT_CHAIN.run(ctx)


# _friendly_upstream_error 抽到 errors.py (无 litellm 依赖, 测试可独立 import).
# 在 app.py 里给一个 alias 别名, 兼容历史 import 路径.
from .errors import friendly_upstream_error as _friendly_upstream_error


async def _invoke_chat_completion(
    body: dict,
    *,
    user_sub: str,
    user_dept: str = "",  # 五一 sprint 5/2 RBAC: quota.record_usage 用
    model_name: str,
    model,
    security_concern: str | None = None,
    is_internal: bool = False,  # BL-F17 (5/5): internal 调用跳 record_usage
) -> dict[str, Any]:
    """Non-streaming chat completion path, with fallback chain support.

    BL-A1.1 (5/8): 加 auto-continue on finish_reason=length. LLM 输出被
    max_tokens 截时自动续写, 直到 stop / tool_calls / 5 次上限. 让员工不需要
    手动说"继续", 真 Agent 行为.
    """
    from . import auto_continue  # noqa: PLC0415  lazy import 防循环

    start = time.time()
    config: Config = app.state.config

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
    _check_context_usage(used_model, prompt_tokens, user_sub)
    log_request_metadata(
        user=user_sub,
        model=used_model.name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=(time.time() - start) * 1000,
        status="ok",
        security_concern=security_concern,
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
    return response.model_dump() if hasattr(response, "model_dump") else response


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    user: User = Depends(get_current_user),
):
    body = await request.json()
    model_name = body.get("model")
    if not model_name:
        raise HTTPException(status_code=400, detail="model parameter required")

    config: Config = app.state.config
    model = _resolve_model(config, model_name)

    # BL-F17 (5/5): 早早判定 internal call, 让后面所有 inject 阶段 (session_meta tick /
    # prompt_security detector / 等) 都能跳过 internal 调用. 这条**必须**在 line 1074
    # session_meta tick 之前赋值, 否则 UnboundLocalError. (5/5 17:13 鸿波报 P0,
    # 之前不知谁不小心注释了, 导致 chat_completions 全 500.)
    is_internal_call = (
        request.headers.get("x-catfish-internal", "").lower() in ("true", "1", "yes")
    )

    if not user.can_access(model):
        raise HTTPException(status_code=403, detail=f"access denied to model: {model_name}")
    if model.mode != "chat":
        raise HTTPException(status_code=400, detail=f"model {model_name} is not a chat model")

    # BL-HERMES013-3 (5/11): /goal Ralph loop client-side commands.
    # 检测最后一条 user message 是不是 /goal 命令. 是的话直接返 fake SSE response,
    # 不调 LLM, 不计 quota. 借鉴 Hermes 0.13 client-side slash commands 设计.
    if not is_internal_call:
        from .session_goals import detect_goal_command  # noqa: PLC0415
        is_goal_cmd, goal_response = detect_goal_command(body.get("messages", []))
        if is_goal_cmd and goal_response:
            logger.info(
                "/goal command intercepted (user=%s): %s",
                user.sub, goal_response[:60]
            )
            return StreamingResponse(
                _fake_sse_response(goal_response, model_name=model_name),
                media_type="text/event-stream",
            )

    # 鲶鱼身份注入：客户端没传 system message 就自动加 SOUL + memory
    # Hermes 这种已自带 system 的不动；客户端可加 X-Catfish-Skip-Identity: true 强制跳过
    # BL-E11 命名权: header X-Catfish-Agent-Name / -Personality 让员工改名 + 选人设
    skip = header_skips_identity(request.headers)
    # BL-LEAN-CHAT (5/15 凌晨): 服务 token (token_use=service, e.g. hermes-cli, cron, a2a)
    # 自动 skip identity. 服务调用不需要"鲶鱼人格", 它跑批 / 跑 skill, 拿原始 LLM 答即可.
    # 鸿波 5/14 端到端测时单 chat "1+1=?" 撞 35713 input tokens, 80% 来自 SOUL/identity.
    # 这条让服务 token 自动 ultra-lean, 用户身份 (web Companion / employee SSO) 不动.
    if user.role == "service":
        skip = True
        logger.info("BL-LEAN-CHAT: service token (sub=%s) auto-skip identity", user.sub)
    agent_name, agent_personality = header_agent_prefs(request.headers)
    body["messages"] = inject_identity_if_needed(
        body.get("messages", []),
        skip=skip,
        agent_name=agent_name,
        agent_personality=agent_personality,
        # BL-SOUL-SCENARIO P2 (5/13): 透传 tools 让 inject 按 tool 候选注入
        # SOUL_BROWSER.md / SOUL_EXECUTE_CODE.md 等场景段, 不再永远全量灌.
        tools=body.get("tools"),
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
    #
    # 模式选 provider:
    #   lean:    {skills_catalog}  (其它都跳, 教学场景只看 skill 列表)
    #   internal: set()  (Registry 自己也跳 inject)
    #   普通:    全跑 (按 priority 顺序)
    #
    # 注: identity (创建 system) / compound_intent / session_goal / hints / session_meta tick
    # 不在 Registry 里, 留下面 app.py 原位.
    from .memory import InjectContext  # noqa: PLC0415
    from .memory.registry import get_global_registry  # noqa: PLC0415
    from .inject_session_history import _extract_user_query  # noqa: PLC0415

    if _lean:
        # 教学场景: 只保留 skills_catalog (LLM 必须看到能调哪些 skill)
        enabled = {"skills_catalog"}
    else:
        # 普通模式: 全部 provider 都跑
        # session_history / employee_journal / feedback 在 internal call 时自动跳
        # (Registry 看 ctx.is_internal_call)
        enabled = None  # None = 全跑

    inject_ctx = InjectContext(
        user_sub=getattr(user, "sub", None),
        user_dept=getattr(user, "dept", None),
        user_role=getattr(user, "role", None),
        messages=body["messages"],
        last_user_message=_extract_user_query(body["messages"]),
        model_name=model.name,
        is_internal_call=is_internal_call,
    )
    body["messages"] = get_global_registry().inject_subset(
        inject_ctx, body["messages"], enabled,
    )

    # BL-COMPOUND-PLAN-EXECUTE (5/15 鸿波 '复合任务 agent 撑不住'): 复合任务
    # ('分析 + 生成 PPT') 检测命中 → 追加 plan-execute 铁律到同一段 system,
    # 不在 messages 中间插新 system (上次 v2 撞过 Qwen 400). prompt-only,
    # agent 自己看历史推断当前 step. 单步任务不触发, 不影响普通会话.
    # 不在 Registry 因为它跟具体 chat 行为强耦合 (compound = 多 turn), 不是单纯 inject.
    if not _lean:
        try:
            from .compound_intent import inject_compound_plan_execute  # noqa: PLC0415

            body["messages"] = inject_compound_plan_execute(body["messages"])
        except Exception as e:  # noqa: BLE001
            logger.debug("compound_intent 注入失败 (%s), 不阻塞", e)

    # 档 4 (BL-HERMES013-3 5/11): 注入员工 /goal 锁定目标. 借鉴 Hermes 0.13 Ralph
    # loop. 单文件 ~/.catfish/session_goal.txt, 员工 /goal xxx 设, 每轮自动 inject
    # 到 system 末尾, LLM 跑偏时被持续拉回. 跟 L7/L8/FIX46 (事后纠偏) 互补 — goal
    # 是**事前锚定**.
    from .session_goals import inject_session_goal  # noqa: PLC0415
    body["messages"] = inject_session_goal(body["messages"])

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
        body["messages"] = tool_retry_hint.inject_tool_retry_hint(body["messages"])

        # BL-A1.3: "幻觉完成" hint
        from . import self_critique  # noqa: PLC0415  lazy import
        body["messages"] = self_critique.inject_completion_critique_hint(body["messages"])

        # ─── BL-FIX24 duplicate-tool-call guard 全部 DELETED (5/13 鸿波"乱七八糟") ──
        # 历史: 软 hint (inject_duplicate_guard_hint) + 物理 hard-block (detect_hard_block_duplicate
        # ≥3 次直接 SSE 推 error + break) 都删. 副作用: 长任务正常重复调 (例如批量
        # 写多份文件) 会被误拦, 把正常流打成失败. duplicate_tool_call_guard.py 模块
        # 本身保留, 不再被 chat_completions 入口调用.

    # BL-E16 关系建立: session_meta inject 已由 SessionMetaProvider (priority=30)
    # 在 Registry inject_subset 阶段接管. 这里只剩 tick (写本次 chat 时间).
    #
    # BL-F17 后续 (5/5 凌晨 39060 次事故): internal 调用也跳 tick, 不然 summarizer
    # 死循环时 today_count 暴涨 (5/4 凌晨 dev-user 跳到 39060). 跟 quota 同思路:
    # 后台 housekeeping 不算"员工今天找了我".
    if not is_internal_call:
        try:
            session_meta.tick()
        except Exception as e:
            logger.warning("session_meta tick 失败 (无关键路径): %s", e)

    # 后台触发: 异步总结 1 个最近结束但没总结过的 session, append 到 journal.
    # fire-and-forget, 不阻塞当前请求, 失败静默. 让 journal 自动持续填充.
    try:
        import asyncio  # noqa: PLC0415
        asyncio.create_task(trigger_background_summary())
    except Exception:
        pass

    # BL-MEMORY-DISTILL-LIVE (5/16 鸿波 'memory_distill 真上线'):
    # 异步 LLM 蒸馏老 journal 段 → 写 ~/.catfish/distilled_facts.md, 24h cooldown.
    # 跟 trigger_background_summary 互补: summary 持续写新段, distill 把老段抽精华
    # 让 inject 不丢 99% 老内容. fire-and-forget, 不阻塞当前请求.
    # 跳: internal call (loopback summary / proactive / distill 自身), 防递归.
    if not is_internal_call:
        try:
            import asyncio  # noqa: PLC0415

            from .memory_distill import maybe_run_llm_distillation  # noqa: PLC0415
            asyncio.create_task(maybe_run_llm_distillation())
        except Exception:  # noqa: BLE001
            pass

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
                user.sub, len(credential_hits), user.sub,
            )
            # 把 hits 暂存到 request state, 让后面 audit log 能拿到
            request.state.credential_hits = credential_hits

    # 5/8 BL-FIX2: 把 role=tool 含 image 重组成 user multipart message.
    # 详见 multimodal_tool_unwrap.py.
    if body.get("messages"):
        # 5/8 BL-FIX2 debug: 跑前 dump 一下 messages 概况, 真撞 400 时能定位
        # 是不是 tool message 含图 (该 unwrap) / 还是别的格式问题.
        msgs_in = body["messages"]
        n_total = len(msgs_in) if isinstance(msgs_in, list) else 0
        n_tool = sum(
            1 for m in msgs_in
            if isinstance(m, dict) and m.get("role") == "tool"
        )
        n_tool_with_image_marker = sum(
            1 for m in msgs_in
            if isinstance(m, dict)
            and m.get("role") == "tool"
            and isinstance(m.get("content"), str)
            and ("data:image/" in m.get("content", "") or '"data_uri"' in m.get("content", ""))
        )
        n_user_multipart = sum(
            1 for m in msgs_in
            if isinstance(m, dict)
            and m.get("role") == "user"
            and isinstance(m.get("content"), list)
        )
        logger.info(
            "BL-FIX2 pre-unwrap: total=%d, tool_msgs=%d, tool_with_image_marker=%d, user_multipart=%d",
            n_total, n_tool, n_tool_with_image_marker, n_user_multipart,
        )
        body["messages"] = unwrap_tool_images(body["messages"])

        # BL-FIX42 (5/11): 历史截图折叠 — 防多张截图累积 prompt 5-10MB 让上游
        # 122b 推理 60-120s, 客户端 idle timeout abort. 真根因诊断:
        # tool_with_image_marker=4 → 重组 4 条 → user multipart base64 累积 →
        # latency 108s status=ok 但 Companion fetch idle 超时早已 abort.
        # 修法: 保留最近 1 张图 (LLM 当前必须看), 老图替成文本占位.
        from .tool_archive.image_folder import (  # noqa: PLC0415
            fold_history_images,
            is_folding_enabled,
        )
        if is_folding_enabled():
            body["messages"] = fold_history_images(body["messages"])

        # BL-Q3-ARCHIVE (5/11): tool message 内容 archive + 摘要双层.
        # 替代 BL-FIX41 硬切 — lossless 保留, LLM 主动 catfish_read_tool_archive
        # 召回中段. 含 features.is_archive_enabled() 灰度开关 (默认开). archive
        # 写挂 → 自动降级 FIX41 硬切 (兜底). 内部用 derive_session_id 自动从
        # first user message hash 派生 session_id, 同会话稳定.
        from .tool_archive import prepare_tool_messages  # noqa: PLC0415
        body["messages"] = prepare_tool_messages(
            body["messages"],
            user_email=user.sub,  # User.sub = email (决策 3)
            origin_model=model_name,  # fix2: summary_worker 用 chat 同款模型
        )

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

    # tool calling 能力自动 reroute: 已知 tool 调用不稳的模型 (qwen 122b a10b)
    # 检测到 user 触发 skill 意图时, 强制切到 flash 系列. 防止 122b 文字幻觉
    # "已生成 X.docx" 但磁盘上没文件 (鸿波 4-29 demo 反复翻车的真因).
    rerouted_tool, tool_hint = route_to_tool_capable_if_needed(
        body, config, model
    )
    if rerouted_tool is not None:
        model = rerouted_tool
        model_name = rerouted_tool.name
        request.state.tool_capability_hint = tool_hint
        logger.info(
            "auto-route to tool-capable: orig_user=%s new_model=%s",
            user.sub, model_name,
        )

    # 防御性清洗 tools 数组 —— 畸形 tool (例如缺 function.name) 直接丢, 不让
    # LiteLLM 转 Gemini functionDeclarations 时 KeyError 把整个请求挂掉。
    body = sanitize_tools(body)

    # Gemini 防退化: 在 system 末尾加禁用 native tool_code 的指令
    # 没用 Gemini 模型 / 客户端不传 system 都会跳过, 无副作用
    body = harden_for_gemini(body, model)

    # BL-COMPRESSION-GATEWAY (5/15 早): 长 session messages 历史压缩.
    # 触发条件: estimate_tokens(messages) > model.context * 50%
    # 保留: 头 2 条 (system/首条 user) + 尾 8 条 (最近上下文), 中间压成 1 句.
    # 跳过:
    #   - is_internal_call (gateway-loopback summarizer/proactive 等, 它们本来就短)
    #   - service token (sub=client:xxx, 也是 1-shot 没历史)
    #   - X-Catfish-Compression-Internal (本模块自调 LLM 时设, 防自递归)
    # 5/15 早鸿波看 audit 撞 chenhongbo@ffcs.cn 单 chat 95K input — 70K 历史 +
    # 25K inject. lean 已省 inject (#77), 历史这块就是 BL-COMPRESSION-GATEWAY 解.
    _compression_internal = (
        request.headers.get("X-Catfish-Compression-Internal", "").lower() == "true"
    )
    if (
        not is_internal_call
        and not _compression_internal
        and user.role != "service"
    ):
        try:
            from .conversation_compressor import maybe_compress_messages  # noqa: PLC0415
            ctx_window = getattr(model, "context_window", None) or 128000
            new_msgs, compress_stats = await maybe_compress_messages(
                body.get("messages") or [],
                user_sub=user.sub,
                model_context_window=ctx_window,
            )
            if compress_stats:
                body["messages"] = new_msgs
                logger.info(
                    "BL-COMPRESSION-GATEWAY: sub=%s 压 %d 条历史 → 摘要, "
                    "token %d→%d (省 %d%%)",
                    user.sub,
                    compress_stats["compressed_count"],
                    compress_stats["pre_token"],
                    compress_stats["post_token"],
                    compress_stats["saved_pct"],
                )
                # 把统计塞 request state 让 audit log 能记
                request.state.compression_stats = compress_stats
        except Exception as e:
            logger.warning(
                "compression hook 异常 (不阻塞主流程, 用原 messages): %s",
                e,
            )

    # ── Quota 阻断 (BL-D9 完整 ship, 5/2 收尾) ────────────────
    # 真实接 chat: 在 LLM 调用前查 quota, 超了直接 429 + friendly message.
    # 估算用 quota.estimate_tokens(prompt 文本拼接), 4 字符 ≈ 1 token, 至少 1000.
    # check_quota 任一维度超 (user_minute / user_day / model_day / dept_day) 即拒.
    #
    # BL-F17 (5/5): X-Catfish-Internal: true → 跳 quota check.
    # 内部 housekeeping (summarizer / proactive / a2a) 是后台总结/起话题/辅助 任务,
    # 不该消耗员工 quota. 员工 1M/天 预算应该给员工**主对话**用, 不是给后台总结烧.
    # 鸿波 5/5 凌晨 explicit: "summarizer 不要去限制用户的 quota 这才是合理的".
    # 注: 不跳 audit log (透明仍要记, 只标 internal=true 区分).
    # 注: 这一行跟 line ~1012 的赋值是冗余 (留作 defensive — 防早期赋值被改回去 crash).
    #     Python 重新绑定同名 local var 同值无害.
    is_internal_call = (
        request.headers.get("x-catfish-internal", "").lower() in ("true", "1", "yes")
    )
    if is_internal_call:
        logger.info(
            "internal call: user=%s model=%s 跳 quota check (X-Catfish-Internal)",
            user.sub, model_name,
        )
    else:
        try:
            prompt_text = "\n".join(
                (m.get("content") or "") if isinstance(m.get("content"), str)
                else "" for m in (body.get("messages") or [])
                if isinstance(m, dict)
            )
            estimated = _quota_module.estimate_tokens(prompt_text)
            qc = _quota_module.check_quota(
                user_email=user.sub,
                department=user.department,
                model=model_name,
                est_tokens=estimated,
                role=user.role,  # BL-FIX39 (5/11): admin / sysadmin 跳 quota
            )
            if not qc.allowed:
                friendly = _quota_module.friendly_quota_message(qc, user.sub, model_name)
                logger.info(
                    "quota deny: user=%s model=%s dimension=%s current=%d limit=%d",
                    user.sub, model_name, qc.dimension, qc.current, qc.limit,
                )
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "quota_exceeded",
                        "message": friendly,
                        "dimension": qc.dimension,
                        "current": qc.current,
                        "limit": qc.limit,
                        "reset_at": qc.reset_at,
                        "model": model_name,
                    },
                )
        except HTTPException:
            raise  # 上面 429 直接抛
        except Exception as e:
            # quota 子系统挂了不该影响主流程, 不阻断 chat
            logger.warning("check_quota 调用炸 (allow chat): %s", e)

    # 注意: 这里传 body (而不是预构建的 params) 给 _invoke / _stream
    # 因为 fallback 时换模型, params 里的 api_base / api_key / 等都得重新构建
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
                body, user_sub=user.sub, user_dept=user.department,
                model_name=model_name, model=model,
                security_concern=security_concern,
                is_internal=is_internal_call,  # BL-F17: 透传, 跳 record_usage
                # teaching_mode 透传 已 DELETED (5/13): _stream_chat_completion 不再用.
                # _teaching_mode / _lean 控制 SOUL inject 在上面 1651 行已用过.
            ),
            media_type="text/event-stream",
        )
    return await _invoke_chat_completion(
        body, user_sub=user.sub, user_dept=user.department,
        model_name=model_name, model=model,
        security_concern=security_concern,
        is_internal=is_internal_call,  # BL-F17: 透传, 跳 record_usage
    )


# Embeddings


@app.post("/v1/embeddings")
async def embeddings(
    request: Request,
    user: User = Depends(get_current_user),
):
    body = await request.json()
    model_name = body.get("model")
    if not model_name:
        raise HTTPException(status_code=400, detail="model parameter required")

    config: Config = app.state.config
    model = _resolve_model(config, model_name)

    if model.mode != "embedding":
        raise HTTPException(
            status_code=400,
            detail=f"model {model_name} is not an embedding model",
        )

    params = _build_litellm_params(body, model)
    start = time.time()

    try:
        response = await litellm.aembedding(**params)
    except Exception as e:
        _raise_upstream_error(
            e,
            user_sub=user.sub,
            model_name=model_name,
            latency_ms=(time.time() - start) * 1000,
            log_context="embedding failed",
        )
        raise  # unreachable
    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
    log_request_metadata(
        user=user.sub,
        model=model_name,
        prompt_tokens=prompt_tokens,
        latency_ms=(time.time() - start) * 1000,
        status="ok",
    )
    # 五一 sprint 5/2 收尾: 同步写 quota_events (embedding 也算 token 用量).
    if prompt_tokens > 0:
        _quota_module.record_usage(
            user_email=user.sub,
            department=user.department,
            model=model_name,
            tokens_in=prompt_tokens,
            tokens_out=0,
        )
    return response.model_dump() if hasattr(response, "model_dump") else response


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
    print(
        f"[catfish] starting uvicorn on {host}:{port} "
        f"(HOST source={host_source}, PORT={port_str} source={port_source})",
        flush=True,
    )
    uvicorn.run(
        "catfish_gateway.app:app",
        host=host,
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    run()
