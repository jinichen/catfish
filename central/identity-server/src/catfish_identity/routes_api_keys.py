"""个人长期钥匙的接口 (11/19, 见 api_keys.py)。

只走网关反代 (nginx 不暴露这些路径, identity 容器也不对外开端口), 跟 admin_router 一样
信任网关注入的 X-Catfish-User-* 头:
  GET    /internal/me/api-keys          我的钥匙 (不含原文)
  POST   /internal/me/api-keys          生成一把, 原文只在这次返回
  DELETE /internal/me/api-keys/{id}     吊销
  POST   /internal/api-keys/introspect  网关拿钥匙换「这个人现在的 claims」(锁了 / 删了 → active=false)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from .admin_router import CallerContext, require_caller
from .api_keys import ApiKeyStore, TooManyKeys


def make_api_keys_router(registry, store: ApiKeyStore) -> APIRouter:
    router = APIRouter()

    def _employee(caller: CallerContext = Depends(require_caller)) -> CallerContext:
        # service 身份 (client:xxx) 不能给自己发长期钥匙; 拿长期钥匙登进来的也不能再发 (网关会标 key 身份)
        if ":" in caller.sub or caller.role == "service":
            raise HTTPException(status_code=403, detail="只有员工本人能管理自己的长期钥匙")
        return caller

    @router.get("/internal/me/api-keys")
    async def list_keys(caller: CallerContext = Depends(_employee)) -> dict:
        return {"keys": [k.public() for k in store.list(caller.sub)]}

    @router.post("/internal/me/api-keys", status_code=201)
    async def create_key(request: Request, caller: CallerContext = Depends(_employee)) -> dict:
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if registry.find(caller.sub) is None:
            raise HTTPException(status_code=404, detail="身份服务里没有这个员工")
        try:
            rec, key = store.create(caller.sub, str((body or {}).get("name") or ""))
        except TooManyKeys as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return {**rec.public(), "key": key}

    @router.delete("/internal/me/api-keys/{key_id}")
    async def revoke_key(key_id: str, caller: CallerContext = Depends(_employee)) -> dict:
        if not store.revoke(caller.sub, key_id):
            raise HTTPException(status_code=404, detail="没有这把钥匙 (或已经吊销了)")
        return {"revoked": key_id}

    @router.post("/internal/api-keys/introspect")
    async def introspect(request: Request) -> dict:
        try:
            key = str((await request.json()).get("key") or "")
        except ValueError:
            key = ""
        rec = store.lookup(key) if key else None
        if rec is None:
            return {"active": False}
        user = registry.find(rec.sub)
        if user is None or user.locked or user.deleted_at:
            return {"active": False, "reason": "user_inactive"}
        claims = await user.to_oidc_claims_async()
        return {"active": True, "key_id": rec.id, "claims": claims}

    return router
