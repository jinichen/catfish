"""只跑手机中转站的精简网关 —— 放 DMZ 用 (docs/MOBILE-CLIENT-DESIGN.md §8.2 可选部署)。

同一个镜像, `CATFISH_ROLE=relay` 启动时 app.run() 改跑这个: 只有 `/relay/ws` 和探活,
不带模型、配额、审计等任何其他接口, 也不碰用户数据。它只需要:
  - 校验身份令牌 (CATFISH_OIDC_ISSUER / CATFISH_OIDC_JWKS_URI 指向内网 identity-server;
    11/08 起 issuer 可以是逗号分隔的一串, 或另设 CATFISH_OIDC_ISSUERS —— 中央 IP + 对外域名
    同时登录时, 手机从外网拿到的是域名那个 iss, 见 auth/__init__.py make_auth_provider)
  - 设备登记表 (CATFISH_DB_URL, 跟中央同一个库; 只有元数据)
  - 每人手机上限 (CATFISH_MOBILE_MAX_DEVICES)

DMZ 的中转站不需要访问中央本体; 中央本体也就不用开放到公网。
"""
from __future__ import annotations

from fastapi import FastAPI

from .relay_router import router as relay_router

app = FastAPI(title="catfish-relay", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(relay_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "role": "relay"}
