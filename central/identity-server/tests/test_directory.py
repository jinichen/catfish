"""同事通讯录 /admin/directory (一起填表选人, docs/FORM-COLLECT-PLAN.md §5)。

判据: 任何登录员工能查全公司; 只出 邮箱 / 姓名 / 部门; 不列删除 / 锁定 / sysadmin / 自己;
没经 gateway (缺 X-Catfish-User-Sub) → 401。
"""
from __future__ import annotations

import urllib.parse

from fastapi import FastAPI
from fastapi.testclient import TestClient

from catfish_identity.admin_router import make_admin_router
from catfish_identity.users_models import IdentityUser


def _u(email, name, dept, role="employee", **kw):
    return IdentityUser(email=email, password_hash="x", name=name, department=dept, role=role, **kw)


class _Registry:
    def __init__(self, users):
        self.users = users

    async def list_users(self, *, include_deleted=False, department_filter=None, role_filter=None):
        return [u for u in self.users if include_deleted or not u.deleted_at]


def _client(users):
    app = FastAPI()
    app.include_router(make_admin_router(_Registry(users)))
    return TestClient(app)


USERS = [
    _u("me@x.com", "我", "安全部"),
    _u("zhang@x.com", "张三", "安全部"),
    _u("li@x.com", "李四", "工程部", role="manager", managed_departments=["工程部"]),
    _u("ops@x.com", "运维", "IT", role="sysadmin"),
    _u("gone@x.com", "离职", "工程部", deleted_at="2026-10-01T00:00:00"),
    _u("lock@x.com", "锁定", "工程部", locked=True),
]
H = {"X-Catfish-User-Sub": "me@x.com", "X-Catfish-User-Role": "employee", "X-Catfish-User-Dept": urllib.parse.quote("安全部")}  # gateway 对非 ASCII 做 percent-encode


def test_员工能查全公司_只出通讯录字段():
    r = _client(USERS).get("/admin/directory", headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(p["email"] for p in body["people"]) == ["li@x.com", "zhang@x.com"]
    assert body["total"] == 2
    for p in body["people"]:
        assert set(p) == {"email", "name", "department"}


def test_不列_自己_删除_锁定_sysadmin():
    emails = {p["email"] for p in _client(USERS).get("/admin/directory", headers=H).json()["people"]}
    assert emails.isdisjoint({"me@x.com", "gone@x.com", "lock@x.com", "ops@x.com"})


def test_按姓名_部门_邮箱搜():
    c = _client(USERS)
    assert [p["email"] for p in c.get("/admin/directory", params={"q": "工程"}, headers=H).json()["people"]] == ["li@x.com"]
    assert [p["email"] for p in c.get("/admin/directory", params={"q": "张"}, headers=H).json()["people"]] == ["zhang@x.com"]
    assert [p["email"] for p in c.get("/admin/directory", params={"q": "ZHANG@"}, headers=H).json()["people"]] == ["zhang@x.com"]


def test_limit_夹住():
    many = [_u(f"u{i}@x.com", f"u{i}", "d") for i in range(250)]
    c = _client(many)
    assert len(c.get("/admin/directory", params={"limit": 0}, headers=H).json()["people"]) == 1
    body = c.get("/admin/directory", params={"limit": 999}, headers=H).json()
    assert len(body["people"]) == 200 and body["total"] == 250


def test_没经gateway_401():
    assert _client(USERS).get("/admin/directory").status_code == 401
