"""Catfish Identity fastapi app — 入口.

# 启动

```
cd central/identity-server
PYTHONPATH=src python3 -m catfish_identity
```

或者通过 catfish-companion 的 watchdog 自动管理 (Phase 1C 加).

# 端口

默认 127.0.0.1:8998 (跟 gateway 8999 错开). env CATFISH_IDENTITY_PORT 改.

# Issuer URL

默认 http://127.0.0.1:8998 (没 https, dev 用). 生产部署改 env CATFISH_IDENTITY_ISSUER.
issuer **必须**跟 gateway 配置的 OIDC issuer 完全一致, 否则 JWT 验签 aud 校验失败.

# Demo user (config/users.yaml 默认)

```
chenhongbo@ffcs.cn  /  catfish123
demo@ffcs.cn        /  demo123
```

5 月 demo 时演示用. 生产部署客户改 yaml.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


# BL-D6 fix2 (5/10 鸿波 'catfish_identity 数据库连接参数没有写入 .env 吗?'):
# 跟 mcp-registry (BL-D3 fix5) / skills-hub (BL-D2) 同款隐性 bug — pyproject 写
# python-dotenv 依赖 + .env 5/9 就建了, 但 app.py 没调 _load_dotenv, 启动 ENV
# 不读. 必须在 import .users (它 import .db 起 PG pool) 之前 load.
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


from .jwt_signer import JwtSigner
from .routes import _CodeStore, make_router
from .users import UserRegistry

logger = logging.getLogger("catfish.identity")
if _ENV_FILE_LOADED:
    logger.info("loaded .env from: %s", _ENV_FILE_LOADED)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8998


def _issuer_url() -> str:
    """OIDC issuer URL. 必须跟 gateway 端配置完全一致."""
    if env := os.environ.get("CATFISH_IDENTITY_ISSUER"):
        return env.rstrip("/")
    host = os.environ.get("CATFISH_IDENTITY_HOST", DEFAULT_HOST)
    port = os.environ.get("CATFISH_IDENTITY_PORT", str(DEFAULT_PORT))
    return f"http://{host}:{port}"


def create_app() -> FastAPI:
    """组装 fastapi app + 所有依赖.

    依赖通过闭包注入到 router (不用 fastapi global state, 测试可以构造小 app).
    """
    issuer = _issuer_url()
    signer = JwtSigner()
    registry = UserRegistry()
    code_store = _CodeStore()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 五一 sprint 5/4 (BL-D17): PG 模式下启动时 init schema + 从 PG 加载 users
        from .db import init_schema, close_pool  # noqa: PLC0415
        ok = await init_schema()
        if ok:
            # 首次启动 PG 空 → 从 yaml seed (一次性). 后续 yaml 改不影响 PG.
            seeded = await registry.seed_pg_from_yaml_if_empty()
            if seeded:
                logger.info("PG 首次 seed %d 个用户从 yaml", seeded)
            await registry.reload_from_pg()
            logger.info("PG 模式: schema OK, users 加载 %d", len(registry))
        else:
            logger.info("PG 未配置 (CATFISH_DB_URL), users 用 yaml fallback (%d 个)", len(registry))
        yield
        await close_pool()

    app = FastAPI(
        title="Catfish Identity",
        description="自建 OIDC server, 给 catfish-gateway 提供 SSO.",
        version="0.1.0",
        docs_url="/__internal/docs" if os.environ.get("CATFISH_ENV") == "dev" else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # BL-ARCH1 (5/10): CORS — 给 catfish-web (浏览器 PKCE flow) 调
    # /.well-known/openid-configuration / /jwks 用. dev 默认 localhost:5173 (vite)
    # + 127.0.0.1:5173 + 任意 origin (regex). 生产应改成具体 origin 列表.
    cors_origins_env = os.environ.get("CATFISH_IDENTITY_CORS_ORIGINS", "").strip()
    if cors_origins_env:
        cors_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]
    else:
        # dev 默认: vite 5173 + nginx 80/443 + 任何 localhost
        cors_origins = [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost",
            "http://127.0.0.1",
        ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:[0-9]+)?",
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        max_age=3600,
    )
    logger.info("CORS allowed origins: %s (+ localhost regex)", cors_origins)

    if len(registry) == 0:
        logger.warning(
            "用户注册表是空的! catfish-identity 没意义跑. "
            "检查 config/users.yaml 或 env CATFISH_IDENTITY_USERS_PATH"
        )

    app.include_router(
        make_router(
            issuer=issuer,
            signer=signer,
            registry=registry,
            code_store=code_store,
        )
    )

    # 五一 sprint Day 4 (BL-M4.1): Plan D · Catfish Federation registry
    # 各 catfish 实例 (Alice / Bob / ...) 通过这个 registry 互相发现 + 拿 jwks
    from .registry import build_registry_router  # noqa: PLC0415
    app.include_router(build_registry_router())

    # BL-ARCH1 P1 (5/10): admin 用户管理 endpoints (catfish-web /admin/users)
    from .admin_router import make_admin_router  # noqa: PLC0415
    app.include_router(make_admin_router(registry))
    logger.info("admin_router: /admin/users CRUD 已挂载")

    @app.get("/healthz")
    async def healthz() -> dict:
        """liveness probe."""
        return {
            "status": "ok",
            "issuer": issuer,
            "user_count": len(registry),
            "kid": signer.kid,
        }

    logger.info(
        "catfish-identity 起来了: issuer=%s, users=%d, kid=%s",
        issuer, len(registry), signer.kid,
    )
    return app


def main() -> None:
    """命令行入口: python -m catfish_identity"""
    import uvicorn  # noqa: PLC0415

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    host = os.environ.get("CATFISH_IDENTITY_HOST", DEFAULT_HOST)
    port = int(os.environ.get("CATFISH_IDENTITY_PORT", str(DEFAULT_PORT)))

    uvicorn.run(
        "catfish_identity.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
