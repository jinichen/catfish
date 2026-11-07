"""11/07 群里发大文件 —— 中央暂存一份加密文件, 收件人各自取。真 PG 验 SQL 语义:
只有发件人能传、只有收件人能取、传完核对块数和大小、大家都取完就删内容、到期删、每人暂存总量有上限。

跑法: CATFISH_TEST_PG_URL=postgresql://... scripts/real_pg_tests.sh (没设就整体 skip)
"""
from __future__ import annotations

import os

import pytest

PG_URL = os.environ.get("CATFISH_TEST_PG_URL", "").strip()
pytestmark = pytest.mark.skipif(not PG_URL, reason="没设 CATFISH_TEST_PG_URL —— 这个文件要真 PG")

A, B, C, X = "a@x", "b@x", "c@x", "mallory@x"


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setenv("CATFISH_DB_URL", PG_URL)
    from catfish_gateway import room_link_blobs_db as DB
    with DB._pg_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM room_link_blobs")
        conn.commit()
    return DB


def _ct(DB, plain_sizes):
    """每块明文大小 → 密文块 (多 16 字节)"""
    return [bytes([i % 251]) * (s + 16) for i, s in enumerate(plain_sizes)]


def _upload(DB, chunks, to=(B, C)):
    bid = DB.create(A, list(to), sum(len(c) for c in chunks), len(chunks))
    for i, c in enumerate(chunks):
        DB.put_chunk(A, bid, i, c)
    DB.finish(A, bid)
    return bid


def test_只有发件人能传_只有收件人能取_取完都删(db):
    DB = db
    chunks = _ct(DB, [DB.CHUNK_BYTES, DB.CHUNK_BYTES, 300])
    bid = DB.create(A, [B, C], sum(len(c) for c in chunks), 3)
    with pytest.raises(DB.BlobError) as e:
        DB.put_chunk(B, bid, 0, chunks[0])
    assert e.value.status == 404
    for i, c in enumerate(chunks[:2]):
        DB.put_chunk(A, bid, i, c)
    with pytest.raises(DB.BlobError, match="还差 1 块"):
        DB.finish(A, bid)
    with pytest.raises(DB.BlobError) as e:
        DB.get_chunk(B, bid, 0)
    assert e.value.status == 409                     # 还没传完不给取
    DB.put_chunk(A, bid, 2, chunks[2])
    DB.finish(A, bid)
    assert DB.get_chunk(B, bid, 1) == chunks[1]
    with pytest.raises(DB.BlobError) as e:
        DB.get_chunk(X, bid, 0)                      # 不是收件人
    assert e.value.status == 404
    assert DB.info(A, bid)["complete"] is True
    assert DB.taken(B, bid) is False
    assert DB.taken(B, bid) is False                 # 重复报不算两次
    assert DB.taken(C, bid) is True                  # 都取完了: 删内容, 元数据留着
    with pytest.raises(DB.BlobError) as e:
        DB.get_chunk(C, bid, 0)
    assert e.value.status == 410
    with DB._pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM room_link_blob_chunks WHERE blob_id = %s", (bid,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT owner_sub, purged FROM room_link_blobs WHERE id = %s", (bid,))
        assert cur.fetchone() == (A, True)


def test_大小块数对不上_超上限_都拒(db, monkeypatch):
    DB = db
    with pytest.raises(DB.BlobError, match="块数"):
        DB.create(A, [B], DB.CHUNK_BYTES * 3, 1)
    monkeypatch.setenv("CATFISH_ROOM_BLOB_MAX_MB", "1")
    with pytest.raises(DB.BlobError) as e:
        DB.create(A, [B], 2 * 1024 * 1024, 2)
    assert e.value.status == 413
    monkeypatch.delenv("CATFISH_ROOM_BLOB_MAX_MB")
    bid = DB.create(A, [B], 100 + 16, 1)
    DB.put_chunk(A, bid, 0, b"x" * 50)
    with pytest.raises(DB.BlobError, match="大小"):
        DB.finish(A, bid)


def test_到期删内容_每人暂存总量有上限(db, monkeypatch):
    DB = db
    bid = _upload(DB, _ct(DB, [500]))
    with DB._pg_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE room_link_blobs SET expires_at = now() - interval '1 second' WHERE id = %s", (bid,))
        conn.commit()
    monkeypatch.setenv("CATFISH_ROOM_BLOB_QUOTA_MB", "1")
    DB.create(A, [B], 900_000, 1)                    # 建新的时顺手清到期的: 到期那个不占额度
    with pytest.raises(DB.BlobError) as e:
        DB.create(A, [B], 900_000, 1)                # 再来一个超 1MB 额度
    assert e.value.status == 507
    with pytest.raises(DB.BlobError) as e:
        DB.get_chunk(B, bid, 0)
    assert e.value.status == 410
