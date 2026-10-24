"""员工本机「数据外发记录」的每日指纹 (10/25 鸿波: 有争议时要能证明记录没被改过).

员工本机每条外发记录带指纹链, 改 / 删中间的行本机就能查出; 删末尾查不出 ——
所以 Companion 每天把链头报上来, 校验时取回来对。

只收指纹 (序号 + sha256), 不收内容; 身份来自 JWT, 只能报 / 看自己的。

端点:
    POST /api/egress/anchor   {last_id, last_hash, row_count}  → {id, received_at}
    GET  /api/egress/anchors?limit=                           → {anchors: [...]}
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from . import egress_anchor_db
from .auth import User, get_current_user

logger = logging.getLogger("catfish.gateway.egress_anchor")

router = APIRouter(prefix="/api/egress", tags=["egress-anchor"])


class AnchorReq(BaseModel):
    last_id: int = Field(ge=0)
    last_hash: str = Field(pattern=r"^([0-9a-f]{64}|GENESIS)$")
    row_count: int = Field(default=0, ge=0)


def _require_pg() -> None:
    if not egress_anchor_db.use_pg():
        raise HTTPException(503, "指纹存储需要 PostgreSQL (CATFISH_DB_URL 未配)")


@router.post("/anchor", status_code=201)
async def add_anchor(req: AnchorReq, user: User = Depends(get_current_user)) -> dict[str, Any]:
    _require_pg()
    sub = user.sub.strip().lower()
    try:
        out = egress_anchor_db.add(sub=sub, last_id=req.last_id, last_hash=req.last_hash, row_count=req.row_count)
    except Exception as e:
        logger.warning("egress 指纹写入失败 (%s): %s", sub, e)
        raise HTTPException(503, "指纹存储暂不可用") from None
    logger.info("egress 指纹 %s last_id=%s", sub, req.last_id)
    return out


@router.get("/anchors")
async def list_anchors(
    limit: int = Query(default=400, ge=1, le=2000),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _require_pg()
    try:
        return {"anchors": egress_anchor_db.list_for(user.sub.strip().lower(), limit)}
    except Exception as e:
        logger.warning("egress 指纹读取失败 (%s): %s", user.sub, e)
        raise HTTPException(503, "指纹存储暂不可用") from None
