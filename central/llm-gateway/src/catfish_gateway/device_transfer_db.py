"""换机直传的中转 (11/21, alembic 20261121_024, docs/DEVICE-MIGRATION-DESIGN.md §12) —— 只转交密文, 边传边删。

跟群文件暂存 (room_link_blobs_db.py) 一个规矩: 这层只接字节, 不解析、不写日志; 能看到的只有谁、哪两台、多大、几块、什么时候。
不同的是这里**不存整包**: 新电脑取走一块就删一块, 中央同时最多留 WINDOW 字节, 旧电脑传满了就等。

一次直传 (两台电脑登录的是同一个人, 所以只认 owner_sub):
  新电脑 create (带上现场生成的公钥 + 一个给人手输的配对码) → 屏幕上出二维码
  旧电脑 扫码拿到 id + 公钥 (或手输配对码 lookup) → start (总大小 / 块数) → put_chunk … (窗口满了 429)
  新电脑 get_chunk → ack (删这一块) … → done (全收齐, 删干净)
  任何一边 cancel; 没开始的 / 半小时没动静的作废、删块。元数据 30 天后整行删。
"""
from __future__ import annotations

import math
import os
import re
import secrets

from .room_link_db import AUDIT_RETENTION_DAYS, _pg_conn, use_pg  # noqa: F401  (use_pg 给 router 用)

CHUNK_BYTES = 1024 * 1024            # 迁移包 (已加密) 按 1MB 切, 最后一块可以小
IDLE_SECONDS = 30 * 60               # 等扫码 / 传输中半小时没动静就作废
MAX_SIZE = 64 * 1024 ** 3            # 单次最大 64GB (连同文件也够了)
CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"   # 去掉 0 O 1 I, 手输不容易错
_RECIPIENT = re.compile(r"^age1[02-9ac-hj-np-z]{58}$")
ACTIVE = ("waiting", "sending")


def window_bytes() -> int:
    """中央同时最多留多少还没被取走的块, 默认 64MB, CATFISH_DEVICE_TRANSFER_WINDOW_MB 可改。"""
    return int(float(os.environ.get("CATFISH_DEVICE_TRANSFER_WINDOW_MB") or 64) * 1024 * 1024)


class TransferError(ValueError):
    """给 router 转 4xx 的人话。"""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status


def new_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def norm_code(code: str) -> str:
    raw = re.sub(r"[\s-]", "", code or "").upper()
    return f"{raw[:4]}-{raw[4:]}" if len(raw) == 8 else ""


def _sweep(cur) -> None:
    """没动静的作废、删块; 审计期过了的整行删。"""
    cur.execute("UPDATE device_transfer SET state = 'expired' WHERE state IN ('waiting', 'sending')"
                " AND last_at < now() - make_interval(secs => %s)", (IDLE_SECONDS,))
    cur.execute("DELETE FROM device_transfer_chunks c USING device_transfer t"
                " WHERE c.transfer_id = t.id AND t.state NOT IN ('waiting', 'sending')")
    cur.execute("DELETE FROM device_transfer WHERE created_at < now() - make_interval(days => %s)", (AUDIT_RETENTION_DAYS,))


def create(owner: str, recipient: str, to_device: str) -> dict:
    """新电脑开一次直传。同一个人之前没完成的作废 (一个人同时只有一次)。"""
    if not _RECIPIENT.match(recipient or ""):
        raise TransferError(400, "公钥格式不对")
    tid = "t_" + secrets.token_hex(16)
    code = new_code()
    with _pg_conn() as conn, conn.cursor() as cur:
        _sweep(cur)
        cur.execute("UPDATE device_transfer SET state = 'cancelled' WHERE owner_sub = %s AND state IN ('waiting', 'sending')", (owner,))
        cur.execute("DELETE FROM device_transfer_chunks c USING device_transfer t"
                    " WHERE c.transfer_id = t.id AND t.owner_sub = %s AND t.state = 'cancelled'", (owner,))
        cur.execute("INSERT INTO device_transfer (id, owner_sub, code, recipient, to_device) VALUES (%s, %s, %s, %s, %s)",
                    (tid, owner, code, recipient, (to_device or "")[:80]))
        conn.commit()
    return {"id": tid, "code": code}


def _row(cur, owner: str, tid: str):
    cur.execute("SELECT state, size, n, acked, recipient, to_device, from_device, code, created_at, relayed"
                " FROM device_transfer WHERE id = %s AND owner_sub = %s", (tid, owner))
    r = cur.fetchone()
    if not r:
        raise TransferError(404, "没有这次直传 (可能已经过期)")
    return r


def _need(r, *states: str) -> None:
    if r[0] in states:
        return
    raise TransferError(410 if r[0] in ("expired", "cancelled") else 409, {
        "expired": "这次直传超时作废了, 请在新电脑上重新开始",
        "cancelled": "这次直传已经取消",
        "done": "这次直传已经完成",
        "waiting": "旧电脑还没开始传",
        "sending": "已经在传了",
    }.get(r[0], "状态不对"))


def lookup(owner: str, code: str) -> dict:
    """旧电脑手输配对码 → 这次直传的 id 和新电脑的公钥 (只找本人、还在等的)。"""
    c = norm_code(code)
    if not c:
        raise TransferError(400, "配对码是 8 位 (字母和数字)")
    with _pg_conn() as conn, conn.cursor() as cur:
        _sweep(cur)
        cur.execute("SELECT id, recipient, to_device, created_at FROM device_transfer"
                    " WHERE owner_sub = %s AND code = %s AND state = 'waiting' ORDER BY created_at DESC LIMIT 1", (owner, c))
        r = cur.fetchone()
        conn.commit()
    if not r:
        raise TransferError(404, "没找到这个配对码: 看看新电脑上的码, 或者新电脑那边已经过期了 (半小时)")
    return {"id": r[0], "recipient": r[1], "to_device": r[2], "created_at": r[3].isoformat()}


def info(owner: str, tid: str) -> dict:
    with _pg_conn() as conn, conn.cursor() as cur:
        _sweep(cur)
        r = _row(cur, owner, tid)
        cur.execute("SELECT COALESCE(MAX(i) + 1, 0), COUNT(*), COALESCE(SUM(length(data)), 0)"
                    " FROM device_transfer_chunks WHERE transfer_id = %s", (tid,))
        top, buffered, buffered_bytes = cur.fetchone()
        conn.commit()
    return {"state": r[0], "size": r[1], "n": r[2], "acked": int(r[3]), "recipient": r[4], "to_device": r[5],
            "from_device": r[6], "created_at": r[8].isoformat(), "relayed": int(r[9]),
            "sent": max(int(top), int(r[3])), "buffered": int(buffered), "buffered_bytes": int(buffered_bytes)}


def start(owner: str, tid: str, size: int, n: int, from_device: str) -> None:
    if not 0 < size <= MAX_SIZE:
        raise TransferError(413, "迁移包太大")
    if n != math.ceil(size / CHUNK_BYTES):
        raise TransferError(400, "块数跟大小对不上")
    with _pg_conn() as conn, conn.cursor() as cur:
        r = _row(cur, owner, tid)
        _need(r, "waiting")
        cur.execute("UPDATE device_transfer SET state = 'sending', size = %s, n = %s, from_device = %s, last_at = now()"
                    " WHERE id = %s", (size, n, (from_device or "")[:80], tid))
        conn.commit()


def put_chunk(owner: str, tid: str, i: int, data: bytes) -> None:
    with _pg_conn() as conn, conn.cursor() as cur:
        r = _row(cur, owner, tid)
        _need(r, "sending")
        size, n = int(r[1]), int(r[2])
        if not 0 <= i < n:
            raise TransferError(400, "块号不对")
        want = CHUNK_BYTES if i < n - 1 else size - CHUNK_BYTES * (n - 1)
        if len(data) != want:
            raise TransferError(400, "这一块大小不对")
        if i < int(r[3]):
            return   # 新电脑已经取过 (重传的): 不再存
        cur.execute("SELECT COALESCE(SUM(length(data)), 0) FROM device_transfer_chunks WHERE transfer_id = %s AND i <> %s", (tid, i))
        if int(cur.fetchone()[0]) + len(data) > window_bytes():
            conn.commit()
            raise TransferError(429, "中转窗口满了, 等新电脑取走一些再传")
        cur.execute("INSERT INTO device_transfer_chunks (transfer_id, i, data) VALUES (%s, %s, %s)"
                    " ON CONFLICT (transfer_id, i) DO UPDATE SET data = excluded.data", (tid, i, data))
        cur.execute("UPDATE device_transfer SET last_at = now() WHERE id = %s", (tid,))
        conn.commit()


def get_chunk(owner: str, tid: str, i: int) -> bytes:
    with _pg_conn() as conn, conn.cursor() as cur:
        r = _row(cur, owner, tid)
        _need(r, "sending")
        cur.execute("SELECT data FROM device_transfer_chunks WHERE transfer_id = %s AND i = %s", (tid, i))
        c = cur.fetchone()
    if not c:
        raise TransferError(404, "这一块还没到")
    return bytes(c[0])


def ack(owner: str, tid: str, i: int) -> None:
    """新电脑收好了第 i 块: 删掉。按顺序收, 所以 acked = 已经收好的块数。"""
    with _pg_conn() as conn, conn.cursor() as cur:
        r = _row(cur, owner, tid)
        _need(r, "sending")
        if i != int(r[3]):
            raise TransferError(409, f"要按顺序收: 下一块应该是 {int(r[3])}")
        cur.execute("DELETE FROM device_transfer_chunks WHERE transfer_id = %s AND i = %s RETURNING length(data)", (tid, i))
        got = cur.fetchone()
        if not got:
            raise TransferError(404, "这一块还没到")
        cur.execute("UPDATE device_transfer SET acked = acked + 1, relayed = relayed + %s, last_at = now() WHERE id = %s",
                    (int(got[0]), tid))
        conn.commit()


def done(owner: str, tid: str) -> None:
    with _pg_conn() as conn, conn.cursor() as cur:
        r = _row(cur, owner, tid)
        _need(r, "sending")
        if int(r[3]) != int(r[2]):
            raise TransferError(409, f"还差 {int(r[2]) - int(r[3])} 块没收")
        cur.execute("UPDATE device_transfer SET state = 'done', last_at = now() WHERE id = %s", (tid,))
        cur.execute("DELETE FROM device_transfer_chunks WHERE transfer_id = %s", (tid,))
        conn.commit()


def cancel(owner: str, tid: str) -> None:
    with _pg_conn() as conn, conn.cursor() as cur:
        r = _row(cur, owner, tid)
        if r[0] in ACTIVE:
            cur.execute("UPDATE device_transfer SET state = 'cancelled', last_at = now() WHERE id = %s", (tid,))
            cur.execute("DELETE FROM device_transfer_chunks WHERE transfer_id = %s", (tid,))
        conn.commit()
