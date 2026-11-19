"""11/19 员工个人长期钥匙在网关这侧: cfk_ 钥匙问身份服务换 User, 降成普通员工, 不能再管钥匙。"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from catfish_gateway import app as app_mod
from catfish_gateway.auth import get_current_user, personal_keys
from catfish_gateway.auth.base import User

CLAIMS = {"email": "Me@X.com", "department": "安全部", "role": "sysadmin", "managed_departments": ["安全部"],
          "effective_allowed_models": ["catfish-auto"], "effective_allowed_tools": ["t1"], "effective_allowed_skills": ["*"]}


@pytest.fixture(autouse=True)
def _clean():
    personal_keys._clear_cache_for_testing()
    yield
    personal_keys._clear_cache_for_testing()
    app_mod.app.dependency_overrides.clear()


def _fake_identity(monkeypatch, answer):
    calls = []

    async def fake(key):
        calls.append(key)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(personal_keys, "_introspect", fake)
    return calls


def test_有效钥匙_按员工本人_但降成普通员工(monkeypatch):
    calls = _fake_identity(monkeypatch, {"active": True, "claims": CLAIMS})
    u = asyncio.run(get_current_user(authorization="Bearer cfk_abc"))
    assert u.sub == "me@x.com" and u.department == "安全部"
    assert u.role == "employee" and not u.is_admin() and u.managed_departments == []
    assert u.auth_method == "personal_key" and u.scopes == []
    assert u.effective_allowed_models == ["catfish-auto"]
    asyncio.run(get_current_user(authorization="Bearer cfk_abc"))
    assert calls == ["cfk_abc"], "一分钟内不重复问身份服务"


def test_无效_吊销_报401_身份服务不通报503(monkeypatch):
    _fake_identity(monkeypatch, {"active": False})
    with pytest.raises(HTTPException) as e:
        asyncio.run(get_current_user(authorization="Bearer cfk_bad"))
    assert e.value.status_code == 401 and "长期钥匙" in e.value.detail
    personal_keys._clear_cache_for_testing()
    _fake_identity(monkeypatch, HTTPException(status_code=503, detail="身份服务暂时连不上"))
    with pytest.raises(HTTPException) as e:
        asyncio.run(get_current_user(authorization="Bearer cfk_x"))
    assert e.value.status_code == 503


def test_普通_JWT_不走钥匙这条(monkeypatch):
    calls = _fake_identity(monkeypatch, {"active": True, "claims": CLAIMS})
    with pytest.raises(HTTPException):
        asyncio.run(get_current_user(authorization="Bearer eyJnot.a.jwt"))
    assert calls == []


def test_长期钥匙登进来的不能再管钥匙(monkeypatch):
    key_user = User(sub="me@x.com", role="employee", auth_method="personal_key")
    app_mod.app.dependency_overrides[get_current_user] = lambda: key_user
    c = TestClient(app_mod.app)
    assert c.post("/api/me/api-keys", json={}).status_code == 403
    assert c.get("/api/me/api-keys").status_code == 403
    svc = User(sub="client:hermes-cli", role="service", auth_method="oidc")
    app_mod.app.dependency_overrides[get_current_user] = lambda: svc
    assert c.post("/api/me/api-keys", json={}).status_code == 403


def test_员工本人_透传到身份服务_带上本人身份(monkeypatch):
    seen = {}

    async def fake_proxy(request, path, user):
        seen["path"], seen["sub"], seen["method"] = path, user.sub, request.method
        from fastapi.responses import JSONResponse
        return JSONResponse({"keys": []})
    monkeypatch.setattr("catfish_gateway.api_keys_routes._proxy", fake_proxy)
    app_mod.app.dependency_overrides[get_current_user] = lambda: User(sub="me@x.com", role="employee", auth_method="oidc")
    c = TestClient(app_mod.app)
    assert c.get("/api/me/api-keys").json() == {"keys": []}
    assert seen == {"path": "/internal/me/api-keys", "sub": "me@x.com", "method": "GET"}
    c.delete("/api/me/api-keys/abc123")
    assert seen["path"] == "/internal/me/api-keys/abc123" and seen["method"] == "DELETE"
