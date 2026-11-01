"""手机中转站的转发核心 (docs/MOBILE-CLIENT-DESIGN.md §2 / §3).

## 只转发、不存、不排队

手机和员工电脑各开一条 WebSocket 连到这里。一帧到了就立刻交给另一端;
另一端不在线就回「不在线」, 这一帧丢掉。`d` 字段是两端之间端到端加密的密文,
这里不解析、不写日志、不落盘。

## 多个网关进程

生产网关跑多个进程 (UVICORN_WORKERS), 手机和电脑可能连在不同进程上。对方不在
本进程时, 查「谁在线」(relay_connection 表) 后用 PostgreSQL 的 NOTIFY 交给持有它
的进程 —— NOTIFY 是即时通知, 没人收就没了, 跟本进程直接转一样不排队。
NOTIFY 单条上限 8000 字节, 所以 `d` 限 6000 字符, 大消息由两端自己分片。

没有 PostgreSQL 时只能单进程 (开发 / 单测)。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from . import relay_db

logger = logging.getLogger("catfish.gateway.relay")

#: 一帧原始文本的上限 (字节)
MAX_FRAME_BYTES = 8192
#: 密文字段上限 (字符) —— 让跨进程的 NOTIFY 留在 8000 字节以内
MAX_DATA_CHARS = 6000
#: 每条连接每秒最多几帧 (令牌桶), 突发上限
RATE_PER_SEC = 30.0
RATE_BURST = 60.0
#: 每人每 10 分钟最多几次配对请求 (防暴力猜验证码)
PAIR_REQUESTS_PER_WINDOW = 5
PAIR_WINDOW_SECONDS = 600
#: 每人同时最多几条连接 (手机上限 + 电脑若干)
EXTRA_CONNECTIONS = 5
HEARTBEAT_SECONDS = 20
#: 配对关系的进程内缓存时长 —— 撤销最迟这么久后中转站也不再放行 (电脑那边是立刻不认)
PAIR_CACHE_SECONDS = 30
NOTIFY_CHANNEL = "catfish_relay"

Sender = Callable[[str], Awaitable[None]]
Closer = Callable[[], Awaitable[None]]


@dataclass
class Conn:
    sub: str
    device: str
    role: str  # "pc" | "phone"
    name: str
    send_text: Sender
    close: Closer
    cid: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    tokens: float = RATE_BURST
    last_refill: float = field(default_factory=time.monotonic)
    pair_cache: dict[str, float] = field(default_factory=dict)

    def allow(self) -> bool:
        now = time.monotonic()
        self.tokens = min(RATE_BURST, self.tokens + (now - self.last_refill) * RATE_PER_SEC)
        self.last_refill = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


def _b64_key_ok(value: Any) -> bool:
    import base64  # noqa: PLC0415

    if not isinstance(value, str) or len(value) > 64:
        return False
    try:
        return len(base64.b64decode(value, validate=True)) == 32
    except Exception:
        return False


class Hub:
    def __init__(self) -> None:
        self.local: dict[tuple[str, str], Conn] = {}
        self._pair_attempts: dict[str, list[float]] = {}
        self._listener: asyncio.Task | None = None
        self._heartbeat: asyncio.Task | None = None
        self._notify_conn = None

    # ── 连接进出 ──────────────────────────────────────────────

    async def join(self, conn: Conn) -> str | None:
        """登记一条连接; 返回拒绝原因 (None = 放行)。"""
        if conn.role == "phone" and relay_db.max_phones() == 0:
            return "mobile_disabled"
        mine = [c for (s, _), c in self.local.items() if s == conn.sub]
        limit = relay_db.max_phones() + EXTRA_CONNECTIONS
        if len(mine) >= limit:
            return "too_many_connections"
        old = self.local.get((conn.sub, conn.device))
        if old is not None:
            # 同一台设备重连: 旧连接让位
            await self._safe_close(old)
        conn.cid = await asyncio.to_thread(relay_db.store().conn_start, conn.sub, conn.device, conn.role, conn.name)
        self.local[(conn.sub, conn.device)] = conn
        self._ensure_background()
        logger.info("relay 连上 %s %s=%s", conn.sub, conn.role, conn.device)
        return None

    async def leave(self, conn: Conn) -> None:
        if self.local.get((conn.sub, conn.device)) is conn:
            del self.local[(conn.sub, conn.device)]
        if conn.cid:
            try:
                await asyncio.to_thread(relay_db.store().conn_end, conn.cid, conn.bytes_in, conn.bytes_out)
            except Exception as e:  # 记账失败不影响断开
                logger.warning("relay 连接记录收尾失败: %s", type(e).__name__)
        logger.info("relay 断开 %s %s=%s in=%d out=%d", conn.sub, conn.role, conn.device,
                    conn.bytes_in, conn.bytes_out)

    async def _safe_close(self, conn: Conn) -> None:
        try:
            await conn.close()
        except Exception:
            pass

    # ── 发送 ─────────────────────────────────────────────────

    async def _send(self, conn: Conn, frame: dict) -> None:
        raw = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        conn.bytes_out += len(raw.encode())
        try:
            await conn.send_text(raw)
        except Exception:
            pass

    async def deliver(self, sub: str, device: str, frame: dict) -> bool:
        """交给同一员工的某台设备; 不在线返回 False (这一帧就此丢掉)。"""
        target = self.local.get((sub, device))
        if target is not None:
            await self._send(target, frame)
            return True
        if not relay_db.use_pg():
            return False
        online = await asyncio.to_thread(relay_db.store().online, sub)
        if not any(p.device == device for p in online):
            return False
        await self._notify({"sub": sub, "to": device, "frame": frame})
        return True

    async def _notify(self, envelope: dict) -> None:
        import psycopg  # noqa: PLC0415

        raw = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        try:
            if self._notify_conn is None or self._notify_conn.closed:
                self._notify_conn = await psycopg.AsyncConnection.connect(
                    os.environ["CATFISH_DB_URL"], autocommit=True)
            await self._notify_conn.execute("SELECT pg_notify(%s, %s)", (NOTIFY_CHANNEL, raw))
        except Exception as e:
            logger.warning("relay 跨进程转发失败: %s", type(e).__name__)
            self._notify_conn = None

    # ── 后台: 跨进程收件 + 心跳 ───────────────────────────────

    def _ensure_background(self) -> None:
        loop = asyncio.get_running_loop()
        if self._heartbeat is None or self._heartbeat.done():
            self._heartbeat = loop.create_task(self._heartbeat_loop())
        if relay_db.use_pg() and (self._listener is None or self._listener.done()):
            self._listener = loop.create_task(self._listen_loop())

    async def _heartbeat_loop(self) -> None:
        while self.local:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            ids = [c.cid for c in self.local.values() if c.cid]
            try:
                await asyncio.to_thread(relay_db.store().conn_heartbeat, ids)
            except Exception as e:
                logger.warning("relay 心跳写入失败: %s", type(e).__name__)

    async def _listen_loop(self) -> None:
        import psycopg  # noqa: PLC0415

        while self.local:
            try:
                async with await psycopg.AsyncConnection.connect(
                        os.environ["CATFISH_DB_URL"], autocommit=True) as conn:
                    await conn.execute(f"LISTEN {NOTIFY_CHANNEL}")
                    async for note in conn.notifies():
                        await self._on_notify(note.payload)
                        if not self.local:
                            return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("relay 跨进程监听断了, 5 秒后重连: %s", type(e).__name__)
                await asyncio.sleep(5)

    async def _on_notify(self, raw: str) -> None:
        try:
            env = json.loads(raw)
        except ValueError:
            return
        target = self.local.get((env.get("sub"), env.get("to")))
        if target is None:
            return
        if env.get("close"):
            await self._safe_close(target)
            return
        frame = env.get("frame")
        if isinstance(frame, dict):
            await self._send(target, frame)

    # ── 配对关系 ─────────────────────────────────────────────

    async def _paired(self, conn: Conn, peer: str) -> bool:
        exp = conn.pair_cache.get(peer)
        if exp is not None and exp > time.monotonic():
            return True
        phone, pc = (conn.device, peer) if conn.role == "phone" else (peer, conn.device)
        ok = await asyncio.to_thread(relay_db.store().is_paired, conn.sub, phone, pc)
        if ok:
            conn.pair_cache[peer] = time.monotonic() + PAIR_CACHE_SECONDS
        else:
            conn.pair_cache.pop(peer, None)
        return ok

    def _pair_rate_ok(self, sub: str) -> bool:
        now = time.monotonic()
        recent = [t for t in self._pair_attempts.get(sub, []) if now - t < PAIR_WINDOW_SECONDS]
        if len(recent) >= PAIR_REQUESTS_PER_WINDOW:
            self._pair_attempts[sub] = recent
            return False
        recent.append(now)
        self._pair_attempts[sub] = recent
        return True

    async def _over_limit(self, sub: str, phone: str) -> bool:
        phones = await asyncio.to_thread(relay_db.store().phones, sub)
        return phone not in phones and len(phones) >= relay_db.max_phones()

    # ── 一帧 ─────────────────────────────────────────────────

    async def handle(self, conn: Conn, raw: str) -> None:
        conn.bytes_in += len(raw.encode())
        if not conn.allow():
            await self._send(conn, {"t": "error", "code": "rate_limited"})
            return
        try:
            frame = json.loads(raw)
        except ValueError:
            await self._send(conn, {"t": "error", "code": "bad_frame"})
            return
        if not isinstance(frame, dict):
            await self._send(conn, {"t": "error", "code": "bad_frame"})
            return
        kind = frame.get("t")
        to = frame.get("to") if isinstance(frame.get("to"), str) else ""
        handler = _HANDLERS.get((conn.role, kind)) or _HANDLERS.get(("*", kind))
        if handler is None:
            await self._send(conn, {"t": "error", "code": "unknown_type"})
            return
        await handler(self, conn, frame, to)

    async def on_ping(self, conn: Conn, frame: dict, to: str) -> None:
        await self._send(conn, {"t": "pong"})

    async def on_peers(self, conn: Conn, frame: dict, to: str) -> None:
        online = await asyncio.to_thread(relay_db.store().online, conn.sub)
        if conn.role == "phone":
            paired = await asyncio.to_thread(relay_db.store().pcs_of, conn.sub, conn.device)
            peers = [{"device": p.device, "name": p.name, "paired": p.device in paired}
                     for p in online if p.role == "pc"]
        else:
            peers = [{"device": p.device, "name": p.name}
                     for p in online if p.role == "phone" and p.device != conn.device]
        await self._send(conn, {"t": "peers", "peers": peers, "max_phones": relay_db.max_phones()})

    async def on_pair_request(self, conn: Conn, frame: dict, to: str) -> None:
        if not _b64_key_ok(frame.get("pk")):
            await self._send(conn, {"t": "error", "code": "bad_key"})
            return
        if not self._pair_rate_ok(conn.sub):
            await self._send(conn, {"t": "error", "code": "pair_rate_limited"})
            return
        if await self._over_limit(conn.sub, conn.device):
            await self._send(conn, {"t": "error", "code": "phone_limit", "max_phones": relay_db.max_phones()})
            return
        ok = await self.deliver(conn.sub, to, {"t": "pair_request", "from": conn.device,
                                               "name": conn.name, "pk": frame["pk"]})
        if not ok:
            await self._send(conn, {"t": "offline", "device": to})

    async def on_pair_offer(self, conn: Conn, frame: dict, to: str) -> None:
        if not _b64_key_ok(frame.get("pk")):
            await self._send(conn, {"t": "error", "code": "bad_key"})
            return
        if not await self.deliver(conn.sub, to, {"t": "pair_offer", "from": conn.device, "pk": frame["pk"]}):
            await self._send(conn, {"t": "offline", "device": to})

    async def on_pair_result(self, conn: Conn, frame: dict, to: str) -> None:
        ok = frame.get("ok") is True
        if ok and await self._over_limit(conn.sub, to):
            await self._send(conn, {"t": "error", "code": "phone_limit", "max_phones": relay_db.max_phones()})
            ok = False
        if ok:
            name = frame.get("name") if isinstance(frame.get("name"), str) else ""
            await asyncio.to_thread(relay_db.store().add_pair, conn.sub, to, conn.device, name[:40])
            logger.info("relay 配对 %s 手机=%s 电脑=%s", conn.sub, to, conn.device)
        await self.deliver(conn.sub, to, {"t": "pair_result", "from": conn.device, "ok": ok})

    async def on_revoke(self, conn: Conn, frame: dict, to: str) -> None:
        phone = frame.get("device") if isinstance(frame.get("device"), str) else ""
        if not phone:
            return
        await asyncio.to_thread(relay_db.store().remove_pair, conn.sub, phone, conn.device)
        conn.pair_cache.pop(phone, None)
        local_phone = self.local.get((conn.sub, phone))
        if local_phone is not None:
            local_phone.pair_cache.pop(conn.device, None)
        logger.info("relay 撤销 %s 手机=%s 电脑=%s", conn.sub, phone, conn.device)
        await self.deliver(conn.sub, phone, {"t": "revoked", "from": conn.device})

    async def on_sync_pairs(self, conn: Conn, frame: dict, to: str) -> None:
        phones = frame.get("phones")
        if not isinstance(phones, list) or not all(isinstance(p, str) for p in phones):
            return
        await asyncio.to_thread(relay_db.store().keep_pairs, conn.sub, conn.device, set(phones))

    async def on_msg(self, conn: Conn, frame: dict, to: str) -> None:
        d = frame.get("d")
        if not isinstance(d, str) or not d or len(d) > MAX_DATA_CHARS:
            await self._send(conn, {"t": "error", "code": "bad_data"})
            return
        if not to or not await self._paired(conn, to):
            await self._send(conn, {"t": "error", "code": "not_paired", "device": to})
            return
        if not await self.deliver(conn.sub, to, {"t": "msg", "from": conn.device, "d": d}):
            await self._send(conn, {"t": "offline", "device": to})


_HANDLERS: dict[tuple[str, Any], Callable[[Hub, Conn, dict, str], Awaitable[None]]] = {
    ("*", "ping"): Hub.on_ping,
    ("*", "peers"): Hub.on_peers,
    ("*", "msg"): Hub.on_msg,
    ("phone", "pair_request"): Hub.on_pair_request,
    ("pc", "pair_offer"): Hub.on_pair_offer,
    ("pc", "pair_result"): Hub.on_pair_result,
    ("pc", "revoke"): Hub.on_revoke,
    ("pc", "sync_pairs"): Hub.on_sync_pairs,
}

hub = Hub()
