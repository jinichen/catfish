"""catfish-secret-broker FastAPI 服务 (BL-G6 dev MVP, 5/9 ship Phase 2).

端口: 8995 (端口约定, 跟 catfish-mcp-registry 8996 / catfish-gateway 8999 错开)

endpoints:
    GET  /health                       服务健康 + backend 名 (keyring/memory)
    POST /v1/secret                    写 secret (body: ref + value)
    GET  /v1/secret/{ref}              读 secret 返 value (鉴权后)
    DELETE /v1/secret/{ref}            删
    GET  /v1/secret/{ref}/exists       存在性 (不返值)

鉴权:
    Phase 2 dev: 信任 gateway 注入的 X-Catfish-User-Sub header (gateway 已验 JWT)
    Prod: 加 mTLS / service token

ACL (待 BL-G6 prod):
    每个 ref 应该有 owner (user_sub), 只能 owner / admin 读. Phase 2 简化:
    任何已认证 user 都能读所有 ref (反正只有 mcp-registry 服务在调).
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import __version__
from .storage import make_storage

logger = logging.getLogger("catfish.secret_broker")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


# ── schemas ──────────────────────────────────────────────────────────


class SetSecretRequest(BaseModel):
    ref: str = Field(..., min_length=1, max_length=200)
    value: str = Field(..., min_length=1)


class SecretResponse(BaseModel):
    ref: str
    value: str


class ExistsResponse(BaseModel):
    ref: str
    exists: bool


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    backend: str


# ── lifespan ─────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage = make_storage()
    app.state.storage = storage
    logger.info(
        "catfish-secret-broker v%s startup, backend=%s",
        __version__, storage.backend_name,
    )
    yield
    logger.info("catfish-secret-broker shutdown")


app = FastAPI(
    title="Catfish Secret Broker",
    description="Token / 凭据集中存储 (BL-G6 dev MVP)",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 鉴权 helper ─────────────────────────────────────────────────────


def _require_user(x_catfish_user_sub: str | None) -> str:
    """Phase 2 dev: 信任 gateway 注入的 user_sub. 没传 → 401.

    Prod 应该加 mTLS 或 service token (BL-G6 后续).
    """
    if not x_catfish_user_sub:
        raise HTTPException(
            status_code=401,
            detail="missing X-Catfish-User-Sub header (gateway 应注入)",
        )
    return x_catfish_user_sub


# ── endpoints ───────────────────────────────────────────────────────


@app.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    storage = request.app.state.storage
    return HealthResponse(
        status="ok",
        version=__version__,
        backend=storage.backend_name,
    )


@app.post("/v1/secret", response_model=ExistsResponse)
async def set_secret(
    body: SetSecretRequest,
    request: Request,
    x_catfish_user_sub: str | None = Header(default=None),
) -> ExistsResponse:
    """写 secret. 已存在则覆盖."""
    user = _require_user(x_catfish_user_sub)
    storage = request.app.state.storage
    storage.set(body.ref, body.value)
    logger.info("secret set: ref=%s by user=%s (backend=%s)",
                body.ref, user, storage.backend_name)
    return ExistsResponse(ref=body.ref, exists=True)


@app.get("/v1/secret/{ref}", response_model=SecretResponse)
async def get_secret(
    ref: str,
    request: Request,
    x_catfish_user_sub: str | None = Header(default=None),
) -> SecretResponse:
    user = _require_user(x_catfish_user_sub)
    storage = request.app.state.storage
    value = storage.get(ref)
    if value is None:
        raise HTTPException(status_code=404, detail=f"secret not found: {ref}")
    logger.info("secret get: ref=%s by user=%s", ref, user)
    return SecretResponse(ref=ref, value=value)


@app.get("/v1/secret/{ref}/exists", response_model=ExistsResponse)
async def secret_exists(
    ref: str,
    request: Request,
    x_catfish_user_sub: str | None = Header(default=None),
) -> ExistsResponse:
    """存在性 — 不返值, 给 mcp-registry 检查 OAuth 已授权."""
    _require_user(x_catfish_user_sub)
    storage = request.app.state.storage
    return ExistsResponse(ref=ref, exists=storage.exists(ref))


@app.delete("/v1/secret/{ref}", response_model=ExistsResponse)
async def delete_secret(
    ref: str,
    request: Request,
    x_catfish_user_sub: str | None = Header(default=None),
) -> ExistsResponse:
    user = _require_user(x_catfish_user_sub)
    storage = request.app.state.storage
    deleted = storage.delete(ref)
    logger.info(
        "secret delete: ref=%s by user=%s deleted=%s",
        ref, user, deleted,
    )
    # 删除后存在性 = False (无论原本是否存在)
    return ExistsResponse(ref=ref, exists=False)


def main() -> None:
    """python -m catfish_secret_broker.app — dev 启动入口."""
    import uvicorn  # noqa: PLC0415

    host = os.environ.get("HOST", "127.0.0.1")
    # 端口约定 (5/9):
    #   8998 catfish-identity, 8999 catfish-gateway, 8997 catfish-skills-hub,
    #   8996 catfish-mcp-registry, 8995 catfish-secret-broker (本服务)
    port = int(os.environ.get("PORT", "8995"))
    uvicorn.run(
        "catfish_secret_broker.app:app",
        host=host,
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    main()
