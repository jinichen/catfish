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

from fastapi import FastAPI

from .jwt_signer import JwtSigner
from .routes import _CodeStore, make_router
from .users import UserRegistry

logger = logging.getLogger("catfish.identity")

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
    app = FastAPI(
        title="Catfish Identity",
        description="自建 OIDC server, 给 catfish-gateway 提供 SSO.",
        version="0.1.0",
        # 生产部署关 docs (避免暴露端点列表给攻击者)
        docs_url="/__internal/docs" if os.environ.get("CATFISH_ENV") == "dev" else None,
        redoc_url=None,
    )

    issuer = _issuer_url()
    signer = JwtSigner()
    registry = UserRegistry()
    code_store = _CodeStore()

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
