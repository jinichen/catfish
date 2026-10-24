"""员工外发记录每日指纹 — 真 PG 验存储层: 写进去读得回、只按 sub 给、表不在时自己建。

跑法同 test_room_link_mailbox_real_pg.py (CATFISH_TEST_PG_URL, 没设整体 skip)。
"""
from __future__ import annotations

import os

import pytest

PG_URL = os.environ.get("CATFISH_TEST_PG_URL", "").strip()

pytestmark = pytest.mark.skipif(not PG_URL, reason="没设 CATFISH_TEST_PG_URL —— 这个文件要真 PG")


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setenv("CATFISH_DB_URL", PG_URL)
    from catfish_gateway import egress_anchor_db as DB

    with DB._pg_conn() as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS egress_anchor")  # 验「没跑 alembic 也能用」
        conn.commit()
    monkeypatch.setattr(DB, "_ready", False)
    yield DB


def test_写入读回_只给自己的(db):
    h1, h2 = "ab" * 32, "cd" * 32
    out = db.add(sub="alice@x.com", last_id=10, last_hash=h1, row_count=9)
    assert out["id"] > 0 and out["received_at"]
    db.add(sub="alice@x.com", last_id=20, last_hash=h2, row_count=19)
    db.add(sub="bob@x.com", last_id=5, last_hash=h1, row_count=5)
    got = db.list_for("alice@x.com")
    assert [a["last_id"] for a in got] == [20, 10]  # 新的在前
    assert got[0]["last_hash"] == h2 and got[0]["row_count"] == 19
    assert [a["last_id"] for a in db.list_for("bob@x.com")] == [5]
