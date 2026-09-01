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


FIELDS = (
    "model",
    "api_base",
    "api_key_env",
    "timeout",
    "param_overrides",
    "param_overrides_scope",
)


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


# ── 端到端迁移的四条, 2026-08-15 删了 ────────────────────────────────
#
# 原来这里有 4 条走内存假库的端到端测试 (七个模型拆成六家 / 迁移前后 upstream
# 逐字段相同 / 重复跑不建重复 / 共用一家但 timeout 不同各自保留)。
#
# 删的理由不是"只是测试", 是**它们从来没跑过, 而且已被更好的一份覆盖**:
#
#   1. fixture 写的是 `sys.path.insert(0, "/tmp"); import fakepg`, 而 fakepg
#      从来不在仓库里 (git ls-files 查无此物)。只有当年那台机器上手工建过
#      /tmp/fakepg.py 才跑得起来, 而 macOS 的 /tmp 还会被清理。
#      实际表现: 从 7/31 引入那天起, 每次跑测试都是 4 个 ERROR。
#
#   2. 隔一天 (8/1) 就有了 test_provider_split_real_pg.py, 用**真 PG 跑真 SQL**
#      逐条覆盖同样这四个场景, 名字几乎一样:
#          七个模型拆成六个供应商         → 同名
#          重复跑不会建出重复供应商       → 重复跑迁移不会建出重复供应商
#          共用一家但 timeout 不同…       → 共用一家供应商但timeout不同…
#          迁移后每个模型的 upstream 仍…  → 拆分前后每个模型的有效配置逐一相同
#      而且多验了内存假库根本验不到的两件事: 整段 SQL 的 JSONB 读写, 以及
#      界面保存一次再读回来 provider 还在不在 (pydantic 会静默丢未声明字段)。
#
# 也就是说这 4 条既没在跑, 也没有独有的断言 —— 留着只是每次测试多 4 个 ERROR。
#
# 真 PG 那份在 CI 里是**真跑的**: .github/workflows/ci.yml 的 gateway-real-pg
# job 起 postgres:16-alpine + alembic upgrade head 专跑这个文件, 挂在 ci-pass
# 的 needs 里, 后面还有一道防假绿的闸 (跑完 grep "N passed", 服务没起来导致
# 整体 skip 时 ::error:: 退出)。所以删这四条不留洞。
#
# (8/15 记一笔: 我第一版在这里写的是"CI 是纯 Windows 打包流水线, 一条 pytest
#  都不跑, 迁移逻辑零覆盖" —— 假的。我只看了 .circleci/config.yml, 没看
#  .github/workflows/。搜索范围比真事窄, 结论却说得很肯定。)
#
# 本机跑真 PG 那份:
#   export CATFISH_TEST_PG_URL=...; alembic upgrade head
#   pytest tests/test_provider_split_real_pg.py -v


# ── 第三步: 存库的 key 在组装时解密 (DESIGN §5.2) ────────────────────


@pytest.fixture()
def cfg_db(tmp_path, monkeypatch):
    """让 get_config 走"库出模型"那条路。"""
    import textwrap

    from catfish_gateway import config as C

    p = tmp_path / "models.yaml"
    p.write_text(
        textwrap.dedent(
            """
            version: 1
            models:
              - name: seed
                tier: public
                display_name: seed
                upstream: {model: openai/x, api_key_env: K}
            """
        ).strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CATFISH_CONFIG", str(p))
    monkeypatch.setenv("CATFISH_CONFIG_TTL", "0")
    C._CACHE = None
    C._CACHE_STAMP = None
    C._CACHE_CHECKED_AT = 0.0
    C._DB_EVER_SERVED = False
    monkeypatch.setattr(C.model_store, "is_enabled", lambda: True)
    monkeypatch.setattr(C.model_store, "revision", lambda: 1)
    yield C
    C._CACHE = None
    C._DB_EVER_SERVED = False


def _row_with_provider(pid: str) -> dict:
    return {
        "name": "m1",
        "tier": "private",
        "display_name": "m1",
        "upstream": {"model": "openai/x", "provider": pid},
    }


def test_存库的_key_解密后能用(cfg_db, monkeypatch):
    from cryptography.fernet import Fernet

    from catfish_gateway import secrets_box as SB

    monkeypatch.setenv(SB.MASTER_KEY_ENV, Fernet.generate_key().decode())
    SB.reset_cache()
    enc = SB.encrypt("sk-存库的")

    monkeypatch.setattr(cfg_db.model_store, "read_models", lambda: [_row_with_provider("p")])
    monkeypatch.setattr(
        cfg_db.provider_store,
        "read_providers",
        lambda: {"p": {"id": "p", "api_base": None, "api_key_env": None,
                       "api_key_enc": enc, "timeout": 60}},
    )

    m = cfg_db.get_config().models[0]
    assert m.upstream.api_key == "sk-存库的"
    assert m.upstream.is_available is True
    assert cfg_db.model_config_errors() == {}
    SB.reset_cache()


def test_主密钥没配时_只有这家不可用_而不是整份配置挂掉(cfg_db, monkeypatch):
    """DESIGN §5.2 的核心。7/30 那条 ${VAR} 教训的同款: 一个供应商的 key
    解不开, 不该让 get_config 抛 —— 冷启动时那等于全站 502。"""
    from catfish_gateway import secrets_box as SB

    monkeypatch.delenv(SB.MASTER_KEY_ENV, raising=False)
    SB.reset_cache()

    monkeypatch.setattr(cfg_db.model_store, "read_models", lambda: [_row_with_provider("p")])
    monkeypatch.setattr(
        cfg_db.provider_store,
        "read_providers",
        lambda: {"p": {"id": "p", "api_base": None, "api_key_env": None,
                       "api_key_enc": b"gAAAAA-not-a-real-ciphertext", "timeout": 60}},
    )

    cfg = cfg_db.get_config()  # 不抛
    m = cfg.models[0]
    assert m.upstream.is_available is False, "这家不可用 —— 员工选不到它"

    # 但必须能在界面上看到原因, 不能只有服务器日志里一行
    errs = cfg_db.model_config_errors()
    assert "m1" in errs
    assert SB.MASTER_KEY_ENV in errs["m1"]
    assert "IT" in errs["m1"] or "重启" in errs["m1"], "要给出可执行的下一步"


def test_主密钥配了但密文对不上_说明要跑轮换脚本(cfg_db, monkeypatch):
    """跟"没配"要给不同的说明 —— 两种情况的下一步动作完全不同。"""
    from cryptography.fernet import Fernet

    from catfish_gateway import secrets_box as SB

    monkeypatch.setenv(SB.MASTER_KEY_ENV, Fernet.generate_key().decode())
    SB.reset_cache()
    old_enc = Fernet(Fernet.generate_key()).encrypt(b"sk-x")

    monkeypatch.setattr(cfg_db.model_store, "read_models", lambda: [_row_with_provider("p")])
    monkeypatch.setattr(
        cfg_db.provider_store,
        "read_providers",
        lambda: {"p": {"id": "p", "api_base": None, "api_key_env": None,
                       "api_key_enc": old_enc, "timeout": 60}},
    )

    cfg_db.get_config()
    note = cfg_db.model_config_errors()["m1"]
    assert "轮换" in note or "改坏" in note
    assert "没有配" not in note, "主密钥是配了的, 别把人往错方向带"
    SB.reset_cache()
