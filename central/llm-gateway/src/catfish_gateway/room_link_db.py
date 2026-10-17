"""RoomLink 邮筒存储层 (P50) — psycopg sync, 跟 advisory_db.py 同模板.

只有两个动作: 投递 / 取件。schema 见 alembic 20260910_009。

中央端**严禁**看员工端数据: 这层只接 `payload: str`, 不 json.loads,
不查内容, 不写日志。能看到的只有 from/to/kind/时间。
"""
from __future__ import annotations

import os

# 邮筒保留期: 取件窗口 10 分钟; 行 (只剩元数据) 留 30 天做审计
MAILBOX_TTL_SECONDS = 10 * 60
AUDIT_RETENTION_DAYS = 30

# 邮件种类: A→B 的请求, B→A 的授权 (请求同事帮忙, 在线握手, 10 分钟)
ROOM_LINK_KINDS = frozenset({"request", "grant"})
# 一起填表 (docs/FORM-COLLECT-PLAN.md): 邀请 / 回填 / 催填 / 结束。要跨好几天、对方可能关机,
# 所以期限由发件端按截止日给 (截止后 7 天), 这里只夹在 [10 分钟, 30 天]。
FORM_KINDS = frozenset({"form_invite", "form_response", "form_remind", "form_close"})
FORM_MAX_TTL_SECONDS = 30 * 24 * 3600
MAILBOX_KINDS = ROOM_LINK_KINDS | FORM_KINDS

# payload 上限: grant token + catalog 实测 ~3KB, 给 64KB 余量, 防人拿邮筒传文件
MAX_PAYLOAD_BYTES = 64 * 1024


def use_pg() -> bool:
    return bool(os.environ.get("CATFISH_DB_URL", "").strip())


def _pg_conn():
    import psycopg  # noqa: PLC0415

    return psycopg.connect(os.environ["CATFISH_DB_URL"])


def ttl_for(kind: str, ttl_seconds: int | None) -> int:
    """请求 / 授权固定 10 分钟; 表单类按发件端给的期限, 夹在 [10 分钟, 30 天], 没给就 30 天。"""
    if kind not in FORM_KINDS:
        return MAILBOX_TTL_SECONDS
    if ttl_seconds is None:
        return FORM_MAX_TTL_SECONDS
    return max(MAILBOX_TTL_SECONDS, min(int(ttl_seconds), FORM_MAX_TTL_SECONDS))


def deliver(*, from_sub: str, to_sub: str, kind: str, payload: str,
            ttl_seconds: int | None = None) -> int:
    """投递一封. 返 id. 顺手把过期件的 payload 清掉、把老审计行删掉.

    raise: psycopg.Error — 邮筒没库就是没法用, 不能静默吞 (投递者会以为送到了)
    """
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE room_link_mailbox SET payload = NULL
             WHERE payload IS NOT NULL AND expires_at < now()
            """
        )
        cur.execute(
            "DELETE FROM room_link_mailbox WHERE created_at < now() - make_interval(days => %s)",
            (AUDIT_RETENTION_DAYS,),
        )
        cur.execute(
            """
            INSERT INTO room_link_mailbox (from_sub, to_sub, kind, payload, expires_at)
            VALUES (%s, %s, %s, %s, now() + make_interval(secs => %s))
            RETURNING id
            """,
            (from_sub, to_sub, kind, payload, ttl_for(kind, ttl_seconds)),
        )
        row = cur.fetchone()
        conn.commit()
        return int(row[0])


# UPDATE ... RETURNING 给的是**新值** (payload 已 NULL), 所以先 SELECT 进 CTE
# 再 UPDATE, 最后从 CTE 读旧值。FOR UPDATE SKIP LOCKED: 4 个 worker 同时被同一个
# 人 poll 到, 一封邮件只能被取走一次。
_TAKE_SQL = """
WITH picked AS (
    SELECT id, from_sub, kind, payload, created_at
      FROM room_link_mailbox
     WHERE to_sub = %s AND payload IS NOT NULL AND expires_at >= now()
       AND kind = ANY(%s)
     FOR UPDATE SKIP LOCKED
), done AS (
    UPDATE room_link_mailbox m
       SET payload = NULL, consumed_at = now()
      FROM picked
     WHERE m.id = picked.id
)
SELECT id, from_sub, kind, payload, created_at FROM picked ORDER BY id
"""


def take_inbox(to_sub: str, kinds: frozenset[str] = ROOM_LINK_KINDS) -> list[dict]:
    """取走 to_sub 的指定种类未过期邮件, 同一事务里清空 payload (中央不留副本).

    只取调用方认得的种类: 老版 Companion 不带 kinds, 只拿请求 / 授权 —— 不然它会把
    认不出的填表邀请取走、解析失败丢掉, 中央又已清空, 邀请就没了。
    返 [{id, from, kind, payload, created_at}], 按投递顺序.
    """
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(_TAKE_SQL, (to_sub, sorted(kinds)))
        rows = cur.fetchall()
        conn.commit()
    return [
        {
            "id": int(r[0]),
            "from": r[1],
            "kind": r[2],
            "payload": r[3],
            "created_at": r[4].isoformat(),
        }
        for r in rows
    ]
