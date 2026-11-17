"""出视频任务的记录 (11/17, alembic 20261117_023 media_jobs)。

视频要几分钟才出来: 员工端提交 → 网关向上游提交拿到上游任务号 → 记一行, 把网关自己的任务号
(v_ + 随机串) 给员工端 → 员工端轮询 → 网关查上游、更新这一行 → 成功时记一次账 (metered)。

只存元数据 (谁、哪个模型、上游任务号、状态、临时下载地址、秒数), 不存提示词和视频。
生产走 PG (跟 quota_events 同库); 单测走 CATFISH_QUOTA_DB 指的 sqlite (quota_base 那套)。
"""
from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any

from .quota_base import _get_conn, _pg_conn, _use_pg

logger = logging.getLogger("catfish.gateway.media_jobs")

#: 行留多久 (天)。上游的下载地址 24 小时就过期, 留 30 天只为审计 / 排查。
RETENTION_DAYS = 30
TERMINAL = frozenset({"succeeded", "failed"})

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS media_jobs (
    id TEXT PRIMARY KEY,
    ts_ms INTEGER NOT NULL,
    updated_ms INTEGER NOT NULL,
    user_email TEXT NOT NULL,
    department TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL,
    mode TEXT NOT NULL,
    upstream_task TEXT NOT NULL,
    status TEXT NOT NULL,
    units REAL NOT NULL DEFAULT 0,
    result_url TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    metered INTEGER NOT NULL DEFAULT 0
);
"""

_COLS = "id, ts_ms, updated_ms, user_email, department, model, mode, upstream_task, status, units, result_url, error, metered"


@dataclass
class Job:
    id: str
    ts_ms: int
    updated_ms: int
    user_email: str
    department: str
    model: str
    mode: str
    upstream_task: str
    status: str
    units: float
    result_url: str
    error: str
    metered: bool

    @classmethod
    def from_row(cls, r: Any) -> "Job":
        return cls(str(r[0]), int(r[1]), int(r[2]), str(r[3]), str(r[4] or ""), str(r[5]), str(r[6]), str(r[7]),
                   str(r[8]), float(r[9] or 0), str(r[10] or ""), str(r[11] or ""), bool(r[12]))


def _sqlite():
    conn = _get_conn()
    conn.executescript(_SQLITE_SCHEMA)
    return conn


def _now_ms() -> int:
    return int(time.time() * 1000)


def create(*, user_email: str, department: str, model: str, mode: str, upstream_task: str) -> Job:
    """记一个刚在上游提交成功的任务。顺手删掉保留期外的老行。"""
    now = _now_ms()
    job = Job("v_" + secrets.token_hex(12), now, now, user_email, department or "", model, mode,
              upstream_task, "running", 0.0, "", "", False)
    cutoff = now - RETENTION_DAYS * 86400 * 1000
    vals = (job.id, job.ts_ms, job.updated_ms, job.user_email, job.department, job.model, job.mode,
            job.upstream_task, job.status, job.units, job.result_url, job.error, job.metered)
    if _use_pg():
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM media_jobs WHERE ts_ms < %s", (cutoff,))
            cur.execute(f"INSERT INTO media_jobs ({_COLS}) VALUES ({', '.join(['%s'] * 13)})", vals)
            conn.commit()
        return job
    conn = _sqlite()
    with conn:
        conn.execute("DELETE FROM media_jobs WHERE ts_ms < ?", (cutoff,))
        conn.execute(f"INSERT INTO media_jobs ({_COLS}) VALUES ({', '.join(['?'] * 13)})",
                     vals[:-1] + (1 if job.metered else 0,))
    conn.close()
    return job


def get(job_id: str) -> Job | None:
    if _use_pg():
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {_COLS} FROM media_jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
    else:
        conn = _sqlite()
        row = conn.execute(f"SELECT {_COLS} FROM media_jobs WHERE id = ?", (job_id,)).fetchone()
        conn.close()
    return Job.from_row(row) if row else None


def update(job_id: str, *, status: str, units: float = 0, result_url: str = "", error: str = "") -> None:
    now = _now_ms()
    if _use_pg():
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE media_jobs SET status = %s, units = %s, result_url = %s, error = %s, updated_ms = %s WHERE id = %s",
                (status, float(units or 0), result_url or "", error or "", now, job_id),
            )
            conn.commit()
        return
    conn = _sqlite()
    with conn:
        conn.execute(
            "UPDATE media_jobs SET status = ?, units = ?, result_url = ?, error = ?, updated_ms = ? WHERE id = ?",
            (status, float(units or 0), result_url or "", error or "", now, job_id),
        )
    conn.close()


def claim_metering(job_id: str) -> bool:
    """成功的任务记账只记一次: 把 metered 从 false 翻成 true, 翻成了的那一次返回 True。

    员工端会反复轮询、可能并发 (多个 worker), 靠这一条带条件的 UPDATE 保证只有一个请求记账。
    """
    if _use_pg():
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE media_jobs SET metered = true WHERE id = %s AND NOT metered", (job_id,))
            n = cur.rowcount
            conn.commit()
        return n == 1
    conn = _sqlite()
    with conn:
        n = conn.execute("UPDATE media_jobs SET metered = 1 WHERE id = ? AND metered = 0", (job_id,)).rowcount
    conn.close()
    return n == 1
