"""wiki-hub 反代: 路由形态 + 转发路径编码 + 身份注入.

9/30 之前 namespace 是 `{namespace}` 一个路径段, 客户端发的是 `dept/finance`,
这里的路由根本匹配不上 (POST 405, GET 404)。客户端 / gateway / hub 三处的 URL
形态必须一致, 这里钉 gateway 这一段。
"""
from __future__ import annotations

import json
import urllib.parse
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from catfish_gateway import wiki_hub_proxy
from catfish_gateway.auth import User, get_current_user


@pytest.fixture()
def seen():
    return []


@pytest.fixture()
def client(monkeypatch, seen):
    cfg = SimpleNamespace(
        wiki_hub=SimpleNamespace(enabled=True, upstream_url="http://hub.test", timeout=5),
    )
    monkeypatch.setattr(wiki_hub_proxy, "get_config", lambda: cfg)

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"ok": True})

    app = FastAPI()
    app.include_router(wiki_hub_proxy.router)
    app.state.wiki_hub_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app.dependency_overrides[get_current_user] = lambda: User(
        sub="alice@x", department="研发部", role="employee",
    )
    return TestClient(app)


def test_list(client, seen):
    assert client.get("/v1/wiki/documents").status_code == 200
    assert seen[-1].url.path == "/wiki/documents"
    assert urllib.parse.unquote(seen[-1].headers["X-Catfish-User-Dept"]) == "研发部"
    assert seen[-1].headers["X-Catfish-User-Sub"] == "alice@x"


def test_publish_has_no_namespace_in_path(client, seen):
    r = client.post("/v1/wiki/documents", content=json.dumps({"title": "t"}))
    assert r.status_code == 200
    assert seen[-1].method == "POST"
    assert seen[-1].url.path == "/wiki/documents"


def test_get_chinese_dept_reencoded(client, seen):
    dept = urllib.parse.quote("研发部")
    assert client.get(f"/v1/wiki/documents/dept/{dept}/abc123").status_code == 200
    assert seen[-1].url.raw_path.decode() == f"/wiki/documents/dept/{dept}/abc123"


def test_unpublish(client, seen):
    dept = urllib.parse.quote("研发部")
    r = client.post(f"/v1/wiki/documents/dept/{dept}/abc123/unpublish", content=b"{}")
    assert r.status_code == 200
    assert seen[-1].url.raw_path.decode() == f"/wiki/documents/dept/{dept}/abc123/unpublish"


def test_old_single_segment_namespace_route_gone(client, seen):
    # 老形态 POST /v1/wiki/documents/dept/finance 不该再被当成发布
    r = client.post("/v1/wiki/documents/dept/finance", content=b"{}")
    assert r.status_code in (404, 405)
    assert seen == []
