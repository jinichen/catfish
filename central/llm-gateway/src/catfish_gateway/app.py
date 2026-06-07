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
from .config import Config, load_config  # noqa: E402
# 5/23 BL-GATEWAY-DROP-LEGACY-SUMMARIZE: inject_employee_journal 5/20 BL-GATEWAY-
# MEMORY-REGISTRY-DELETE 时已 disable (registry.providers 永远空), 实际无 caller.
# 函数体也从 employee_journal.py 删, 该模块剩下 read_journal / append_to_journal
# 给 a2a_journal_hook (写 bob 本机 journal) 用. 老 import 移除.
from .fallback import with_fallback  # noqa: E402
from .feedback_inject import inject_feedback  # noqa: E402  BL-MM6
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
# 5/23 BL-GATEWAY-DROP-LEGACY-SUMMARIZE: session_summarizer 整文件已删, plugin 接管
# (catfish-memory on_session_end 写 employee_journal). 老 import 移除.
from .skill_guard import inject_skill_guard  # noqa: E402
# 5/26 P0 砍 skills_inject (中央扫员工本机 SKILL.md → 上游 LLM, 隐私违规).
# 详见 skills_loader.py 顶部 DEPRECATED 说明.
from .stats_guard import inject_stats_guard  # noqa: E402
from .model_handoff import apply_soft_handoff  # noqa: E402
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

    # BL-CENTRAL-EDGE-BOUNDARY + BL-QUOTA-SQLITE-DEPRECATE (5/17 鸿波):
    # 生产必配 CATFISH_DB_URL (PG). 没配 → 启动报警, 防 audit 数据静默丢 / 写
    # 员工本机. 单测走 CATFISH_QUOTA_DB env override (test fixture 设).
    from .quota import _audit_backend_configured  # noqa: PLC0415
    if not _audit_backend_configured():
        if env_mode == "prod":
            logger.error(
                "❌ CATFISH_DB_URL 未配, 但 CATFISH_ENV=prod — audit 数据将静默丢失. "
                "中央端必须配 PG (BL-CENTRAL-EDGE-BOUNDARY 规则: 不允许写员工本机 "
                "sqlite 兜底). 立刻配 CATFISH_DB_URL 重启."
            )
        else:
            logger.warning(
                "⚠️ CATFISH_DB_URL 未配 (CATFISH_ENV=%s) — audit 写会被丢. "
                "dev 没问题, 但要测 quota / audit 功能时该配本地 PG 或单测设 "
                "CATFISH_QUOTA_DB env.", env_mode,
            )

    # BL-FIX37 (5/10): gateway 内部 loopback (proactive_starter / session_summarizer)
    # 用 internal-only token 调自己 /v1/chat/completions, 修 BL-FIX29 关掉员工
    # dev_token 后内部调用 401 的副作用. 没显式配 → 自动生成 32B random.
    from .auth.dev_token import ensure_internal_dev_token  # noqa: PLC0415
    ensure_internal_dev_token()

    # BL-GATEWAY-MEMORY-REGISTRY-DELETE (5/20): memory_registry 整套删除.
    # 5/19 BL-MEMORY-OWNERSHIP-FIX Phase 2-3 已 disable provider 注册.
    # 5/20 Day 2 catfish-memory plugin (hermes 侧) 接管 5 provider 的 prefetch 路径.
    # 5/20 audit verdict: memory/ 整目录 (~1387 LOC) 是 no-op 死代码, 跟 caller 一起删.

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

    # A2A self_register 5/26 砍 — Plan D Federation 整套停 (0 真客户用, 详见
    # docs/HERMES-013-ALIGN.md A2A 段). 不再调 self_register.

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

    # 6/7 BL-CATFISH-MANIFESTO clean-up: tool message archive summary_worker
    # 整套停. 历史:
    #   - 5/11 BL-Q3-ARCHIVE v1: gateway 端写 PG tool_archives 表 + haiku 摘要
    #   - 5/22 BL-CENTRAL-EDGE-TOOL-ARCHIVE Phase 6a: gateway PG archive 强制
    #     停, edge 端 (tool-bridge tool_archive_local.py) 接管 archive 写读
    #   - 5/26 BL-BOUNDARY: db.py 砍 PG 路径死代码, content 全员工本机
    #   - 6/7 (现在): summary_worker 没新 archive 进 PG 可摘要了, 跑空, 整套停
    # summary_worker 死代码留 git history, 后续 BL-MANIFESTO-CLEAN-DEAD 整套 rm.
    archive_summary_task = None

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
async def quota_me(
    user: User = Depends(get_current_user),
    x_catfish_user: str | None = Header(default=None, alias="X-Catfish-User"),
) -> dict[str, Any]:
    from . import quota  # 懒 import 避免顶层循环

    # 5/23 BL-QUOTA-EFFECTIVE-USER (鸿波): chat 写 quota_events 时按 effective_user_email
    # (resolve 后真员工 chenhongbo@ffcs.cn). quota_me 之前用 user.sub 查, hermes service
    # token 时 sub=client:hermes-cli, 查到 0 → dashboard 永远显示 0/不限. 改用同款 resolve
    # 让"读"按"写"一致.
    effective_user = resolve_effective_user_email(user, x_catfish_user)

    config = quota.load_quota_config()
    user_q = config.per_user_for(effective_user)

    now_ms = int(time.time() * 1000)
    minute_cutoff = now_ms - 60_000
    day_cutoff = now_ms - 86_400_000

    used_minute = quota.sum_tokens_user_since(effective_user, minute_cutoff)
    used_day = quota.sum_tokens_user_since(effective_user, day_cutoff)

    dept_used_day = 0
    dept_limit_day = 0
    if user.department:
        dept_used_day = quota.sum_tokens_dept_since(user.department, day_cutoff)
        dept_q = config.department_quotas.get(user.department)
        if dept_q is not None:
            dept_limit_day = dept_q.tokens_per_day

    return {
        "user_email": effective_user,
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
async def api_me(
    user: User = Depends(get_current_user),
    x_catfish_user: str | None = Header(default=None, alias="X-Catfish-User"),
) -> dict[str, Any]:
    """返当前 user 元信息. Companion useMe() 调.

    BL-AUTH-DECOUPLE-A1-API-ME-FIX (6/1 鸿波实盘): 之前直接返 user.sub +
    user.department/role, hermes service token (sub=client:hermes-cli) 时
    返了 service token 自己的元数据, 不是真员工.

    修: 用 resolve_effective_user_email 拿真员工 email, 再调
    fetch_user_metadata 查 identity users 表拿真 department/role/managed_dept.
    fallback: PG 没配 / 没找到员工 → 退到 service token 自己的元数据 (兼容
    单机 dev). 关键展示字段全对了, Companion conditional render 对路径.
    """
    from .db import fetch_user_metadata

    effective_email = resolve_effective_user_email(user, x_catfish_user)

    # 查真员工 metadata. service token 路径下用 effective email 查 identity
    # users 表; 普通 user token 路径下 effective_email == user.sub, 查到的应
    # 该跟 token claims 一致 (双重确认).
    real_meta = await fetch_user_metadata(effective_email)
    if real_meta is not None:
        department = real_meta["department"]
        role = real_meta["role"]
        managed_departments = real_meta["managed_departments"]
    else:
        # PG 没配 / 没找到 → fallback service token 元数据 (graceful)
        department = user.department
        role = user.role
        managed_departments = user.managed_departments or []

    return {
        "email": effective_email,
        "department": department,
        "role": role,
        "managed_departments": managed_departments,
        "auth_method": user.auth_method,
    }


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


@app.post("/api/learn/start_recording")
async def api_learn_start_recording(
    body: dict,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """开 RecMode session — 连 Catfish Chrome CDP + 起后台 listener (thin proxy → edge tool-bridge).

    Body: {"session_id": str, "chrome_ws": str (可选, 默认 ws://localhost:9222)}

    Returns: {session_id, started_at, output_dir}

    5/26 BL-RECMODE-MIGRATE-TO-EDGE batch 1: gateway 不再直接调 cdp_listener
    (中央代码不读写 ~/.catfish/recordings/). 转 Unix socket JSON-RPC 给 tool-bridge.  # noqa: BOUNDARY
    """
    from . import tool_bridge_rpc as _tb_rpc  # noqa: PLC0415

    session_id = (body.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id 不能空")
    chrome_ws = (body.get("chrome_ws") or "ws://localhost:9222").strip()
    connect_ws = bool(body.get("connect_ws", True))

    try:
        out = await _tb_rpc.call(
            "recmode/start_recording",
            {"session_id": session_id, "chrome_ws": chrome_ws, "connect_ws": connect_ws},
        )
    except _tb_rpc.ToolBridgeUnreachable as e:
        raise HTTPException(status_code=502, detail=f"tool-bridge 不可达: {e}") from e
    except _tb_rpc.ToolBridgeRPCError as e:
        # tool-bridge INVALID_PARAMS 含 "重复" → 409, 含 "失败" (RuntimeError) → 503
        if "重复" in e.message or "409" in e.message:
            raise HTTPException(status_code=409, detail=e.message) from e
        if "503" in e.message:
            raise HTTPException(status_code=503, detail=e.message) from e
        raise HTTPException(status_code=502, detail=e.message) from e

    if isinstance(out, dict):
        out["viewer"] = user.sub
    return out  # type: ignore[return-value]


@app.post("/api/learn/stop_recording")
async def api_learn_stop_recording(
    body: dict,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """停 RecMode session — flush events.jsonl + meta.json (thin proxy → edge tool-bridge).

    Body: {"session_id": str}
    Returns: meta dict (events_count, keyframes_count, duration_s, output_dir)
    """
    from . import tool_bridge_rpc as _tb_rpc  # noqa: PLC0415

    session_id = (body.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id 不能空")
    try:
        out = await _tb_rpc.call("recmode/stop_recording", {"session_id": session_id})
    except _tb_rpc.ToolBridgeUnreachable as e:
        raise HTTPException(status_code=502, detail=f"tool-bridge 不可达: {e}") from e
    except _tb_rpc.ToolBridgeRPCError as e:
        # tool-bridge "不存在" → gateway 映 404
        if "不存在" in e.message or "404" in e.message:
            raise HTTPException(status_code=404, detail=e.message) from e
        raise HTTPException(status_code=502, detail=e.message) from e

    if isinstance(out, dict):
        out["viewer"] = user.sub
    return out  # type: ignore[return-value]


@app.post("/api/learn/record_transcript")
async def api_learn_record_transcript(
    body: dict,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """RecMode A — thin proxy → edge tool-bridge.

    5/26 BL-RECMODE-MIGRATE-TO-EDGE batch 2 (E.1 收尾): batch 1 漏的 3 个
    app.py 内联 endpoint 之一. gateway 不再直接写员工 recordings/transcripts.jsonl,
    转 tool-bridge 处理.
    """
    from . import tool_bridge_rpc as _tb_rpc  # noqa: PLC0415

    session_id = (body.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id 不能空")
    catfish_home = os.environ.get("CATFISH_HOME", "").strip()
    try:
        out = await _tb_rpc.call(
            "recmode/record_transcript",
            {
                "session_id": session_id,
                "text": body.get("text", ""),
                "ts_offset": body.get("ts_offset"),
                "duration": body.get("duration"),
                "catfish_home": catfish_home,
            },
        )
    except _tb_rpc.ToolBridgeUnreachable as e:
        raise HTTPException(status_code=502, detail=f"tool-bridge 不可达: {e}") from e
    except _tb_rpc.ToolBridgeRPCError as e:
        if "不存在" in e.message or "404" in e.message:
            raise HTTPException(status_code=404, detail=e.message) from e
        raise HTTPException(status_code=400, detail=e.message) from e

    if isinstance(out, dict):
        out["viewer"] = user.sub
    return out  # type: ignore[return-value]


@app.get("/api/learn/skill_content")
async def api_learn_skill_content(
    skill_dir: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """RecMode B — thin proxy → edge tool-bridge.

    5/26 BL-RECMODE-MIGRATE-TO-EDGE batch 2: gateway 不再读员工 fs, 转 tool-bridge.
    """
    from . import tool_bridge_rpc as _tb_rpc  # noqa: PLC0415

    catfish_home = os.environ.get("CATFISH_HOME", "").strip()
    try:
        out = await _tb_rpc.call(
            "recmode/skill_content",
            {"skill_dir": skill_dir, "catfish_home": catfish_home},
        )
    except _tb_rpc.ToolBridgeUnreachable as e:
        raise HTTPException(status_code=502, detail=f"tool-bridge 不可达: {e}") from e
    except _tb_rpc.ToolBridgeRPCError as e:
        if "404" in e.message or "不存在" in e.message:
            raise HTTPException(status_code=404, detail=e.message) from e
        if "受信" in e.message:
            raise HTTPException(status_code=403, detail=e.message) from e
        raise HTTPException(status_code=400, detail=e.message) from e

    if isinstance(out, dict):
        out["viewer"] = user.sub
    return out  # type: ignore[return-value]


@app.post("/api/learn/save_skill")
async def api_learn_save_skill(
    body: dict,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """RecMode C — thin proxy → edge tool-bridge.

    5/26 BL-RECMODE-MIGRATE-TO-EDGE batch 2: 整段 fs 操作 (mv draft → skills,
    keep_forever flag) 搬 tool-bridge. gateway 不再 shutil.copytree 员工 fs.
    """
    from . import tool_bridge_rpc as _tb_rpc  # noqa: PLC0415

    catfish_home = os.environ.get("CATFISH_HOME", "").strip()
    try:
        out = await _tb_rpc.call(
            "recmode/save_skill",
            {
                "draft_dir": body.get("draft_dir", ""),
                "namespace": body.get("namespace", ""),
                "name": body.get("name", ""),
                "keep_forever": bool(body.get("keep_forever", False)),
                "catfish_home": catfish_home,
            },
            timeout_s=60.0,  # copytree 可能慢
        )
    except _tb_rpc.ToolBridgeUnreachable as e:
        raise HTTPException(status_code=502, detail=f"tool-bridge 不可达: {e}") from e
    except _tb_rpc.ToolBridgeRPCError as e:
        if "404" in e.message or "不存在" in e.message:
            raise HTTPException(status_code=404, detail=e.message) from e
        if "受信" in e.message or "必须在" in e.message:
            raise HTTPException(status_code=403, detail=e.message) from e
        if "422" in e.message:
            raise HTTPException(status_code=422, detail=e.message) from e
        raise HTTPException(status_code=400, detail=e.message) from e

    if isinstance(out, dict):
        out["viewer"] = user.sub
    return out  # type: ignore[return-value]


@app.post("/api/learn/repair_selector")
async def api_learn_repair_selector(
    body: dict,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """V2 #68 selector 漂移修复: skill 跑时 find_by_text(hint) 找不到 →
    catfish_browser_runtime POST 这里, gateway 转 tool-bridge 调 vision 看截图找新 selector.

    Body: {
        "hint": {"text": "应用", "near_text": "通讯录", "role": "tab"},
        "screenshot_b64": str,
        "context": str (录制时语音转写, 可空)
    }

    Returns: {found, text, near_text, role, confidence, reason}

    5/25 BL-RECMODE-MIGRATE-TO-EDGE: gateway 不再直接调 selector_repair.
    转 Unix socket JSON-RPC 给 edge tool-bridge (跑在 ~/.catfish/tool-bridge.sock).  # noqa: BOUNDARY
    中央代码 (`central/`) 不再读 / 写 ~/.catfish/recordings/. 详见  # noqa: BOUNDARY
    `docs/CENTRAL-EDGE-DATA-BOUNDARY.md` E.1 / `tool_bridge_rpc.py`.
    """
    from . import tool_bridge_rpc as _tb_rpc  # noqa: PLC0415
    hint = body.get("hint") or {}
    screenshot = (body.get("screenshot_b64") or "").strip()
    context = (body.get("context") or "").strip()
    if not hint:
        raise HTTPException(status_code=400, detail="hint 不能空")
    if not screenshot:
        raise HTTPException(status_code=400, detail="screenshot_b64 不能空 (vision 必须看图)")

    auth_token = os.environ.get("CATFISH_DEV_TOKEN", "")
    try:
        out = await _tb_rpc.call(
            "recmode/repair_selector",
            {
                "hint": hint,
                "screenshot_b64": screenshot,
                "context": context,
                "auth_token": auth_token,
            },
        )
    except _tb_rpc.ToolBridgeUnreachable as e:
        # tool-bridge 没起 / socket 缺 — caller (skill runtime) 期望 502
        raise HTTPException(
            status_code=502,
            detail=f"tool-bridge 不可达, RecMode vision 不可用: {e}",
        ) from e
    except _tb_rpc.ToolBridgeRPCError as e:
        # tool-bridge 自己抛错. INVALID_PARAMS 转 400, 别的转 502
        status = 400 if e.code == _tb_rpc.JSONRPC_INVALID_PARAMS else 502
        raise HTTPException(status_code=status, detail=e.message) from e

    if isinstance(out, dict):
        out["viewer"] = user.sub
    return out  # type: ignore[return-value]


@app.post("/api/learn/cleanup")
async def api_learn_cleanup(
    body: dict,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """显式触发 RecMode 录屏清理 (thin proxy → edge tool-bridge).

    5/25 BL-RECMODE-NO-AUTO-DELETE: catfish 后台 daemon 已撤掉, 此 endpoint
    成为**唯一**的清理触发入口 — Dashboard "我的录屏" 区 / admin CLI / 测试
    显式调. 没人调 = 录屏永久留 (员工本机, 员工自主).

    Body: {"dry_run"?: bool (默认 false 真删), "ttl_days"?: int (默认 14)}
    Returns: cleanup_old_recordings stats

    5/26 BL-RECMODE-MIGRATE-TO-EDGE batch 1: cleanup.py 搬 edge, gateway thin proxy.
    """
    from . import tool_bridge_rpc as _tb_rpc  # noqa: PLC0415

    dry_run = bool(body.get("dry_run", False))
    ttl_days = int(body.get("ttl_days") or 14)
    try:
        stats = await _tb_rpc.call(
            "recmode/cleanup",
            {"dry_run": dry_run, "ttl_days": ttl_days},
        )
    except _tb_rpc.ToolBridgeUnreachable as e:
        raise HTTPException(status_code=502, detail=f"tool-bridge 不可达: {e}") from e
    except _tb_rpc.ToolBridgeRPCError as e:
        raise HTTPException(status_code=502, detail=e.message) from e

    if isinstance(stats, dict):
        stats["viewer"] = user.sub
    return stats  # type: ignore[return-value]


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

    # 从 skill_dir 推 namespace/name (路径形如 ~/.catfish/skills/personal/skill_x)  # noqa: BOUNDARY
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
    request: Request,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """触发 RecMode aggregator (thin proxy → edge tool-bridge): 读 session_dir →
    调 catfish-private-main → 解析 JSON → 落 SKILL.md + main.py 到
    ~/.catfish/skills/<namespace>/<name>/.  # noqa: BOUNDARY

    Body: {"session_id": str, "skills_root": str (可选, 默认 ~/.catfish/skills)}  # noqa: BOUNDARY

    Returns: {skill_name, namespace, skill_dir, steps_count, confidence,
              questions_for_user}

    Caller (Companion): 录屏完点 ✅ → 先 POST /stop_recording → 再 POST /analyze
    → 拿 skill_dir → 读 SKILL.md / main.py 显 preview UI 给用户 review.

    5/25 BL-RECMODE-MIGRATE-TO-EDGE: gateway 不再直接调 aggregator (中央代码
    不读写 ~/.catfish/recordings/ ~/.catfish/skills/). 透传 Unix socket JSON-RPC  # noqa: BOUNDARY
    到 edge tool-bridge, 由 tool-bridge 进程跑 aggregate_session + 落盘.
    详见 docs/CENTRAL-EDGE-DATA-BOUNDARY.md E.1 / tool_bridge_rpc.py.

    5/27 BL-RECMODE-AUTH-FORWARD (鸿波): aggregator 调 LLM 时之前只读 env
    CATFISH_DEV_TOKEN, 5/19 BL-AUTH-DECOUPLE 切到 API_SERVER_KEY + OAuth 后
    env 多半没设 → 报"没 auth token" 502. 改成透传 caller 的 Authorization
    + user.email 当 X-Catfish-User, 让 aggregator 用员工身份调 catfish-gateway.
    """
    from . import tool_bridge_rpc as _tb_rpc  # noqa: PLC0415

    session_id = (body.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id 不能空")

    skills_root_str = (body.get("skills_root") or "").strip()
    # CATFISH_HOME 透传给 tool-bridge (它进程可能没拿到同样的 env)
    catfish_home = os.environ.get("CATFISH_HOME", "").strip()

    # BL-RECMODE-AUTH-FORWARD (5/27 鸿波): 优先用 caller 的 Bearer (Companion
    # 在 hermes 模式下带 API_SERVER_KEY service token, 在 OAuth 模式下带员工
    # token). env CATFISH_DEV_TOKEN 当兜底, 保留单机 dev 流程能跑.
    auth_token = ""
    incoming_auth = request.headers.get("Authorization", "") or ""
    if incoming_auth.lower().startswith("bearer "):
        auth_token = incoming_auth[7:].strip()
    if not auth_token:
        auth_token = os.environ.get("CATFISH_DEV_TOKEN", "")
    # X-Catfish-User: service token 模式 (sub=client:hermes-cli) 必须带, 否则
    # 下游 gateway BL-AUTH-DECOUPLE-A1 直接 400. user 已经从 OAuth/header 解出.
    # BL-RECMODE-AUTH-FORWARD fallback: user.email 空时, 尝试从 caller 的
    # X-Catfish-User header 兜底 (Companion 在 hermes 模式下本来就带这 header).
    effective_user = (getattr(user, "email", "") or "").strip()
    if not effective_user:
        effective_user = (request.headers.get("X-Catfish-User", "") or "").strip()
    # BL-RECMODE-AUTH-FORWARD (5/27): info-level 留诊断, 不污染 warning 流.
    # TODO 5/28: 内网验完 effective_user 是否拿到员工 email 后, 可降级 debug 或砍.
    # (踩过的坑: user.email 是空 → 从 X-Catfish-User header 兜底; 看 log 这条
    #  能直接确认是哪段拿到的)
    logger.info(
        "BL-RECMODE-AUTH-FORWARD: api_learn_analyze user.email=%r "
        "X-Catfish-User-header=%r → effective_user=%r auth_token_present=%s",
        getattr(user, "email", None),
        request.headers.get("X-Catfish-User"),
        effective_user,
        bool(auth_token),
    )

    draft_only = bool(body.get("draft_only", True))

    try:
        out = await _tb_rpc.call(
            "recmode/analyze",
            {
                "session_id": session_id,
                "skills_root": skills_root_str,
                "catfish_home": catfish_home,
                "auth_token": auth_token,
                "effective_user": effective_user,
                "draft_only": draft_only,
            },
        )
    except _tb_rpc.ToolBridgeUnreachable as e:
        raise HTTPException(
            status_code=502,
            detail=f"tool-bridge 不可达, RecMode aggregate 不可用: {e}",
        ) from e
    except _tb_rpc.ToolBridgeRPCError as e:
        # session_dir 不存在 / params 错 → 400, 别的 → 502
        # (tool-bridge server.py 用 INVALID_PARAMS 报 "session_dir 不存在", 我们
        #  把这种保留 404 语义 — caller Companion UI 依据 404 提示员工先录屏)
        if e.code == _tb_rpc.JSONRPC_INVALID_PARAMS:
            # 区分 "session_dir 不存在" vs 别的参数错
            status = 404 if "session_dir" in e.message and "不存在" in e.message else 400
            raise HTTPException(status_code=status, detail=e.message) from e
        # INTERNAL_ERROR — aggregator 内部 (LLM 挂 / JSON 解析挂)
        if "参数错" in e.message:  # ValueError 转过来的
            raise HTTPException(status_code=422, detail=e.message) from e
        raise HTTPException(status_code=502, detail=e.message) from e

    if isinstance(out, dict):
        out["viewer"] = user.sub
    return out  # type: ignore[return-value]


@app.get("/api/learn/active")
async def api_learn_active(
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """列当前在录的 RecMode session (调试 / 监控用) — thin proxy → edge tool-bridge.

    5/26 BL-RECMODE-MIGRATE-TO-EDGE batch 1.
    """
    from . import tool_bridge_rpc as _tb_rpc  # noqa: PLC0415
    try:
        out = await _tb_rpc.call("recmode/active", {})
    except _tb_rpc.ToolBridgeUnreachable as e:
        raise HTTPException(status_code=502, detail=f"tool-bridge 不可达: {e}") from e
    except _tb_rpc.ToolBridgeRPCError as e:
        raise HTTPException(status_code=502, detail=e.message) from e

    if isinstance(out, dict):
        out["viewer"] = user.sub
    return out  # type: ignore[return-value]


@app.get("/api/learn/status/{session_id}")
async def api_learn_status(
    session_id: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """RecMode F (5/14): 单 session 实时状态 — Companion 录制浮层 polling (thin proxy).

    返 elapsed_s + events_count + keyframes_count + ws_connected.
    没在录中 → 404 (Companion 应停 polling).
    """
    from . import tool_bridge_rpc as _tb_rpc  # noqa: PLC0415
    try:
        out = await _tb_rpc.call("recmode/status", {"session_id": session_id})
    except _tb_rpc.ToolBridgeUnreachable as e:
        raise HTTPException(status_code=502, detail=f"tool-bridge 不可达: {e}") from e
    except _tb_rpc.ToolBridgeRPCError as e:
        # tool-bridge "没在录" → gateway 映 404 (老语义)
        if "没在录" in e.message or "404" in e.message:
            raise HTTPException(status_code=404, detail=f"session {session_id} 没在录") from e
        raise HTTPException(status_code=502, detail=e.message) from e

    if isinstance(out, dict):
        out["viewer"] = user.sub
    return out  # type: ignore[return-value]


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


# ── BL-EDGE-TOOL-KEY (5/24 鸿波): hermes 边缘工具中央派发 backend key ──
#
# 让 catfish-cli refresh-hermes 拉这个 endpoint, 把 Tavily/Firecrawl 等
# 第三方 key 从中央 .env 同步到员工 ~/.hermes/.env, admin 改 key 50 台机器  # noqa: BOUNDARY
# 下次 refresh 自动拿新值, 不用 ssh 全跑一遍.
#
# 详见 catfish_gateway/edge_tool_config.py docstring.


@app.get("/v1/edge/tool-config/{tool_name}")
async def edge_tool_config(
    tool_name: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """中央派发 hermes 边缘工具的 backend key + yaml block.

    协议:
      - tool 不在 registry → 404
      - tool 在 registry 但 RBAC 拦 → 403 (user.can_use_tool false)
      - tool 在 registry + RBAC 通 + gateway env 没配 key → 503
      - 200 → {tool_name, tool_group, provider, env_vars, yaml_block}

    被调约定:
      - Bearer 任意 JWT (用户 OAuth token / hermes-cli service token 都行).
      - CLI 拿到响应后, env_vars 合并写 ~/.hermes/.env (per-key update),  # noqa: BOUNDARY
        yaml_block 合并写 ~/.hermes/config.yaml (preserve sibling keys).  # noqa: BOUNDARY
    """
    from . import edge_tool_config as etc  # 懒 import 防循环

    if not etc.is_supported_tool(tool_name):
        raise HTTPException(
            status_code=404,
            detail={
                "error": f"tool {tool_name!r} 不在中央派发 registry",
                "supported": etc.list_supported_tools(),
            },
        )

    # RBAC — sysadmin / 空 effective_allowed_tools = 全允许 (User.can_use_tool 内置)
    if not user.can_use_tool(tool_name):
        raise HTTPException(
            status_code=403,
            detail=(
                f"department={user.department} 未获批工具 {tool_name}. "
                f"effective_allowed_tools={user.effective_allowed_tools} "
                f"(让 admin 在 identity-server 部门 RBAC 加这个 tool)"
            ),
        )

    status, body = etc.build_response(tool_name)
    if status == 503:
        raise HTTPException(status_code=503, detail=body)
    if status != 200:
        # registry 命中校验已经在上面做过, 走到这只能是未来加的新错误码
        raise HTTPException(status_code=status, detail=body)
    return body


@app.get("/v1/edge/tool-config")
async def edge_tool_config_list(
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """给 CLI 用 — 一次拉支持的 tool 列表, 不返 key 值 (key 走 per-tool endpoint).

    CLI 用法:
        names = GET /v1/edge/tool-config → ["web_search", "web_extract", ...]
        for name in names: GET /v1/edge/tool-config/{name}
    """
    from . import edge_tool_config as etc

    return {
        "supported": etc.list_supported_tools(),
        # 顺手把 group 元信息暴露, CLI 可以提前去重 (3 个 web tool 共一份 env)
        "groups": _group_metadata(),
    }


def _group_metadata() -> dict[str, dict[str, Any]]:
    """给 edge_tool_config_list 用: { tool_group: {provider, env_var_name, tools: [...]} }."""
    from . import edge_tool_config as etc

    out: dict[str, dict[str, Any]] = {}
    for name, cfg in etc.EDGE_TOOL_REGISTRY.items():
        g = out.setdefault(
            cfg.tool_group,
            {"provider": cfg.provider, "env_var_name": cfg.env_var_name, "tools": []},
        )
        g["tools"].append(name)
    for g in out.values():
        g["tools"].sort()
    return out


# /api/audit/me — 员工自查 "中央到底存了我啥".
#
# BL-EMPLOYEE-PRIVACY-VERIFICATION (#79, 5/25): Companion 隐私 tab + privacy-audit
# CLI 都接这条. 员工不需要任何 RBAC 角色, 谁登录返谁的数据.
#
# Privacy contract (写死, 永远不回退):
#   1. 返的字段全是 metadata (count / token / model / 时间戳), 没有 prompt / response
#   2. 员工只能拿到自己 sub 的数据 (filter 在 quota.audit_summary_user_since 里, 不走 query 参数 — 防 IDOR)
#   3. first_seen_ts / last_seen_ts 不局限 cutoff_ms, 让员工知道"中央存了我多久"


@app.get("/api/audit/me")
async def api_audit_me(
    user: User = Depends(get_current_user),
    x_catfish_user: str | None = Header(default=None, alias="X-Catfish-User"),
) -> dict[str, Any]:
    """员工自查: 中央对我存了啥 metadata. 不需 RBAC, 谁登录返谁的.

    BL-AUTH-DECOUPLE-A1-API-ME-FIX (6/1): 跟 /api/me + /api/quota/me 同款 resolve.
    chat 写 quota_events 用 effective_user_email (真员工 chenhongbo@ffcs.cn),
    audit_summary_user_since 查也要按 effective 查, 才能找回真员工 audit 记录.
    """
    from . import quota

    from .db import fetch_user_metadata

    effective_email = resolve_effective_user_email(user, x_catfish_user)

    # 查真员工 department (同 /api/me 处理) — service token 时 user.department 是
    # service 维度 (infra), 真员工 department 在 identity users 表.
    real_meta = await fetch_user_metadata(effective_email)
    real_department = real_meta["department"] if real_meta else user.department

    now_ms = int(time.time() * 1000)
    day_cutoff = now_ms - 86_400_000

    summary = quota.audit_summary_user_since(effective_email, day_cutoff)

    # 6/2 BL-PRIVACY-CARD-QUOTA-PROGRESS (鸿波 6/2 凌晨): PrivacyCard 把"今天用了
    # 7.99M"改成 quota 进度条. 这里加 quota_day_limit 字段, 客户端就能渲染
    # 已用/上限 = 百分比. /api/quota/me 早已返这字段, 但 PrivacyCard 调的是
    # /api/audit/me — 不给员工拼两个 API, 直接在这条加上.
    # tokens_per_day=0 表示该 user 不限 (yaml overrides 没配 → 走 default_user 1M).
    quota_cfg = quota.load_quota_config()
    user_q = quota_cfg.per_user_for(effective_email)
    quota_day_limit = user_q.tokens_per_day  # 0 = 不限

    return {
        "user_email": effective_email,
        "department": real_department,
        "since_ms": day_cutoff,
        # 让客户端知道"中央存的字段长这样", 防员工担心还有别的没暴露
        "schema_note": "本端点只返 metadata: count / tokens / model / 时间戳. 中央不存 prompt / response 文本.",
        "quota_day_limit": quota_day_limit,
        **summary,  # request_count / total_tokens / by_model / first_seen_ts / last_seen_ts
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
    since_hours: int = 24,
    model: str | None = None,
    dept: str | None = None,
    user_email: str | None = None,
) -> dict[str, Any]:
    """全员 audit 聚合 — admin 看请求总数 / 模型分布 / 部门分布 / top 员工.

    BL-AUDIT-UX-P1 (5/17): 加 since_hours 时间窗 + 上期对照.
    BL-AUDIT-UX-P2 (5/17): 加 drill-down filter (model/dept/user_email).
      点 audit 页某行 → 前端把该值塞进 URL query, /api/audit/global 收到后
      把 SQL 加 WHERE. 上期 trend 跟当前期同 filter 才有意义.

      since_hours: 1-720 (1 小时-30 天), 默认 24h.
      model: catalog ID (例 'catfish-public-nvidia-nemotron'), None=全部
      dept: 部门名 (含 '(未分组)' 合成桶), None=全部
      user_email: 员工 email (含 '(未分组员工)' 合成桶), None=全部
    """
    from . import quota
    _require_admin(user)

    # 钳到合理范围: 1 小时 - 30 天
    hours = max(1, min(720, int(since_hours)))
    window_ms = hours * 3_600_000

    now_ms = int(time.time() * 1000)
    period_start_ms = now_ms - window_ms
    prev_period_start_ms = period_start_ms - window_ms

    # 空字符串当 None 处理 — 前端 /api/audit/global?model=&dept=eng 这种半填的也兼容
    filter_kwargs = {
        "filter_model": model or None,
        "filter_dept": dept or None,
        "filter_user": user_email or None,
    }

    summary = quota.audit_summary_global_since(period_start_ms, **filter_kwargs)
    prev = quota.audit_period_totals(
        prev_period_start_ms, period_start_ms, **filter_kwargs,
    )

    return {
        "since_ms": period_start_ms,
        "since_hours": hours,
        **summary,
        # BL-AUDIT-UX-P1: 上期对照, 给前端做 trend ↑12% / ↓8% 用
        "previous_request_count": prev["request_count"],
        "previous_total_tokens": prev["total_tokens"],
        "previous_active_users": prev["active_users"],
        "previous_active_departments": prev["active_departments"],
        # BL-AUDIT-UX-P2: 把当前 filter echo 回前端, 显示 pill 用
        "filter": {
            "model": filter_kwargs["filter_model"],
            "dept": filter_kwargs["filter_dept"],
            "user_email": filter_kwargs["filter_user"],
        },
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


# 5/26 BL-PROACTIVE-DECOUPLE v2 (CORS 修):
# 老 5/26 早版本用 header (X-Catfish-Journal-Tail-B64 + X-Catfish-Last-Model)
# 透传, 但 Companion 走 hermes proxy CORS allowlist 不含这俩自定义 header,
# 浏览器层 preflight 直接 block (TypeError: Load failed). 改 body 字段透传:
#   - /api/proactive/starter: GET → POST, body 含 journal_tail + last_model
#   - /api/proactive/contextual: 仍 POST, 加 last_model body 字段
# body 走 Content-Type: application/json, 标准 CORS allowlist 无问题.
# 老 GET caller (5/26 早版本 Companion) 走 405, 不致命 — caller 立即 fallback.


@app.post("/api/proactive/starter")
async def api_proactive_starter(
    body: dict[str, Any] | None = None,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """返一个上下文感知的 starter (引用员工 journal + 时段). 失败返 fallback 模板.

    5/26 BL-PROACTIVE-DECOUPLE: gateway 不再读员工本机 journal + state.db.
    Companion 通过 body 字段透传:
      body = {
        "journal_tail": "<员工 ~/.catfish/employee_journal.md 末尾 UTF-8>",  # noqa: BOUNDARY
        "last_model": "<员工最近 session 用的 model name>"
      }
    body 缺 / 字段空 → gateway fallback 模板.
    """
    from . import proactive
    b = body or {}
    journal_tail = (b.get("journal_tail") or "")[:65536]  # cap 64K 防滥发
    model_name = (b.get("last_model") or "").strip() or None
    return await proactive.generate_starter(
        user_email=user.sub, journal_tail=journal_tail, model_name=model_name,
    )


# 5/6 BL-E13.5 真主动 Phase B: 信号触发的针对性 starter
@app.post("/api/proactive/contextual")
async def api_proactive_contextual(
    body: dict[str, Any],
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """信号触发的 starter. body = {signal_kind: str, context: dict, last_model?: str}.

    5/26 BL-PROACTIVE-DECOUPLE v2: model_name 从 body 字段 last_model 拿
    (老版本走 header, 撞 hermes CORS allowlist, 改 body 绕开).
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
    model_name = (body.get("last_model") or "").strip() or None
    return await proactive.generate_contextual_starter(
        signal_kind, context, user_email=user.sub, model_name=model_name,
    )


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


# 6/2 BL-PROMPT-CACHE-PHASE1 (鸿波 6/2 下午拍): provider 真不支持 cache_control 标记
# 的 prefix. LiteLLM 1.86 实测: nvidia_nim / groq 没 cache transform, 标记可能让 NIM
# 严格 schema 校验报 400 BadRequest. 这些 provider 跳过, 0 标记 0 副作用.
#
# 真支持矩阵 (LiteLLM llms/<provider>/chat/transformation.py 含 cache_control 处理):
#   anthropic / dashscope / openrouter / cometapi 真转
#   gemini: 转 cached_content (有 32K 最低 cache size, gemini-3.5-flash; pro 4K)
#   deepseek: openai 协议透传, server 端自动 implicit cache (不依赖客户端标记)
#   私有 vLLM (openai/qwen_*): vLLM prefix cache 自动, 加标记 silently 忽略
#
# 6/2 晚事故经审: 鸿波生产 chat 全挂的真原因是 **hermes API server 8642 CORS 缺
# X-Catfish-* header allowlist**, request preflight 就被浏览器拒, 根本没到 gateway.
# 跟本 cache_control 0 关系. 我曾误"预防性"改 allowlist 收紧, 已 revert. 保持原
# blocklist 模式.
_CACHE_UNSUPPORTED_PROVIDERS = ("nvidia_nim/", "groq/")


def _provider_supports_cache_marker(upstream_model: str) -> bool:
    """True 时给 system + tools 加 cache_control 标记.

    LiteLLM 转上游时:
    - 支持的 (anthropic/dashscope/gemini): 真省 input tokens 计费
    - 透传不破的 (deepseek/私有 vLLM): silently 忽略, 0 副作用
    - 真破的 (nvidia_nim/groq): 跳过, 防 400 BadRequest
    """
    if not upstream_model:
        return False
    for bad in _CACHE_UNSUPPORTED_PROVIDERS:
        if upstream_model.startswith(bad):
            return False
    return True


def _apply_prompt_cache_markers(params: dict, model) -> None:
    """6/2 BL-PROMPT-CACHE-PHASE1: 给 messages[0] (system) + tools[-1] 加 cache_control.

    Anthropic 风格: 在 system message content list 最后块 + tools 数组最后一个 tool
    上各加一个 cache_control breakpoint. LiteLLM 转给各 provider 原生协议.

    最多 4 个 cache breakpoint (Anthropic 限制), 我们用 2 个 (system / tools), 留
    2 个未来扩展 (user history 长 prompt 时再加).

    幂等: 若 content 已经是 list-of-blocks 且最后块已有 cache_control, 不重复.

    6/2 晚 BL-CACHE-MARKER-EMERGENCY-OFF v2: 真生产 CORS 修通后所有 model
    撞 400 BadRequest, 不只 Gemini. 真原因暂不明 (可能 LiteLLM 1.86 对所有 provider
    都不接 cache_control content list-of-blocks), 暂时**默认关** 防生产挂.
    env CATFISH_CACHE_MARKER_ENABLE=1 显式开 (上线前周一真测过几个 provider 再 toggle).
    """
    if os.environ.get("CATFISH_CACHE_MARKER_ENABLE", "").lower() not in ("1", "true", "yes"):
        return  # 6/2 晚事故: 默认关, 真生产稳定为先
    if not _provider_supports_cache_marker(model.upstream.model):
        return

    messages = params.get("messages")
    if not isinstance(messages, list) or not messages:
        return

    # ── system message: content str → list-of-blocks + cache_control ──
    # 注意 hermes 真生产里第一条总是 system (BL-FIX2 pre-unwrap 也保证), 真实操作上
    # 14.5K 大头都在这条 — 缓存它是收益最大的.
    first = messages[0]
    if first.get("role") == "system":
        content = first.get("content")
        if isinstance(content, str) and content.strip():
            first["content"] = [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        elif isinstance(content, list) and content:
            # 已是 list (vision 或 历史 multipart). 给最后一块 text 加 cache_control.
            for block in reversed(content):
                if isinstance(block, dict) and block.get("type") == "text":
                    if "cache_control" not in block:
                        block["cache_control"] = {"type": "ephemeral"}
                    break

    # ── tools: 最后一个 tool 加 cache_control (Anthropic 风格 — 标 prefix 结尾) ──
    tools = params.get("tools")
    if isinstance(tools, list) and tools:
        last = tools[-1]
        if isinstance(last, dict) and "cache_control" not in last:
            last["cache_control"] = {"type": "ephemeral"}


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

    # 6/2 BL-PROMPT-CACHE-PHASE1 (鸿波 6/2 下午拍): 给 system message + tools
    # 加 cache_control. audit 显示真 prompt 34K 里 19K 是 tools + 14.5K 是 system,
    # 99% 是固定开销, 真 user input 占 3%. 上游缓存这部分能省 70-90%.
    #
    # 兼容矩阵 (6/2 audit, LiteLLM 1.86.0):
    #   - deepseek      : 服务端自动 implicit cache, 客户端标记 silently 忽略, 0 副作用
    #   - dashscope     : LiteLLM dashscope transformation 真转 (catfish-public-qwen-flash)
    #   - gemini        : LiteLLM 转 cached_content (gemini-3.5-flash 32K 最低, pro 4K)
    #   - anthropic     : 原生 cache_control 协议 (catfish 现在没用 anthropic 直连)
    #   - 私有 vLLM qwen: vLLM 自动 prefix cache, 标记忽略 (省 GPU 时间不省 token)
    #   - nvidia_nim    : 不支持, 标记可能让 NIM 校验报错 (跳过)
    #   - groq          : 不支持, 标记跳过
    _apply_prompt_cache_markers(params, model)

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


def _extract_nested_usage(usage, key: str) -> int:
    """BL-CACHE-AUDIT (5/17): cache_* tokens 可能挂在 usage.prompt_tokens_details
    或 usage 顶 (LiteLLM 不同 provider 不同). 试两个位置都拿不到返 0.
    """
    if usage is None:
        return 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        v = getattr(details, key, None)
        if v:
            return int(v)
        if isinstance(details, dict):
            v = details.get(key)
            if v:
                return int(v)
    if isinstance(usage, dict):
        v = usage.get(key)
        if v:
            return int(v)
    return 0


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
    source_hint: str = "unknown",  # BL-RBAC-DAY4-HARDENING (5/17): X-Catfish-Source audit
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
        # 6/7 BL-GROQ-TPM-PREFLIGHT: 检查上游 rate_limits.tpm, 超就 raise 413 +
        # 友好中文 + 替代 model. 拦下来不让员工看 stack trace.
        from .rate_limit_preflight import preflight_check_rate_limits  # noqa: PLC0415
        preflight_check_rate_limits(model, prompt_estimate, config=config)
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

        # ── BL-TASK-ASSESS-1-GATEWAY (5/15 鸿波"客户端要评估完成情况") ──
        # 在 [DONE] 之前多发一条 task_assessment 事件, Companion 接到后做
        # promise-vs-reality 检测 (assistant 文字说了"已生成"但 cum_tc=false +
        # 文件路径不存在 → ⚠ 嘴炮). 不重复算 — gateway 一次性把状态给客户端.
        #
        # 格式: 一条普通 data: 行, object="task_assessment". OpenAI 兼容客户端
        # 看到 unknown object 会忽略 (extra-field 容忍). Companion 嗅 object
        # 字段拿 metadata.
        try:
            # 5/26 P0 砍: skills_loader.discover_skills + has_skill_intent 全砍
            # (中央扫员工本机 SKILL.md → 上游 LLM, 隐私违规). sg_fired 永远 False
            # 是预期行为 — LLM 走 hermes tool calling 自己知道有哪些 skill 可调.
            sg_fired = False

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

    config: Config = app.state.config
    model = _resolve_model(config, model_name)

    # BL-F17 (5/5): 早早判定 internal call, 让后面所有 inject 阶段 (session_meta tick /
    # prompt_security detector / 等) 都能跳过 internal 调用. 这条**必须**在 line 1074
    # session_meta tick 之前赋值, 否则 UnboundLocalError. (5/5 17:13 鸿波报 P0,
    # 之前不知谁不小心注释了, 导致 chat_completions 全 500.)
    #
    # 5/21 BL-CORS-DEBT-FIX: hermes proxy 8642 CORS allow-list 没配 X-Catfish-*, 撞 preflight.
    # 加 query param fallback (?catfish_internal=1) 兼容. header 仍优先, 老 caller 不破.
    is_internal_call = (
        request.headers.get("x-catfish-internal", "").lower() in ("true", "1", "yes")
        or request.query_params.get("catfish_internal", "").lower() in ("true", "1", "yes")
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

        # tool message 长内容兜底: 走 FIX41 硬切 (truncate) — 永远不写 PG.
        #
        # 历史:
        #   - 5/11 BL-Q3-ARCHIVE v1: gateway PG archive + summary worker
        #   - 5/22 BL-CENTRAL-EDGE-TOOL-ARCHIVE Phase 6a: edge 端 (tool-bridge
        #     tool_archive_local.py) 接管, gateway PG 路径 default 禁用
        #   - 5/26 BL-BOUNDARY: db.py 砍 PG, content 100% 员工本机
        #   - 6/7 BL-CATFISH-MANIFESTO + CLEAN-DEAD: 删 env=1 PG 回滚后门 + rm
        #     6 个 dead module (router/db/reader/prompts/features/summary_worker),
        #     archiver.py 缩到只剩 prepare_tool_messages truncate-only wrapper
        #
        # 现在 prepare_tool_messages 物理上**只能 truncate** — 跟 manifesto 公理 4
        # "API surface 物理无能"一致, 没任何回滚到 PG archive 的能力.
        #
        # 真 archive 在 edge 端 tool-bridge tool_archive_local.py 做, gateway 看不到.
        from .tool_archive import prepare_tool_messages  # noqa: PLC0415
        body["messages"] = prepare_tool_messages(
            body["messages"],
            user_email=effective_user_email,  # X-Catfish-User on-behalf-of 模式覆盖
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
            # BL-AUTH-DECOUPLE-A1 (5/19): 压缩按 effective_user_email 归账 (service
            # on-behalf-of 时 = X-Catfish-User 指定的员工).
            new_msgs, compress_stats = await maybe_compress_messages(
                body.get("messages") or [],
                user_sub=effective_user_email,
                model_context_window=ctx_window,
                # BL-INTERNAL-MODEL-FOLLOW-USER (5/17): 压缩用员工选的同款 model
                origin_model=model_name,
            )
            if compress_stats:
                body["messages"] = new_msgs
                logger.info(
                    "BL-COMPRESSION-GATEWAY: sub=%s 压 %d 条历史 → 摘要, "
                    "token %d→%d (省 %d%%)",
                    effective_user_email,
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
            effective_user_email, model_name,
        )
    else:
        try:
            prompt_text = "\n".join(
                (m.get("content") or "") if isinstance(m.get("content"), str)
                else "" for m in (body.get("messages") or [])
                if isinstance(m, dict)
            )
            estimated = _quota_module.estimate_tokens(prompt_text)
            # BL-AUTH-DECOUPLE-A1 (5/19): quota 归账给 effective_user_email.
            # 普通 user JWT: 跟 user.sub 一致, 行为不变.
            # service token on-behalf-of (hermes-cli): quota 归到 X-Catfish-User
            # 指定的员工, 不归到 client:hermes-cli (后者会让所有员工共享一个 quota).
            qc = _quota_module.check_quota(
                user_email=effective_user_email,
                department=user.department,
                model=model_name,
                est_tokens=estimated,
                role=user.role,  # BL-FIX39 (5/11): admin / sysadmin 跳 quota
            )
            if not qc.allowed:
                friendly = _quota_module.friendly_quota_message(
                    qc, effective_user_email, model_name,
                )
                logger.info(
                    "quota deny: user=%s model=%s dimension=%s current=%d limit=%d",
                    effective_user_email, model_name, qc.dimension, qc.current, qc.limit,
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
                body, user_sub=effective_user_email, user_dept=user.department,
                model_name=model_name, model=model,
                security_concern=security_concern,
                is_internal=is_internal_call,  # BL-F17: 透传, 跳 record_usage
                source_hint=source_hint,  # BL-RBAC-DAY4-HARDENING (5/17)
                # BL-AUTH-DECOUPLE-A1 (5/19): user_sub 透传 effective_user_email,
                # audit / record_usage 都按 X-Catfish-User 归账 (service token on-behalf-of).
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
