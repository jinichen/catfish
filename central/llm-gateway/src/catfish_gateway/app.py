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

import litellm  # noqa: E402
from fastapi import Depends, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402

from .auth import User, get_current_user, get_current_user_optional  # noqa: E402
from .catalog import build_catalog  # noqa: E402
from .config import Config, load_config  # noqa: E402
from .gemini_guard import harden_for_gemini  # noqa: E402
from .multimodal_guard import route_to_vision_if_needed  # noqa: E402
from .tool_capability_guard import route_to_tool_capable_if_needed  # noqa: E402
from .employee_journal import inject_employee_journal  # noqa: E402
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

    # 五一 sprint Day 5 (B 方案): 启动时自动 register 到 catfish-identity registry.
    # 这样别的 catfish 实例 (Plan D Federation) 能通过 lookup 找到本机.
    # 失败不阻塞启动 (a2a 不可用, 其他功能正常).
    try:
        from .a2a_self_register import self_register  # noqa: PLC0415
        await self_register()
    except Exception as e:
        logger.warning("a2a_self_register 失败 (Plan D A2A 不可用): %s", e)

    yield

    refresh_task.cancel()
    try:
        await refresh_task
    except asyncio.CancelledError:
        pass
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
    """返 dev_users.yaml 配置的所有测试账号. 生产模式返 404."""
    if os.environ.get("CATFISH_ENV", "dev").lower() == "prod":
        raise HTTPException(status_code=404, detail="not found")
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
    if model.upstream.api_base:
        params["api_base"] = model.upstream.api_base

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


async def _stream_chat_completion(
    body: dict,
    *,
    user_sub: str,
    user_dept: str,  # 五一 sprint 5/2 RBAC: quota.record_usage 需要部门
    model_name: str,
    model,
    security_concern: str | None = None,
) -> AsyncIterator[str]:
    """SSE async generator for streaming chat completions, with fallback chain.

    Fallback 时机:
        在 "acompletion + 首 chunk" 阶段失败 → 切下一个模型重试
        已开始流之后挂掉 → 没法切, 直接转 SSE error 返回 (中途换模型会乱掉客户端解析)
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
        # 写出首 chunk (可能是 None, 表示流空)
        if first_chunk is not None:
            data = first_chunk.model_dump() if hasattr(first_chunk, "model_dump") else first_chunk
            if isinstance(data, dict):
                usage = data.get("usage") or {}
                prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                completion_tokens = usage.get("completion_tokens", completion_tokens)
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

        # 后续 chunks 流出去 —— 这阶段挂了不再 fallback.
        # 用 _stream_with_keepalive 包装: 上游 chunk 间隔 > 30s 时插 SSE comment
        # 防客户端/中间代理 timeout 断开. 私有 LLM tool calling 思考阶段尤其需要.
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
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
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
        if status_str == "ok" and (prompt_tokens > 0 or completion_tokens > 0):
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
) -> dict[str, Any]:
    """Non-streaming chat completion path, with fallback chain support."""
    start = time.time()
    config: Config = app.state.config

    async def _call(candidate_model):
        params = _build_litellm_params(body, candidate_model)
        return await litellm.acompletion(**params)

    try:
        response, used_model, _attempts = await with_fallback(config, model, _call)
    except Exception as e:
        _raise_upstream_error(
            e,
            user_sub=user_sub,
            model_name=model_name,
            latency_ms=(time.time() - start) * 1000,
            log_context="chat completion failed",
        )
        raise  # unreachable; satisfies type checker

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
    if prompt_tokens > 0 or completion_tokens > 0:
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

    # BL-E16 关系建立: 注入 session_meta (距上次 N 天 N 小时 / 今天第几次)
    # 让 LLM 知道时间感, 跨天回来时能自然说"好几天没找我了".
    # 同时 tick: 写本次 chat 时间, 累计 today_count.
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
            ),
            media_type="text/event-stream",
        )
    return await _invoke_chat_completion(
        body, user_sub=user.sub, user_dept=user.department,
        model_name=model_name, model=model,
        security_concern=security_concern,
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

    host = os.environ.get("HOST", "0.0.0.0")
    port_str = os.environ.get("PORT", "8000")
    port = int(port_str)
    port_source = "env PORT" if "PORT" in os.environ else "default"
    print(
        f"[catfish] starting uvicorn on {host}:{port} (PORT={port_str}, source={port_source})",
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
