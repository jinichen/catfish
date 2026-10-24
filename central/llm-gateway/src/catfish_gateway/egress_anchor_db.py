"""员工本机数据外发记录的每日指纹 (锚点) — psycopg sync, 跟 room_link_db.py 同模板.

schema 见 alembic 20261025_013 (表不在时这里也会建, 免得没跑 alembic 就 500)。
只有 sub / 序号 / sha256 / 时间, 没有任何内容。
"""
from __future__ import annotations

import os

# 锚点留一年多: 员工本机保留期最长可设 10 年, 但争议一般在一年内
RETENTION_DAYS = 400
_DDL = """
CREATE TABLE IF NOT EXISTS egress_anchor (
    id           BIGSERIAL PRIMARY KEY,
    sub          TEXT        NOT NULL,
    last_id      BIGINT      NOT NULL,
    last_hash    TEXT        NOT NULL,
    row_count    BIGINT      NOT NULL DEFAULT 0,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_egress_anchor_sub ON egress_anchor (sub, received_at DESC);
"""
_ready = False


def use_pg() -> bool:
    return bool(os.environ.get("CATFISH_DB_URL", "").strip())


def _pg_conn():
    import psycopg  # noqa: PLC0415

    return psycopg.connect(os.environ["CATFISH_DB_URL"])


def _ensure(cur) -> None:
    global _ready
    if not _ready:
        cur.execute(_DDL)
        _ready = True


def add(*, sub: str, last_id: int, last_hash: str, row_count: int) -> dict:
    """记一个锚点, 顺手删一年多以前的。返 {id, received_at}。"""
    with _pg_conn() as conn, conn.cursor() as cur:
        _ensure(cur)
        cur.execute(
            "DELETE FROM egress_anchor WHERE received_at < now() - make_interval(days => %s)",
            (RETENTION_DAYS,),
        )
        cur.execute(
            """
            INSERT INTO egress_anchor (sub, last_id, last_hash, row_count)
            VALUES (%s, %s, %s, %s) RETURNING id, received_at
            """,
            (sub, last_id, last_hash, row_count),
        )
        row = cur.fetchone()
        conn.commit()
    return {"id": int(row[0]), "received_at": row[1].isoformat()}


def list_for(sub: str, limit: int = 400) -> list[dict]:
    with _pg_conn() as conn, conn.cursor() as cur:
        _ensure(cur)
        cur.execute(
            """
            SELECT last_id, last_hash, row_count, received_at FROM egress_anchor
             WHERE sub = %s ORDER BY received_at DESC LIMIT %s
            """,
            (sub, limit),
        )
        rows = cur.fetchall()
        conn.commit()
    return [
        {"last_id": int(r[0]), "last_hash": r[1], "row_count": int(r[2]), "received_at": r[3].isoformat()}
        for r in rows
    ]
