"""专家包反代 (10/18): 路径透传、manager 管的部门要带过去、下载按 allowed_skills 拦。"""
from __future__ import annotations

import urllib.parse
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from catfish_gateway import skills_hub_proxy
from catfish_gateway.auth import User, get_current_user


@pytest.fixture()
def env(monkeypatch):
    seen: list[httpx.Request] = []
    cfg = SimpleNamespace(skills_hub=SimpleNamespace(enabled=True, upstream_url="http://hub.test", timeout=5))
    monkeypatch.setattr(skills_hub_proxy, "get_config", lambda: cfg)
    app = FastAPI()
    app.include_router(skills_hub_proxy.router)
    app.state.skills_hub_client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: (seen.append(req), httpx.Response(200, json={"ok": True}))[1]))

    def as_user(**kw):
        app.dependency_overrides[get_current_user] = lambda: User(**{"sub": "boss@x", "department": "安全部",
                                                                    "role": "manager", **kw})
    as_user(managed_departments=["安全部", "工程部"])
    return TestClient(app), seen, as_user


def test_审核列表带上manager管的部门(env):
    c, seen, _ = env
    assert c.get("/v1/hub/experts/review").status_code == 200
    assert seen[-1].url.path == "/experts/review"
    got = [urllib.parse.unquote(x) for x in seen[-1].headers["X-Catfish-User-Managed-Depts"].split(",")]
    assert got == ["安全部", "工程部"]


def test_审核和下载路径透传(env):
    c, seen, _ = env
    assert c.post("/v1/hub/experts/qual/1.0.0/review", json={"decision": "approve"}).status_code == 200
    assert seen[-1].url.path == "/experts/qual/1.0.0/review" and seen[-1].method == "POST"
    assert c.get("/v1/hub/experts/qual/1.0.0/download").status_code == 200
    assert seen[-1].url.path == "/experts/qual/1.0.0/download"


def test_allowed_skills不含就拦下载(env):
    c, seen, as_user = env
    as_user(role="employee", effective_allowed_skills=["personal/*"])
    n = len(seen)
    r = c.get("/v1/hub/experts/qual/1.0.0/download")
    assert r.status_code == 403 and "expert/qual" in r.json()["detail"]
    assert len(seen) == n, "拦在网关, 不往 hub 发"
    as_user(role="employee", effective_allowed_skills=["expert/*"])
    assert c.get("/v1/hub/experts/qual/1.0.0/download").status_code == 200
