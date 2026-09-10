"""RoomLink 邮筒 (P50) — 同事间横向协同的中央中转.

## 为什么中央只当邮筒

hermes 0.21 RoomLink 让 A 远程驱动 B 的 hermes, 但 A 要先拿到 B 签的 grant,
而 A 和 B 互相不知道对方地址。中央两边都认识, 所以只做一件事: 把 A 的
「请求」送到 B, 把 B 的「授权」送回 A。之后 A 直连 B:8642, 中央不参与。

## 中央看不到内容

- payload 是不透明字符串: 不解析、不校验结构、不写日志。
- 收件人取走即清空 (room_link_db.take_inbox), 中央不留副本。
- 中央能看到的只有 from / to / kind / 时间 —— 也就是审计。
- Phase 2 上 PKI 后 payload 换成密文, 本文件一行不用改。

## 身份

发件人 = JWT 的 `user.sub` (email), 不信 body 里自报的。收件人只能取自己的。

端点:
    POST /api/room-link/mailbox   {to, kind, payload}  → {id}
    GET  /api/room-link/mailbox                        → {messages: [...]}
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import room_link_db
from .auth import User, get_current_user

logger = logging.getLogger("catfish.gateway.room_link")

router = APIRouter(prefix="/api", tags=["room-link"])


class MailboxDeliverReq(BaseModel):
    to: str = Field(min_length=1, max_length=320)  # email 上限 RFC 5321
    kind: str
    payload: str = Field(min_length=1, max_length=room_link_db.MAX_PAYLOAD_BYTES)


def _require_pg() -> None:
    if not room_link_db.use_pg():
        raise HTTPException(503, "邮筒需要 PostgreSQL (CATFISH_DB_URL 未配)")


@router.post("/room-link/mailbox", status_code=201)
async def deliver(
    req: MailboxDeliverReq,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _require_pg()
    if req.kind not in room_link_db.MAILBOX_KINDS:
        raise HTTPException(400, f"kind={req.kind} 不在 {sorted(room_link_db.MAILBOX_KINDS)}")
    # email 大小写不敏感: 两端都归一化, 否则 B 用 Bob@x.com 登录就收不到发给 bob@x.com 的
    from_sub = user.sub.strip().lower()
    to_sub = req.to.strip().lower()
    if to_sub == from_sub:
        raise HTTPException(400, "不能给自己投递")
    try:
        msg_id = room_link_db.deliver(
            from_sub=from_sub, to_sub=to_sub, kind=req.kind, payload=req.payload,
        )
    except Exception as e:
        logger.warning("room-link 邮筒投递失败 (%s → %s, kind=%s): %s", user.sub, to_sub, req.kind, e)
        raise HTTPException(503, "邮筒暂不可用") from None
    # 审计: 只有元数据, 绝不带 payload
    logger.info("room-link 投递 %s → %s kind=%s id=%s", user.sub, to_sub, req.kind, msg_id)
    return {"id": msg_id}


@router.get("/room-link/mailbox")
async def take_inbox(
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _require_pg()
    try:
        messages = room_link_db.take_inbox(user.sub.strip().lower())
    except Exception as e:
        logger.warning("room-link 邮筒取件失败 (%s): %s", user.sub, e)
        raise HTTPException(503, "邮筒暂不可用") from None
    if messages:
        logger.info(
            "room-link 取件 %s ← %s",
            user.sub,
            ", ".join(f"{m['from']}({m['kind']})" for m in messages),
        )
    return {"messages": messages}
