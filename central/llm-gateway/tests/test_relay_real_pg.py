"""手机中转站 — 真 PG 验两件替身验不了的事:

  1. 两个网关进程 (这里用两个 Hub 模拟): 手机在 A、电脑在 B, 一帧经 NOTIFY 交过去, 原样到达
  2. 「谁在线」和配对关系的 SQL: 断开后不再算在线, 30 天前的连接记录被清

跑法:
    CATFISH_TEST_PG_URL=postgresql://... scripts/real_pg_tests.sh
没设 CATFISH_TEST_PG_URL 整体 skip。
"""
from __future__ import annotations

import asyncio
import os

import pytest

PG_URL = os.environ.get("CATFISH_TEST_PG_URL", "").strip()

pytestmark = pytest.mark.skipif(not PG_URL, reason="没设 CATFISH_TEST_PG_URL —— 这个文件要真 PG")

SUB = "relay-test@x.com"


@pytest.fixture()
def store(monkeypatch):
    monkeypatch.setenv("CATFISH_DB_URL", PG_URL)
    from catfish_gateway import relay_db

    s = relay_db.PgStore(PG_URL)
    relay_db._set_store_for_testing(s)
    with s._conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mobile_pair WHERE sub = %s", (SUB,))
        cur.execute("DELETE FROM relay_connection WHERE sub = %s", (SUB,))
        conn.commit()
    yield s
    relay_db._set_store_for_testing(None)


def _conn(device, role, inbox):
    from catfish_gateway.relay_hub import Conn

    async def send(text):
        inbox.append(text)

    async def close():
        pass

    return Conn(sub=SUB, device=device, role=role, name=device, send_text=send, close=close)


async def test_跨进程转发_原样到达(store):
    from catfish_gateway.relay_hub import Hub

    a, b = Hub(), Hub()
    pc_inbox: list[str] = []
    phone_inbox: list[str] = []
    pc = _conn("pc-real-0001", "pc", pc_inbox)
    phone = _conn("phone-real-0001", "phone", phone_inbox)
    assert await b.join(pc) is None
    assert await a.join(phone) is None
    store.add_pair(SUB, phone.device, pc.device, "iPhone")
    await asyncio.sleep(0.5)  # 等 B 的 LISTEN 就绪

    await a.handle(phone, '{"t":"msg","to":"pc-real-0001","d":"Q2lwaGVy"}')
    for _ in range(50):
        if pc_inbox:
            break
        await asyncio.sleep(0.05)
    assert pc_inbox == ['{"t":"msg","from":"phone-real-0001","d":"Q2lwaGVy"}']
    assert phone_inbox == []

    await b.leave(pc)
    await a.handle(phone, '{"t":"msg","to":"pc-real-0001","d":"x"}')
    assert phone_inbox[-1] == '{"t":"offline","device":"pc-real-0001"}'
    await a.leave(phone)
    for h in (a, b):
        for t in (h._listener, h._heartbeat):
            if t:
                t.cancel()


def test_在线和配对的SQL(store):
    cid = store.conn_start(SUB, "pc-real-0002", "pc", "电脑")
    assert [p.device for p in store.online(SUB)] == ["pc-real-0002"]
    store.conn_end(cid, 10, 20)
    assert store.online(SUB) == []

    store.add_pair(SUB, "phone-a-0001", "pc-real-0002", "A")
    store.add_pair(SUB, "phone-b-0001", "pc-real-0002", "B")
    assert store.phones(SUB) == {"phone-a-0001", "phone-b-0001"}
    store.keep_pairs(SUB, "pc-real-0002", {"phone-a-0001"})
    assert store.phones(SUB) == {"phone-a-0001"}
    store.remove_pair(SUB, "phone-a-0001", "pc-real-0002")
    assert not store.is_paired(SUB, "phone-a-0001", "pc-real-0002")

    with store._conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO relay_connection (sub, device_id, role, started_at) "
            "VALUES (%s, 'old-dev-0001', 'pc', now() - interval '31 days')", (SUB,))
        conn.commit()
    store.conn_start(SUB, "pc-real-0003", "pc", "")
    with store._conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM relay_connection WHERE sub = %s AND device_id = 'old-dev-0001'", (SUB,))
        assert cur.fetchone()[0] == 0
