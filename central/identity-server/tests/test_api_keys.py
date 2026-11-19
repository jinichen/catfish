"""11/19 员工个人长期钥匙 (给 Claude Code 用): 员工自己生成 / 列 / 吊销; 网关拿钥匙换当前 claims。"""
from __future__ import annotations

import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from catfish_identity.api_keys import MAX_ACTIVE, PREFIX, ApiKeyStore, hash_key
from catfish_identity.routes_api_keys import make_api_keys_router
from catfish_identity.users_models import IdentityUser


class _Registry:
    def __init__(self, users):
        self.users = {u.email: u for u in users}

    def find(self, email):
        return self.users.get((email or "").strip().lower())


def _setup(tmp_path, users=None):
    users = users or [IdentityUser(email="me@x.com", password_hash="x", name="我", department="安全部", role="admin",
                                   allowed_models=["catfish-auto"])]
    reg = _Registry(users)
    store = ApiKeyStore(tmp_path / "api_keys.db")
    app = FastAPI()
    app.include_router(make_api_keys_router(reg, store))
    return TestClient(app), store, reg


H = {"X-Catfish-User-Sub": "me@x.com", "X-Catfish-User-Role": "admin"}


def test_生成_原文只给一次_库里只存哈希(tmp_path):
    c, store, _ = _setup(tmp_path)
    r = c.post("/internal/me/api-keys", headers=H, json={"name": "笔记本上的 Claude Code"})
    assert r.status_code == 201, r.text
    body = r.json()
    key = body["key"]
    assert key.startswith(PREFIX) and len(key) > 40
    assert body["name"] == "笔记本上的 Claude Code" and body["hint"].endswith(key[-4:])
    listed = c.get("/internal/me/api-keys", headers=H).json()["keys"]
    assert len(listed) == 1 and "key" not in listed[0]
    raw = sqlite3.connect(store.db_path).execute("SELECT key_hash FROM api_keys").fetchall()
    assert raw == [(hash_key(key),)]
    assert key not in (tmp_path / "api_keys.db").read_bytes().decode("latin-1")


def test_introspect_拿当前_claims_吊销_锁人后失效(tmp_path):
    c, store, reg = _setup(tmp_path)
    key = c.post("/internal/me/api-keys", headers=H, json={}).json()["key"]
    r = c.post("/internal/api-keys/introspect", json={"key": key}).json()
    assert r["active"] is True and r["claims"]["email"] == "me@x.com" and r["claims"]["department"] == "安全部"
    assert "effective_allowed_models" in r["claims"]
    assert c.post("/internal/api-keys/introspect", json={"key": key + "x"}).json() == {"active": False}
    assert c.post("/internal/api-keys/introspect", json={"key": "sk-whatever"}).json() == {"active": False}
    reg.users["me@x.com"].locked = True
    assert c.post("/internal/api-keys/introspect", json={"key": key}).json()["active"] is False
    reg.users["me@x.com"].locked = False
    kid = c.get("/internal/me/api-keys", headers=H).json()["keys"][0]["id"]
    assert c.delete(f"/internal/me/api-keys/{kid}", headers=H).status_code == 200
    assert c.post("/internal/api-keys/introspect", json={"key": key}).json()["active"] is False
    assert c.delete(f"/internal/me/api-keys/{kid}", headers=H).status_code == 404


def test_只能管自己的_不能吊销别人的(tmp_path):
    other = IdentityUser(email="zhang@x.com", password_hash="x", name="张", department="d")
    me = IdentityUser(email="me@x.com", password_hash="x", name="我", department="d")
    c, _, _ = _setup(tmp_path, [me, other])
    c.post("/internal/me/api-keys", headers={"X-Catfish-User-Sub": "zhang@x.com"}, json={})
    zid = c.get("/internal/me/api-keys", headers={"X-Catfish-User-Sub": "zhang@x.com"}).json()["keys"][0]["id"]
    assert c.get("/internal/me/api-keys", headers=H).json()["keys"] == []
    assert c.delete(f"/internal/me/api-keys/{zid}", headers=H).status_code == 404
    assert len(c.get("/internal/me/api-keys", headers={"X-Catfish-User-Sub": "zhang@x.com"}).json()["keys"]) == 1


def test_上限_服务身份不能发_不经网关不行(tmp_path):
    c, _, _ = _setup(tmp_path)
    for _ in range(MAX_ACTIVE):
        assert c.post("/internal/me/api-keys", headers=H, json={}).status_code == 201
    assert c.post("/internal/me/api-keys", headers=H, json={}).status_code == 409
    assert c.post("/internal/me/api-keys", headers={"X-Catfish-User-Sub": "client:hermes-cli", "X-Catfish-User-Role": "service"}, json={}).status_code == 403
    assert c.get("/internal/me/api-keys").status_code == 401
