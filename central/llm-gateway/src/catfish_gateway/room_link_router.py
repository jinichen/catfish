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
    POST /api/room-link/mailbox   {to, kind, payload, ttl_seconds?}  → {id}
    GET  /api/room-link/mailbox?kinds=a,b                            → {messages: [...]}

不带 kinds 只给请求 / 授权 (老版 Companion 的行为); 填表的 form_*、派任务的 task_*、群聊的 room_* 要显式要。

11/6 端到端加密 (docs/GROUP-CHAT-PLAN.md §4.1) —— 员工电脑的公钥目录 (只有公钥, 不涉内容):
    PUT  /api/room-link/keys         {device_id, pubkey}   → {ok}       登记我这台电脑的公钥 (身份来自 JWT)
    GET  /api/room-link/keys?subs=a,b                      → {keys: {sub: [{device_id, pubkey, updated_at}]}}
"""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from . import room_link_db
from .auth import User, get_current_user

logger = logging.getLogger("catfish.gateway.room_link")

router = APIRouter(prefix="/api", tags=["room-link"])


class MailboxDeliverReq(BaseModel):
    to: str = Field(min_length=1, max_length=320)  # email 上限 RFC 5321
    kind: str
    payload: str = Field(min_length=1, max_length=room_link_db.MAX_PAYLOAD_BYTES)
    # 只对 form_* / task_* 生效 (截止后 7 天, 夹到 [10 分钟, 30 天]); 请求 / 授权固定 10 分钟
    ttl_seconds: int | None = Field(default=None, ge=1)


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
            ttl_seconds=req.ttl_seconds,
        )
    except Exception as e:
        logger.warning("room-link 邮筒投递失败 (%s → %s, kind=%s): %s", user.sub, to_sub, req.kind, e)
        raise HTTPException(503, "邮筒暂不可用") from None
    # 审计: 只有元数据, 绝不带 payload
    logger.info("room-link 投递 %s → %s kind=%s id=%s", user.sub, to_sub, req.kind, msg_id)
    return {"id": msg_id}


@router.get("/room-link/mailbox")
async def take_inbox(
    kinds: str | None = Query(default=None, max_length=200),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _require_pg()
    wanted = room_link_db.ROOM_LINK_KINDS
    if kinds:
        wanted = frozenset(k.strip() for k in kinds.split(",") if k.strip())
        unknown = wanted - room_link_db.MAILBOX_KINDS
        if unknown or not wanted:
            raise HTTPException(400, f"kinds 里有不认识的: {sorted(unknown)}")
    try:
        messages = room_link_db.take_inbox(user.sub.strip().lower(), wanted)
    except Exception as e:
        logger.warning("room-link 邮筒取件失败 (%s): %s", user.sub, e)
        raise HTTPException(503, "邮筒暂不可用") from None
    if messages:
        # 审计只留 谁(kind): 先归约再打, 不把 messages 本身放进日志参数
        # (test_central_log_no_content 按变量名静态扫, payload 一个字都不能沾)
        senders = ", ".join(f"{m['from']}({m['kind']})" for m in messages)
        logger.info("room-link 取件 %s ← %s", user.sub, senders)
    return {"messages": messages}


# ── 公钥目录 (11/6, 端到端加密) ──────────────────────────────────────

_DEVICE_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_PUBKEY = re.compile(r"^[A-Za-z0-9_-]{43}=?$")   # X25519 公钥 32 字节, urlsafe base64
MAX_KEY_LOOKUP = 60   # 一次最多查 60 人 (群最多 50 人, 留余量)


class KeyPutReq(BaseModel):
    device_id: str = Field(min_length=8, max_length=64)
    pubkey: str = Field(min_length=43, max_length=44)


@router.put("/room-link/keys")
async def put_key(req: KeyPutReq, user: User = Depends(get_current_user)) -> dict[str, Any]:
    _require_pg()
    if not _DEVICE_ID.match(req.device_id) or not _PUBKEY.match(req.pubkey):
        raise HTTPException(400, "device_id / pubkey 格式不对")
    sub = user.sub.strip().lower()   # 只能登记自己的: 身份来自 JWT
    try:
        room_link_db.put_key(sub, req.device_id, req.pubkey)
    except Exception as e:
        logger.warning("公钥登记失败 (%s): %s", sub, e)
        raise HTTPException(503, "公钥目录暂不可用") from None
    logger.info("公钥登记 %s device=%s", sub, req.device_id)
    return {"ok": True}


@router.get("/room-link/keys")
async def get_keys(subs: str = Query(min_length=3, max_length=60 * 330),
                   user: User = Depends(get_current_user)) -> dict[str, Any]:
    _require_pg()
    wanted = sorted({s.strip().lower() for s in subs.split(",") if s.strip()})
    if not wanted or len(wanted) > MAX_KEY_LOOKUP:
        raise HTTPException(400, f"一次查 1 到 {MAX_KEY_LOOKUP} 人")
    try:
        keys = room_link_db.get_keys(wanted)
    except Exception as e:
        logger.warning("公钥查询失败 (%s): %s", user.sub, e)
        raise HTTPException(503, "公钥目录暂不可用") from None
    return {"keys": keys}
