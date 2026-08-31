"""目录 / 模型 / embeddings / 身份 / 边缘工具配置 等零散路由 —— 从 app.py 拆出 (8/15)。

/v1/catalog · /v1/models · /v1/models/{id} · /v1/embeddings · /v1/roles ·
/v1/edge/tool-config[/{tool}] · /api/me · /api/dev/users ·
/api/proactive/{starter,contextual} · 四个探活 stub (/healthz /version
/api/v1/models /props)

放一起不是因为它们相关, 恰恰是因为**互不相关**: 每个都是独立 endpoint,
不共享任何模块级状态 (拆前扫过: 零 global、零 yield)。凑一个文件只是为了
让 app.py 降下来; 哪组长大了从这里再分出去就是。

形式跟 audit_router / quota_router 一样用 `register_misc_routes(app)` ——
app.py:621 定的规矩: 路由要挂在同一个 app 上, 没法 `from X import *`。

`_group_metadata` 只被本组用 (全库 grep 确认), 一起搬。
`_resolve_model` **留在 app.py** —— chat_completions 也要用它, 搬走就得两边
各一份。这里走函数体内延迟 import, 免得顶层 import 成环 (app.py import 本模块)。
"""
from __future__ import annotations

import os
import time
from typing import Any

import litellm
from fastapi import Depends, Header, HTTPException, Request

from . import quota as _quota_module
from .auth import (
    User,
    X_CATFISH_USER_HEADER,
    get_current_user,
    get_current_user_optional,
    is_service_principal,
    resolve_effective_user_email,
)
from .catalog import build_catalog
from .config import Config, get_config, invalidate_config, load_raw_models
from .llm_params import (
    _build_litellm_params,
    _extract_nested_usage,
    _model_info_payload,
    _raise_upstream_error,
    _resolve_auto_sentinel,
)
from .metrics import log_request_metadata

import logging

# 跟 app.py 同名 —— logging.getLogger 同名返回同一对象
logger = logging.getLogger("catfish.gateway")


def _resolve_model(*a, **kw):
    """转调 app._resolve_model —— 见模块 docstring: 它留在 app.py。

    函数体内 import: app.py 会 import 本模块, 顶层回指就成环。
    """
    from .app import _resolve_model as _f
    return _f(*a, **kw)


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



def register_misc_routes(app) -> None:
    """把这一组路由挂到 app 上 (形式见模块 docstring)。"""

    @app.get("/health")
    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "catfish-gateway"}

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
            must_change_password = real_meta.get("must_change_password", False)
        else:
            # PG 没配 / 没找到 → fallback service token 元数据 (graceful)
            department = user.department
            role = user.role
            managed_departments = user.managed_departments or []
            # 老 token / dev token 没有这个 claim, 默认不强制改密。
            must_change_password = False

        return {
            "email": effective_email,
            "department": department,
            "role": role,
            "managed_departments": managed_departments,
            "auth_method": user.auth_method,
            "must_change_password": must_change_password,
        }

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

    @app.get("/v1/roles")
    async def list_roles() -> dict[str, Any]:
        """P3.5.29 (6/17 鸿波) — model role 抽象 机器可读 mapping.

        返 roles.yaml resolve 后全 payload: roles dict + fallback_chain
        (flat model names, 已递归 resolve) + rbac_default_allowed (RBAC role
        → model list).

        anonymous endpoint (不要求 auth) — Companion / hermes 启动时拉预 auth.
        内容不敏感 (业务意图 → model name, 没 token / 没员工数据).

        failure mode:
            - roles.yaml 没 load (startup 失败 / 文件不存在) → 503
            - load 成功 → 200 + 完整 payload
        """
        from . import roles as roles_module
        try:
            return roles_module.to_public_dict()
        except roles_module.RolesNotLoadedError as e:
            raise HTTPException(
                status_code=503,
                detail=f"roles.yaml 没加载 — gateway startup 失败 / 文件不存在: {e}",
            )

    @app.get("/v1/models")
    async def list_models(user: User = Depends(get_current_user)) -> dict[str, Any]:
        """OpenAI-compatible model list. Hides models whose API key is not configured."""
        config: Config = get_config()
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
        """OpenAI-compatible single-model metadata endpoint.

        `catfish-auto` 在这里跟在 POST /v1/chat/completions 一样被解析成真 model ——
        见 `_resolve_auto_sentinel` 的长注释 (hermes 靠这个端点拿 context_length)。

        返回体里的 `id` 用**解析后**的真名, 不是请求里的 sentinel: 报 sentinel 等于
        对客户端撒谎, 而 hermes 的 context 缓存本来就按请求名 (`model@base_url`)
        做 key, 不看返回体的 id, 所以说实话没有代价。
        """
        config: Config = get_config()
        m = config.get_model(_resolve_auto_sentinel(model_id))
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
        config: Config = get_config()
        upstream_status = getattr(app.state, "upstream_status", None)
        return build_catalog(config, user, upstream_status)

    @app.post("/v1/embeddings")
    async def embeddings(
        request: Request,
        user: User = Depends(get_current_user),
    ):
        body = await request.json()
        model_name = body.get("model")

        # 8/14: 省略 model = "用中央配的那个向量模型"。
        #
        # 向量模型跟对话模型不一样, 它是**管道类**, 员工不选也选不了 —— catalog.py:48
        # 就把 mode=embedding 的模型从 /v1/catalog 里摘掉了。所以它的真源只能在中央:
        # roles.yaml 的 `embedding` 角色 (= 控制台 /admin/models 里那条"向量"类型的模型)。
        #
        # 为什么要开这个口子: 员工端 (Companion services/embedding_config.rs) 原来自己
        # 存了一份模型名 "catfish-private-embed"。sysadmin 在控制台换掉向量模型之后,
        # 员工端还在按老名字请求 → 404 → Companion 静默退回本机 ONNX。不报错, 只是悄悄
        # 换了个模型换了个维度, 现场根本看不出来。
        #
        # 让 model 可省, 员工端就一份副本都不用存了。
        #
        # 顺带: roles.yaml 里 `embedding: catfish-private-embed` 和 roles.py 的
        # Role.EMBEDDING 从 P3.5.29 起就定义着, 但**生产代码零消费方** (只有
        # tests/test_roles.py 断言它能解析)。这里是第一个真用它的地方。
        #
        # 向后兼容: 传了 model 就还是用传的, 老客户端不受影响。
        if not model_name:
            from . import roles as roles_module

            model_name = roles_module.resolve_or_none(roles_module.Role.EMBEDDING)
            if model_name:
                logger.info(
                    "embeddings: 请求没带 model, 按 roles.yaml embedding 角色解析 → %s",
                    model_name,
                )

        if not model_name:
            raise HTTPException(
                status_code=400,
                detail=(
                    "model parameter required — 或者在 roles.yaml 里配 "
                    "`embedding: <模型名>` 让网关自己解析"
                ),
            )

        config: Config = get_config()
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
