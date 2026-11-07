"""群里发大文件的暂存 (11/07, alembic 20261107_021) —— 中央只存密文, 只存一份, 取完就删。

跟邮筒 (room_link_db.py) 同一个规矩: 这层只接字节, 不解析、不写日志; 能看到的只有谁发的、给谁、多大、几块、什么时候。

- 发件人建一个 (create) → 一块块传 (put_chunk) → 传完 (finish, 核对块数)。
- 收件人 (recipients 里的人) 看状态 (info)、按块取 (get_chunk), 取完报一声 (taken);
  所有收件人都报过 → 删内容。到期 (默认 7 天) 也删。元数据行留 30 天审计, 然后整行删。
- 每个人同时在中央暂存的总量有上限 (默认 2GB), 防一个人把中央磁盘塞满。
"""
from __future__ import annotations

import os
import secrets

from .room_link_db import AUDIT_RETENTION_DAYS, _pg_conn, use_pg  # noqa: F401  (use_pg 给 router 用)

CHUNK_BYTES = 1024 * 1024          # 明文 1MB 一块
CHUNK_MAX = CHUNK_BYTES + 64       # 加密后多 16 字节标签, 留点余量
TTL_SECONDS = 7 * 24 * 3600
MAX_RECIPIENTS = 60


def max_bytes() -> int:
    """单个文件上限, 默认 200MB (跟 nginx 上传上限一致), CATFISH_ROOM_BLOB_MAX_MB 可改。"""
    return int(float(os.environ.get("CATFISH_ROOM_BLOB_MAX_MB") or 200) * 1024 * 1024)


def quota_bytes() -> int:
    """一个人同时暂存的总量上限, 默认 2GB, CATFISH_ROOM_BLOB_QUOTA_MB 可改。"""
    return int(float(os.environ.get("CATFISH_ROOM_BLOB_QUOTA_MB") or 2048) * 1024 * 1024)


class BlobError(ValueError):
    """给 router 转 4xx 的人话。"""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status


def _sweep(cur) -> None:
    """到期的删内容; 审计期过了的整行删。"""
    cur.execute("UPDATE room_link_blobs SET purged = true WHERE NOT purged AND expires_at < now()")
    cur.execute("DELETE FROM room_link_blob_chunks c USING room_link_blobs b WHERE c.blob_id = b.id AND b.purged")
    cur.execute("DELETE FROM room_link_blobs WHERE created_at < now() - make_interval(days => %s)", (AUDIT_RETENTION_DAYS,))


def create(owner: str, recipients: list[str], size: int, n: int) -> str:
    if not 0 < size <= max_bytes():
        raise BlobError(413, f"文件太大 (单个最多 {max_bytes() // 1024 // 1024}MB)")
    # size 是密文总长: 明文每 1MB 一块, 每块加密后多 16 字节
    if n < 1 or not (n - 1) * (CHUNK_BYTES + 16) < size <= n * (CHUNK_BYTES + 16):
        raise BlobError(400, "块数跟大小对不上")
    if not recipients or len(recipients) > MAX_RECIPIENTS:
        raise BlobError(400, f"收件人要 1 到 {MAX_RECIPIENTS} 个")
    bid = "b_" + secrets.token_hex(16)
    with _pg_conn() as conn, conn.cursor() as cur:
        _sweep(cur)
        cur.execute("SELECT COALESCE(SUM(size), 0) FROM room_link_blobs WHERE owner_sub = %s AND NOT purged", (owner,))
        if int(cur.fetchone()[0]) + size > quota_bytes():
            conn.commit()
            raise BlobError(507, "你在中央暂存的文件太多了, 等同事取完 (或 7 天到期) 再发")
        cur.execute(
            """INSERT INTO room_link_blobs (id, owner_sub, recipients, size, n, expires_at)
               VALUES (%s, %s, %s, %s, %s, now() + make_interval(secs => %s))""",
            (bid, owner, recipients, size, n, TTL_SECONDS))
        conn.commit()
    return bid


def _row(cur, bid: str):
    cur.execute("SELECT owner_sub, recipients, taken_by, size, n, complete, purged, expires_at"
                " FROM room_link_blobs WHERE id = %s", (bid,))
    return cur.fetchone()


def put_chunk(owner: str, bid: str, i: int, data: bytes) -> None:
    if not 0 < len(data) <= CHUNK_MAX:
        raise BlobError(400, "这一块大小不对")
    with _pg_conn() as conn, conn.cursor() as cur:
        r = _row(cur, bid)
        if not r or r[0] != owner:
            raise BlobError(404, "没有这个文件")
        if r[5] or r[6]:
            raise BlobError(409, "这个文件已经传完 / 已删")
        if not 0 <= i < r[4]:
            raise BlobError(400, "块号不对")
        cur.execute("INSERT INTO room_link_blob_chunks (blob_id, i, data) VALUES (%s, %s, %s)"
                    " ON CONFLICT (blob_id, i) DO UPDATE SET data = excluded.data", (bid, i, data))
        conn.commit()


def finish(owner: str, bid: str) -> None:
    with _pg_conn() as conn, conn.cursor() as cur:
        r = _row(cur, bid)
        if not r or r[0] != owner:
            raise BlobError(404, "没有这个文件")
        cur.execute("SELECT COUNT(*), COALESCE(SUM(length(data)), 0) FROM room_link_blob_chunks WHERE blob_id = %s", (bid,))
        count, total = cur.fetchone()
        if int(count) != r[4]:
            raise BlobError(409, f"还差 {r[4] - int(count)} 块没传")
        if int(total) != r[3]:
            raise BlobError(409, "传上来的大小跟登记的不一样")
        cur.execute("UPDATE room_link_blobs SET complete = true WHERE id = %s", (bid,))
        conn.commit()


def info(sub: str, bid: str) -> dict:
    with _pg_conn() as conn, conn.cursor() as cur:
        r = _row(cur, bid)
    if not r or (sub != r[0] and sub not in r[1]):
        raise BlobError(404, "没有这个文件")
    return {"size": int(r[3]), "n": int(r[4]), "complete": bool(r[5]), "purged": bool(r[6]),
            "expires_at": r[7].isoformat(), "taken": len(r[2]), "recipients": len(r[1])}


def get_chunk(sub: str, bid: str, i: int) -> bytes:
    with _pg_conn() as conn, conn.cursor() as cur:
        r = _row(cur, bid)
        if not r or sub not in r[1]:
            raise BlobError(404, "没有这个文件")
        if r[6]:
            raise BlobError(410, "这个文件在中央已经删了 (过了 7 天, 或大家都取完了)")
        if not r[5]:
            raise BlobError(409, "发件人还没传完")
        cur.execute("SELECT data FROM room_link_blob_chunks WHERE blob_id = %s AND i = %s", (bid, i))
        c = cur.fetchone()
    if not c:
        raise BlobError(404, "块号不对")
    return bytes(c[0])


def taken(sub: str, bid: str) -> bool:
    """收件人取完了。返回: 是不是所有人都取完了 (那就删内容)。"""
    with _pg_conn() as conn, conn.cursor() as cur:
        r = _row(cur, bid)
        if not r or sub not in r[1]:
            raise BlobError(404, "没有这个文件")
        cur.execute("UPDATE room_link_blobs SET taken_by = array_append(taken_by, %s)"
                    " WHERE id = %s AND NOT (%s = ANY(taken_by))", (sub, bid, sub))
        cur.execute("SELECT recipients, taken_by FROM room_link_blobs WHERE id = %s", (bid,))
        recipients, taken_by = cur.fetchone()
        everyone = set(recipients) <= set(taken_by)
        if everyone:
            cur.execute("UPDATE room_link_blobs SET purged = true WHERE id = %s", (bid,))
            cur.execute("DELETE FROM room_link_blob_chunks WHERE blob_id = %s", (bid,))
        conn.commit()
    return everyone
