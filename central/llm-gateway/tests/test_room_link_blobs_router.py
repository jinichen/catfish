"""11/07 群文件暂存 —— router 层 (存储层用替身; SQL 语义见 test_room_link_blobs_real_pg.py)。
钉: 身份来自 JWT、收件人里去掉自己、编号格式不对直接 404、块内容不进日志、PG 没配 → 503。"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    from catfish_gateway import room_link_blobs_db as DB
    from catfish_gateway.app import app
    from catfish_gateway.auth import User, get_current_user

    calls: list[tuple] = []
    monkeypatch.setattr(DB, "use_pg", lambda: True)
    monkeypatch.setattr(DB, "create", lambda owner, to, size, n: calls.append(("create", owner, to, size, n)) or "b_" + "a" * 32)
    monkeypatch.setattr(DB, "put_chunk", lambda owner, bid, i, data: calls.append(("put", owner, bid, i, data)))
    monkeypatch.setattr(DB, "get_chunk", lambda sub, bid, i: b"\x00secret-bytes")

    def boom(sub, bid):
        raise DB.BlobError(410, "已经删了")
    monkeypatch.setattr(DB, "taken", boom)
    app.dependency_overrides[get_current_user] = lambda: User(sub="Alice@X.com", role="employee", department="d")
    yield TestClient(app), calls, DB
    app.dependency_overrides.clear()


def test_建_传_取(client, caplog):
    c, calls, DB = client
    caplog.set_level(logging.INFO)
    r = c.post("/api/room-link/blobs", json={"recipients": ["bob@x.com", "alice@x.com", "BOB@x.com"], "size": 10, "n": 1})
    assert r.status_code == 201 and r.json()["id"].startswith("b_")
    assert calls[0] == ("create", "alice@x.com", ["bob@x.com"], 10, 1)
    bid = r.json()["id"]
    assert c.put(f"/api/room-link/blobs/{bid}/chunks/0", content=b"\x01\x02secret").status_code == 200
    assert calls[1] == ("put", "alice@x.com", bid, 0, b"\x01\x02secret")
    got = c.get(f"/api/room-link/blobs/{bid}/chunks/0")
    assert got.content == b"\x00secret-bytes" and got.headers["content-type"] == "application/octet-stream"
    assert "secret" not in caplog.text


def test_编号不对_404_存储层的人话照转(client):
    c, _, _ = client
    assert c.get("/api/room-link/blobs/../../etc/chunks/0").status_code == 404
    assert c.get("/api/room-link/blobs/b_xyz/chunks/0").status_code == 404
    r = c.post("/api/room-link/blobs/b_" + "a" * 32 + "/taken")
    assert (r.status_code, r.json()["detail"]) == (410, "已经删了")


def test_没配_PG_503(client, monkeypatch):
    c, _, DB = client
    monkeypatch.setattr(DB, "use_pg", lambda: False)
    assert c.post("/api/room-link/blobs", json={"recipients": ["b@x"], "size": 1, "n": 1}).status_code == 503
