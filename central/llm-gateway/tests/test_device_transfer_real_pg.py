"""11/21 换机直传 —— 真 PG 验 SQL 语义: 只认本人、中央同时只留一个窗口的块、按顺序收、收一块删一块、
收齐 / 取消 / 半小时没动静都删干净、一个人同时只有一次直传。

跑法: CATFISH_TEST_PG_URL=postgresql://... scripts/real_pg_tests.sh (没设就整体 skip)
"""
from __future__ import annotations

import os

import pytest

PG_URL = os.environ.get("CATFISH_TEST_PG_URL", "").strip()
pytestmark = pytest.mark.skipif(not PG_URL, reason="没设 CATFISH_TEST_PG_URL —— 这个文件要真 PG")

A, X = "a@x", "mallory@x"
RCPT = "age1" + "q" * 58
MB = 1024 * 1024


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setenv("CATFISH_DB_URL", PG_URL)
    monkeypatch.setenv("CATFISH_DEVICE_TRANSFER_WINDOW_MB", "2")
    from catfish_gateway import device_transfer_db as DB
    with DB._pg_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM device_transfer")
        conn.commit()
    return DB


def _chunks(size):
    out, left, i = [], size, 0
    while left > 0:
        n = min(MB, left)
        out.append(bytes([i % 251]) * n)
        left -= n
        i += 1
    return out


def _buffered(DB, tid):
    with DB._pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM device_transfer_chunks WHERE transfer_id = %s", (tid,))
        return int(cur.fetchone()[0])


def test_扫码_边传边取_收齐删干净(db):
    t = db.create(A, RCPT, "新 Mac")
    assert db.lookup(A, t["code"].replace("-", "").lower())["id"] == t["id"]
    with pytest.raises(db.TransferError) as e:
        db.lookup(X, t["code"])                       # 别人拿到配对码也找不到
    assert e.value.status == 404
    with pytest.raises(db.TransferError):
        db.info(X, t["id"])
    parts = _chunks(3 * MB + 100)
    db.start(A, t["id"], 3 * MB + 100, 4, "旧 Mac")
    db.put_chunk(A, t["id"], 0, parts[0])
    db.put_chunk(A, t["id"], 1, parts[1])
    with pytest.raises(db.TransferError) as e:
        db.put_chunk(A, t["id"], 2, parts[2])          # 窗口 2MB 满了
    assert e.value.status == 429
    with pytest.raises(db.TransferError) as e:
        db.ack(A, t["id"], 1)                          # 要按顺序收
    assert e.value.status == 409
    assert db.get_chunk(A, t["id"], 0) == parts[0]
    db.ack(A, t["id"], 0)
    db.put_chunk(A, t["id"], 2, parts[2])              # 取走一块, 又能传了
    db.put_chunk(A, t["id"], 0, parts[0])              # 重传已收过的: 不再存
    assert _buffered(db, t["id"]) == 2
    for i in (1, 2):
        assert db.get_chunk(A, t["id"], i) == parts[i]
        db.ack(A, t["id"], i)
    with pytest.raises(db.TransferError) as e:
        db.put_chunk(A, t["id"], 3, b"x" * 99)         # 最后一块大小不对
    assert e.value.status == 400
    db.put_chunk(A, t["id"], 3, parts[3])
    with pytest.raises(db.TransferError):
        db.done(A, t["id"])                            # 还差一块
    db.ack(A, t["id"], 3)
    db.done(A, t["id"])
    info = db.info(A, t["id"])
    assert (info["state"], info["acked"], info["relayed"], _buffered(db, t["id"])) == ("done", 4, 3 * MB + 100, 0)


def test_块数对不上_新开一次旧的作废(db):
    t1 = db.create(A, RCPT, "")
    with pytest.raises(db.TransferError):
        db.start(A, t1["id"], 2 * MB, 3, "")
    db.start(A, t1["id"], 2 * MB, 2, "")
    db.put_chunk(A, t1["id"], 0, b"a" * MB)
    t2 = db.create(A, RCPT, "")
    assert db.info(A, t1["id"])["state"] == "cancelled" and _buffered(db, t1["id"]) == 0
    with pytest.raises(db.TransferError) as e:
        db.put_chunk(A, t1["id"], 1, b"a" * MB)
    assert e.value.status == 410
    db.cancel(A, t2["id"])
    with pytest.raises(db.TransferError):
        db.lookup(A, t2["code"])


def test_半小时没动静_作废删块(db):
    t = db.create(A, RCPT, "")
    db.start(A, t["id"], MB, 1, "")
    db.put_chunk(A, t["id"], 0, b"z" * MB)
    with db._pg_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE device_transfer SET last_at = now() - interval '31 minutes' WHERE id = %s", (t["id"],))
        conn.commit()
    assert db.info(A, t["id"])["state"] == "expired"
    assert _buffered(db, t["id"]) == 0
    with pytest.raises(db.TransferError) as e:
        db.get_chunk(A, t["id"], 0)
    assert e.value.status == 410


def test_公钥格式不对拒绝(db):
    with pytest.raises(db.TransferError):
        db.create(A, "ssh-rsa AAAA", "")
