"""P50 邮筒存储层 — 真 PG 验 SQL 语义 (替身验不了).

要验的三件事都是 SQL 行为:
  1. 取走即清空: 第二次 GET 空, 且库里那行 payload 已 NULL 但 from/to/kind 还在 (审计)
  2. 过期不给: expires_at 过了的邮件取不到, 下次投递时 payload 被清
  3. 只给收件人

跑法:
    CATFISH_TEST_PG_URL=postgresql://... scripts/real_pg_tests.sh
没设 CATFISH_TEST_PG_URL 整体 skip (输出里看得见).
"""
from __future__ import annotations

import os

import pytest

PG_URL = os.environ.get("CATFISH_TEST_PG_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not PG_URL,
    reason="没设 CATFISH_TEST_PG_URL —— 这个文件要真 PG",
)


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setenv("CATFISH_DB_URL", PG_URL)
    from catfish_gateway import room_link_db as DB

    with DB._pg_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM room_link_mailbox")
        conn.commit()
    yield DB


def _row(DB, msg_id):
    with DB._pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT from_sub, to_sub, kind, payload, consumed_at FROM room_link_mailbox WHERE id=%s",
            (msg_id,),
        )
        return cur.fetchone()


def test_取走即清空_行留作审计(db):
    DB = db
    mid = DB.deliver(from_sub="a@x", to_sub="b@x", kind="request", payload="secret")
    got = DB.take_inbox("b@x")
    assert [(m["id"], m["from"], m["kind"], m["payload"]) for m in got] == [
        (mid, "a@x", "request", "secret")
    ]
    assert DB.take_inbox("b@x") == []
    from_sub, to_sub, kind, payload, consumed = _row(DB, mid)
    assert (from_sub, to_sub, kind) == ("a@x", "b@x", "request")
    assert payload is None
    assert consumed is not None


def test_只给收件人(db):
    DB = db
    DB.deliver(from_sub="a@x", to_sub="b@x", kind="request", payload="for-b")
    DB.deliver(from_sub="a@x", to_sub="c@x", kind="request", payload="for-c")
    assert [m["payload"] for m in DB.take_inbox("c@x")] == ["for-c"]
    assert [m["payload"] for m in DB.take_inbox("b@x")] == ["for-b"]


def test_过期不给_且下次投递时清掉(db):
    DB = db
    mid = DB.deliver(from_sub="a@x", to_sub="b@x", kind="grant", payload="stale")
    with DB._pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE room_link_mailbox SET expires_at = now() - interval '1 second' WHERE id=%s",
            (mid,),
        )
        conn.commit()
    assert DB.take_inbox("b@x") == []
    assert _row(DB, mid)[3] == "stale", "还没被清 —— 清理挂在投递上"
    DB.deliver(from_sub="c@x", to_sub="d@x", kind="request", payload="fresh")
    assert _row(DB, mid)[3] is None


def test_投递顺序(db):
    DB = db
    for i in range(3):
        DB.deliver(from_sub="a@x", to_sub="b@x", kind="request", payload=f"m{i}")
    assert [m["payload"] for m in DB.take_inbox("b@x")] == ["m0", "m1", "m2"]
