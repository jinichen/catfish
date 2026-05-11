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
from copy import deepcopy
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

from .auth import User, get_current_user, get_current_user_optional  # noqa: E402
from .catalog import build_catalog  # noqa: E402
from .config import Config, load_config  # noqa: E402
from .gemini_guard import harden_for_gemini  # noqa: E402
from .multimodal_guard import route_to_vision_if_needed  # noqa: E402
from .multimodal_tool_unwrap import unwrap_tool_images  # noqa: E402
from .tool_capability_guard import route_to_tool_capable_if_needed  # noqa: E402
from .employee_journal import inject_employee_journal  # noqa: E402
from .feedback_inject import inject_feedback  # noqa: E402  BL-MM6
from .inject_session_history import inject_session_history  # noqa: E402
from .session_facts import inject_session_facts  # noqa: E402
from . import session_meta  # noqa: E402  BL-E16 关系建立: tick + inject 时间元
from .session_summarizer import trigger_background_summary  # noqa: E402
from .skill_guard import inject_skill_guard  # noqa: E402
from .skills_inject import inject_skills_catalog  # noqa: E402
from .stats_guard import inject_stats_guard  # noqa: E402
from .fallback import should_fallback, with_fallback  # noqa: E402
from .tools_sanitizer import sanitize_tools  # noqa: E402
from .identity_inject import (  # noqa: E402
    header_agent_prefs,
    header_skips_identity,
    inject_identity_if_needed,
)
from .metrics import log_request_metadata  # noqa: E402
from . import quota as _quota_module  # noqa: E402  五一 sprint 5/2 收尾: chat 后写 quota_events

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
    # BL-FIX23 L4a (5/9): 客户端没传 max_tokens 时强制兜底 4096. 鸿波 5/9 报
    # '半截就停' 真因: Qwen vLLM 默认 max_tokens 太小 (~600 token), 长 docx
    # 输出被截 finish_reason=length, streaming 路径 BL-A1.1 auto-continue
    # 没覆盖, 直接 stream 结束. 兜底 4K 让大多数任务一次完成. 续写 streaming
    # 版 BL-A1.2 后续做.
    if "max_tokens" not in params or params["max_tokens"] is None:
        params["max_tokens"] = 4096
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
        except asyncio.TimeoutError:
            yield "__keepalive__"
        except StopAsyncIteration:
            return


# BL-FIX23 L5 (5/9): 鸿波拍板"方案 C, 不要考虑别的". gateway 检测 plan-only
# finish_reason=stop 自动重发, 客户端无感. 三次诊断后真根因: Qwen3.5 122B
# 长 context (79 messages, 5万字 journal) + RLHF "礼貌等确认" 模式 — LLM 收
# self_critique hint 后**还是** finish_reason=stop, 嘴上说要做但没 emit tool_call.
# SOUL 纪律治不了 (RLHF > system prompt), self_critique 已经 inject 但模型不听.
# L5 真招: gateway 内部起新一轮 acompletion 加硬 hint, 把新 stream 接到原 SSE.
#
# 触发条件 (4 条都满足):
#   1. finish_reason == "stop" (LLM 自然结束, 不是 length/tool_calls)
#   2. 累积没 emit 任何 productive tool_call (execute_code / catfish_run_skill / ...)
#   3. 累积 content 是 plan-only (含承诺关键词或未来意图词)
#   4. 上一条 user message 是反馈 (短消息或含反馈关键词)
# 防死循环: 重发上限 2 次. 每次重发记 WARNING.
_PLAN_ONLY_PROMISE_KEYWORDS = (
    # 完成承诺 (跟 self_critique 一致, 不再 import 防循环)
    "已生成", "已保存", "已完成", "已创建", "已修改", "已写入",
    "已输出", "已写好", "已经生成", "已经保存", "已经完成",
    # 未来意图 (LLM 经常说"我立刻..."然后停)
    "立刻", "我现在", "现在重新", "我马上", "马上动手", "重新生成",
    "我立即", "立即生成", "现在生成", "现在调整", "重新调整",
)
_PLAN_ONLY_FEEDBACK_KEYWORDS = (
    # 短反馈词 (员工提细节调整时常见)
    "改", "调整", "错了", "漏", "继续", "做啊", "干完", "干一半",
    "还有", "不对", "不要", "再改", "重做", "没做完", "怎么", "还是",
    "完成", "写完", "做完",
)
_PLAN_ONLY_PRODUCTIVE_TOOLS = {
    "execute_code", "python", "bash", "shell_exec", "sh",
    "catfish_run_skill", "write_file", "edit_file", "create_file",
    "save_file", "tauri_save_file", "memory_save", "catfish_remember",
}
_PLAN_ONLY_HARD_HINT = (
    "[BL-FIX23 L5 plan-only-retry]\n"
    "你刚回了一段话但**没 emit 任何 tool_call**. 员工要的是真做事不是嘴上承诺.\n\n"
    "立刻发起 tool_call 真做出来:\n"
    "- 写文档/改文档 → execute_code 调 python-docx 直接读写文件\n"
    "- 跑 skill → catfish_run_skill\n"
    "- 写文件 → write_file / tauri_save_file\n\n"
    "**一个字解释都不要发**, 直接 tool_call. "
    "鸿波刚才已经反馈了这正是他要的 — '怎么干一半就停了'."
)
# BL-FIX23 L6 (5/11): retry 上限 2 → 1. 鸿波 5/11 演示前夜遇到 24+ 轮 L5 retry
# 死循环 (每次 HTTP request retry counter 都从 0 起, 总累积 ≥ 24 次同样的
# 'execute_code 生成文档' → 'plan-only stop' → retry).
# 单次 request 内 retry 1 次足够 — 1 次还 plan-only 就接受是"等反馈"不是偷懒.
_MAX_PLAN_ONLY_RETRIES = 1

# BL-FIX23 L6: Jaccard 阈值 — 当前 attempt content 跟历史 assistant 消息相似度
# 超过这个值就**不再 retry** (LLM 已经在重复说话, 再 retry 一定再说一遍).
_REPETITIVE_JACCARD_THRESHOLD = 0.55


def _jaccard_bigram(s1: str, s2: str) -> float:
    """字符 bigram Jaccard 系数 — 粗糙但快的相似度判定. 0~1, 1 = 完全相同."""
    if not s1 or not s2:
        return 0.0
    # 取前 200 字 (足够指纹 + 避免长文本计算开销)
    s1 = s1[:200]
    s2 = s2[:200]
    if len(s1) < 2 or len(s2) < 2:
        return 0.0
    bg1 = {s1[i : i + 2] for i in range(len(s1) - 1)}
    bg2 = {s2[i : i + 2] for i in range(len(s2) - 1)}
    if not bg1 or not bg2:
        return 0.0
    return len(bg1 & bg2) / len(bg1 | bg2)


def _assistant_history_too_repetitive(messages: list, current_content: str) -> bool:
    """BL-FIX23 L6 (5/11): 历史里有 assistant message 跟当前内容高度相似 → 死循环征兆.

    防 24+ 轮"已生成请检查" + execute_code 反复跑同样动作的 case (鸿波 5/11 实测).

    判定: 当前 content ≥ 50 字 + 历史里有任一 assistant msg 跟当前 Jaccard > 阈值.
    Jaccard 用字符 bigram 集合, 取前 200 字, 中文敏感.
    """
    if not current_content or len(current_content) < 50:
        return False
    if not messages:
        return False
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        prev = msg.get("content", "")
        if isinstance(prev, list):
            # multipart, 取 text 部分
            prev = " ".join(
                p.get("text", "")
                for p in prev
                if isinstance(p, dict) and p.get("type") == "text"
            )
        if not isinstance(prev, str) or len(prev) < 50:
            continue
        if _jaccard_bigram(prev, current_content) >= _REPETITIVE_JACCARD_THRESHOLD:
            return True
    return False


def _last_role_is_tool_result(messages: list) -> bool:
    """BL-FIX23 L6 (5/11): 最后一条 message 是 tool 结果 (说明上一轮已经调过 tool,
    LLM 现在是看完工具结果在汇报, 不应该再 retry 让它"又干一遍").

    一次 request 末尾 tool result + LLM 输出 "已完成" 是合理流程, 不是 plan-only 偷懒.
    """
    if not messages:
        return False
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "tool":
            return True
        if role in {"user", "assistant", "system"}:
            return False
    return False


def _is_plan_only_content(content: str) -> bool:
    """累积 content 是 plan-only (含承诺/未来意图词).

    不限长度 — 关键词本身够 specific (e.g. '已生成' / '我立刻'), 不会误判
    一般 ack ('好' / 'OK'). 触发还要外层 4 条 AND (没 tool_call + user 反馈
    + retries 未满), 这层只判内容形态.
    """
    if not content or not isinstance(content, str):
        return False
    for kw in _PLAN_ONLY_PROMISE_KEYWORDS:
        if kw in content:
            return True
    return False


def _last_user_message_is_feedback(messages: list) -> bool:
    """上一条 user message 看起来是反馈 (短消息或含反馈词)."""
    if not messages:
        return False
    # 倒序找最近的 user message (跳过 tool / assistant)
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        # multipart 取 text 部分
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        if not isinstance(content, str):
            return False
        # 短消息 (< 30 字, 一般是反馈) 或 含反馈关键词
        if len(content) < 30:
            return True
        for kw in _PLAN_ONLY_FEEDBACK_KEYWORDS:
            if kw in content:
                return True
        return False  # 长 user message + 没反馈词 → 不是反馈
    return False


async def _stream_chat_completion(
    body: dict,
    *,
    user_sub: str,
    user_dept: str,  # 五一 sprint 5/2 RBAC: quota.record_usage 需要部门
    model_name: str,
    model,
    security_concern: str | None = None,
    is_internal: bool = False,  # BL-F17 (5/5): internal 调用跳 record_usage
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

        (iterator, first_chunk), used_model, attempts_log = await with_fallback(
            config, model, _start_stream,
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

        # BL-FIX23 L5 (5/9): plan-only retry 跨次累积状态. 一次 stream 跑完
        # 后看是不是 plan-only stop, 是的话不发 [DONE], 起新一轮 acompletion
        # 加硬 hint, 把新 chunks 接到原 SSE 流上, 客户端无感.
        cumulative_content = ""
        cumulative_has_tool_call = False
        plan_only_retries = 0
        current_body = body  # 第一轮用原 body, retry 时 deepcopy 加 hint

        while True:  # outer plan-only retry loop
            # ── 跑一轮 stream attempt ────────────────────────────────────
            chunk_stats = {"total": 0, "content": 0, "reasoning": 0, "tool_calls": 0, "empty": 0}
            last_finish_reason: str | None = None
            attempt_content = ""
            attempt_has_tool_call = False

            # 写出首 chunk (只第一轮 retry 有 first_chunk, 后续重发都从 iterator 起)
            if first_chunk is not None:
                data = first_chunk.model_dump() if hasattr(first_chunk, "model_dump") else first_chunk
                if isinstance(data, dict):
                    usage = data.get("usage") or {}
                    prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                    completion_tokens = usage.get("completion_tokens", completion_tokens)
                    # 也对 first_chunk 做 stat 累积 (跟下面的 async for 一致)
                    choices = data.get("choices") or []
                    if choices:
                        choice0 = choices[0]
                        delta = choice0.get("delta") or {}
                        chunk_stats["total"] += 1
                        has_any = False
                        if delta.get("content"):
                            chunk_stats["content"] += 1
                            attempt_content += delta["content"]
                            has_any = True
                        if delta.get("reasoning_content"):
                            chunk_stats["reasoning"] += 1
                            has_any = True
                        if delta.get("tool_calls"):
                            chunk_stats["tool_calls"] += 1
                            attempt_has_tool_call = True
                            has_any = True
                        if not has_any:
                            chunk_stats["empty"] += 1
                        if choice0.get("finish_reason"):
                            last_finish_reason = choice0["finish_reason"]
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                first_chunk = None  # 用过了, 后续 retry 不再有首 chunk 特殊处理

            # 后续 chunks 流出去 —— 这阶段挂了不再 fallback.
            # 用 _stream_with_keepalive 包装: 上游 chunk 间隔 > 30s 时插 SSE comment
            # 防客户端/中间代理 timeout 断开.
            async for chunk in _stream_with_keepalive(iterator):
                if chunk == "__keepalive__":
                    # SSE comment 行, 客户端会忽略, 但 TCP 连接保活.
                    yield ": keepalive\n\n"
                    continue
                data = chunk.model_dump() if hasattr(chunk, "model_dump") else chunk
                if isinstance(data, dict):
                    usage = data.get("usage") or {}
                    prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                    completion_tokens = usage.get("completion_tokens", completion_tokens)
                    # BL-FIX23 L3: 采样 delta 形态分布 (debug 用)
                    choices = data.get("choices") or []
                    if choices:
                        choice0 = choices[0]
                        delta = choice0.get("delta") or {}
                        chunk_stats["total"] += 1
                        has_any = False
                        if delta.get("content"):
                            chunk_stats["content"] += 1
                            attempt_content += delta["content"]
                            has_any = True
                        if delta.get("reasoning_content"):
                            chunk_stats["reasoning"] += 1
                            has_any = True
                        if delta.get("tool_calls"):
                            chunk_stats["tool_calls"] += 1
                            attempt_has_tool_call = True
                            has_any = True
                        if not has_any:
                            chunk_stats["empty"] += 1
                        # BL-FIX23 L4b: 跟踪 finish_reason.
                        if choice0.get("finish_reason"):
                            last_finish_reason = choice0["finish_reason"]
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

            # ── 一轮 stream 跑完, 累积状态 ───────────────────────────────
            cumulative_content += attempt_content
            cumulative_has_tool_call = cumulative_has_tool_call or attempt_has_tool_call

            # BL-FIX23 L3+L4b: 流末尾打 chunk 形态分布 + finish_reason.
            #   reasoning>0 content=0   → 前端没读 reasoning_content (BL-FE3 配套)
            #   finish_reason=length    → max_tokens 截了, BL-A1.2 streaming
            #                              auto-continue 该排上 / 或 client 传更大
            #                              max_tokens
            #   finish_reason=stop      → LLM 自然结束 (BL-FIX23 L5 看是不是 plan-only)
            if chunk_stats["total"] > 0:
                logger.info(
                    "chunk stats: model=%s total=%d content=%d reasoning=%d "
                    "tool_calls=%d empty=%d finish_reason=%s "
                    "(retry=%d cum_content=%d cum_tc=%s)",
                    used_model.name,
                    chunk_stats["total"],
                    chunk_stats["content"],
                    chunk_stats["reasoning"],
                    chunk_stats["tool_calls"],
                    chunk_stats["empty"],
                    last_finish_reason,
                    plan_only_retries,
                    len(cumulative_content),
                    cumulative_has_tool_call,
                )
                if last_finish_reason == "length":
                    logger.warning(
                        "finish_reason=length: model=%s max_tokens 截了 (L4a 兜底 4096 "
                        "还撞 → 调大 / 真做 BL-A1.2 streaming auto-continue).",
                        used_model.name,
                    )

            # ── BL-FIX23 L5+L6: plan-only retry 触发判定 ──────────────────
            # L6 (5/11) 新加 3 条 "不 retry" 保险, 修死循环:
            #   (1) messages 末尾是 tool result → 上一轮已经 tool, LLM 现在汇报合理
            #   (2) 当前内容跟历史 assistant 高度相似 (Jaccard ≥ 0.55) → LLM 在重复
            #   (3) retry 上限 2 → 1, 单 request 内 1 次足够
            msgs_for_check = current_body.get("messages") or []
            last_is_tool = _last_role_is_tool_result(msgs_for_check)
            too_repetitive = _assistant_history_too_repetitive(msgs_for_check, cumulative_content)
            should_retry = (
                last_finish_reason == "stop"
                and not cumulative_has_tool_call
                and _is_plan_only_content(cumulative_content)
                and _last_user_message_is_feedback(msgs_for_check)
                and plan_only_retries < _MAX_PLAN_ONLY_RETRIES
                and not last_is_tool          # L6 fix1
                and not too_repetitive        # L6 fix2
            )
            if not should_retry:
                # 留 log 方便 debug — 为啥这次没 retry (主要是 L6 新加的 2 条决策点)
                if (
                    last_finish_reason == "stop"
                    and not cumulative_has_tool_call
                    and _is_plan_only_content(cumulative_content)
                ):
                    logger.info(
                        "BL-FIX23 L6 skip retry: last_is_tool=%s too_repetitive=%s "
                        "retries=%d/%d (避免死循环)",
                        last_is_tool, too_repetitive,
                        plan_only_retries, _MAX_PLAN_ONLY_RETRIES,
                    )
                yield "data: [DONE]\n\n"
                break

            # ── 触发 plan-only retry: 起新一轮 acompletion + 注入硬 hint ─
            plan_only_retries += 1
            logger.warning(
                "BL-FIX23 L5 plan-only retry %d/%d: model=%s 检测 finish_reason=stop + "
                "0 tool_call + plan-only content (%d 字) + user 反馈 → 重发硬 hint",
                plan_only_retries,
                _MAX_PLAN_ONLY_RETRIES,
                used_model.name,
                len(cumulative_content),
            )
            # deepcopy current_body 防原 body 被改 (chat_completions 调用方还会用)
            current_body = deepcopy(current_body)
            messages = current_body.setdefault("messages", [])
            # 把这一轮的 assistant 输出补到 messages (LLM 看到自己说过啥)
            messages.append({"role": "assistant", "content": attempt_content})
            # 加 user 硬 hint, "立刻 emit tool_call 一字不解释"
            messages.append({"role": "user", "content": _PLAN_ONLY_HARD_HINT})
            # 重新 acompletion (同 used_model, 不再 fallback — fallback 链已在最初做过)
            params = _build_litellm_params(current_body, used_model)
            response = await litellm.acompletion(**params)
            iterator = response.__aiter__()
            # 不再有特殊 first_chunk, 直接进 outer while 下一轮 async for
            # (continue 自动从 outer while 顶上跑下一轮 stream attempt)
    except Exception as e:  # noqa: BLE001
        status_str = "error"
        err = str(e)
        logger.exception(
            "streaming chat completion failed (attempts=%s)",
            " -> ".join(attempts_log) if attempts_log else "single",
        )
        # 给客户端一个 friendly 错误 —— 把内部 trace 简化成人话
        friendly = _friendly_upstream_error(err)
        yield f"data: {json.dumps({'error': friendly})}\n\n"
    finally:
        # metrics 用实际用的 model_name (fallback 时跟客户端请求的不一样)
        actual_model_name = used_model.name if used_model is not None else model_name
        if used_model is not None and prompt_tokens > 0:
            _check_context_usage(used_model, prompt_tokens, user_sub)
        log_request_metadata(
            user=user_sub,
            model=actual_model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=(time.time() - start) * 1000,
            ttft_ms=ttft_ms,
            status=status_str,
            error=err,
            security_concern=security_concern,
        )
        # 五一 sprint 5/2 收尾: 同步写 quota_events. ok 才记 (error 时 tokens=0).
        # BL-F17 (5/5): internal 调用跳 record_usage, 不算到员工 user_day quota.
        # audit log 仍写 (透明), 只 quota 跳过.
        if (
            status_str == "ok"
            and (prompt_tokens > 0 or completion_tokens > 0)
            and not is_internal
        ):
            _quota_module.record_usage(
                user_email=user_sub,
                department=user_dept,
                model=actual_model_name,
                tokens_in=prompt_tokens,
                tokens_out=completion_tokens,
            )


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
        resp, used, _attempts = await with_fallback(config, model, _call)
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

    # 鲶鱼身份注入：客户端没传 system message 就自动加 SOUL + memory
    # Hermes 这种已自带 system 的不动；客户端可加 X-Catfish-Skip-Identity: true 强制跳过
    # BL-E11 命名权: header X-Catfish-Agent-Name / -Personality 让员工改名 + 选人设
    skip = header_skips_identity(request.headers)
    agent_name, agent_personality = header_agent_prefs(request.headers)
    body["messages"] = inject_identity_if_needed(
        body.get("messages", []),
        skip=skip,
        agent_name=agent_name,
        agent_personality=agent_personality,
    )

    # session_facts 注入: 把员工本 session 内明确告诉过的硬事实 (catfish_remember
    # 写到 ~/.catfish/session_facts.json) 拼到最后一条 system message 末尾.
    # 工程级 attention 兜底, 不依赖模型自觉 quote (SOUL.md 复述模式是软纪律).
    body["messages"] = inject_session_facts(body["messages"])

    # stats_guard 注入: 员工最近一句要求"统计 / 多少 / 合计" 等, 强制提醒模型
    # 必须 execute_code 用 Python 算, 不许自数. SOUL.md § 数据统计 = 代码统计
    # 配套硬规则. 鸿波 2026-04-29 反馈"软纪律已修正多次仍出错".
    body["messages"] = inject_stats_guard(body["messages"])

    # ⚠️ 2026-04-30 一度禁用 → 立即撤回 (B 方案假设错了)
    #
    # 历史:
    #   - 4-30 上午: 鸿波建议 catfish skill 装到 hermes ~/.hermes/skills/productivity/
    #     catfish-* 下, 走 hermes 原生调用. 我假设 hermes skill 是"模型自动可见的
    #     tool", 复制过去就能用. 软禁用了 A 方案的 inject + catfish_run_skill.
    #   - 4-30 下午测试: 实际 hermes skill 不是自动 tool, 模型不会主动调用. 它会调
    #     skill_manage / execute_code 自写代码, 还是绕开 catfish 工程审定 skill.
    #   - 结论: A 方案 (catfish_run_skill 工具直接执行 script.py) 才是符合实际的设计.
    #     立刻撤回 B 方案的禁用.
    #
    # B 方案做的 install_to_hermes.sh 复制 SKILL.md 到 hermes 路径无害, 留着备用 (作为
    # hermes 端"看得到 catfish skill 存在"的兜底). 但调用走 A 方案的 catfish_run_skill.
    body["messages"] = inject_skills_catalog(body["messages"])
    body["messages"] = inject_skill_guard(body["messages"], body)

    # ── 跨 session 上下文 (鸿波 4-30 反馈"跨对话信息割裂, 不像真实个体") ──
    # 档 1: 注入最近 7 天 session 元信息 (id / 时间 / 首条 user message), 模型
    #       看到至少**意识到**有这些历史存在
    body["messages"] = inject_session_history(body["messages"])
    # 档 2: 注入 ~/.catfish/employee_journal.md 内容 (LLM 总结过的关键决策 /
    #       偏好 / 里程碑), 模型看到员工"过去几天究竟讲了啥决定了啥"
    body["messages"] = inject_employee_journal(body["messages"])

    # 档 3 (BL-MM6 5/5): 注入员工最近 7 天 negative feedback (👎 / 改).
    #       跟 BL-MM5 主动学习 (软纪律) 配合, 这条是显式 + 工程级 — 员工 explicit
    #       点了 button 才入, 比 LLM 自觉观察的权重高. internal call 也 inject —
    #       summarizer / proactive 用一致风格, 也要尊重员工 feedback.
    body["messages"] = inject_feedback(body["messages"])

    # BL-A1.2 (5/8): 检测 messages 历史里 LLM 连续多次同 tool 失败 → 注入 hint
    # 让 LLM 换思路, 不要重复同样错误. 真 Agent retry 行为.
    # 跟 SOUL.md 软纪律配合 — 软纪律失效时工程兜底.
    if not is_internal_call:
        from . import tool_retry_hint  # noqa: PLC0415  lazy import
        body["messages"] = tool_retry_hint.inject_tool_retry_hint(body["messages"])

    # BL-A1.3 (5/8): 检测 LLM "幻觉完成" — 说"已生成 X" 但前面没调 execute_code.
    # 注入 hint 强制下次调用时真做工具调用, 不要嘴说.
    # 鸿波 4-29 demo 反复翻车的真因, 5/14 demo 必修.
    if not is_internal_call:
        from . import self_critique  # noqa: PLC0415  lazy import
        body["messages"] = self_critique.inject_completion_critique_hint(body["messages"])

    # BL-FIX24 (5/9): 检测 LLM 重复跑同一段 productive tool_call (execute_code
    # 跑同一份 code 5 次产同一文件). 鸿波 5/9 demo 现场死循环 — 鲶鱼真做事
    # 但记不住做过 + 主动问 "需要再做一次?" 拉员工回 "立刻执行" 又重做.
    # 跟 self_critique 互补 — 一个治"该做没做", 一个治"做了又做".
    if not is_internal_call:
        from . import duplicate_tool_call_guard  # noqa: PLC0415  lazy import
        body["messages"] = duplicate_tool_call_guard.inject_duplicate_guard_hint(
            body["messages"]
        )

    # BL-E16 关系建立: 注入 session_meta (距上次 N 天 N 小时 / 今天第几次)
    # 让 LLM 知道时间感, 跨天回来时能自然说"好几天没找我了".
    # 同时 tick: 写本次 chat 时间, 累计 today_count.
    #
    # BL-F17 后续 (5/5 凌晨 39060 次事故): internal 调用也跳 tick, 不然 summarizer
    # 死循环时 today_count 暴涨 (5/4 凌晨 dev-user 跳到 39060). 跟 quota 同思路:
    # 后台 housekeeping 不算"员工今天找了我".
    if not is_internal_call:
        try:
            meta_block = session_meta.build_meta_block()
            if meta_block:
                # 找已有的 system message 拼到末尾; 没有则前插一条
                inserted = False
                for m in body["messages"]:
                    if m.get("role") == "system":
                        m["content"] = (m.get("content") or "") + "\n\n---\n\n" + meta_block
                        inserted = True
                        break
                if not inserted:
                    body["messages"].insert(0, {"role": "system", "content": meta_block})
            session_meta.tick()
        except Exception as e:
            logger.warning("session_meta inject/tick 失败 (无关键路径): %s", e)

    # 后台触发: 异步总结 1 个最近结束但没总结过的 session, append 到 journal.
    # fire-and-forget, 不阻塞当前请求, 失败静默. 让 journal 自动持续填充.
    try:
        import asyncio  # noqa: PLC0415
        asyncio.create_task(trigger_background_summary())
    except Exception:
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

        # BL-Q3-ARCHIVE (5/11): tool message 内容 archive + 摘要双层.
        # 替代 BL-FIX41 硬切 — lossless 保留, LLM 主动 catfish_read_tool_archive
        # 召回中段. 含 features.is_archive_enabled() 灰度开关 (默认开). archive
        # 写挂 → 自动降级 FIX41 硬切 (兜底). 内部用 derive_session_id 自动从
        # first user message hash 派生 session_id, 同会话稳定.
        from .tool_archive import prepare_tool_messages  # noqa: PLC0415
        body["messages"] = prepare_tool_messages(
            body["messages"],
            user_email=user.email,
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
            f"[catfish] ⚠️ HOST=0.0.0.0 — gateway 暴露到所有网卡 (局域网可访问). "
            f"仅服务器部署用. 员工电脑应改回 127.0.0.1.",
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
