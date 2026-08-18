"""鲶鱼 LLM 网关的 FastAPI 入口。

做四件事：鉴权来访请求（P0 阶段用 dev token）、把 OpenAI 兼容调用路由到
配置好的上游模型、对员工侧暴露 /v1/catalog 用于选模型、以及记录请求
元数据（token 数、延迟、错误码等；对话内容永不入库）。
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

def _load_dotenv() -> Path | None:
    try:
        from dotenv import load_dotenv  # noqa: PLC0415
    except ImportError:
        return None
    for p in [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent.parent / ".env",
    ]:
        if p.exists():
            load_dotenv(p, override=False)
            return p
    return None

_ENV_FILE_LOADED = _load_dotenv()

from .network import precheck_and_setup  # noqa: E402, PLC0415

_NETWORK_STATUS = precheck_and_setup()

import litellm  # noqa: E402
from fastapi import Depends, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402

from .auth import (  # noqa: E402
    User,
    X_CATFISH_USER_HEADER,
    get_current_user,
    is_service_principal,
    resolve_effective_user_email,
)
from . import quota as _quota_module  # noqa: F401  compatibility export
from .config import (  # noqa: E402
    Config,
    get_config,
)
from .gemini_guard import harden_for_gemini  # noqa: E402
from .identity_inject import (  # noqa: E402
    header_agent_prefs,
    request_skips_identity,
    inject_identity_if_needed,
)
from .multimodal_guard import route_to_vision_if_needed  # noqa: E402
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
from .gateway_startup import (  # noqa: E402,F401
    _seed_and_migrate_models,
    run_startup,
)
from .chat_prepare import (  # noqa: E402
    enforce_quota,
    maybe_compress,
    prepare_messages,
)
from .model_handoff import apply_soft_handoff  # noqa: E402
from .tools_sanitizer import sanitize_tools  # noqa: E402

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("catfish.gateway")

_FILE_HANDLER = None

def _attach_file_handler_to_uvicorn() -> None:
    """把文件 handler 挂到 uvicorn 的三个 logger 上。**必须在 uvicorn 配置日志之后调。**

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

    改 `propagate = True` 会让每条访问日志同时走 uvicorn 自己的 stdout handler
    和 root handler, stdout 里打两遍 —— 6/30 P3.5.149 刚修过一次"log 每条写
    两遍"。传 `log_config=None` 则会连 uvicorn 的彩色 stdout 格式一起丢掉。
    只加 handler 是副作用最小的做法。

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
        if any(
            isinstance(x, RotatingFileHandler)
            and os.path.abspath(x.baseFilename) == target
            for x in lg.handlers
        ):
            continue
        lg.addHandler(_FILE_HANDLER)

def _setup_file_logging() -> None:
    global _FILE_HANDLER
    log_file = os.environ.get("CATFISH_LOG_FILE")
    if log_file == "-":
        return  # 显式禁用
    if not log_file:
        home = os.environ.get("HOME") or os.path.expanduser("~")  # noqa: BOUNDARY
        log_file = os.path.join(home, "Library", "Logs", "catfish", "gateway.log")
    try:
        from logging.handlers import RotatingFileHandler  # noqa: PLC0415
        root_logger = logging.getLogger()
        for existing in root_logger.handlers:
            if isinstance(existing, RotatingFileHandler) and \
                    os.path.abspath(existing.baseFilename) == os.path.abspath(log_file):
                _FILE_HANDLER = existing
                _attach_file_handler_to_uvicorn()
                return
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        h = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5)
        h.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        root_logger.addHandler(h)

        _FILE_HANDLER = h
        _attach_file_handler_to_uvicorn()

        logger.info("file logging → %s (10MB × 5 rotation, 含 uvicorn access)", log_file)
    except Exception as e:
        logger.warning("file logging 启用失败 (继续仅 stdout): %s", e)

_setup_file_logging()

litellm.set_verbose = False
litellm.drop_params = True

@asynccontextmanager
async def lifespan(app: FastAPI):
    refresh_task, archive_summary_task = await run_startup(app, _ENV_FILE_LOADED)

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

    try:
        await app.state.mcp_registry_client.aclose()
    except Exception as e:
        logger.debug("mcp_registry_client aclose: %s", e)

    try:
        await app.state.skills_hub_client.aclose()
    except Exception as e:
        logger.debug("skills_hub_client aclose: %s", e)

    try:
        await app.state.wiki_hub_client.aclose()
    except Exception as e:
        logger.debug("wiki_hub_client aclose: %s", e)

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
register_audit_routes(app)
register_quota_routes(app)
register_misc_routes(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # 不带 cookie，dev token 走 Authorization 头
    allow_methods=["*"],      # 含 OPTIONS / POST / GET 等
    allow_headers=["*"],      # 含 Authorization / Content-Type / X-Catfish-* 等
)

try:
    from .mcp_registry_proxy import router as mcp_registry_router  # noqa: PLC0415
    app.include_router(mcp_registry_router)
    logger.info("mcp_registry_proxy: /v1/mcp/* 反代已挂载")
except Exception as e:
    logger.warning("mcp_registry_proxy 挂载失败 (BL-D3 反代不可用): %s", e)

try:
    from .skills_hub_proxy import router as skills_hub_router  # noqa: PLC0415
    app.include_router(skills_hub_router)
    logger.info("skills_hub_proxy: /v1/hub/* 反代已挂载")
except Exception as e:
    logger.warning("skills_hub_proxy 挂载失败 (BL-D2 反代不可用): %s", e)

try:
    from .wiki_hub_proxy import router as wiki_hub_router  # noqa: PLC0415
    app.include_router(wiki_hub_router)
    logger.info("wiki_hub_proxy: /v1/wiki/* 反代已挂载 (P3.3.18)")
except Exception as e:
    logger.warning("wiki_hub_proxy 挂载失败 (P3.3.18 反代不可用): %s", e)

try:
    from .admin_proxy import router as admin_router  # noqa: PLC0415
    app.include_router(admin_router)
    logger.info("admin_proxy: /api/admin/* 反代已挂载")
except Exception as e:
    logger.warning("admin_proxy 挂载失败: %s", e)

try:
    from .facts_router import router as facts_router  # noqa: PLC0415
    app.include_router(facts_router)
    logger.info("facts_router: /api/facts/* 已挂载 (BL-Q3-FACT P0 MVP)")
except Exception as e:
    logger.warning("facts_router 挂载失败: %s", e)

try:
    from .advisory_router import router as advisory_router  # noqa: PLC0415
    app.include_router(advisory_router)
    logger.info("advisory_router: /advisory/feed.json 已挂载 (manifesto Phase 1)")
except Exception as e:
    logger.warning("advisory_router 挂载失败: %s", e)

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

def _sse_error_payload(friendly: str) -> dict[str, dict[str, str]]:
    """Build the OpenAI-compatible object shape used by streaming errors."""
    return {"error": {"message": friendly, "type": "upstream_error"}}

# _KEEPALIVE_INTERVAL_SECS 的定义挪到了 chat_runtime (那儿才是唯一用它的地方),
# 这里 re-export 回来保住 `app._KEEPALIVE_INTERVAL_SECS` 这个对外名字。
from .chat_runtime import (  # noqa: E402,F401
    _KEEPALIVE_INTERVAL_SECS,
    _invoke_chat_completion,
    _stream_chat_completion,
    _stream_with_keepalive,
)
@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    user: User = Depends(get_current_user),
):
    body = await request.json()
    model_name = body.get("model")
    if not model_name:
        raise HTTPException(status_code=400, detail="model parameter required")

    resolved = _resolve_auto_sentinel(model_name)
    if resolved != model_name:
        logger.info(
            "BL-CATFISH-AUTO-ROUTE: model=%s user=%s role=%s → resolved=%s",
            model_name, user.sub, getattr(user, "role", "?"), resolved,
        )
        model_name = resolved
        body["model"] = resolved

    config: Config = get_config()
    model = _resolve_model(config, model_name)

    _wants_internal = (
        request.headers.get("x-catfish-internal", "").lower() in ("true", "1", "yes")
        or request.query_params.get("catfish_internal", "").lower() in ("true", "1", "yes")
    )
    is_internal_call = _wants_internal and user.has_scope("background.tasks")
    if _wants_internal and not is_internal_call:
        logger.warning(
            "X-Catfish-Internal 请求被拒 (缺 background.tasks scope): user=%s "
            "auth_method=%s scopes=%s → 照常扣 quota. "
            "若是 hermes 侧后台任务 (distill / summarize / memory_enforce): "
            "该 caller 的 service token 需带 background.tasks scope — "
            "确认 identity clients.yaml 的 hermes-cli allowed_scopes 已含它, "
            "然后重新走 client_credentials 换 token (见 refresh-jwt-hermes-env.sh)",
            user.sub, user.auth_method, user.scopes,
        )

    source_hint = (
        request.headers.get("x-catfish-source", "").strip()
        or request.query_params.get("catfish_source", "").strip()
        or "unknown"
    )

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

    skip = request_skips_identity(request)  # 5/21: header 或 ?catfish_skip_identity=1 都生效
    if user.role == "service":
        skip = True
        logger.info("BL-LEAN-CHAT: service token (sub=%s) auto-skip identity", user.sub)
    agent_name, agent_personality = header_agent_prefs(request.headers)
    identity_bundle = body.pop("_catfish_identity_bundle", None)
    if identity_bundle is not None and not isinstance(identity_bundle, dict):
        identity_bundle = None  # 防御: 客户端格式错就当没传
    body["messages"] = inject_identity_if_needed(
        body.get("messages", []),
        skip=skip,
        agent_name=agent_name,
        agent_personality=agent_personality,
        tools=body.get("tools"),
        bundle=identity_bundle,
    )

    _teaching_mode = request.headers.get("X-Catfish-Teaching-Mode") == "1"
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

    _hints_disabled = os.environ.get("CATFISH_DISABLE_GATEWAY_HINTS", "0") == "1"
    if not is_internal_call and not _lean and not _hints_disabled:
        from . import tool_retry_hint  # noqa: PLC0415  lazy import
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
            return JSONResponse(synthetic)
        body["messages"] = tool_retry_hint.inject_tool_retry_hint(body["messages"])

    if not is_internal_call:
        from .prompt_security import detect_credentials_in_messages  # noqa: PLC0415
        credential_hits = detect_credentials_in_messages(body.get("messages", []))
        if credential_hits:
            logger.warning(
                "user=%s 在 prompt 里检测到密码 / 凭据明文 (%d 处). "
                "建议员工用 secret_ref (keychain:// 或 env://) 替代. user_sub=%s",
                effective_user_email, len(credential_hits), effective_user_email,
            )
            request.state.credential_hits = credential_hits

    body = prepare_messages(body, model, model_name, effective_user_email)
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

    body = sanitize_tools(body, user=user, source_hint=source_hint)

    body = harden_for_gemini(body, model)

    body = await maybe_compress(
        body, request, model, model_name, user,
        is_internal_call, _is_service_call, effective_user_email,
    )
    enforce_quota(body, user, model_name, effective_user_email, is_internal_call)
    is_stream = bool(body.get("stream", False))

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

def run():
    from .network import print_banner  # noqa: PLC0415
    print_banner(_NETWORK_STATUS)

    import uvicorn  # noqa: PLC0415

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
