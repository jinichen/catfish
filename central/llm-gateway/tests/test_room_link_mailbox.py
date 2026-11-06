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

    def _deliver(*, from_sub, to_sub, kind, payload, ttl_seconds=None):
        box.append({"from": from_sub, "to": to_sub, "kind": kind, "payload": payload,
                    "ttl": DB.ttl_for(kind, ttl_seconds)})
        return len(box)

    def _take(to_sub, kinds=DB.ROOM_LINK_KINDS):
        mine = [m for m in box if m["to"] == to_sub and m["kind"] in kinds]
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
    monkeypatch.setattr(DB, "take_inbox", lambda to_sub, kinds=None: _boom())
    assert _post(c, to="bob@x.com", kind="request", payload="p").status_code == 503
    assert c.get("/api/room-link/mailbox").status_code == 503


def test_存储层常量(client):
    """判据独立于实现: 请求 / 授权 10 分钟取件窗口; 填表 4 种, 最长 30 天."""
    from catfish_gateway import room_link_db as DB

    assert DB.MAILBOX_TTL_SECONDS == 600
    assert DB.ROOM_LINK_KINDS == {"request", "grant"}
    assert DB.FORM_KINDS == {"form_invite", "form_response", "form_remind", "form_close"}
    assert DB.TASK_KINDS == {"task_invite", "task_reply", "task_remind", "task_cancel"}
    assert DB.ROOM_KINDS == {"room_invite", "room_join", "room_msg", "room_member", "room_leave"}
    assert DB.MAILBOX_KINDS == DB.ROOM_LINK_KINDS | DB.FORM_KINDS | DB.TASK_KINDS | DB.ROOM_KINDS
    assert DB.FORM_MAX_TTL_SECONDS == 30 * 24 * 3600


def test_填表期限_按kind夹(client):
    """表单类按发件端给的期限, 夹到 [10 分钟, 30 天]; 请求 / 授权给了也还是 10 分钟."""
    c, box, _ = client
    day = 24 * 3600
    _post(c, to="bob@x.com", kind="form_invite", payload="p", ttl_seconds=10 * day)
    _post(c, to="bob@x.com", kind="form_invite", payload="p", ttl_seconds=90 * day)
    _post(c, to="bob@x.com", kind="form_response", payload="p", ttl_seconds=5)
    _post(c, to="bob@x.com", kind="form_remind", payload="p")
    _post(c, to="bob@x.com", kind="request", payload="p", ttl_seconds=10 * day)
    _post(c, to="bob@x.com", kind="task_invite", payload="p", ttl_seconds=8 * day)
    _post(c, to="bob@x.com", kind="task_reply", payload="p")
    _post(c, to="bob@x.com", kind="room_msg", payload="p", ttl_seconds=7 * day)
    _post(c, to="bob@x.com", kind="room_invite", payload="p", ttl_seconds=60 * day)
    assert [m["ttl"] for m in box] == [10 * day, 30 * day, 600, 30 * day, 600, 8 * day, 30 * day, 7 * day, 30 * day]


def test_群聊邮件_要显式要_取走即清空(client):
    """群聊 room_* 跟任务一样: 老客户端不带 kinds 拿不到; 显式要了取走即清空, 只取要的种类."""
    c, box, as_user = client
    _post(c, to="bob@x.com", kind="room_msg", payload="hi", ttl_seconds=7 * 24 * 3600)
    _post(c, to="bob@x.com", kind="task_invite", payload="t")
    as_user("bob@x.com")
    assert c.get("/api/room-link/mailbox").json()["messages"] == []
    kinds = "room_invite,room_join,room_msg,room_member,room_leave"
    got = c.get("/api/room-link/mailbox", params={"kinds": kinds}).json()["messages"]
    assert [(m["kind"], m["payload"]) for m in got] == [("room_msg", "hi")]
    assert [m["kind"] for m in box] == ["task_invite"]


def test_不带kinds_老客户端拿不到填表邮件(client):
    """老版 Companion 不带 kinds: 只给请求 / 授权, 填表邀请留在中央等新版来取 (不被取走丢掉)."""
    c, box, as_user = client
    _post(c, to="bob@x.com", kind="form_invite", payload="invite")
    _post(c, to="bob@x.com", kind="request", payload="req")
    as_user("bob@x.com")
    assert [m["payload"] for m in c.get("/api/room-link/mailbox").json()["messages"]] == ["req"]
    assert [m["kind"] for m in box] == ["form_invite"]
    got = c.get("/api/room-link/mailbox", params={"kinds": "form_invite,form_close"}).json()["messages"]
    assert [m["payload"] for m in got] == ["invite"]
    assert box == []


def test_kinds_有不认识的_400(client):
    c, _, as_user = client
    as_user("bob@x.com")
    assert c.get("/api/room-link/mailbox", params={"kinds": "form_invite,chat"}).status_code == 400
    assert c.get("/api/room-link/mailbox", params={"kinds": " , "}).status_code == 400


# ── 11/6 公钥目录 (端到端加密) ───────────────────────────────────────

@pytest.fixture()
def keys(client, monkeypatch):
    c, box, as_user = client
    from catfish_gateway import room_link_db as DB
    store: dict[str, dict[str, str]] = {}
    monkeypatch.setattr(DB, "put_key", lambda sub, dev, pk: store.setdefault(sub, {}).__setitem__(dev, pk))
    monkeypatch.setattr(DB, "get_keys", lambda subs: {s: [{"device_id": d, "pubkey": p, "updated_at": "x"} for d, p in store[s].items()]
                                                      for s in subs if s in store})
    return c, store, as_user


PK = "A" * 43 + "="


def test_公钥只能登记自己的_按邮箱查(keys):
    c, store, as_user = keys
    as_user("Alice@x.com")
    r = c.put("/api/room-link/keys", json={"device_id": "dev_alice_01", "pubkey": PK, "sub": "mallory@x.com"})
    assert r.status_code == 200
    assert list(store) == ["alice@x.com"]                 # 身份来自 JWT, 不信 body
    as_user("bob@x.com")
    got = c.get("/api/room-link/keys", params={"subs": "ALICE@x.com, carol@x.com"}).json()["keys"]
    assert list(got) == ["alice@x.com"] and got["alice@x.com"][0]["pubkey"] == PK   # 没登记的人不出现


def test_公钥格式和查询人数(keys):
    c, _store, _ = keys
    assert c.put("/api/room-link/keys", json={"device_id": "x", "pubkey": PK}).status_code == 422
    assert c.put("/api/room-link/keys", json={"device_id": "dev_alice_01", "pubkey": "!" * 43}).status_code == 400
    many = ",".join(f"u{i}@x.com" for i in range(61))
    assert c.get("/api/room-link/keys", params={"subs": many}).status_code == 400


def test_密文放宽到128KB():
    from catfish_gateway import room_link_db as DB
    assert DB.MAX_PAYLOAD_BYTES == 128 * 1024
