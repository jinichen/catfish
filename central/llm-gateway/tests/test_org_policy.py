"""公司级设置 (11/6): 员工能读、只有管理员能改、天数有范围。存储层用替身。"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    logging.disable(logging.CRITICAL)
    from catfish_gateway import org_policy_router as R
    from catfish_gateway.app import app
    from catfish_gateway.auth import User, get_current_user
    store: dict = {"room_retention_days": None, "updated_at": None, "updated_by": None}
    monkeypatch.setattr(R, "read_policy", lambda: dict(store))
    monkeypatch.setattr(R, "write_policy", lambda days, who: store.update(room_retention_days=days, updated_by=who, updated_at="x"))

    def as_user(sub, role):
        app.dependency_overrides[get_current_user] = lambda: User(sub=sub, role=role, department="d")
    yield TestClient(app), store, as_user
    app.dependency_overrides.clear()
    logging.disable(logging.NOTSET)


def test_员工能读_只有管理员能改(client):
    c, store, as_user = client
    as_user("zhang@x.com", "employee")
    assert c.get("/api/org/policy").json()["room_retention_days"] is None
    assert c.put("/api/admin/org/policy", json={"room_retention_days": 90}).status_code == 403
    as_user("it@x.com", "admin")
    r = c.put("/api/admin/org/policy", json={"room_retention_days": 90}).json()
    assert r["room_retention_days"] == 90 and r["updated_by"] == "it@x.com"
    as_user("zhang@x.com", "employee")
    assert c.get("/api/org/policy").json()["room_retention_days"] == 90


def test_天数范围_清空回默认(client):
    c, store, as_user = client
    as_user("it@x.com", "admin")
    assert c.put("/api/admin/org/policy", json={"room_retention_days": 3}).status_code == 422
    assert c.put("/api/admin/org/policy", json={"room_retention_days": 4000}).status_code == 422
    assert c.put("/api/admin/org/policy", json={"room_retention_days": None}).json()["room_retention_days"] is None
