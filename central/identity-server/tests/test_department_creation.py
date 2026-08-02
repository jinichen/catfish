"""本地联调覆盖：部门创建的数据库参数、默认权限和重复校验。"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from catfish_identity import db
from catfish_identity import admin_router
from catfish_identity.departments import Department, DepartmentRegistry


class _Conn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, *args: object) -> None:
        self.calls.append((query, args))


class _Acquire:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    async def __aenter__(self) -> _Conn:
        return self.conn

    async def __aexit__(self, *args: object) -> None:
        return None


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def acquire(self) -> _Acquire:
        return _Acquire(self.conn)


@pytest.mark.asyncio
async def test_create_department_writes_scopes_and_open_defaults(monkeypatch) -> None:
    conn = _Conn()
    registry = DepartmentRegistry()
    registry._loaded = True
    async def get_pool():
        return _Pool(conn)

    monkeypatch.setattr(db, "get_pool", get_pool)

    ok, error = await registry.create(
        "finance",
        by_email="admin@example.com",
        allowed_models=["model-a"],
        description="财务部门",
    )

    assert (ok, error) == (True, "")
    query, args = conn.calls[0]
    assert "INSERT INTO departments" in query
    assert args[0] == "finance"
    assert json.loads(args[1]) == ["model-a"]
    assert json.loads(args[2]) == []
    assert json.loads(args[3]) == []
    assert args[4] == "财务部门"


@pytest.mark.asyncio
async def test_create_department_rejects_duplicate_and_invalid_name(monkeypatch) -> None:
    conn = _Conn()
    registry = DepartmentRegistry()
    registry._loaded = True
    registry._cache["finance"] = object()  # type: ignore[assignment]
    async def get_pool():
        return _Pool(conn)

    monkeypatch.setattr(db, "get_pool", get_pool)

    assert (await registry.create("finance", by_email="admin"))[0] is False
    assert (await registry.create("bad name", by_email="admin"))[0] is False
    assert conn.calls == []


def test_create_department_route_returns_created_department(monkeypatch) -> None:
    created = Department(name="finance", description="财务")

    class FakeRegistry:
        async def create(self, name: str, **kwargs):
            assert name == "finance"
            assert kwargs["description"] == "财务"
            return True, ""

        async def get(self, name: str):
            assert name == "finance"
            return created

    monkeypatch.setattr(
        "catfish_identity.departments.get_global_registry",
        lambda: FakeRegistry(),
    )
    app = FastAPI()
    app.include_router(admin_router.make_admin_router(object()))  # type: ignore[arg-type]
    app.dependency_overrides[admin_router.require_admin_or_above] = lambda: admin_router.CallerContext(
        "admin@example.com", "", "admin"
    )

    response = TestClient(app).post(
        "/admin/departments",
        json={"name": "finance", "description": "财务"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "department": created.to_dict()}
