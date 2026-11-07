"""群里发大文件 (11/07) —— 加密文件在中央暂存一份, 收件人各自来取。存储层见 room_link_blobs_db.py。

中央看不到内容: 文件在发件人电脑上用随机密钥加密后才传上来, 密钥只在发给每个人的那封端到端加密群消息里。
中央能看到的只有: 谁发的、给谁、多大、几块、什么时候 —— 日志也只记这些, 绝不记块内容。

端点 (身份一律来自 JWT):
    POST /api/room-link/blobs                    {recipients[], size, n}  → {id}    发件人建
    PUT  /api/room-link/blobs/{id}/chunks/{i}    body = 这一块的密文 (≤ 1MB + 64)    发件人传
    POST /api/room-link/blobs/{id}/finish                                          发件人传完 (核对块数 / 大小)
    GET  /api/room-link/blobs/{id}                                → {size, n, complete, purged, ...}  发件人 / 收件人
    GET  /api/room-link/blobs/{id}/chunks/{i}                     → 密文              收件人
    POST /api/room-link/blobs/{id}/taken                          → {everyone}       收件人取完; 都取完就删
"""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import room_link_blobs_db as DB
from .auth import User, get_current_user

logger = logging.getLogger("catfish.gateway.room_link_blobs")
router = APIRouter(prefix="/api", tags=["room-link"])
_BID = re.compile(r"^b_[0-9a-f]{32}$")


class BlobCreateReq(BaseModel):
    recipients: list[str] = Field(min_length=1, max_length=DB.MAX_RECIPIENTS)
    size: int = Field(gt=0)
    n: int = Field(gt=0, le=100_000)


def _me(user: User) -> str:
    return user.sub.strip().lower()


def _guard(bid: str) -> None:
    if not DB.use_pg():
        raise HTTPException(503, "群文件暂存需要 PostgreSQL (CATFISH_DB_URL 未配)")
    if bid and not _BID.match(bid):
        raise HTTPException(404, "没有这个文件")


def _call(fn, *args):
    try:
        return fn(*args)
    except DB.BlobError as e:
        raise HTTPException(e.status, str(e)) from None
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("群文件暂存出错 (%s): %s", fn.__name__, type(e).__name__)
        raise HTTPException(503, "群文件暂存暂不可用") from None


@router.post("/room-link/blobs", status_code=201)
async def create(req: BlobCreateReq, user: User = Depends(get_current_user)) -> dict[str, Any]:
    _guard("")
    me = _me(user)
    to = sorted({r.strip().lower() for r in req.recipients if r.strip()} - {me})
    bid = _call(DB.create, me, to, req.size, req.n)
    logger.info("群文件暂存 建 %s → %d 人 size=%d n=%d id=%s", me, len(to), req.size, req.n, bid)
    return {"id": bid, "chunk_bytes": DB.CHUNK_BYTES}


@router.put("/room-link/blobs/{bid}/chunks/{i}")
async def put_chunk(bid: str, i: int, request: Request, user: User = Depends(get_current_user)) -> dict[str, Any]:
    _guard(bid)
    body = await request.body()
    _call(DB.put_chunk, _me(user), bid, i, body)
    return {"ok": True}


@router.post("/room-link/blobs/{bid}/finish")
async def finish(bid: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    _guard(bid)
    _call(DB.finish, _me(user), bid)
    logger.info("群文件暂存 传完 %s id=%s", _me(user), bid)
    return {"ok": True}


@router.get("/room-link/blobs/{bid}")
async def info(bid: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    _guard(bid)
    return _call(DB.info, _me(user), bid)


@router.get("/room-link/blobs/{bid}/chunks/{i}")
async def get_chunk(bid: str, i: int, user: User = Depends(get_current_user)) -> Response:
    _guard(bid)
    data = _call(DB.get_chunk, _me(user), bid, i)
    return Response(content=data, media_type="application/octet-stream")


@router.post("/room-link/blobs/{bid}/taken")
async def taken(bid: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    _guard(bid)
    everyone = _call(DB.taken, _me(user), bid)
    logger.info("群文件暂存 取完 %s id=%s%s", _me(user), bid, " (都取完了, 已删)" if everyone else "")
    return {"everyone": everyone}
