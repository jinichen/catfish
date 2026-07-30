"""模型配置管理接口的护栏 (7/30).

这里验的**不是"能存能取"**, 而是那几道拦截 —— 它们存在的意义是不让一次
误操作把服务打瘫, 而且每一条挡的都是"当时看不出来、过一阵才炸"的情况。

沙箱没有 PG, 所以 model_store 整个被 mock 掉。真正的 SQL 要在有库的环境验。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    import logging

    logging.disable(logging.CRITICAL)
    from catfish_gateway import config as C
    from catfish_gateway import model_store as MS
    from catfish_gateway.app import app
    from catfish_gateway.auth import User, get_current_user

    # 一个内存版的 store, 免得碰真库
    store: dict[str, dict] = {}
    rev = {"v": 1}

    monkeypatch.setattr(MS, "is_enabled", lambda: True)
    monkeypatch.setattr(MS, "revision", lambda: rev["v"])
    monkeypatch.setattr(MS, "read_models", lambda: list(store.values()))

    def _upsert(name, payload, by):
        store[name] = payload
        rev["v"] += 1

    def _delete(name, by):
        existed = name in store
        store.pop(name, None)
        if existed:
            rev["v"] += 1
        return existed

    monkeypatch.setattr(MS, "upsert_model", _upsert)
    monkeypatch.setattr(MS, "delete_model", _delete)
    monkeypatch.setattr(MS, "set_order", lambda names, by: None)

    C._CACHE = None
    C._CACHE_STAMP = None
    C._CACHE_CHECKED_AT = 0.0
    C._DB_EVER_SERVED = False
    monkeypatch.setenv("CATFISH_CONFIG_TTL", "0")

    def _as(role: str):
        async def _u() -> User:
            return User(sub="admin@x.com", role=role, department="d")

        return _u

    app.dependency_overrides[get_current_user] = _as("sysadmin")
    yield TestClient(app), store, _as, app, get_current_user
    app.dependency_overrides.clear()
    C._CACHE = None
    C._DB_EVER_SERVED = False


def _model(name: str, **kw) -> dict:
    d = {
        "name": name,
        "tier": "public",
        "display_name": name,
        "upstream": {"model": "openai/x", "api_key_env": "K"},
    }
    d.update(kw)
    return d


def test_非_sysadmin_拒绝(client):
    c, store, as_role, app, dep = client
    app.dependency_overrides[dep] = as_role("employee")
    assert c.get("/api/admin/models").status_code == 403
    assert c.put("/api/admin/models/m", json=_model("m")).status_code == 403


def test_库没启用时明确拒绝而不是假装成功(client, monkeypatch):
    """静默返回 ok 的话, 界面会显示"保存成功"但什么都没发生 —— 那比报错难查."""
    from catfish_gateway import model_store as MS

    c, *_ = client
    monkeypatch.setattr(MS, "is_enabled", lambda: False)
    r = c.put("/api/admin/models/m", json=_model("m"))
    assert r.status_code == 503
    assert "未启用" in r.json()["detail"]


def test_路径名和_body_名不一致要拒绝(client):
    """不能"以路径为准"地悄悄改掉 —— 那会造成"我明明改 A 却动了 B"."""
    c, *_ = client
    r = c.put("/api/admin/models/aaa", json=_model("bbb"))
    assert r.status_code == 400
    assert "不一致" in r.json()["detail"]


def test_不合法的配置要拒绝(client):
    """放进库的必须是 gateway 能正常加载的 —— 否则下次重载配置整个起不来."""
    c, *_ = client
    r = c.put("/api/admin/models/m", json={"display_name": "缺 upstream"})
    assert r.status_code == 400


def test_新增然后能读到(client):
    c, store, *_ = client
    assert c.put("/api/admin/models/m1", json=_model("m1")).json()["created"] is True
    names = [m["name"] for m in c.get("/api/admin/models").json()["models"]]
    assert "m1" in names


def test_设新默认会清掉旧默认(client):
    """两个 default 时 default_model() 返回的是"列表第一个", 取决于排序,
    员工下次开聊用哪个模型不可预测."""
    c, store, *_ = client
    c.put("/api/admin/models/m1", json=_model("m1", default=True))
    c.put("/api/admin/models/m2", json=_model("m2", default=True))
    defaults = [m["name"] for m in c.get("/api/admin/models").json()["models"] if m["default"]]
    assert defaults == ["m2"], f"应只剩一个默认, 实际 {defaults}"


def test_不许删到一个不剩(client):
    c, store, *_ = client
    c.put("/api/admin/models/only", json=_model("only"))
    r = c.delete("/api/admin/models/only")
    assert r.status_code == 400
    assert "最后一个" in r.json()["detail"]


def test_不许删掉还被_fallback_链引用的(client):
    """fallback 只在上游出错时才走, 平时看不出来 —— 所以必须在删的时候拦."""
    c, store, *_ = client
    c.put("/api/admin/models/target", json=_model("target"))
    c.put(
        "/api/admin/models/main",
        json=_model("main", fallback={"chain": ["target"]}),
    )
    r = c.delete("/api/admin/models/target")
    assert r.status_code == 400
    assert "fallback" in r.json()["detail"]
    assert "main" in r.json()["detail"]


def test_删掉默认模型会自动指定新默认(client):
    """否则 default_model() 退化成"列表第一个", 取决于排序."""
    c, store, *_ = client
    c.put("/api/admin/models/m1", json=_model("m1", default=True))
    c.put("/api/admin/models/m2", json=_model("m2"))
    r = c.delete("/api/admin/models/m1")
    assert r.status_code == 200
    assert r.json()["promoted_default"] == "m2"
    defaults = [m["name"] for m in c.get("/api/admin/models").json()["models"] if m["default"]]
    assert defaults == ["m2"]


def test_删不存在的是幂等的(client):
    c, store, *_ = client
    c.put("/api/admin/models/m1", json=_model("m1"))
    c.put("/api/admin/models/m2", json=_model("m2"))
    r = c.delete("/api/admin/models/不存在")
    assert r.status_code == 200
    assert r.json()["deleted"] is False


def test_重排路径不会被当成模型名(client):
    """/api/admin/models/_order 会被 {name} 抢先匹配, 所以路径特意放在
    /api/admin/model-order。这条测试钉住这个约定。"""
    c, *_ = client
    r = c.put("/api/admin/model-order", json={"names": ["m1"]})
    assert r.status_code == 200
    assert r.json()["ok"] is True
