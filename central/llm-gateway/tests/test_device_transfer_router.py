"""11/21 换机直传 —— router 层 (存储层用替身; SQL 语义见 test_device_transfer_real_pg.py)。
钉: 身份来自 JWT (小写)、编号格式不对直接 404、块内容不进日志、存储层的人话照转、PG 没配 → 503。"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

TID = "t_" + "a" * 32
RECIPIENT = "age1" + "q" * 58


@pytest.fixture()
def client(monkeypatch):
    from catfish_gateway import device_transfer_db as DB
    from catfish_gateway.app import app
    from catfish_gateway.auth import User, get_current_user

    calls: list[tuple] = []
    monkeypatch.setattr(DB, "use_pg", lambda: True)
    monkeypatch.setattr(DB, "create", lambda owner, rcpt, dev: calls.append(("create", owner, rcpt, dev)) or {"id": TID, "code": "ABCD-EFGH"})
    monkeypatch.setattr(DB, "put_chunk", lambda owner, tid, i, data: calls.append(("put", owner, tid, i, data)))
    monkeypatch.setattr(DB, "get_chunk", lambda owner, tid, i: b"\x00secret-bytes")
    monkeypatch.setattr(DB, "ack", lambda owner, tid, i: calls.append(("ack", owner, tid, i)))

    def full(owner, tid, size, n, dev):
        raise DB.TransferError(429, "中转窗口满了, 等新电脑取走一些再传")
    monkeypatch.setattr(DB, "start", full)
    app.dependency_overrides[get_current_user] = lambda: User(sub="Alice@X.com", role="employee", department="d")
    yield TestClient(app), calls, DB
    app.dependency_overrides.clear()


def test_开_传_取_收(client, caplog):
    c, calls, _ = client
    caplog.set_level(logging.INFO)
    r = c.post("/api/device-transfer", json={"recipient": RECIPIENT, "to_device": "新 MacBook"})
    assert r.status_code == 201 and r.json()["id"] == TID and r.json()["code"] == "ABCD-EFGH" and r.json()["chunk_bytes"] == 1024 * 1024
    assert calls[0] == ("create", "alice@x.com", RECIPIENT, "新 MacBook")
    assert c.put(f"/api/device-transfer/{TID}/chunks/0", content=b"\x01\x02secret").status_code == 200
    assert calls[1] == ("put", "alice@x.com", TID, 0, b"\x01\x02secret")
    got = c.get(f"/api/device-transfer/{TID}/chunks/0")
    assert got.content == b"\x00secret-bytes" and got.headers["content-type"] == "application/octet-stream"
    assert c.post(f"/api/device-transfer/{TID}/chunks/0/ack").status_code == 200 and calls[2] == ("ack", "alice@x.com", TID, 0)
    assert "secret" not in caplog.text


def test_编号不对_404_人话照转(client):
    c, _, _ = client
    assert c.get("/api/device-transfer/../../etc/chunks/0").status_code == 404
    assert c.get("/api/device-transfer/t_xyz/chunks/0").status_code == 404
    r = c.post(f"/api/device-transfer/{TID}/start", json={"size": 10, "n": 1})
    assert (r.status_code, r.json()["detail"]) == (429, "中转窗口满了, 等新电脑取走一些再传")


def test_没配_PG_503(client, monkeypatch):
    c, _, DB = client
    monkeypatch.setattr(DB, "use_pg", lambda: False)
    assert c.post("/api/device-transfer", json={"recipient": RECIPIENT}).status_code == 503


def test_配对码规整():
    from catfish_gateway import device_transfer_db as DB
    assert DB.norm_code(" abcd efgh ") == "ABCD-EFGH" and DB.norm_code("abcd-efg") == ""
    code = DB.new_code()
    assert len(code) == 9 and code[4] == "-" and all(ch in DB.CODE_ALPHABET for ch in code.replace("-", ""))
