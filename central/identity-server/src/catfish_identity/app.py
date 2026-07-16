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


from .clients import ClientRegistry
from .code_store import CodeStore
from .jwt_signer import JwtSigner
from .refresh_tokens import RefreshTokenStore
from .routes import make_router
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


def create_app(
    *,
    signer: JwtSigner | None = None,
    registry: UserRegistry | None = None,
    code_store: CodeStore | None = None,
    client_registry: ClientRegistry | None = None,
    refresh_token_store: RefreshTokenStore | None = None,
) -> FastAPI:
    """组装 fastapi app + 所有依赖.

    依赖通过闭包注入到 router (不用 fastapi global state, 测试可以构造小 app).

    6/9 BL-F11.P2 (鸿波): 所有依赖支持注入. 不传 → 用默认构造 (生产模式, RSA key
    从 ~/.catfish 加载, yaml 从默认路径加载). 传 → 用测试 fixture.

    multi-worker 部署用 module 底部的 lazy `app` 单例 (uvicorn import string
    `catfish_identity.app:app` 触发 PEP 562 __getattr__ 一次性构造).
    """
    issuer = _issuer_url()
    signer = signer if signer is not None else JwtSigner()
    registry = registry if registry is not None else UserRegistry()
    # 6/9 BL-F11.P2: code_store sqlite 持久化, 跨 worker 共享 authorization_code state.
    # 老版 in-memory dict 在 multi-worker 下 75% 登不进 (worker A 颁的 code worker B
    # 找不到). sqlite 短连接 + busy_timeout 5s 串行化写, 4 worker 不撞.
    code_store = code_store if code_store is not None else CodeStore()
    # BL-RBAC P0 + B sprint Day 1 (5/14): OAuth client_credentials grant.
    # ClientRegistry 加载 clients.yaml (没文件 → 空注册表, /token client_credentials
    # 返 503 cleanly degrade). 见 docs/RBAC-DESIGN.md §10/§12.
    client_registry = client_registry if client_registry is not None else ClientRegistry()
    # BL-IDENTITY-REFRESH-TOKEN (5/15 凌晨): refresh_token grant 让 access_token 过期
    # 后无感续 (catfish login CLI / hermes-cli 用). sqlite 单文件存. 见
    # refresh_tokens.py 模块顶部 doc.
    refresh_token_store = (
        refresh_token_store if refresh_token_store is not None else RefreshTokenStore()
    )

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
    #
    # 7/17 BL-TAURI-CORS: Tauri v2 desktop app 的 WebView origin 是:
    #   - macOS/iOS/Linux: `tauri://localhost`
    #   - Windows:         `https://tauri.localhost`
    # Companion 里 useServerReachable hook 用 fetch API 走 browser CORS check.
    # 老 regex `https?://(localhost|127\.0\.0\.1)` 不匹配 tauri:// scheme, 员工首启
    # Companion 检测 identity 时 CORS 拦截 → 显示"认证服务不通 (Load failed)".
    # allow_origin_regex 扩展支持 tauri scheme + `tauri.localhost` host.
    cors_origins_env = os.environ.get("CATFISH_IDENTITY_CORS_ORIGINS", "").strip()
    if cors_origins_env:
        cors_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]
    else:
        # dev 默认: vite 5173 + nginx 80/443 + 任何 localhost + Tauri desktop
        cors_origins = [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost",
            "http://127.0.0.1",
            "tauri://localhost",            # Tauri v2 mac/Linux WebView origin
            "https://tauri.localhost",      # Tauri v2 Windows WebView origin
        ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        # 三种 scheme 都接: http(s) + tauri. Host 允许 localhost / 127.0.0.1 / tauri.localhost
        allow_origin_regex=r"(https?|tauri)://(localhost|127\.0\.0\.1|tauri\.localhost)(:[0-9]+)?",
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
            client_registry=client_registry,
            refresh_token_store=refresh_token_store,
        )
    )
    if len(client_registry) > 0:
        logger.info(
            "OAuth client_credentials grant: 加载 %d 个 client (path=%s)",
            len(client_registry), client_registry.clients_path,
        )
    else:
        logger.info(
            "OAuth client_credentials grant: 未配 client (path=%s 不存在或空), "
            "/token 此 grant 返 503 — dev 期间 hermes-cli 仍走 dev_token fallback.",
            client_registry.clients_path,
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


# 6/9 BL-F11.P2 (鸿波): module-level lazy app singleton — 让 uvicorn workers>1 跑稳.
#
# 老版用 `uvicorn.run("catfish_identity.app:create_app", factory=True, workers=N)`,
# factory + workers>1 在 macOS docker 跑 multiprocess 撞 port bind 竞态. 新版改用
# `uvicorn.run("catfish_identity.app:app", workers=N)` — uvicorn import 这模块时
# PEP 562 __getattr__ 触发一次性构造, 主进程 fork N worker 后各 worker 继承同
# RSA signer / yaml registry (read-only copy-on-write OK); _CodeStore /
# RefreshTokenStore 已 sqlite 化, 跨 worker 共享.
#
# Lazy 不是 module-top `app = create_app()`: 那样测试 `from catfish_identity.app
# import create_app` 也触发 heavy init (yaml load, sqlite open, RSA gen), 拖慢
# 测试 + 污染 default ~/.catfish 路径.
_module_app: FastAPI | None = None


def __getattr__(name: str):
    """PEP 562 module-level __getattr__: lazy build module singleton app.

    只有访问 `catfish_identity.app.app` (= uvicorn import string) 时才 init.
    测试 `from catfish_identity.app import create_app` 不触发.
    """
    global _module_app
    if name == "app":
        if _module_app is None:
            _module_app = create_app()
        return _module_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main() -> None:
    """命令行入口: python -m catfish_identity"""
    import uvicorn  # noqa: PLC0415

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    host = os.environ.get("CATFISH_IDENTITY_HOST", DEFAULT_HOST)
    port = int(os.environ.get("CATFISH_IDENTITY_PORT", str(DEFAULT_PORT)))
    # 6/9 BL-F11.P2 ship: identity 真支持 multi-worker.
    #
    # 历史: BL-F11 6/9 上午发现 factory=True + workers>1 不兼容 (port bind 竞态),
    # 短期 hard-code workers=1. 上午 bench 跑出来 P99 189s (1000 user / 单 worker
    # bcrypt 12 round 排队). 下午鸿波说"直接上 B, 代码又不多" — 把 _CodeStore 从
    # in-memory 移到 sqlite (跨 worker 共享 authorization_code state), create_app
    # 拆 dependency 注入 + module-level lazy app 单例, uvicorn 改 import string
    # 路径不走 factory, multi-worker 起来稳.
    #
    # 默认 2 worker (跟 docker-compose.yml / .env.production.example 一致). 重负
    # 载机房 IDENTITY_WORKERS=4. 单 worker 极限 ~5-7 verify/s (实测), 4 worker 应
    # 该接近 20 verify/s — bench 重跑验证.
    workers = max(1, int(os.environ.get("UVICORN_WORKERS", "2")))

    uvicorn.run(
        "catfish_identity.app:app",  # ← 不用 factory=True, 走 module-level lazy 单例
        host=host,
        port=port,
        workers=workers,
        log_level="info",
    )


if __name__ == "__main__":
    main()
