"""手机中转站 (relay_router + relay_hub) — 用进程内存版登记表, 走真 WebSocket.

钉住的判据 (docs/MOBILE-CLIENT-DESIGN.md §3 / §4 / §8):
  - 第一帧必须登录; 服务账号不能连
  - 只有同一个人、配过对的手机和电脑能互发; 密文原样转, 中转站不改
  - 对方不在线立即回「不在线」, 不排队
  - 手机数量上限可设置 (CATFISH_MOBILE_MAX_DEVICES), 0 = 不开放
  - 撤销后手机发不过去
"""
from __future__ import annotations

import base64
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

PK1 = base64.b64encode(b"\x01" * 32).decode()
PK2 = base64.b64encode(b"\x02" * 32).decode()
PC = "pc-device-0001"
PHONE = "phone-device-0001"
PHONE2 = "phone-device-0002"


class _FakeProvider:
    users = {"tok-alice": "alice@x.com", "tok-bob": "bob@x.com", "tok-svc": "client:hermes-cli"}

    def verify_bearer(self, authorization):
        from catfish_gateway.auth import User

        sub = self.users.get((authorization or "")[7:])
        return User(sub=sub) if sub else None


@pytest.fixture()
def client(monkeypatch):
    logging.disable(logging.CRITICAL)
    from catfish_gateway import auth, relay_db, relay_hub, relay_router

    monkeypatch.delenv("CATFISH_MOBILE_MAX_DEVICES", raising=False)
    relay_db._set_store_for_testing(relay_db.MemoryStore())
    monkeypatch.setattr(relay_router, "hub", relay_hub.Hub())
    auth._set_provider_for_testing(_FakeProvider())
    app = FastAPI()
    app.include_router(relay_router.router)
    with TestClient(app) as c:
        c.opened = []
        yield c
        for ws in c.opened:
            try:
                ws.__exit__(None, None, None)
            except Exception:
                pass
    auth._set_provider_for_testing(None)
    relay_db._set_store_for_testing(None)
    logging.disable(logging.NOTSET)


def _connect(c, token, role, device, name="设备"):
    ws = c.websocket_connect("/relay/ws").__enter__()
    c.opened.append(ws)
    ws.send_json({"t": "auth", "token": token, "role": role, "device": device, "name": name})
    return ws


def _pair(c, phone_id=PHONE):
    pc = _connect(c, "tok-alice", "pc", PC, "办公室电脑")
    assert pc.receive_json()["t"] == "ready"
    phone = _connect(c, "tok-alice", "phone", phone_id, "iPhone")
    assert phone.receive_json()["t"] == "ready"
    phone.send_json({"t": "pair_request", "to": PC, "pk": PK1})
    req = pc.receive_json()
    assert req == {"t": "pair_request", "from": phone_id, "name": "iPhone", "pk": PK1}
    pc.send_json({"t": "pair_offer", "to": phone_id, "pk": PK2})
    assert phone.receive_json() == {"t": "pair_offer", "from": PC, "pk": PK2}
    pc.send_json({"t": "pair_result", "to": phone_id, "ok": True, "name": "iPhone"})
    assert phone.receive_json() == {"t": "pair_result", "from": PC, "ok": True}
    return pc, phone


def test_第一帧不是登录就断(client):
    with client.websocket_connect("/relay/ws") as ws:
        ws.send_json({"t": "ping"})
        assert ws.receive_json() == {"t": "error", "code": "auth_required"}


def test_令牌不对或服务账号_拒绝(client):
    for tok in ("nope", "tok-svc"):
        with client.websocket_connect("/relay/ws") as ws:
            ws.send_json({"t": "auth", "token": tok, "role": "pc", "device": PC})
            assert ws.receive_json() == {"t": "error", "code": "auth_failed"}


def test_配对后密文原样互转(client):
    pc, phone = _pair(client)
    phone.send_json({"t": "msg", "to": PC, "d": "AbCd=="})
    assert pc.receive_json() == {"t": "msg", "from": PHONE, "d": "AbCd=="}
    pc.send_json({"t": "msg", "to": PHONE, "d": "ZZZ"})
    assert phone.receive_json() == {"t": "msg", "from": PC, "d": "ZZZ"}


def test_手机能看到在线电脑和是否配过(client):
    pc, phone = _pair(client)
    phone.send_json({"t": "peers"})
    got = phone.receive_json()
    assert got["peers"] == [{"device": PC, "name": "办公室电脑", "paired": True}]
    assert got["max_phones"] == 3


def test_没配对不能发(client):
    pc = _connect(client, "tok-alice", "pc", PC)
    pc.receive_json()
    phone = _connect(client, "tok-alice", "phone", PHONE)
    phone.receive_json()
    phone.send_json({"t": "msg", "to": PC, "d": "x"})
    assert phone.receive_json()["code"] == "not_paired"


def test_别人的电脑收不到(client):
    pc = _connect(client, "tok-alice", "pc", PC)
    pc.receive_json()
    bob = _connect(client, "tok-bob", "phone", PHONE)
    bob.receive_json()
    bob.send_json({"t": "pair_request", "to": PC, "pk": PK1})
    assert bob.receive_json() == {"t": "offline", "device": PC}


def test_对方不在线立即回不在线(client):
    pc, phone = _pair(client)
    pc.close()
    phone.send_json({"t": "msg", "to": PC, "d": "x"})
    assert phone.receive_json() == {"t": "offline", "device": PC}


def test_手机数量上限可设置(client, monkeypatch):
    monkeypatch.setenv("CATFISH_MOBILE_MAX_DEVICES", "1")
    pc, _ = _pair(client)
    phone2 = _connect(client, "tok-alice", "phone", PHONE2)
    assert phone2.receive_json()["max_phones"] == 1
    phone2.send_json({"t": "pair_request", "to": PC, "pk": PK1})
    assert phone2.receive_json() == {"t": "error", "code": "phone_limit", "max_phones": 1}


def test_上限设成0_不开放手机端(client, monkeypatch):
    monkeypatch.setenv("CATFISH_MOBILE_MAX_DEVICES", "0")
    with client.websocket_connect("/relay/ws") as ws:
        ws.send_json({"t": "auth", "token": "tok-alice", "role": "phone", "device": PHONE})
        assert ws.receive_json() == {"t": "error", "code": "mobile_disabled"}


def test_撤销后手机发不过去(client):
    pc, phone = _pair(client)
    pc.send_json({"t": "revoke", "device": PHONE})
    assert phone.receive_json() == {"t": "revoked", "from": PC}
    phone.send_json({"t": "msg", "to": PC, "d": "x"})
    assert phone.receive_json()["code"] == "not_paired"


def test_电脑同步配对名单_多余的删掉(client):
    pc, phone = _pair(client)
    pc.send_json({"t": "sync_pairs", "phones": []})
    phone.send_json({"t": "ping"})  # 等上一帧处理完
    assert phone.receive_json() == {"t": "pong"}
    from catfish_gateway import relay_db

    assert relay_db.store().phones("alice@x.com") == set()


def test_手机不能冒充电脑发配对结果(client):
    pc, phone = _pair(client)
    phone.send_json({"t": "pair_result", "to": PC, "ok": True})
    assert phone.receive_json()["code"] == "unknown_type"


def test_密文过长拒绝(client):
    pc, phone = _pair(client)
    phone.send_json({"t": "msg", "to": PC, "d": "x" * 6001})
    assert phone.receive_json()["code"] == "bad_data"
