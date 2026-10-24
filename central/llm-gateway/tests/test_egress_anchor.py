"""员工外发记录每日指纹 — router 层 (存储层用替身): 身份来自 JWT、只看自己的、格式校验、PG 没配 → 503。"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    logging.disable(logging.CRITICAL)
    from catfish_gateway import egress_anchor_db as DB
    from catfish_gateway.app import app
    from catfish_gateway.auth import User, get_current_user

    rows: list[dict] = []
    monkeypatch.setattr(DB, "use_pg", lambda: True)

    def _add(*, sub, last_id, last_hash, row_count):
        rows.append({"sub": sub, "last_id": last_id, "last_hash": last_hash, "row_count": row_count,
                     "received_at": "2026-10-25T00:00:00+00:00"})
        return {"id": len(rows), "received_at": "2026-10-25T00:00:00+00:00"}

    monkeypatch.setattr(DB, "add", _add)
    monkeypatch.setattr(DB, "list_for", lambda sub, limit=400: [
        {k: r[k] for k in ("last_id", "last_hash", "row_count", "received_at")} for r in rows if r["sub"] == sub])

    def as_user(sub: str):
        app.dependency_overrides[get_current_user] = lambda: User(sub=sub, role="employee", department="d")

    as_user("Alice@x.com")
    yield TestClient(app), rows, as_user, DB
    app.dependency_overrides.clear()
    logging.disable(logging.NOTSET)


def test_报指纹_身份来自JWT_只看自己的(client):
    c, rows, as_user, _ = client
    h = "ab" * 32
    r = c.post("/api/egress/anchor", json={"last_id": 42, "last_hash": h, "row_count": 40, "sub": "mallory@x.com"})
    assert r.status_code == 201, r.text
    assert rows[0]["sub"] == "alice@x.com"
    assert c.get("/api/egress/anchors").json()["anchors"][0]["last_hash"] == h
    as_user("bob@x.com")
    assert c.get("/api/egress/anchors").json()["anchors"] == []


def test_只收指纹格式(client):
    c, rows, _, _ = client
    assert c.post("/api/egress/anchor", json={"last_id": 1, "last_hash": "员工的内容"}).status_code == 422
    assert c.post("/api/egress/anchor", json={"last_id": 1, "last_hash": "AB" * 32}).status_code == 422
    assert c.post("/api/egress/anchor", json={"last_id": 0, "last_hash": "GENESIS"}).status_code == 201
    assert len(rows) == 1


def test_没PG_503(client, monkeypatch):
    c, _, _, DB = client
    monkeypatch.setattr(DB, "use_pg", lambda: False)
    assert c.post("/api/egress/anchor", json={"last_id": 1, "last_hash": "ab" * 32}).status_code == 503
