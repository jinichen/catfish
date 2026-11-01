"""手机中转站的 WebSocket 入口 (docs/MOBILE-CLIENT-DESIGN.md §2 / §4).

    WS /relay/ws

连上后第一帧必须是登录帧 (浏览器的 WebSocket 不能带 Authorization 头; 令牌也不放
网址里 —— 网址会进反向代理的访问日志):

    {"t": "auth", "token": "<单位账号令牌>", "role": "pc" | "phone",
     "device": "<设备号>", "name": "<设备名>"}

通过回 {"t": "ready", "device", "max_phones"}; 之后的帧见 relay_hub。
令牌到期就断开, 两端用新令牌重连。服务账号 (client:*) 不能连。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import relay_db
from .auth import _get_provider, is_service_principal
from .relay_hub import MAX_FRAME_BYTES, Conn, hub

logger = logging.getLogger("catfish.gateway.relay")

router = APIRouter(tags=["relay"])

AUTH_TIMEOUT_SECONDS = 10
_DEVICE_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _token_exp(token: str) -> float | None:
    """令牌里的到期时间 (已验过签, 这里只是读出来)。dev 令牌没有 → None。"""
    parts = token.split(".")
    if len(parts) < 2:
        return None
    try:
        seg = parts[1] + "=" * (-len(parts[1]) % 4)
        exp = json.loads(base64.urlsafe_b64decode(seg)).get("exp")
        return float(exp) if isinstance(exp, (int, float)) else None
    except Exception:
        return None


async def _close(ws: WebSocket, code: int, reason: str) -> None:
    try:
        await ws.send_text(json.dumps({"t": "error", "code": reason}))
        await ws.close(code=code)
    except Exception:
        pass


@router.websocket("/relay/ws")
async def relay_ws(ws: WebSocket) -> None:
    await ws.accept()
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=AUTH_TIMEOUT_SECONDS)
        hello = json.loads(raw) if len(raw) <= MAX_FRAME_BYTES else None
    except (asyncio.TimeoutError, ValueError, WebSocketDisconnect, RuntimeError):
        await _close(ws, 4401, "auth_required")
        return
    if not isinstance(hello, dict) or hello.get("t") != "auth":
        await _close(ws, 4401, "auth_required")
        return
    token = hello.get("token") if isinstance(hello.get("token"), str) else ""
    user = _get_provider().verify_bearer(f"Bearer {token}") if token else None
    if user is None or is_service_principal(user):
        await _close(ws, 4401, "auth_failed")
        return
    role = hello.get("role")
    device = hello.get("device") if isinstance(hello.get("device"), str) else ""
    if role not in ("pc", "phone") or not _DEVICE_RE.match(device):
        await _close(ws, 4400, "bad_hello")
        return
    name = hello.get("name") if isinstance(hello.get("name"), str) else ""

    conn = Conn(
        sub=user.sub.strip().lower(), device=device, role=role, name=name.strip()[:40],
        send_text=ws.send_text, close=lambda: ws.close(code=4409),
    )
    refused = await hub.join(conn)
    if refused:
        await _close(ws, 4403, refused)
        return

    exp = _token_exp(token)
    watchdog = None
    if exp is not None:
        async def _expire() -> None:
            await asyncio.sleep(max(0.0, exp - time.time()))
            await _close(ws, 4401, "token_expired")
        watchdog = asyncio.create_task(_expire())

    try:
        await ws.send_text(json.dumps({"t": "ready", "device": device, "max_phones": relay_db.max_phones()}))
        while True:
            raw = await ws.receive_text()
            if len(raw.encode()) > MAX_FRAME_BYTES:
                await _close(ws, 1009, "frame_too_large")
                break
            await hub.handle(conn, raw)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if watchdog is not None:
            watchdog.cancel()
        await hub.leave(conn)
