"""换机直传 (11/21, docs/DEVICE-MIGRATION-DESIGN.md §12) —— 中央只转交迁移包的密文, 边传边删。存储层见 device_transfer_db.py。

两台电脑登录的是同一个人: 身份一律来自 JWT, 只能碰自己的直传。中央看不到内容: 迁移包在旧电脑上用新电脑现场生成的
公钥 (age x25519) 加密, 私钥只在新电脑内存里。日志只记 谁 / 哪台 / 多大 / 几块, 绝不记块内容。

端点:
    POST /api/device-transfer                       {recipient, to_device}      → {id, code, chunk_bytes}   新电脑开
    GET  /api/device-transfer/by-code/{code}                                      → {id, recipient, to_device} 旧电脑手输配对码
    GET  /api/device-transfer/{id}                                                → 状态 / 进度                两边
    POST /api/device-transfer/{id}/start            {size, n, from_device}                                  旧电脑
    PUT  /api/device-transfer/{id}/chunks/{i}       body = 这一块密文 (窗口满了 429)                          旧电脑
    GET  /api/device-transfer/{id}/chunks/{i}       → 密文 (还没到 404)                                       新电脑
    POST /api/device-transfer/{id}/chunks/{i}/ack   收好了, 删掉这一块                                         新电脑
    POST /api/device-transfer/{id}/done             全收齐                                                    新电脑
    POST /api/device-transfer/{id}/cancel                                                                     两边
"""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import device_transfer_db as DB
from .auth import User, get_current_user

logger = logging.getLogger("catfish.gateway.device_transfer")
router = APIRouter(prefix="/api", tags=["device-transfer"])
_TID = re.compile(r"^t_[0-9a-f]{32}$")


class CreateReq(BaseModel):
    recipient: str = Field(min_length=10, max_length=100)
    to_device: str = Field(default="", max_length=80)


class StartReq(BaseModel):
    size: int = Field(gt=0)
    n: int = Field(gt=0)
    from_device: str = Field(default="", max_length=80)


def _me(user: User) -> str:
    return user.sub.strip().lower()


def _guard(tid: str) -> None:
    if not DB.use_pg():
        raise HTTPException(503, "换机直传需要 PostgreSQL (CATFISH_DB_URL 未配)")
    if tid and not _TID.match(tid):
        raise HTTPException(404, "没有这次直传")


def _call(fn, *args):
    try:
        return fn(*args)
    except DB.TransferError as e:
        raise HTTPException(e.status, str(e)) from None
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("换机直传出错 (%s): %s", fn.__name__, type(e).__name__)
        raise HTTPException(503, "换机直传暂不可用") from None


@router.post("/device-transfer", status_code=201)
async def create(req: CreateReq, user: User = Depends(get_current_user)) -> dict[str, Any]:
    _guard("")
    out = _call(DB.create, _me(user), req.recipient.strip(), req.to_device.strip())
    logger.info("换机直传 开 %s → %s id=%s", _me(user), req.to_device.strip()[:40], out["id"])
    return {**out, "chunk_bytes": DB.CHUNK_BYTES, "idle_seconds": DB.IDLE_SECONDS}


@router.get("/device-transfer/by-code/{code}")
async def lookup(code: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    _guard("")
    return _call(DB.lookup, _me(user), code)


@router.get("/device-transfer/{tid}")
async def info(tid: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    _guard(tid)
    return _call(DB.info, _me(user), tid)


@router.post("/device-transfer/{tid}/start")
async def start(tid: str, req: StartReq, user: User = Depends(get_current_user)) -> dict[str, Any]:
    _guard(tid)
    _call(DB.start, _me(user), tid, req.size, req.n, req.from_device.strip())
    logger.info("换机直传 开始传 %s id=%s size=%d n=%d", _me(user), tid, req.size, req.n)
    return {"ok": True, "chunk_bytes": DB.CHUNK_BYTES, "window_bytes": DB.window_bytes()}


@router.put("/device-transfer/{tid}/chunks/{i}")
async def put_chunk(tid: str, i: int, request: Request, user: User = Depends(get_current_user)) -> dict[str, Any]:
    _guard(tid)
    data = await request.body()
    _call(DB.put_chunk, _me(user), tid, i, data)
    return {"ok": True}


@router.get("/device-transfer/{tid}/chunks/{i}")
async def get_chunk(tid: str, i: int, user: User = Depends(get_current_user)) -> Response:
    _guard(tid)
    data = _call(DB.get_chunk, _me(user), tid, i)
    return Response(content=data, media_type="application/octet-stream")


@router.post("/device-transfer/{tid}/chunks/{i}/ack")
async def ack(tid: str, i: int, user: User = Depends(get_current_user)) -> dict[str, Any]:
    _guard(tid)
    _call(DB.ack, _me(user), tid, i)
    return {"ok": True}


@router.post("/device-transfer/{tid}/done")
async def done(tid: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    _guard(tid)
    _call(DB.done, _me(user), tid)
    logger.info("换机直传 收齐 %s id=%s", _me(user), tid)
    return {"ok": True}


@router.post("/device-transfer/{tid}/cancel")
async def cancel(tid: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    _guard(tid)
    _call(DB.cancel, _me(user), tid)
    logger.info("换机直传 取消 %s id=%s", _me(user), tid)
    return {"ok": True}
