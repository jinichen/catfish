"""向量模型的真源只在中央 (8/14)。

# 病历

Companion 的 `services/embedding_config.rs` 自己存了一份向量模型名:

    fn default_remote_model() -> String { "catfish-private-embed".to_string() }

sysadmin 在控制台 /admin/models 把向量模型换掉之后, 员工端还按老名字请求 →
`_resolve_model` 404 → Companion 拿到非 200 → **静默退回本机 ONNX**。

不报错。只是悄悄换了个模型、换了个维度, 现场看不出来 —— 而且 Windows 的 msi
根本没编进 ort (Cargo.toml 只在 aarch64 拉 ort), 连本机 ONNX 都没有, 那边直接
就是没有向量。

# 修法

向量模型是**管道类**, 员工不选也选不了 (catalog.py:48 把 mode=embedding 从
/v1/catalog 里摘掉了)。所以真源只能在中央: roles.yaml 的 `embedding` 角色。
让 /v1/embeddings 的 model 可省, 员工端一份副本都不用存。

# 这个文件钉什么

1. 省略 model → 走 roles.yaml 的 embedding 角色 (换模型全网跟着走)
2. 传了 model → 还是用传的 (向后兼容, 老客户端不能被这次改动打死)
3. 解析出来的名字要真的用到 —— 进 litellm 的 upstream、进用量日志、进配额
4. roles.yaml 没配 embedding → 400 且话说清楚, 不能 500 也不能静默用别的模型
5. 解析到的模型必须仍然是 mode=embedding —— roles.yaml 被人指到一个对话模型
   上时要当场拒绝, 不能把对话模型当向量模型调
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


# upstream.api_key / is_available 都读这个 env（config.py:117/133），fixture 里设上
_KEY_ENV = "CATFISH_TEST_EMBED_KEY"

EMBED_MODEL = "catfish-private-embed"
OTHER_EMBED_MODEL = "customer-x-embed"


def _model(name: str, *, mode: str = "embedding"):
    """造一个真的 ModelConfig, **不用 SimpleNamespace duck-type**。

    第一版是 duck-type, 结果撞在 `model.upstream.param_overrides` 上 ——
    handler 读的字段比我猜的多。duck-type 的问题不是这次少写了一个字段,
    是它**永远落后于真类型**: 以后 ModelConfig 加字段, 假对象不会跟着长,
    测试会因为 AttributeError 变红 (还算好的) 或者更糟, 悄悄测了个不存在的形状。

    用真类型就没这问题, pydantic 的默认值自己会跟上。
    """
    from catfish_gateway.config import ModelConfig

    return ModelConfig(
        name=name,
        display_name=name,
        mode=mode,
        upstream={"model": f"openai/{name}-upstream", "api_key_env": _KEY_ENV},
    )


@pytest.fixture()
def harness(monkeypatch):
    """TestClient + 打桩的 config / roles / litellm。

    返回 (client, roles_map, calls)。
    · roles_map 可以就地改, 模拟 sysadmin 在控制台换向量模型
    · calls 记录真正发给 litellm 的参数 + 记账用的 model 名
    """
    import logging

    logging.disable(logging.CRITICAL)
    monkeypatch.setenv(_KEY_ENV, "test-key")

    from catfish_gateway import app as A
    from catfish_gateway import roles as R
    from catfish_gateway.app import app
    # 8/15: /v1/embeddings 搬到了 misc_routes.py。
    #
    # 下面四处 patch 里要分清两种:
    #   · `A.litellm.aembedding` / `A._quota_module.record_usage`
    #     —— 打在**模块对象的属性**上, litellm / quota 两边引的是同一个模块,
    #        所以打 A 上照样生效, 不用改。
    #   · `get_config` / `log_request_metadata`
    #     —— 是**名字绑定**。misc_routes 有自己那一份, 打 A 上它看不见
    #        (`from X import name` 建新绑定不是别名)。必须打到 M 上。
    from catfish_gateway import misc_routes as M
    from catfish_gateway.auth import User, get_current_user

    models = [_model(EMBED_MODEL), _model(OTHER_EMBED_MODEL), _model("chat-model", mode="chat")]
    by_name = {m.name: m for m in models}
    cfg = SimpleNamespace(models=models, get_model=by_name.get)
    monkeypatch.setattr(M, "get_config", lambda: cfg)

    # roles.yaml 的内容 —— 就地改它 = 在控制台换向量模型
    roles_map = {"embedding": EMBED_MODEL}
    monkeypatch.setattr(R, "resolve_or_none", lambda role: roles_map.get(
        role.value if hasattr(role, "value") else str(role)
    ))

    calls: dict[str, object] = {}

    async def _fake_aembedding(**params):
        calls["params"] = params
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=7),
            model_dump=lambda: {"data": [{"embedding": [0.1, 0.2]}]},
        )

    monkeypatch.setattr(A.litellm, "aembedding", _fake_aembedding)
    monkeypatch.setattr(
        M, "log_request_metadata", lambda **kw: calls.__setitem__("logged", kw)
    )
    monkeypatch.setattr(
        A._quota_module, "record_usage", lambda **kw: calls.__setitem__("quota", kw)
    )

    async def _user() -> User:
        return User(sub="e@x.com", role="employee", department="d")

    app.dependency_overrides[get_current_user] = _user
    yield TestClient(app), roles_map, calls
    app.dependency_overrides.clear()
    logging.disable(logging.NOTSET)


# ── 核心 ────────────────────────────────────────────────────


def test_省略_model_走_roles_yaml_的_embedding_角色(harness):
    """★★★ 员工端不用存模型名。"""
    c, _roles, calls = harness
    r = c.post("/v1/embeddings", json={"input": "你好"})
    assert r.status_code == 200, r.text
    assert calls["params"]["model"] == f"openai/{EMBED_MODEL}-upstream"


def test_控制台换了向量模型_下一次请求就跟着走(harness):
    """★★★ 这就是这次改动的全部意义。

    员工端存副本的话, 这里换完之后它还在按老名字请求 → 404 → 静默退回本机 ONNX。
    """
    c, roles, calls = harness
    roles["embedding"] = OTHER_EMBED_MODEL          # sysadmin 在 /admin/models 改
    r = c.post("/v1/embeddings", json={"input": "x"})
    assert r.status_code == 200, r.text
    assert calls["params"]["model"] == f"openai/{OTHER_EMBED_MODEL}-upstream"


def test_显式传_model_仍然优先(harness):
    """★★ 向后兼容 —— 老客户端 (以及 yaml 里显式 override 的员工) 不能被打死。"""
    c, roles, calls = harness
    roles["embedding"] = OTHER_EMBED_MODEL
    r = c.post("/v1/embeddings", json={"model": EMBED_MODEL, "input": "x"})
    assert r.status_code == 200, r.text
    assert calls["params"]["model"] == f"openai/{EMBED_MODEL}-upstream"


def test_解析出来的名字要进用量日志和配额(harness):
    """★★ 否则审计和配额里会留下一个空 model, 或者老名字。

    这条不是凑数: model_name 在 handler 里既用来查表又用来记账, 只改查表那处
    就会出现"调的是新模型、账记在老名字上"。
    """
    c, roles, calls = harness
    roles["embedding"] = OTHER_EMBED_MODEL
    c.post("/v1/embeddings", json={"input": "x"})
    assert calls["logged"]["model"] == OTHER_EMBED_MODEL
    assert calls["quota"]["model"] == OTHER_EMBED_MODEL


# ── 失败面 ──────────────────────────────────────────────────


def test_roles_没配_embedding_时_400_且说得清楚(harness):
    """★★ 不能 500, 也不能静默挑一个模型顶上。"""
    c, roles, calls = harness
    roles.clear()
    r = c.post("/v1/embeddings", json={"input": "x"})
    assert r.status_code == 400, r.text
    assert "roles.yaml" in r.json()["detail"], r.json()
    assert "params" not in calls, "没解析出模型却把请求发出去了"


def test_roles_指到对话模型上时拒绝(harness):
    """★★★ roles.yaml 是人手改的, 指错很正常。

    指错了必须当场 400。要是放过去, 就是拿对话模型算向量 —— 维度对不上,
    写进 sqlite 的 BLOB 长度也不对, 症状会推迟到"语义搜索全是乱的"才出现。
    """
    c, roles, calls = harness
    roles["embedding"] = "chat-model"
    r = c.post("/v1/embeddings", json={"input": "x"})
    assert r.status_code == 400, r.text
    assert "not an embedding model" in r.json()["detail"]
    assert "params" not in calls


def test_roles_指到不存在的模型上时_404(harness):
    """★ 控制台把模型删了但 roles.yaml 没跟着改 —— 要报出来, 不是静默。"""
    c, roles, _calls = harness
    roles["embedding"] = "已经删掉的模型"
    r = c.post("/v1/embeddings", json={"input": "x"})
    assert r.status_code == 404, r.text


# ── 配置本身 ────────────────────────────────────────────────


def test_仓里的_roles_yaml_真的配了_embedding():
    """★★ 上面全是打桩的 roles。真文件没配的话, 生产上第一次请求就 400。

    这条不打桩, 读真 config/roles.yaml。
    """
    from pathlib import Path

    import yaml

    p = Path(__file__).resolve().parents[1] / "config" / "roles.yaml"
    assert p.exists(), f"roles.yaml 不在 {p}"
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    roles = (data or {}).get("roles") or {}
    assert "embedding" in roles, (
        "config/roles.yaml 的 roles 段没有 embedding —— "
        "员工端已经不传模型名了, 这里没配就等于向量整个不可用"
    )
