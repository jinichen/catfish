"""部门 wiki 发布 / 安装 跟 gateway 的往返 (httpx MockTransport 模拟 gateway).

钉三件事:
1. 发布 POST 到 /v1/wiki/documents (不带 namespace) —— 9/30 之前是
   /v1/wiki/documents/dept/finance, 路由匹配不上, 发布从来没成功过
2. 同一个文件重发自动带上次的 file_id —— 之前每发一次多一条重复的
3. 安装把 hub 的 updated_at 记进 .meta.json —— Companion 靠它判断"有更新"
"""
from __future__ import annotations

import json
import urllib.parse

import httpx
import pytest

from catfish_tool_bridge import wiki_install, wiki_publish


@pytest.fixture()
def gateway(monkeypatch):
    calls: list[httpx.Request] = []
    responses: list[httpx.Response] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return responses.pop(0)

    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", fake_client)
    monkeypatch.setattr(wiki_publish.skill_publish, "_read_id_token", lambda: "tok")
    return calls, responses


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(wiki_publish, "PUBLISH_STATE_PATH", tmp_path / ".catfish" / "wiki_publish_state.json")
    monkeypatch.setattr(wiki_publish, "SENSITIVE_TERMS_PATH", tmp_path / "none")
    monkeypatch.setattr(wiki_install, "WIKI_SHARED_ROOT", tmp_path / ".catfish" / "wiki-shared")
    monkeypatch.setattr(wiki_install, "WIKI_INSTALL_AUDIT", tmp_path / ".catfish" / "audit.jsonl")
    d = tmp_path / ".catfish" / "wiki" / "entities"
    d.mkdir(parents=True)
    (d / "老李.md").write_text("---\ntitle: 老李\n---\n\n正文\n", encoding="utf-8")
    return tmp_path


def _ok(file_id: str, updated_at: str) -> httpx.Response:
    return httpx.Response(200, json={
        "ok": True, "namespace": "dept/研发部", "file_id": file_id,
        "published_at": updated_at, "updated_at": updated_at, "size_bytes": 10,
    })


def test_publish_then_republish_reuses_file_id(home, gateway):
    calls, responses = gateway
    responses.append(_ok("abc123def456", "2026-09-30T01:00:00Z"))
    first = wiki_publish.wiki_publish({"wiki_rel_path": "wiki/entities/老李.md"})
    assert first["ok"] is True, first
    assert first["republished"] is False
    assert calls[0].method == "POST"
    assert calls[0].url.path == "/v1/wiki/documents"
    assert "file_id" not in json.loads(calls[0].content)

    responses.append(_ok("abc123def456", "2026-09-30T02:00:00Z"))
    second = wiki_publish.wiki_publish({"wiki_rel_path": "wiki/entities/老李.md"})
    assert second["ok"] is True
    assert second["republished"] is True
    assert json.loads(calls[1].content)["file_id"] == "abc123def456"
    assert "更新" in second["summary"]
    assert second["hub_url"].endswith(
        f"/v1/wiki/documents/dept/{urllib.parse.quote('研发部')}/abc123def456",
    )


def test_failed_publish_does_not_record_file_id(home, gateway):
    calls, responses = gateway
    responses.append(httpx.Response(400, json={"detail": "你的账号没有设置部门"}))
    r = wiki_publish.wiki_publish({"wiki_rel_path": "wiki/entities/老李.md"})
    assert r["ok"] is False
    assert "部门" in r["error"]
    assert not wiki_publish.PUBLISH_STATE_PATH.exists()


def test_install_records_hub_updated_at_and_encodes_dept(home, gateway):
    calls, responses = gateway
    responses.append(httpx.Response(200, json={
        "namespace": "dept/研发部", "file_id": "abc123def456",
        "filename": "wiki/entities/老李.md", "title": "老李", "kind": "entity",
        "frontmatter_yaml": "title: 老李", "body_md": "正文",
        "published_by": "alice@x", "published_at": "2026-09-30T01:00:00Z",
        "updated_at": "2026-09-30T02:00:00Z", "stale_after_unpublish": False,
    }))
    r = wiki_install.wiki_install({"hub_namespace": "dept/研发部", "hub_file_id": "abc123def456"})
    assert r["ok"] is True, r
    assert calls[0].url.raw_path.decode() == (
        f"/v1/wiki/documents/dept/{urllib.parse.quote('研发部')}/abc123def456"
    )
    meta_path = wiki_install.WIKI_SHARED_ROOT / "dept" / "研发部" / "abc123def456.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["hub_updated_at"] == "2026-09-30T02:00:00Z"


def test_install_forbidden_other_dept(home, gateway):
    _, responses = gateway
    responses.append(httpx.Response(403, json={"detail": "x"}))
    r = wiki_install.wiki_install({"hub_namespace": "dept/销售部", "hub_file_id": "abc123def456"})
    assert r["ok"] is False
    assert "不是你的部门" in r["error"]


@pytest.mark.parametrize("ns,ok", [
    ("dept/研发部", True),
    ("dept/finance", True),
    ("dept/", False),
    ("dept/a/b", False),
    ("dept/..", False),
    ("dept/a:b", False),
    ("public", False),
])
def test_install_namespace_validation(ns, ok):
    assert (wiki_install._validate_ns_and_id(ns, "abc123def456") is None) is ok
