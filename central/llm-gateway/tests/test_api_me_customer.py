"""10/31 /api/me 带 customer (中央 .env CATFISH_CUSTOMER, 默认空) —— Companion 据此决定加载哪份客户 SOUL。"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    logging.disable(logging.CRITICAL)
    from catfish_gateway import db
    from catfish_gateway.app import app
    from catfish_gateway.auth import User, get_current_user

    async def _no_meta(_email):
        return None

    monkeypatch.setattr(db, "fetch_user_metadata", _no_meta)
    app.dependency_overrides[get_current_user] = lambda: User(sub="alice@x.com", role="employee", department="d")
    yield TestClient(app)
    app.dependency_overrides.clear()
    logging.disable(logging.NOTSET)


def test_没配客户_返空(client, monkeypatch):
    monkeypatch.delenv("CATFISH_CUSTOMER", raising=False)
    assert client.get("/api/me").json()["customer"] == ""


def test_配了客户_返小写(client, monkeypatch):
    monkeypatch.setenv("CATFISH_CUSTOMER", " FFCS ")
    assert client.get("/api/me").json()["customer"] == "ffcs"
