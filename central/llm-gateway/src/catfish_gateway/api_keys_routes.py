"""员工管理自己的长期钥匙: /api/me/api-keys (11/19)。

透传到身份服务 /internal/me/api-keys (那边按网关注入的 X-Catfish-User-Sub 认人, 见
identity routes_api_keys.py)。Companion「设置」里那张卡调这里。

拿长期钥匙登进来的请求**不能**再管钥匙 (403): 不然一把泄露的钥匙能给自己续出新钥匙, 吊销也追不上。
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response

from .admin_proxy import _proxy
from .auth import User, get_current_user


def _owner(user: User = Depends(get_current_user)) -> User:
    if user.auth_method == "personal_key":
        raise HTTPException(status_code=403, detail="长期钥匙不能用来管理钥匙, 请在鲶鱼桌面端登录后操作")
    if user.role == "service" or ":" in (user.sub or ""):
        raise HTTPException(status_code=403, detail="只有员工本人能管理自己的长期钥匙")
    return user


def register_api_keys_routes(app) -> None:
    @app.get("/api/me/api-keys")
    async def list_api_keys(request: Request, user: User = Depends(_owner)) -> Response:
        return await _proxy(request, "/internal/me/api-keys", user)

    @app.post("/api/me/api-keys")
    async def create_api_key(request: Request, user: User = Depends(_owner)) -> Response:
        return await _proxy(request, "/internal/me/api-keys", user)

    @app.delete("/api/me/api-keys/{key_id}")
    async def revoke_api_key(key_id: str, request: Request, user: User = Depends(_owner)) -> Response:
        return await _proxy(request, f"/internal/me/api-keys/{key_id}", user)
