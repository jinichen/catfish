"""供应商拆分 (8/1).

判据只有一条: **UpstreamConfig 的五个成员, 拆分前后逐字段相同**。

全仓 30 处读 upstream, 全部走 model / api_base / api_key_env / timeout /
is_available / api_key。只要合并之后这些值不变, 那 30 处就一个都不用改 ——
这是这次改动能安全做的全部前提。如果这一条不成立, 方案要重想
(见 docs/DESIGN-PROVIDER-SPLIT-20260730.md §10)。
"""
from __future__ import annotations

import pytest

from catfish_gateway.config import ModelConfig
from catfish_gateway.config_providers import (
    merge_provider,
    split_upstream,
    suggest_provider_id,
)


# 现有 7 个模型的 upstream, 照 models.yaml 实抄 (7/30 实查)
REAL_UPSTREAMS = {
    "catfish-private-main": {
        "model": "openai/qwen_v3_5_122b_a10b",
        "api_base": "${INTERNAL_LLM_BASE_QWEN_MAIN}",
        "api_key_env": "INTERNAL_LLM_KEY",
        "timeout": 180,
    },
    "catfish-private-vision": {
        "model": "openai/qwen_v3_6_35b_a3b",
        "api_base": "${INTERNAL_LLM_BASE_QWEN_VISION}",
        "api_key_env": "INTERNAL_LLM_KEY",
        "timeout": 180,
    },
    "catfish-private-embed": {
        "model": "openai/bge-m3",
        "api_base": "${INTERNAL_LLM_BASE_BGE_M3}",
        "api_key_env": "INTERNAL_LLM_KEY",
        "timeout": 30,
    },
    "catfish-public-qwen-flash": {
        "model": "openai/qwen3.7-flash-2026-07-15",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY",
        "timeout": 60,
    },
    "catfish-public-deepseek-flash": {
        "model": "deepseek/deepseek-v4-flash",
        "api_base": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "timeout": 60,
    },
    # ⚠ 这两个是同一家: 都没有 api_base, 都用 GEMINI_API_KEY
    "catfish-public-gemini-pro": {
        "model": "gemini/gemini-3.1-pro",
        "api_key_env": "GEMINI_API_KEY",
        "timeout": 90,
    },
    "catfish-public-gemini-flash": {
        "model": "gemini/gemini-3.5-flash",
        "api_key_env": "GEMINI_API_KEY",
        "timeout": 60,
    },
}


def _model(name: str, up: dict) -> dict:
    return {"name": name, "tier": "private", "display_name": name, "upstream": up}


FIELDS = ("model", "api_base", "api_key_env", "timeout", "param_overrides")


# ── 黄金对照: 拆分前后 UpstreamConfig 逐字段相同 ────────────────────────


@pytest.mark.parametrize("name", sorted(REAL_UPSTREAMS))
def test_拆分前后_upstream_逐字段相同(name, monkeypatch):
    monkeypatch.setenv("INTERNAL_LLM_KEY", "k-internal")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k-dash")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k-deep")
    monkeypatch.setenv("GEMINI_API_KEY", "k-gem")

    old_up = REAL_UPSTREAMS[name]
    before = ModelConfig.model_validate(_model(name, old_up)).upstream

    # 按 split_upstream 拆出供应商, 模型改成引用
    _, pid, prow = split_upstream(old_up)
    new_up = {"model": old_up["model"], "provider": pid}
    if old_up.get("timeout") and old_up["timeout"] != prow["timeout"]:
        new_up["timeout"] = old_up["timeout"]

    merged, err = merge_provider(_model(name, new_up), {pid: {**prow, "id": pid}})
    assert err is None
    after = ModelConfig.model_validate(merged).upstream

    for f in FIELDS:
        assert getattr(after, f) == getattr(before, f), f"{name}.{f} 变了"
    # 属性也要一致 —— 9 处代码读 is_available, 3 处读 api_key
    assert after.is_available == before.is_available
    assert after.api_key == before.api_key


def test_两个_gemini_模型去重到同一家():
    """去重键是 (api_base, api_key_env)。gemini-pro 和 gemini-flash 都没有
    api_base、都用 GEMINI_API_KEY —— 必须是同一个供应商, 否则拆分没有意义。"""
    k1, id1, _ = split_upstream(REAL_UPSTREAMS["catfish-public-gemini-pro"])
    k2, id2, _ = split_upstream(REAL_UPSTREAMS["catfish-public-gemini-flash"])
    assert k1 == k2
    assert id1 == id2 == "gemini"


def test_三个内网端点不去重成一个():
    """它们共用 INTERNAL_LLM_KEY 但 **api_base 不同** —— 是三个端点。
    去重成一个的话, 视觉模型会被打到主力模型的地址上。"""
    keys = {
        split_upstream(REAL_UPSTREAMS[n])[0]
        for n in ("catfish-private-main", "catfish-private-vision", "catfish-private-embed")
    }
    assert len(keys) == 3


@pytest.mark.parametrize(
    "base,key_env,want",
    [
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY", "dashscope"),
        ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", "deepseek"),
        ("", "GEMINI_API_KEY", "gemini"),
        # 内网 IP 取不出域名 → 落回 key 变量名
        ("http://10.10.40.102:32730/openapi/uuid/v1", "INTERNAL_LLM_KEY", "internal-llm"),
        # 迁移时读的是库里的原样值, 所以内网那几个**必然**是占位符。
        # 变量名恰恰信息最全 —— 三个内网端点因此得到三个可区分的 id,
        # 而不是 internal-llm / -2 / -3 那种让人猜的编号。
        ("${INTERNAL_LLM_BASE_QWEN_MAIN}", "INTERNAL_LLM_KEY", "internal-llm-qwen-main"),
        ("${INTERNAL_LLM_BASE_QWEN_VISION}", "INTERNAL_LLM_KEY", "internal-llm-qwen-vision"),
        ("${INTERNAL_LLM_BASE_BGE_M3}", "INTERNAL_LLM_KEY", "internal-llm-bge-m3"),
    ],
)
def test_供应商_id_可读(base, key_env, want):
    """id 会出现在界面的下拉框上。客户对着 prov_a3f9 没法判断该选哪个。"""
    assert suggest_provider_id(base, key_env) == want


# ── 迁移期两种形态并存 ──────────────────────────────────────────────


def test_老形态原样通过():
    """DESIGN §6.2 第一步的基础: 没有 provider 键的模型不受影响。"""
    row = _model("m1", REAL_UPSTREAMS["catfish-public-deepseek-flash"])
    out, err = merge_provider(row, {})
    assert err is None
    assert out == row


def test_引用了不存在的供应商_不掀翻整份配置():
    """跟 interpolate_model_row 同一个原则: 单个模型的问题只影响它自己,
    但必须能在界面上看到原因。"""
    row = _model("m1", {"model": "openai/x", "provider": "没这个"})
    out, err = merge_provider(row, {})
    assert err and "没这个" in err
    assert out == row, "出错时返回原样, 让上层照常把模型放进列表"


def test_模型自己的_timeout_优先于供应商默认值():
    """timeout 是模型属性 —— 同一个内网 vLLM 上视觉 180s、embedding 30s。"""
    p = {"api_base": "http://x/v1", "api_key_env": "K", "timeout": 60}
    out, _ = merge_provider(
        _model("m", {"model": "openai/x", "provider": "p", "timeout": 180}), {"p": p}
    )
    assert out["upstream"]["timeout"] == 180

    out2, _ = merge_provider(_model("m", {"model": "openai/x", "provider": "p"}), {"p": p})
    assert out2["upstream"]["timeout"] == 60, "模型没写才落到供应商默认值"


def test_供应商没配_env_key_时_is_available_为假(monkeypatch):
    """第二步之后 key 存库的供应商 api_key_env 是 NULL。
    UpstreamConfig.api_key_env 是 str 有默认值, 传 None 会被 pydantic 拒 ——
    所以给空串, os.environ.get("") 得 None, 语义正好是"没配 env key"。"""
    out, _ = merge_provider(
        _model("m", {"model": "openai/x", "provider": "p"}),
        {"p": {"api_base": None, "api_key_env": None, "timeout": 60}},
    )
    up = ModelConfig.model_validate(out).upstream
    assert up.api_key_env == ""
    assert up.is_available is False


def test_param_overrides_跟着模型走(monkeypatch):
    """它是模型级的 (Gemini 3 必须 temperature=1.0), 不是供应商级。"""
    row = _model("m", {"model": "g/x", "provider": "p", "param_overrides": {"temperature": 1.0}})
    out, _ = merge_provider(row, {"p": {"api_base": None, "api_key_env": "K", "timeout": 60}})
    assert ModelConfig.model_validate(out).upstream.param_overrides == {"temperature": 1.0}


# ── 端到端: 7 个老模型 → 5 个供应商 (沙箱无 PG, 用内存假库) ──────────────
#
# 真正的 SQL 要在有库的环境验 (同 model_store 的做法)。这里验的是**去重和
# 回写的决策逻辑** —— 那才是容易写错、且错了会静默的部分:
#   · 去重键选错 → 三个内网端点合成一个, 视觉模型被打到主力模型的地址上
#   · timeout 漏搬 → 视觉模型从 180s 掉回 60s, 长图必超时
#   · 重复跑不幂等 → 每次重启多出一批 dashscope-2 / dashscope-3


@pytest.fixture()
def fake_db(monkeypatch):
    import sys

    sys.path.insert(0, "/tmp")
    import fakepg

    from catfish_gateway import provider_store as PS

    models = {
        n: _model(n, dict(up)) for n, up in REAL_UPSTREAMS.items()
    }
    Conn, providers = fakepg.make(models)
    monkeypatch.setattr(PS, "is_enabled", lambda: True)
    monkeypatch.setattr(PS, "_conn", lambda: Conn())
    monkeypatch.setattr(PS, "_bump_revision", lambda cur: None)
    return PS, models, providers


def test_七个模型拆成六个供应商(fake_db):
    PS, models, providers = fake_db
    created = PS.migrate_models_to_providers()

    assert len(providers) == 6, f"应该去重成 6 家, 实际 {sorted(providers)}"
    assert sorted(created) == sorted(providers)
    # 两个 gemini 指向同一家
    assert (
        models["catfish-public-gemini-pro"]["upstream"]["provider"]
        == models["catfish-public-gemini-flash"]["upstream"]["provider"]
    )
    # 三个内网各自一家 (端点不同)
    internal = {
        models[n]["upstream"]["provider"]
        for n in ("catfish-private-main", "catfish-private-vision", "catfish-private-embed")
    }
    assert len(internal) == 3


def test_迁移后每个模型的_upstream_仍逐字段相同(fake_db, monkeypatch):
    """整件事的要害。拆完之后 gateway 拿到的东西必须跟拆之前一模一样。"""
    monkeypatch.setenv("INTERNAL_LLM_KEY", "k-internal")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k-dash")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k-deep")
    monkeypatch.setenv("GEMINI_API_KEY", "k-gem")

    PS, models, providers = fake_db
    before = {
        n: ModelConfig.model_validate(_model(n, up)).upstream
        for n, up in REAL_UPSTREAMS.items()
    }
    PS.migrate_models_to_providers()

    for n, row in models.items():
        merged, err = merge_provider(row, providers)
        assert err is None, f"{n}: {err}"
        after = ModelConfig.model_validate(merged).upstream
        for f in FIELDS:
            assert getattr(after, f) == getattr(before[n], f), f"{n}.{f} 变了"


def test_重复跑不会建出重复供应商(fake_db):
    """4 个 worker 每次启动都会跑。不幂等的话每次重启多出一批 dashscope-2。"""
    PS, models, providers = fake_db
    PS.migrate_models_to_providers()
    n1 = len(providers)
    snapshot = {k: dict(v) for k, v in models.items()}

    assert PS.migrate_models_to_providers() == [], "第二次不该再建"
    assert len(providers) == n1
    assert models == snapshot, "第二次不该再改模型"


def test_共用一家但_timeout_不同的两个模型_都不能被改掉(fake_db):
    """迁移里最容易写错、而且**完全静默**的一处。

    gemini-pro timeout 90 / gemini-flash timeout 60, 两个共用 gemini 这一家。

    第一版代码判断"模型的 timeout 要不要单独保留"时, 拿的是**当前模型重新
    算出来的** prow.timeout —— 那永远等于它自己, 结论永远是"相同, 可以省掉",
    于是 flash 的 60 被省掉、落到供应商的 90 上。

    表现: 一个本该 60 秒超时的快模型变成 90 秒。配置上看不出少了什么,
    日志里也没有任何东西 —— 只有员工感觉"这个快模型怎么卡这么久"。
    """
    PS, models, providers = fake_db
    PS.migrate_models_to_providers()

    pro = models["catfish-public-gemini-pro"]["upstream"]
    flash = models["catfish-public-gemini-flash"]["upstream"]
    assert pro["provider"] == flash["provider"], "前提: 两个共用一家"

    pid = pro["provider"]
    # 至少有一个模型必须显式带着自己的 timeout, 否则必然有一个被改掉
    assert pro.get("timeout", providers[pid]["timeout"]) == 90
    assert flash.get("timeout", providers[pid]["timeout"]) == 60
