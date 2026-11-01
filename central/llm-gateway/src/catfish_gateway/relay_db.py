"""手机中转站的登记表 (docs/MOBILE-CLIENT-DESIGN.md §3 / §8) — 只有元数据.

两张表 (schema 见 alembic 20261101_017):

  mobile_pair      哪个员工的哪台手机配了哪台电脑 (配对关系)。数手机上限、判能不能互发都看它。
  relay_connection 谁的哪台设备什么时候连上 / 断开、转了多少字节。另兼「谁在线」:
                   ended_at 为空且 last_seen 在 60 秒内的就是在线 (多个网关进程共用这张表)。

**没有任何内容列**: 中转站转的是端到端密文, 这层连密文都不经手。
连接记录 30 天删 (跟同事邮筒同一个保留期)。

没配 CATFISH_DB_URL (开发 / 单测) 时退到进程内存 —— 只适合单进程, 重启就清空。
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

AUDIT_RETENTION_DAYS = 30
#: 设备在线的判据: 心跳 (每 20 秒) 在这个窗口内
PRESENCE_TTL_SECONDS = 60
DEFAULT_MAX_PHONES = 3


def max_phones() -> int:
    """每人最多配几台手机 (CATFISH_MOBILE_MAX_DEVICES, 默认 3; 0 = 不开放手机端)。"""
    raw = os.environ.get("CATFISH_MOBILE_MAX_DEVICES", "").strip()
    if not raw:
        return DEFAULT_MAX_PHONES
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_MAX_PHONES


@dataclass(frozen=True)
class Presence:
    device: str
    role: str
    name: str


class MemoryStore:
    """进程内存版 —— 开发和测试用。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pairs: set[tuple[str, str, str]] = set()  # (sub, phone, pc)
        self._conns: dict[int, dict] = {}
        self._next = 1

    def phones(self, sub: str) -> set[str]:
        with self._lock:
            return {p for s, p, _ in self._pairs if s == sub}

    def is_paired(self, sub: str, phone: str, pc: str) -> bool:
        with self._lock:
            return (sub, phone, pc) in self._pairs

    def pcs_of(self, sub: str, phone: str) -> set[str]:
        with self._lock:
            return {c for s, p, c in self._pairs if s == sub and p == phone}

    def add_pair(self, sub: str, phone: str, pc: str, name: str) -> None:
        with self._lock:
            self._pairs.add((sub, phone, pc))

    def remove_pair(self, sub: str, phone: str, pc: str) -> None:
        with self._lock:
            self._pairs.discard((sub, phone, pc))

    def keep_pairs(self, sub: str, pc: str, phones: set[str]) -> None:
        with self._lock:
            self._pairs = {t for t in self._pairs if not (t[0] == sub and t[2] == pc and t[1] not in phones)}

    def conn_start(self, sub: str, device: str, role: str, name: str) -> int:
        with self._lock:
            cid = self._next
            self._next += 1
            self._conns[cid] = {"sub": sub, "device": device, "role": role, "name": name,
                                "seen": time.time(), "ended": False}
            return cid

    def conn_heartbeat(self, ids: list[int]) -> None:
        with self._lock:
            for cid in ids:
                if cid in self._conns:
                    self._conns[cid]["seen"] = time.time()

    def conn_end(self, cid: int, bytes_in: int, bytes_out: int) -> None:
        with self._lock:
            self._conns.pop(cid, None)

    def online(self, sub: str) -> list[Presence]:
        cutoff = time.time() - PRESENCE_TTL_SECONDS
        with self._lock:
            seen: dict[str, Presence] = {}
            for c in self._conns.values():
                if c["sub"] == sub and c["seen"] >= cutoff:
                    seen[c["device"]] = Presence(c["device"], c["role"], c["name"])
            return list(seen.values())


class PgStore:
    """PostgreSQL 版 —— 生产, 多个网关进程共用。"""

    def __init__(self, url: str) -> None:
        self._url = url

    def _conn(self):
        import psycopg  # noqa: PLC0415

        return psycopg.connect(self._url)

    def phones(self, sub: str) -> set[str]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT DISTINCT phone_device FROM mobile_pair WHERE sub = %s", (sub,))
            return {r[0] for r in cur.fetchall()}

    def is_paired(self, sub: str, phone: str, pc: str) -> bool:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM mobile_pair WHERE sub = %s AND phone_device = %s AND pc_device = %s",
                (sub, phone, pc),
            )
            return cur.fetchone() is not None

    def pcs_of(self, sub: str, phone: str) -> set[str]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT pc_device FROM mobile_pair WHERE sub = %s AND phone_device = %s", (sub, phone))
            return {r[0] for r in cur.fetchall()}

    def add_pair(self, sub: str, phone: str, pc: str, name: str) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mobile_pair (sub, phone_device, pc_device, phone_name)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (sub, phone_device, pc_device) DO UPDATE SET phone_name = EXCLUDED.phone_name
                """,
                (sub, phone, pc, name),
            )
            conn.commit()

    def remove_pair(self, sub: str, phone: str, pc: str) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM mobile_pair WHERE sub = %s AND phone_device = %s AND pc_device = %s",
                (sub, phone, pc),
            )
            conn.commit()

    def keep_pairs(self, sub: str, pc: str, phones: set[str]) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM mobile_pair WHERE sub = %s AND pc_device = %s AND NOT (phone_device = ANY(%s))",
                (sub, pc, sorted(phones)),
            )
            conn.commit()

    def conn_start(self, sub: str, device: str, role: str, name: str) -> int:
        with self._conn() as conn, conn.cursor() as cur:
            # 顺手删 30 天前的连接记录 (同邮筒: 在写入路径上清, 不另起定时任务)
            cur.execute(
                "DELETE FROM relay_connection WHERE started_at < now() - make_interval(days => %s)",
                (AUDIT_RETENTION_DAYS,),
            )
            cur.execute(
                """
                INSERT INTO relay_connection (sub, device_id, role, device_name)
                VALUES (%s, %s, %s, %s) RETURNING id
                """,
                (sub, device, role, name),
            )
            row = cur.fetchone()
            conn.commit()
            return int(row[0])

    def conn_heartbeat(self, ids: list[int]) -> None:
        if not ids:
            return
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE relay_connection SET last_seen = now() WHERE id = ANY(%s)", (ids,))
            conn.commit()

    def conn_end(self, cid: int, bytes_in: int, bytes_out: int) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE relay_connection SET ended_at = now(), bytes_in = %s, bytes_out = %s WHERE id = %s",
                (bytes_in, bytes_out, cid),
            )
            conn.commit()

    def online(self, sub: str) -> list[Presence]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (device_id) device_id, role, device_name
                  FROM relay_connection
                 WHERE sub = %s AND ended_at IS NULL
                   AND last_seen > now() - make_interval(secs => %s)
                 ORDER BY device_id, last_seen DESC
                """,
                (sub, PRESENCE_TTL_SECONDS),
            )
            return [Presence(r[0], r[1], r[2] or "") for r in cur.fetchall()]


_store: MemoryStore | PgStore | None = None


def store() -> MemoryStore | PgStore:
    global _store
    if _store is None:
        url = os.environ.get("CATFISH_DB_URL", "").strip()
        _store = PgStore(url) if url else MemoryStore()
    return _store


def use_pg() -> bool:
    return isinstance(store(), PgStore)


def _set_store_for_testing(s: MemoryStore | PgStore | None) -> None:
    global _store
    _store = s
