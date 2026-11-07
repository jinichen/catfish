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
    store: dict = {"room_retention_days": None, "org_mail_domains": [], "updated_at": None, "updated_by": None}
    monkeypatch.setattr(R, "read_policy", lambda: dict(store))

    def write(changes, who):
        for k, v in changes.items():
            store[k] = [] if k == "org_mail_domains" and v is None else v
        store.update(updated_by=who, updated_at="x")
    monkeypatch.setattr(R, "write_policy", write)

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


def test_本单位邮箱域名_规范化_去重_只改传了的那项(client):
    c, store, as_user = client
    as_user("it@x.com", "admin")
    c.put("/api/admin/org/policy", json={"room_retention_days": 90})
    r = c.put("/api/admin/org/policy", json={"org_mail_domains": [" @ChinaTelecom.cn ", "ffcs.cn", "chinatelecom.cn", ""]}).json()
    assert r["org_mail_domains"] == ["chinatelecom.cn", "ffcs.cn"]
    assert r["room_retention_days"] == 90   # 没传的不动
    as_user("zhang@x.com", "employee")
    assert c.get("/api/org/policy").json()["org_mail_domains"] == ["chinatelecom.cn", "ffcs.cn"]
    assert c.put("/api/admin/org/policy", json={"org_mail_domains": ["x.cn"]}).status_code == 403


def test_本单位邮箱域名_公共邮箱和乱填的拒绝(client):
    c, store, as_user = client
    as_user("it@x.com", "admin")
    r = c.put("/api/admin/org/policy", json={"org_mail_domains": ["chinatelecom.cn", "qq.com"]})
    assert r.status_code == 422 and r.json()["detail"] == "qq.com 是公共邮箱, 谁都能注册, 不能当本单位域名"
    r = c.put("/api/admin/org/policy", json={"org_mail_domains": ["不是域名"]})
    assert r.status_code == 422 and "不像邮箱域名" in r.text
    assert c.put("/api/admin/org/policy", json={}).status_code == 422
    assert c.put("/api/admin/org/policy", json={"org_mail_domains": []}).json()["org_mail_domains"] == []
