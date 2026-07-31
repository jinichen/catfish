"""供应商管理接口的护栏 (8/1, DESIGN-PROVIDER-SPLIT §5.3 / §7).

跟 test_admin_models_api 一样, 验的**不是"能存能取"**, 而是那几道拦截 ——
它们挡的都是"当时看不出来、过一阵才炸"的情况。

沙箱没有 PG, provider_store 整个被 mock 掉。真正的 SQL 要在有库的环境验。
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    import logging

    logging.disable(logging.CRITICAL)
    from catfish_gateway import config as C
    from catfish_gateway import model_store as MS
    from catfish_gateway import provider_store as PS
    from catfish_gateway import secrets_box as SB
    from catfish_gateway.app import app
    from catfish_gateway.auth import User, get_current_user

    store: dict[str, dict] = {}
    refs: dict[str, list[str]] = {}

    monkeypatch.setattr(MS, "is_enabled", lambda: True)
    monkeypatch.setattr(PS, "is_enabled", lambda: True)
    monkeypatch.setattr(PS, "read_providers", lambda: dict(store))
    monkeypatch.setattr(PS, "models_using", lambda pid: refs.get(pid, []))

    def _upsert(pid, row, by):
        cur = store.get(pid, {})
        # 真实实现里 api_key_enc 只在 row 里显式出现时才写 —— 这里照做,
        # 否则测不出"编辑别的字段会不会把 key 清掉"
        new = {**cur, **{k: v for k, v in row.items()}, "id": pid}
        if "api_key_enc" not in row:
            new["api_key_enc"] = cur.get("api_key_enc")
        store[pid] = new

    def _delete(pid, by):
        return store.pop(pid, None) is not None

    monkeypatch.setattr(PS, "upsert_provider", _upsert)
    monkeypatch.setattr(PS, "delete_provider", _delete)

    C._CACHE = None
    C._DB_EVER_SERVED = False
    SB.reset_cache()

    def _as(role: str):
        async def _u() -> User:
            return User(sub="admin@x.com", role=role, department="d")

        return _u

    app.dependency_overrides[get_current_user] = _as("sysadmin")
    yield TestClient(app), store, refs, _as, app, get_current_user
    app.dependency_overrides.clear()
    C._CACHE = None
    SB.reset_cache()


@pytest.fixture()
def master(monkeypatch):
    from catfish_gateway import secrets_box as SB

    monkeypatch.setenv(SB.MASTER_KEY_ENV, Fernet.generate_key().decode())
    SB.reset_cache()
    yield
    SB.reset_cache()


def _body(**kw):
    return {"display_name": "测试家", "timeout": 60, **kw}


# ── key 永不回传 ────────────────────────────────────────────────────


def test_接口里不含_key_的任何字节(client, master):
    """§5.3 的核心。密文和明文都不该出这个进程。"""
    c, store, *_ = client
    SECRET = "sk-绝密-不该出现-12345"
    assert c.put("/api/admin/providers/p1", json=_body(api_key=SECRET)).status_code == 200

    r = c.get("/api/admin/providers")
    body = r.text
    assert SECRET not in body, "明文漏了"
    assert "sk-" not in body, "连前缀都不该有"
    # 密文也不能返 —— 它虽然解不开, 但拿到密文 + 主密钥就能解
    enc = store["p1"]["api_key_enc"]
    assert enc.decode("utf-8", "ignore")[:20] not in body
    assert "api_key_enc" not in body

    p = r.json()["providers"][0]
    assert p["key_source"] == "stored"
    assert p["key_ok"] is True


def test_编辑别的字段不会把_key_清掉(client, master):
    """密码字段的标准做法: 留空 = 不改。

    不这么做的话, 管理员改一次显示名就把 key 清掉了, 而且没有任何提示 ——
    要等员工调用失败才发现。
    """
    c, store, *_ = client
    c.put("/api/admin/providers/p1", json=_body(api_key="sk-原来的"))
    enc_before = store["p1"]["api_key_enc"]

    # 只改显示名, 不传 api_key
    c.put("/api/admin/providers/p1", json=_body(display_name="改了个名"))
    assert store["p1"]["api_key_enc"] == enc_before
    assert store["p1"]["display_name"] == "改了个名"


def test_传空串是显式清掉(client, master):
    """"不传"和"传空"要分开 —— 用 str | None 而不是默认空串。"""
    c, store, *_ = client
    c.put("/api/admin/providers/p1", json=_body(api_key="sk-x"))
    assert store["p1"]["api_key_enc"]
    c.put("/api/admin/providers/p1", json=_body(api_key="", api_key_env="FALLBACK_ENV"))
    assert store["p1"]["api_key_enc"] is None
    assert store["p1"]["api_key_env"] == "FALLBACK_ENV"


# ── 主密钥没配 ──────────────────────────────────────────────────────


def test_主密钥没配时拒绝存_key_并说明下一步(client, monkeypatch):
    """不能静默不存 —— 那会变成"界面显示保存成功但员工调用一直失败"。"""
    from catfish_gateway import secrets_box as SB

    monkeypatch.delenv(SB.MASTER_KEY_ENV, raising=False)
    SB.reset_cache()
    c, store, *_ = client

    r = c.put("/api/admin/providers/p1", json=_body(api_key="sk-x"))
    assert r.status_code == 400
    d = r.json()["detail"]
    assert SB.MASTER_KEY_ENV in d
    assert "密码管理器" in d, "必须提醒备份"
    assert "环境变量名" in d, "要给出在那之前能用的替代路径"
    assert "p1" not in store, "拒绝时不该留下半条记录"


def test_主密钥没配时仍能建走环境变量的供应商(client, monkeypatch):
    """迁移期两条路并存 —— 不配主密钥也要能正常干活。"""
    from catfish_gateway import secrets_box as SB

    monkeypatch.delenv(SB.MASTER_KEY_ENV, raising=False)
    SB.reset_cache()
    c, store, *_ = client
    assert c.put("/api/admin/providers/p1", json=_body(api_key_env="MY_KEY")).status_code == 200
    assert store["p1"]["api_key_env"] == "MY_KEY"


def test_列表要告诉界面主密钥配没配(client, monkeypatch):
    """没配就该在保存前说清楚, 而不是让人填完点保存才撞 400。"""
    from catfish_gateway import secrets_box as SB

    c, *_ = client
    monkeypatch.delenv(SB.MASTER_KEY_ENV, raising=False)
    SB.reset_cache()
    assert c.get("/api/admin/providers").json()["secret_key_configured"] is False

    monkeypatch.setenv(SB.MASTER_KEY_ENV, Fernet.generate_key().decode())
    SB.reset_cache()
    assert c.get("/api/admin/providers").json()["secret_key_configured"] is True


# ── key 到底能不能用 ────────────────────────────────────────────────


def test_走环境变量但变量没设时_key_ok_为假(client, monkeypatch):
    """管理员最想知道的一件事, 而它既不在库里也不在配置里 ——
    之前只能等员工调用失败才发现。"""
    c, *_ = client
    monkeypatch.delenv("NOT_SET_ENV", raising=False)
    c.put("/api/admin/providers/p1", json=_body(api_key_env="NOT_SET_ENV"))
    p = c.get("/api/admin/providers").json()["providers"][0]
    assert p["key_source"] == "env"
    assert p["key_ok"] is False

    monkeypatch.setenv("NOT_SET_ENV", "有了")
    assert c.get("/api/admin/providers").json()["providers"][0]["key_ok"] is True


def test_两条来源都没有时给出警告(client, master):
    """不拦 (可能是先建后配), 但要说清楚, 免得管理员以为配好了。"""
    c, *_ = client
    r = c.put("/api/admin/providers/p1", json=_body())
    assert r.status_code == 200
    assert r.json()["warning"] and "不可用" in r.json()["warning"]


# ── 删除保护 ────────────────────────────────────────────────────────


def test_还有模型在用时拒绝删除并点名(client, master):
    """只说"删不掉"的话, 管理员得自己一个个翻模型去找。"""
    c, store, refs, *_ = client
    c.put("/api/admin/providers/p1", json=_body())
    refs["p1"] = ["catfish-private-main", "catfish-private-vision"]

    r = c.delete("/api/admin/providers/p1")
    assert r.status_code == 400
    d = r.json()["detail"]
    assert "catfish-private-main" in d and "catfish-private-vision" in d
    assert "p1" in store, "拦截必须发生在真删之前"


def test_没有模型引用时可以删(client, master):
    c, store, *_ = client
    c.put("/api/admin/providers/p1", json=_body())
    assert c.delete("/api/admin/providers/p1").status_code == 200
    assert "p1" not in store


# ── 权限 / 输入校验 ─────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["employee", "manager", "admin"])
def test_非_sysadmin_拒绝(client, role):
    c, store, refs, as_role, app, dep = client
    app.dependency_overrides[dep] = as_role(role)
    assert c.get("/api/admin/providers").status_code == 403
    assert c.put("/api/admin/providers/p", json=_body()).status_code == 403
    assert c.delete("/api/admin/providers/p").status_code == 403


@pytest.mark.parametrize("bad", ["有中文", "Upper", "-开头", "a" * 64, "", "a b"])
def test_不合法的供应商标识要拒绝(client, master, bad):
    """它会进 URL、进模型配置、进界面下拉框 —— 三处都不该需要转义。"""
    c, store, *_ = client
    r = c.put(f"/api/admin/providers/{bad}", json=_body())
    assert r.status_code in (400, 404, 405), f"{bad!r} 不该被接受"
    assert bad not in store


# ── 表还没建 vs 真的一家都没有 (8/1 鸿波第一次打开这一页就撞上) ──────────
#
# read_providers 遇到"表不存在"返 None、遇到"表在但空"返 {}。接口层一句
# `or {}` 就把两者合并了, 界面上都显示"0 家" —— 而管理员会去点
# 「+ 新增供应商」, 然后保存失败。
#
# 两种情况的下一步动作完全不同: 跑 alembic upgrade head vs 点新增。


def test_表不存在时要说出来而不是显示零家(client, monkeypatch):
    from catfish_gateway import provider_store as PS

    c, *_ = client
    monkeypatch.setattr(PS, "read_providers", lambda: None)  # 表不存在
    r = c.get("/api/admin/providers").json()
    assert r["table_ready"] is False
    assert r["providers"] == []


def test_表在但确实没有供应商(client, monkeypatch):
    from catfish_gateway import provider_store as PS

    c, *_ = client
    monkeypatch.setattr(PS, "read_providers", lambda: {})  # 表在, 空的
    r = c.get("/api/admin/providers").json()
    assert r["table_ready"] is True, "这才是'点新增'能解决的情况"
    assert r["providers"] == []
