"""roles.yaml 指着的模型必须真的存在 (8/14)。

# 病历

roles.yaml 里的值是**纯字符串**, 而:

  · `roles.py` 不 import config, `_validate_schema` 自己的注释写着
    「不能 detect 物理 name 错 (catalog 没 load), 只 catch role typo」
  · `admin_models_router.py` 在 8/14 之前**整个文件 grep 不到一个 roles 字样**
    —— 控制台删模型 / 改名 (改名 = 删 + 新建, 因为 PUT 拒绝 body.name 跟路径名
    不一致) 没有任何东西拦
  · `_ensure_loaded()` 只在 `_roles is None` 时报错, **不重读文件** ——
    改了 roles.yaml 必须重启网关

于是:

    控制台把向量模型改名
    → roles.yaml 的 embedding 还指着老名字
    → /v1/embeddings 的 _resolve_model 抛 404 model not found
    → Companion 拿到非 200 → **静默退回本机 ONNX**
    → Windows 的 msi 没编 ort (Cargo.toml 只在 aarch64 拉), 等于完全没有向量

全程零报错, 表现只是"语义搜索悄悄变差"。

# 三道防线, 这个文件各钉一道

1. 纯函数: roles_referencing / stale_model_refs 本身对不对
2. 控制台删模型时拦住 (拦在**制造 stale 的那一刻**)
3. 已经 stale 的状态要在界面上看得见 (/api/admin/models 的 role_errors)

# 为什么第 3 道不是"启动就 raise"

模型列表可能来自库。config.py:578 写明冷启动时库不可用会**降级到 models.yaml**
—— 那种时刻只在库里的模型全都"不存在"。启动 raise 等于把一次库抖动变成网关
起不来。所以启动只打 error 日志, 拦截靠第 2 道。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ── 1. 纯函数 ────────────────────────────────────────────────


@pytest.fixture()
def loaded_roles(monkeypatch):
    """直接摆一份 roles, 不碰真 yaml。"""
    from catfish_gateway import roles as R

    monkeypatch.setattr(
        R,
        "_roles",
        {
            "chat_default": "model-a",
            "embedding": "model-embed",
            "vision": "model-a",
        },
    )
    return R


def test_roles_referencing_找出所有指着它的角色(loaded_roles):
    R = loaded_roles
    # 一个模型可能被多个角色指着 —— 只报一个的话, 管理员改完一个以为好了
    assert R.roles_referencing("model-a") == ["chat_default", "vision"]
    assert R.roles_referencing("model-embed") == ["embedding"]
    assert R.roles_referencing("没人用的模型") == []


def test_stale_model_refs_只报不存在的(loaded_roles):
    R = loaded_roles
    assert R.stale_model_refs({"model-a", "model-embed"}) == {}
    assert R.stale_model_refs({"model-a"}) == {"embedding": "model-embed"}
    assert R.stale_model_refs(set()) == {
        "chat_default": "model-a",
        "embedding": "model-embed",
        "vision": "model-a",
    }


def test_roles_没_load_时不炸(monkeypatch):
    """★ 生产上 load_roles 是 hard fail (app.py:360), 到不了这里。

    但不 load roles 的单测有的是 —— 这两个函数不能因此把它们全带崩。
    """
    from catfish_gateway import roles as R

    monkeypatch.setattr(R, "_roles", None)
    assert R.roles_referencing("x") == []
    assert R.stale_model_refs({"a"}) == {}


def test_只查存在性_不查可用性(loaded_roles):
    """★★ 判据是"模型在不在列表里", 不是"它的 key 配没配"。

    可用性随 .env 变, 每个部署都不一样。混进来就会天天误报, 而误报多了
    这个信号就没人看了 —— 那还不如没有。
    """
    R = loaded_roles
    # 传进来的只是名字集合, 函数根本没有机会去看 upstream —— 这条测的是
    # **签名本身**就不给它这个机会, 而不是"这次实现里没查"。
    import inspect

    sig = inspect.signature(R.stale_model_refs)
    assert list(sig.parameters) == ["known_models"]
    assert R.stale_model_refs({"model-a", "model-embed"}) == {}


# ── 2 & 3. 控制台 ─────────────────────────────────────────────


@pytest.fixture()
def console(monkeypatch):
    """带库的 admin 控制台 + 一份 roles。返回 (client, store, roles_dict)。"""
    import logging

    logging.disable(logging.CRITICAL)
    from catfish_gateway import config as C
    from catfish_gateway import model_store as MS
    from catfish_gateway import roles as R
    from catfish_gateway.app import app
    from catfish_gateway.auth import User, get_current_user

    def _row(name: str, mode: str = "chat") -> dict:
        return {
            "name": name,
            "tier": "public",
            "display_name": name,
            "mode": mode,
            "upstream": {"model": f"openai/{name}", "api_key_env": "K"},
        }

    store: dict[str, dict] = {
        "chat-a": _row("chat-a"),
        "chat-b": _row("chat-b"),
        "embed-x": _row("embed-x", mode="embedding"),
    }
    rev = {"v": 1}
    monkeypatch.setattr(MS, "is_enabled", lambda: True)
    monkeypatch.setattr(MS, "revision", lambda: rev["v"])
    monkeypatch.setattr(MS, "read_models", lambda: list(store.values()))
    monkeypatch.setattr(MS, "upsert_model", lambda n, p, by: store.__setitem__(n, p))

    def _delete(name, by):
        return store.pop(name, None) is not None

    monkeypatch.setattr(MS, "delete_model", _delete)
    monkeypatch.setattr(MS, "set_order", lambda names, by: None)

    roles = {"chat_default": "chat-a", "embedding": "embed-x"}
    monkeypatch.setattr(R, "_roles", roles)

    C._CACHE = None
    C._CACHE_STAMP = None
    C._CACHE_CHECKED_AT = 0.0
    C._DB_EVER_SERVED = False
    monkeypatch.setenv("CATFISH_CONFIG_TTL", "0")

    async def _u() -> User:
        return User(sub="admin@x.com", role="sysadmin", department="d")

    app.dependency_overrides[get_current_user] = _u
    yield TestClient(app), store, roles
    app.dependency_overrides.clear()
    C._CACHE = None
    C._DB_EVER_SERVED = False
    logging.disable(logging.NOTSET)


def test_删掉被角色引用的模型要被拦住(console):
    """★★★ 拦在制造 stale 的那一刻。"""
    c, store, _roles = console
    r = c.delete("/api/admin/models/embed-x")
    assert r.status_code == 400, r.text
    d = r.json()["detail"]
    assert "roles.yaml" in d
    assert "embedding" in d, "要说清楚是哪个角色, 否则管理员不知道去改什么"
    assert "embed-x" in store, "拦住了却还是删了"


def test_没被角色引用的模型照常能删(console):
    """★★ 别修过头 —— 拦成"什么都删不掉"比不拦还烦。"""
    c, store, _roles = console
    assert c.delete("/api/admin/models/chat-b").status_code == 200
    assert "chat-b" not in store


def test_改完_roles_之后就能删了(console):
    """★★ 拦截必须有出路, 而且出路就是提示里说的那条。"""
    c, store, roles = console
    assert c.delete("/api/admin/models/embed-x").status_code == 400
    roles["embedding"] = "别的模型"          # 管理员照提示改了 roles.yaml
    assert c.delete("/api/admin/models/embed-x").status_code == 200
    assert "embed-x" not in store


def test_列表接口把_stale_角色报出来(console):
    """★★★ 已经 stale 的状态要在界面上看得见。

    前端按模型行渲染 config_errors (ModelConfigPage.tsx:321
    `config_errors[m.name]`), 而 stale 角色指的模型**压根不在列表里** ——
    塞进那个 dict 等于永远不显示, 所以单独一个 role_errors 字段。
    """
    c, _store, roles = console
    assert c.get("/api/admin/models").json()["role_errors"] == {}

    roles["embedding"] = "早就删掉的模型"
    body = c.get("/api/admin/models").json()
    assert body["role_errors"] == {"embedding": "早就删掉的模型"}
    # 没有混进按模型名索引的那个 dict
    assert "embedding" not in (body.get("config_errors") or {})


def test_非_sysadmin_拿不到这些(console):
    from catfish_gateway.app import app
    from catfish_gateway.auth import User, get_current_user

    c, *_ = console

    async def _emp() -> User:
        return User(sub="e@x.com", role="employee", department="d")

    app.dependency_overrides[get_current_user] = _emp
    assert c.get("/api/admin/models").status_code == 403
    assert c.delete("/api/admin/models/chat-b").status_code == 403


# ── 真配置 ───────────────────────────────────────────────────


def test_仓里的_roles_yaml_跟_models_yaml_对得上():
    """★★ 上面全是打桩的。真文件对不上的话, 这次改动第一天就在报警。"""
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[1] / "config"
    roles = (yaml.safe_load((root / "roles.yaml").read_text(encoding="utf-8")) or {}).get(
        "roles"
    ) or {}
    models = yaml.safe_load((root / "models.yaml").read_text(encoding="utf-8")) or {}
    names = {m["name"] for m in models.get("models") or []}
    assert roles, "roles.yaml 的 roles 段空了"
    bad = {r: t for r, t in roles.items() if t not in names}
    assert not bad, (
        f"roles.yaml 指向 models.yaml 里没有的模型: {bad}\n"
        f"models.yaml 现有: {sorted(names)}"
    )
