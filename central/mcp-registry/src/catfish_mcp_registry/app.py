"""catfish-mcp-registry FastAPI 服务 (BL-D3 Phase 1, 5/9 ship).

Phase 1 endpoints:
  GET  /health               健康 + 加载几个 manifest
  GET  /v1/mcp/registry      列连接器 (按部门过滤)
  GET  /v1/mcp/manifest/{id} 单连接器详情 (含 OAuth / mcp_command 内部字段)

Phase 2+ 加:
  POST /v1/mcp/subscribe / DELETE / GET subscribed
  POST /v1/mcp/oauth/start / callback

鉴权: Phase 1 直接信任 X-Catfish-User-Sub / X-Catfish-User-Dept header
(走 catfish-gateway 转发, 网关已 verify JWT). Phase 2+ 加 OIDC.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .loader import ManifestRegistry
from .models import (
    ConnectorListItem,
    HealthResponse,
    ManifestResponse,
    RegistryResponse,
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
    """startup: 扫 manifests/*.yaml. shutdown: 啥也不用关 (内存 only)."""
    registry = ManifestRegistry(_default_manifests_dir())
    count = registry.load_all()
    app.state.registry = registry
    logger.info(
        "catfish-mcp-registry v%s startup: %d manifests loaded from %s",
        __version__, count, registry.manifests_dir,
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


def _manifest_to_list_item(manifest) -> ConnectorListItem:
    """McpManifest → ConnectorListItem (列表展示, 不含 OAuth / mcp_command)."""
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
        subscribed=False,  # Phase 2+ join DB
        subscriber_count=0,
    )


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
    status_filter: str | None = None,
) -> RegistryResponse:
    """列可用 MCP 连接器 (按部门过滤).

    Header: X-Catfish-User-Dept = 当前员工部门 (gateway 从 JWT inject).
    没传 dept 时只返 allowed_dept 空 (= 全员可见) 的连接器, 防漏管控.

    Query: ?status_filter=active|preview|deprecated 选择性过滤状态.
    """
    registry = _registry(request)
    matched = registry.list_for_dept(x_catfish_user_dept)

    if status_filter:
        matched = [m for m in matched if m.status == status_filter]

    items = [_manifest_to_list_item(m) for m in matched]
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


def main() -> None:
    """python -m catfish_mcp_registry.app — dev 启动入口."""
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8997"))
    uvicorn.run(
        "catfish_mcp_registry.app:app",
        host=host,
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    main()
