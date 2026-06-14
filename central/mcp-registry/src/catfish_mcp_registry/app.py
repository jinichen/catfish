"""catfish-mcp-registry FastAPI 服务 (BL-D3 Phase 1+2, 5/9 ship).

Phase 1 endpoints:
  GET  /health               健康 + 加载几个 manifest
  GET  /v1/mcp/registry      列连接器 (按部门过滤, 含订阅状态/订阅人数)
  GET  /v1/mcp/manifest/{id} 单连接器详情 (含 OAuth / mcp_command 内部字段)

Phase 2 (5/9 加, P3.4.1 6/13 改):
  POST   /v1/mcp/subscribe              员工订阅 (auth_type=none → 直 active;
                                                 oauth2 → pending_oauth)
  DELETE /v1/mcp/subscribe/{sub_id}     取消订阅 (status=revoked, Companion 自删本机 token)
  GET    /v1/mcp/subscribed             我订阅的列表
  POST   /v1/mcp/oauth/start            返 authorize_url (Phase 2 mock 模式 dev)
  POST   /v1/mcp/oauth/callback         OAuth code → exchange → 直接返 access_token
                                        给 Companion 落本机 + mark active
                                        (P3.4.1: 中央不再持 token, 不再 POST secret-broker)

鉴权: 信任 X-Catfish-User-Sub / X-Catfish-User-Dept header (走 catfish-gateway
转发, 网关已 verify JWT 注入). 直连本服务 dev 时也可手填.
"""
from __future__ import annotations

import logging
import os
import secrets
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware


# BL-D3 fix5 (5/10 鸿波诊断): 之前依赖在 pyproject 但 app.py 没 load_dotenv,
# 启动 `python -m catfish_mcp_registry.app` 不读 .env, CATFISH_DB_URL 落空,
# db.py 走 sqlite fallback (~/.catfish/mcp_registry.db). 跟 gateway / skills-hub
# 同款补上.
def _load_dotenv() -> Path | None:
    try:
        from dotenv import load_dotenv  # noqa: PLC0415  懒 import
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


from . import __version__
from .db import make_db
from .loader import ManifestRegistry
from .models import (
    ConnectorListItem,
    HealthResponse,
    ManifestResponse,
    OAuthCallbackRequest,
    OAuthCallbackResponse,
    OAuthStartRequest,
    OAuthStartResponse,
    RegistryResponse,
    SubscribeRequest,
    SubscribeResponse,
    SubscriptionListResponse,
    SubscriptionView,
)

logger = logging.getLogger("catfish.mcp_registry")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def _default_manifests_dir() -> Path:
    """默认 manifests 目录: <repo>/central/mcp-registry/manifests/.

    可用 env CATFISH_MCP_MANIFESTS_DIR 覆盖 (生产部署用).
    """
    env_dir = os.environ.get("CATFISH_MCP_MANIFESTS_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    # 当前文件位于 src/catfish_mcp_registry/app.py, 回到项目根的 manifests/
    return (Path(__file__).resolve().parent.parent.parent / "manifests").resolve()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """startup: 扫 manifests/*.yaml + 起 sqlite db.

    P3.4.1 (6/13 hb): 删 secret-broker httpx 客户端. 中央不再持员工 token.
    """
    registry = ManifestRegistry(_default_manifests_dir())
    count = registry.load_all()
    app.state.registry = registry

    app.state.db = make_db()

    logger.info(
        "catfish-mcp-registry v%s startup: %d manifests, db backend=%s",
        __version__,
        count,
        app.state.db.backend,
    )
    yield
    logger.info("catfish-mcp-registry shutdown")


app = FastAPI(
    title="Catfish MCP Connector Registry",
    description="企业 MCP 连接器仓库 — 员工订阅 / 管理员审批 / OAuth (BL-D3)",
    version=__version__,
    lifespan=lifespan,
)

# Companion 走 gateway 转发, 但 dev 时也允许直连
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── helpers ──────────────────────────────────────────────────────────


def _registry(request: Request) -> ManifestRegistry:
    """从 app.state 拿 registry. 单测里也方便 mock."""
    return request.app.state.registry


def _manifest_to_list_item(
    manifest,
    *,
    subscribed: bool = False,
    subscriber_count: int = 0,
) -> ConnectorListItem:
    """McpManifest → ConnectorListItem (列表展示, 不含 OAuth / mcp_command).

    Phase 2 (5/9): join 订阅状态 + 订阅人数 (db 查).
    """
    return ConnectorListItem(
        id=manifest.id,
        name=manifest.name,
        version=manifest.version,
        description=manifest.description,
        provider=manifest.provider,
        status=manifest.status,
        allowed_dept=manifest.allowed_dept,
        auth_type=manifest.auth_type,
        tools=manifest.tools,
        ui=manifest.ui,
        subscribed=subscribed,
        subscriber_count=subscriber_count,
    )


def _sub_row_to_view(row: dict, manifest=None) -> SubscriptionView:
    """db row → API view, 可选 join manifest 元信息."""
    return SubscriptionView(
        id=row["id"],
        user_sub=row["user_sub"],
        connector_id=row["connector_id"],
        status=row["status"],
        oauth_token_ref=row.get("oauth_token_ref"),
        subscribed_at=row["subscribed_at"],
        connector_name=manifest.name if manifest else None,
        connector_version=manifest.version if manifest else None,
    )


def _decode_header(value: str | None) -> str | None:
    """BL-D3 fix2 (5/9): gateway proxy 注入 header 时 percent-encode 中文 dept.
    这里 unquote 还原. ASCII 不变."""
    if value is None:
        return None
    try:
        return urllib.parse.unquote(value)
    except Exception:
        return value


def _require_user_sub(x_catfish_user_sub: str | None) -> str:
    decoded = _decode_header(x_catfish_user_sub)
    if not decoded:
        raise HTTPException(
            status_code=401,
            detail="missing X-Catfish-User-Sub (gateway 应注入或 dev 直连请手填)",
        )
    return decoded


# ── endpoints ────────────────────────────────────────────────────────


@app.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """健康 — 给 Companion / catfish-gateway 探活用."""
    registry = _registry(request)
    return HealthResponse(
        status="ok",
        version=__version__,
        manifests_loaded=registry.count,
        extras={"manifests_dir": str(registry.manifests_dir)},
    )


@app.get("/v1/mcp/registry", response_model=RegistryResponse)
async def list_connectors(
    request: Request,
    x_catfish_user_dept: str | None = Header(default=None),
    x_catfish_user_sub: str | None = Header(default=None),
    status_filter: str | None = None,
) -> RegistryResponse:
    """列可用 MCP 连接器 (按部门过滤, 含订阅状态).

    Header: X-Catfish-User-Dept = 当前员工部门 (gateway 从 JWT inject).
    没传 dept 时只返 allowed_dept 空 (= 全员可见) 的连接器, 防漏管控.

    Phase 2 (5/9): X-Catfish-User-Sub 也透传, 用来 join 订阅状态. 没传时
    subscribed=False / subscriber_count 仍真返 (匿名也能看 demo 数字).

    Query: ?status_filter=active|preview|deprecated 选择性过滤状态.
    """
    # BL-D3 fix2 (5/9): gateway proxy percent-encode 中文 dept (HTTP header 必 ASCII)
    x_catfish_user_dept = _decode_header(x_catfish_user_dept)
    x_catfish_user_sub = _decode_header(x_catfish_user_sub)

    registry = _registry(request)
    db = request.app.state.db
    matched = registry.list_for_dept(x_catfish_user_dept)

    if status_filter:
        matched = [m for m in matched if m.status == status_filter]

    # Phase 2: 拿当前员工已订阅的 connector_id 集
    user_sub_ids: set[str] = set()
    if x_catfish_user_sub:
        for sub in db.list_user_subscriptions(x_catfish_user_sub):
            if sub["status"] == "active":
                user_sub_ids.add(sub["connector_id"])

    items = [
        _manifest_to_list_item(
            m,
            subscribed=(m.id in user_sub_ids),
            subscriber_count=db.count_subscribers(m.id),
        )
        for m in matched
    ]
    return RegistryResponse(
        connectors=items,
        total=len(items),
        user_dept=x_catfish_user_dept or "",
        filtered_by_dept=bool(x_catfish_user_dept),
    )


@app.get("/v1/mcp/manifest/{connector_id}", response_model=ManifestResponse)
async def get_manifest(
    connector_id: str,
    request: Request,
    x_catfish_user_dept: str | None = Header(default=None),
) -> ManifestResponse:
    """单连接器完整 manifest (含 OAuth / mcp_command, 给 Companion 详情页 / 订阅时用).

    部门权限校验: 不在 allowed_dept (非空时) → 403.
    """
    # BL-D3 fix2 (5/9): 中文 dept percent-encode 还原
    x_catfish_user_dept = _decode_header(x_catfish_user_dept)

    registry = _registry(request)
    manifest = registry.get(connector_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"connector not found: {connector_id}")

    # 部门权限: allowed_dept 空 = 全员可见; 非空且员工不在列 = 403
    if manifest.allowed_dept and (
        not x_catfish_user_dept or x_catfish_user_dept not in manifest.allowed_dept
    ):
        raise HTTPException(
            status_code=403,
            detail=f"connector {connector_id} 仅 {manifest.allowed_dept} 部门可订阅",
        )

    return ManifestResponse(manifest=manifest)


# ── Phase 2 (5/9): 订阅 / OAuth endpoints ────────────────────────────


@app.post("/v1/mcp/subscribe", response_model=SubscribeResponse)
async def subscribe(
    body: SubscribeRequest,
    request: Request,
    x_catfish_user_sub: str | None = Header(default=None),
    x_catfish_user_dept: str | None = Header(default=None),
) -> SubscribeResponse:
    """订阅 connector.

    auth_type=none / path_allowlist → 直接 status='active' (不需 OAuth).
    auth_type=oauth2 / api_key      → status='pending_oauth', next_step='oauth'.

    部门权限校验: connector.allowed_dept 非空且员工 dept 不在列 → 403.
    """
    user_sub = _require_user_sub(x_catfish_user_sub)
    # BL-D3 fix2 (5/9): 中文 dept percent-encode 还原
    x_catfish_user_dept = _decode_header(x_catfish_user_dept)

    registry = _registry(request)
    db = request.app.state.db

    manifest = registry.get(body.connector_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"connector {body.connector_id} 不存在")

    # 部门权限
    if manifest.allowed_dept and (
        not x_catfish_user_dept or x_catfish_user_dept not in manifest.allowed_dept
    ):
        raise HTTPException(
            status_code=403,
            detail=f"connector {manifest.id} 仅 {manifest.allowed_dept} 部门可订阅",
        )

    auth_required = manifest.auth_type in ("oauth2", "api_key")
    sub_row = db.create_subscription(user_sub, manifest.id, auth_required)
    db.write_audit(
        user_sub=user_sub,
        connector_id=manifest.id,
        action="subscribe",
        meta={"auth_type": manifest.auth_type, "auth_required": auth_required},
    )

    view = _sub_row_to_view(sub_row, manifest)
    if sub_row["status"] == "active":
        return SubscribeResponse(subscription=view, next_step="ready", oauth_start_url=None)
    return SubscribeResponse(
        subscription=view,
        next_step="oauth",
        oauth_start_url=f"/v1/mcp/oauth/start (POST subscription_id={sub_row['id']})",
    )


@app.delete("/v1/mcp/subscribe/{subscription_id}", response_model=SubscriptionView)
async def unsubscribe(
    subscription_id: str,
    request: Request,
    x_catfish_user_sub: str | None = Header(default=None),
) -> SubscriptionView:
    """取消订阅. 标 revoked. P3.4.1 (6/13 hb): 中央不再持 token, 不删 secret-broker.
    Companion 端拿 oauth_token_ref 自己删 ~/.catfish/mcp/oauth-tokens/<ref>.
    """
    user_sub = _require_user_sub(x_catfish_user_sub)
    db = request.app.state.db

    sub = db.get_subscription_by_id(subscription_id)
    if sub is None:
        raise HTTPException(status_code=404, detail=f"subscription {subscription_id} 不存在")
    if sub["user_sub"] != user_sub:
        # 不是你的 subscription, 防员工取消别人的
        raise HTTPException(status_code=403, detail="无权操作他人订阅")

    db.revoke(subscription_id)

    # P3.4.1 (6/13 hb): 中央不再持 token, 没什么需要清的.
    # Companion 端拿 oauth_token_ref 字段自己删本机 ~/.catfish/mcp/oauth-tokens/.
    # (sub_after.oauth_token_ref 仍在 response 里, Companion 拉 my_subscriptions
    # 时拿不到老 ref 就触发本机删除; 或者前端 unsubscribe 调用后自己删本机)

    db.write_audit(
        user_sub=user_sub,
        connector_id=sub["connector_id"],
        action="unsubscribe",
    )
    sub_after = db.get_subscription_by_id(subscription_id)
    manifest = _registry(request).get(sub["connector_id"])
    return _sub_row_to_view(sub_after, manifest)  # type: ignore[arg-type]


@app.get("/v1/mcp/subscribed", response_model=SubscriptionListResponse)
async def my_subscriptions(
    request: Request,
    x_catfish_user_sub: str | None = Header(default=None),
    status_filter: str | None = None,
) -> SubscriptionListResponse:
    """我订阅的列表 (默认所有 status, ?status_filter=active 过滤)."""
    user_sub = _require_user_sub(x_catfish_user_sub)
    db = request.app.state.db
    registry = _registry(request)
    rows = db.list_user_subscriptions(user_sub, status=status_filter)
    views = [
        _sub_row_to_view(r, registry.get(r["connector_id"]))
        for r in rows
    ]
    return SubscriptionListResponse(subscriptions=views, total=len(views))


@app.post("/v1/mcp/oauth/start", response_model=OAuthStartResponse)
async def oauth_start(
    body: OAuthStartRequest,
    request: Request,
    x_catfish_user_sub: str | None = Header(default=None),
) -> OAuthStartResponse:
    """启 OAuth flow — 返 authorize_url 给 Companion 跳转浏览器.

    Phase 2 dev mock 模式: 返一个本服务的 mock callback URL, Companion 跳转
    后调 /v1/mcp/oauth/callback 立即模拟成功. 真接 Jira/GitLab 时返 provider
    的真 authorize_url.
    """
    user_sub = _require_user_sub(x_catfish_user_sub)
    db = request.app.state.db
    registry = _registry(request)

    sub = db.get_subscription_by_id(body.subscription_id)
    if sub is None:
        raise HTTPException(status_code=404, detail=f"subscription {body.subscription_id} 不存在")
    if sub["user_sub"] != user_sub:
        raise HTTPException(status_code=403, detail="无权操作他人订阅")
    if sub["status"] != "pending_oauth":
        raise HTTPException(
            status_code=400,
            detail=f"subscription 状态={sub['status']}, 不需 OAuth 或已完成",
        )

    manifest = registry.get(sub["connector_id"])
    if manifest is None or manifest.oauth is None:
        raise HTTPException(status_code=400, detail="connector 无 OAuth 配置")

    state = sub.get("oauth_state") or secrets.token_urlsafe(24)
    # Phase 2 dev mock: 返本服务 mock callback URL, Companion 一跳就完成.
    # 真接时返 manifest.oauth.authorize_url 拼 client_id/redirect_uri/state.
    use_mock = os.environ.get("CATFISH_MCP_OAUTH_MODE", "mock") == "mock"
    if use_mock:
        authorize_url = (
            f"http://127.0.0.1:8996/v1/mcp/oauth/mock-callback?state={state}"
        )
    else:
        # 真 OAuth (Phase 2.1 后续)
        authorize_url = manifest.oauth.authorize_url
    return OAuthStartResponse(authorize_url=authorize_url, state=state)


@app.post("/v1/mcp/oauth/callback", response_model=OAuthCallbackResponse)
async def oauth_callback(
    body: OAuthCallbackRequest,
    request: Request,
    x_catfish_user_sub: str | None = Header(default=None),
) -> OAuthCallbackResponse:
    """OAuth callback — code 换 token + 直接返 access_token 给 Companion + mark active.

    P3.4.1 (6/13 hb): 中央不再写 secret-broker. token 在 response 里返,
    Companion 落 ~/.catfish/mcp/oauth-tokens/<ref> 本机文件 0600.

    Phase 2 dev mock 模式: body.mock_token 直接当 access_token 存. 真接时用
    code 调 manifest.oauth.token_url exchange.
    """
    user_sub = _require_user_sub(x_catfish_user_sub)
    db = request.app.state.db

    sub = db.find_by_oauth_state(body.state)
    if sub is None:
        raise HTTPException(status_code=400, detail="invalid or expired state")
    if sub["user_sub"] != user_sub:
        raise HTTPException(status_code=403, detail="state 不属于当前员工")
    if sub["status"] != "pending_oauth":
        raise HTTPException(
            status_code=400, detail=f"subscription 状态={sub['status']}, 重复回调"
        )

    # mock 模式: body.mock_token 直接当 access_token; 真接: code → exchange
    use_mock = os.environ.get("CATFISH_MCP_OAUTH_MODE", "mock") == "mock"
    if use_mock:
        access_token = body.mock_token or f"mock-token-{secrets.token_hex(8)}"
    else:
        # Phase 2.1 (5/9): 真 OAuth2 token exchange. 调 manifest.oauth.token_url
        # 用 authorization_code grant 换 access_token. client_id/secret 从 env
        # 读 (跟 manifest.oauth.required_env 对齐).
        manifest = _registry(request).get(sub["connector_id"])
        if manifest is None or manifest.oauth is None:
            raise HTTPException(
                status_code=500,
                detail="connector OAuth 配置丢了 (subscribe 时还在, callback 时没了?)",
            )
        client_id_env = next(
            (k for k in manifest.oauth.required_env if "CLIENT_ID" in k.upper()), None,
        )
        client_secret_env = next(
            (k for k in manifest.oauth.required_env if "CLIENT_SECRET" in k.upper()), None,
        )
        client_id = os.environ.get(client_id_env or "", "")
        client_secret = os.environ.get(client_secret_env or "", "")
        if not client_id or not client_secret:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"真 OAuth 模式需 env {client_id_env} + {client_secret_env}, "
                    f"未配置. 改回 mock 或补 env."
                ),
            )
        redirect_uri = os.environ.get(
            "CATFISH_MCP_OAUTH_REDIRECT_URI",
            "http://127.0.0.1:8996/v1/mcp/oauth/mock-callback",
        )
        try:
            async with httpx.AsyncClient(timeout=15) as oauth_client:
                token_resp = await oauth_client.post(
                    manifest.oauth.token_url,
                    data={
                        "grant_type": "authorization_code",
                        "code": body.code,
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "redirect_uri": redirect_uri,
                    },
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
        except httpx.RequestError as e:
            db.write_audit(
                user_sub=user_sub,
                connector_id=sub["connector_id"],
                action="oauth_failed",
                meta={"reason": "token_endpoint_unreachable", "error": str(e)},
            )
            raise HTTPException(
                status_code=502,
                detail=f"OAuth token endpoint 不可达 ({manifest.oauth.token_url}): {e}",
            ) from e
        if token_resp.status_code != 200:
            db.write_audit(
                user_sub=user_sub,
                connector_id=sub["connector_id"],
                action="oauth_failed",
                meta={
                    "reason": "token_exchange_failed",
                    "status": token_resp.status_code,
                    "body_preview": token_resp.text[:300],
                },
            )
            raise HTTPException(
                status_code=400,
                detail=f"token exchange 失败 {token_resp.status_code}: "
                f"{token_resp.text[:200]}",
            )
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(
                status_code=502,
                detail=f"token endpoint 返回无 access_token 字段: {token_data}",
            )

    # P3.4.1 (6/13 hb): 砍中央 secret-broker 写入 — token 不再上中央.
    #
    # 老逻辑: token POST 到 secret-broker (中央 :8995 集中存储). 跟 manifesto
    # "信息分级保护 / 数据本地化处理" 红线冲突 — 中央拿到员工 token 等于能
    # 以员工身份操作 SaaS, 政企信安场景下不合规.
    #
    # 新逻辑: token 直接返 Companion, Companion 落本机
    # ~/.catfish/mcp/oauth-tokens/<token_ref_local> 文件 0600. 中央仅留
    # token_ref_local 作为标识符 (不含 value), 用于 unsubscribe 时通知
    # Companion 删本机.
    #
    # oauth_token_ref 字段含义变更: 老语义 = secret-broker ref;
    # 新语义 = Companion 本机文件名 (相对 ~/.catfish/mcp/oauth-tokens/).
    token_ref_local = f"{sub['connector_id']}-{user_sub.replace('@', '-at-')}.token"
    sub_after = db.mark_active(sub["id"], oauth_token_ref=token_ref_local)
    db.write_audit(
        user_sub=user_sub,
        connector_id=sub["connector_id"],
        action="oauth_complete",
        meta={"token_ref_local": token_ref_local},  # 不记 token value
    )

    manifest = _registry(request).get(sub["connector_id"])
    return OAuthCallbackResponse(
        subscription=_sub_row_to_view(sub_after, manifest),  # type: ignore[arg-type]
        access_token=access_token,
        token_ref_local=token_ref_local,
    )


def main() -> None:
    """python -m catfish_mcp_registry.app — dev 启动入口."""
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    # 端口约定 (5/9, P3.4.1 6/13 砍 8995):
    #   8998 catfish-identity     (OIDC + a2a registry)
    #   8999 catfish-gateway      (LLM 主网关)
    #   8997 catfish-skills-hub   (历史占)
    #   8996 catfish-mcp-registry (本服务, 5/9 加, 避 skills-hub)
    #   ~~8995 catfish-secret-broker~~ (P3.4.1 砍, OAuth token 改员工本机存)
    port = int(os.environ.get("PORT", "8996"))
    uvicorn.run(
        "catfish_mcp_registry.app:app",
        host=host,
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    main()
