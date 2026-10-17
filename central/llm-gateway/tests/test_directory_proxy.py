"""/api/directory 反代 (一起填表选人): 任何登录员工可调, 透传到 identity /admin/directory。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    from catfish_gateway import admin_proxy
    from catfish_gateway.app import app
    from catfish_gateway.auth import User, get_current_user

    seen: dict = {}

    async def _fake_proxy(request, upstream_path, user):
        from fastapi import Response
        seen.update(path=upstream_path, sub=user.sub, q=dict(request.query_params))
        return Response(content=b'{"people":[],"total":0}', media_type="application/json")

    monkeypatch.setattr(admin_proxy, "_proxy", _fake_proxy)
    app.dependency_overrides[get_current_user] = lambda: User(sub="e@x.com", role="employee", department="d")
    yield TestClient(app), seen
    app.dependency_overrides.clear()


def test_员工可调_透传到identity(client):
    c, seen = client
    r = c.get("/api/directory", params={"q": "张"})
    assert r.status_code == 200, r.text
    assert seen == {"path": "/admin/directory", "sub": "e@x.com", "q": {"q": "张"}}
