"""P50 RoomLink 邮筒 — router 层 (存储层用替身).

存储层的 SQL 语义 (取走即清空 / 过期不给 / 只给收件人) 在
test_room_link_mailbox_real_pg.py 用真库验, 这里只钉 router 的判据:
身份来自 JWT 而不是 body、kind 白名单、payload 不落日志、PG 没配 → 503。
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    logging.disable(logging.CRITICAL)
    from catfish_gateway import room_link_db as DB
    from catfish_gateway.app import app
    from catfish_gateway.auth import User, get_current_user

    box: list[dict] = []
    monkeypatch.setattr(DB, "use_pg", lambda: True)

    def _deliver(*, from_sub, to_sub, kind, payload):
        box.append({"from": from_sub, "to": to_sub, "kind": kind, "payload": payload})
        return len(box)

    def _take(to_sub):
        mine = [m for m in box if m["to"] == to_sub]
        for m in mine:
            box.remove(m)
        return [
            {"id": i, "from": m["from"], "kind": m["kind"], "payload": m["payload"],
             "created_at": "2026-09-10T00:00:00+00:00"}
            for i, m in enumerate(mine, 1)
        ]

    monkeypatch.setattr(DB, "deliver", _deliver)
    monkeypatch.setattr(DB, "take_inbox", _take)

    def as_user(sub: str):
        app.dependency_overrides[get_current_user] = lambda: User(
            sub=sub, role="employee", department="d"
        )

    as_user("alice@x.com")
    yield TestClient(app), box, as_user
    app.dependency_overrides.clear()
    logging.disable(logging.NOTSET)


def _post(c, **body):
    return c.post("/api/room-link/mailbox", json=body)


def test_发件人来自JWT_不信body(client):
    c, box, _ = client
    r = _post(c, to="bob@x.com", kind="request", payload="opaque", **{"from": "mallory@x.com"})
    assert r.status_code == 201, r.text
    assert box[0]["from"] == "alice@x.com"
    assert box[0]["to"] == "bob@x.com"


def test_收件人只能取自己的(client):
    c, box, as_user = client
    _post(c, to="bob@x.com", kind="request", payload="for-bob")
    _post(c, to="carol@x.com", kind="request", payload="for-carol")

    as_user("bob@x.com")
    r = c.get("/api/room-link/mailbox")
    assert r.status_code == 200
    got = r.json()["messages"]
    assert [m["payload"] for m in got] == ["for-bob"]
    assert got[0]["from"] == "alice@x.com"
    assert got[0]["kind"] == "request"
    # carol 的还在
    assert [m["to"] for m in box] == ["carol@x.com"]


def test_email大小写归一化(client):
    c, box, as_user = client
    _post(c, to="Bob@X.com", kind="grant", payload="p")
    assert box[0]["to"] == "bob@x.com"
    as_user("BOB@x.com")
    assert [m["payload"] for m in c.get("/api/room-link/mailbox").json()["messages"]] == ["p"]


def test_kind白名单(client):
    c, box, _ = client
    r = _post(c, to="bob@x.com", kind="chat", payload="p")
    assert r.status_code == 400
    assert box == []


def test_不能给自己投递(client):
    c, box, _ = client
    r = _post(c, to="Alice@x.com", kind="request", payload="p")
    assert r.status_code == 400
    assert box == []


def test_payload_超限拒收(client):
    from catfish_gateway import room_link_db as DB

    c, box, _ = client
    r = _post(c, to="bob@x.com", kind="request", payload="x" * (DB.MAX_PAYLOAD_BYTES + 1))
    assert r.status_code == 422
    assert box == []


def test_payload_不进日志(client, caplog):
    """中央严禁看内容: 审计日志只有 from/to/kind, 绝不带 payload."""
    c, _, as_user = client
    logging.disable(logging.NOTSET)
    secret = "GRANT-TOKEN-SHOULD-NEVER-BE-LOGGED"
    with caplog.at_level(logging.DEBUG, logger="catfish.gateway.room_link"):
        _post(c, to="bob@x.com", kind="grant", payload=secret)
        as_user("bob@x.com")
        c.get("/api/room-link/mailbox")
    assert caplog.records, "审计日志一条都没写"
    assert secret not in caplog.text
    assert "alice@x.com" in caplog.text and "bob@x.com" in caplog.text


def test_没配PG_503(client, monkeypatch):
    from catfish_gateway import room_link_db as DB

    c, box, _ = client
    monkeypatch.setattr(DB, "use_pg", lambda: False)
    assert _post(c, to="bob@x.com", kind="request", payload="p").status_code == 503
    assert c.get("/api/room-link/mailbox").status_code == 503
    assert box == []


def test_库炸了_503_不是500(client, monkeypatch):
    from catfish_gateway import room_link_db as DB

    c, _, _ = client

    def _boom(**_):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(DB, "deliver", _boom)
    monkeypatch.setattr(DB, "take_inbox", lambda to_sub: _boom())
    assert _post(c, to="bob@x.com", kind="request", payload="p").status_code == 503
    assert c.get("/api/room-link/mailbox").status_code == 503


def test_存储层常量(client):
    """判据独立于实现: 10 分钟取件窗口, 两种 kind."""
    from catfish_gateway import room_link_db as DB

    assert DB.MAILBOX_TTL_SECONDS == 600
    assert DB.MAILBOX_KINDS == {"request", "grant"}
