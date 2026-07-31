"""配置组装: yaml 出顶层字段, 库出模型列表 (7/30).

沙箱/CI 里没有 postgres, 所以这里 mock 掉 model_store 的三个入口
(is_enabled / read_models / revision), 验的是**组装和降级的决策逻辑** ——
那才是容易写错、且错了会静默的部分。真正的 SQL 要在有 PG 的环境跑。

盯的几个具体事故形态:
  · 库不可用时悄悄退回 yaml → 客户在界面上改的模型突然消失、换回出厂默认
  · 代次只盯库不盯文件 → 改了 yaml 顶层字段永远不生效
  · 代次只盯文件不盯库 → 界面上改模型永远不生效
"""
from __future__ import annotations

import textwrap

import pytest

from catfish_gateway import config as C


def _yaml_with(path, *names: str) -> None:
    models = "\n".join(
        textwrap.dedent(
            f"""
              - name: {n}
                tier: public
                display_name: "{n}"
                upstream:
                  model: openai/x
                  api_key_env: K
            """
        ).rstrip()
        for n in names
    )
    path.write_text(
        f"version: 1\nauto_fallback: true\nmodels:\n{models}\n", encoding="utf-8"
    )


def _row(name: str) -> dict:
    return {
        "name": name,
        "tier": "public",
        "display_name": name,
        "upstream": {"model": "openai/x", "api_key_env": "K"},
    }


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    p = tmp_path / "models.yaml"
    _yaml_with(p, "from-yaml")
    monkeypatch.setenv("CATFISH_CONFIG", str(p))
    monkeypatch.setenv("CATFISH_CONFIG_TTL", "0")
    C._CACHE = None
    C._CACHE_STAMP = None
    C._CACHE_CHECKED_AT = 0.0
    C._DB_EVER_SERVED = False
    yield p
    C._CACHE = None
    C._CACHE_STAMP = None
    C._CACHE_CHECKED_AT = 0.0
    C._DB_EVER_SERVED = False


def test_没配库时用_yaml(cfg, monkeypatch):
    monkeypatch.setattr(C.model_store, "is_enabled", lambda: False)
    assert [m.name for m in C.get_config().models] == ["from-yaml"]


def test_库有数据时以库为准(cfg, monkeypatch):
    monkeypatch.setattr(C.model_store, "is_enabled", lambda: True)
    monkeypatch.setattr(C.model_store, "revision", lambda: 1)
    monkeypatch.setattr(C.model_store, "read_models", lambda: [_row("from-db")])
    assert [m.name for m in C.get_config().models] == ["from-db"]


def test_库通但没播种时用_yaml(cfg, monkeypatch):
    """首次启动: 库连得上但表是空的 → 先用 yaml, 别把模型列表变成空."""
    monkeypatch.setattr(C.model_store, "is_enabled", lambda: True)
    monkeypatch.setattr(C.model_store, "revision", lambda: 0)
    monkeypatch.setattr(C.model_store, "read_models", lambda: [])
    assert [m.name for m in C.get_config().models] == ["from-yaml"]


def test_库挂了沿用上一份而不是退回_yaml(cfg, monkeypatch):
    """这条最要紧。

    库临时不可用时如果悄悄退回 yaml, 客户在界面上配的模型会**突然消失、
    换回出厂默认**, 而且没有任何报错。宁可短暂用旧缓存。
    """
    monkeypatch.setattr(C.model_store, "is_enabled", lambda: True)
    monkeypatch.setattr(C.model_store, "revision", lambda: 1)
    monkeypatch.setattr(C.model_store, "read_models", lambda: [_row("from-db")])
    assert [m.name for m in C.get_config().models] == ["from-db"]

    # 库挂了
    monkeypatch.setattr(C.model_store, "read_models", lambda: None)
    monkeypatch.setattr(C.model_store, "revision", lambda: None)
    assert [m.name for m in C.get_config().models] == ["from-db"], (
        "库不可用时必须沿用上一份库配置, 不能退回 yaml"
    )


def test_冷启动时库连不上要降级而不是起不来(cfg, monkeypatch):
    """跟上一条相反的情形, 别混为一谈。

    从没成功读到过库 (冷启动时 PG 还没起来 / 本机 dev 没跑 PG) 时,
    没有"上一份"可沿用。此时抛异常会让 gateway **直接起不来** —— 而 yaml
    里本来就有一份完整可用的模型配置, 没有理由让整个服务挂掉。

    7/30 第一版就是写成了无条件抛, 结果本机跑测试全红 (conftest 会加载
    .env, 里面有 CATFISH_DB_URL, 于是"库启用"但连不上)。
    """
    C._DB_EVER_SERVED = False
    monkeypatch.setattr(C.model_store, "is_enabled", lambda: True)
    monkeypatch.setattr(C.model_store, "revision", lambda: None)
    monkeypatch.setattr(C.model_store, "read_models", lambda: None)
    assert [m.name for m in C.get_config().models] == ["from-yaml"]


def test_库恢复后自动切回不用重启(cfg, monkeypatch):
    """冷启动降级到 yaml 之后, 库起来了要能自己切回去."""
    C._DB_EVER_SERVED = False
    monkeypatch.setattr(C.model_store, "is_enabled", lambda: True)
    monkeypatch.setattr(C.model_store, "revision", lambda: None)
    monkeypatch.setattr(C.model_store, "read_models", lambda: None)
    assert [m.name for m in C.get_config().models] == ["from-yaml"]

    monkeypatch.setattr(C.model_store, "revision", lambda: 1)
    monkeypatch.setattr(C.model_store, "read_models", lambda: [_row("from-db")])
    assert [m.name for m in C.get_config().models] == ["from-db"]


def test_顶层字段始终来自_yaml(cfg, monkeypatch):
    """模型进库了, 但 auto_fallback 这类部署形态字段仍只从 yaml 读."""
    monkeypatch.setattr(C.model_store, "is_enabled", lambda: True)
    monkeypatch.setattr(C.model_store, "revision", lambda: 1)
    monkeypatch.setattr(C.model_store, "read_models", lambda: [_row("from-db")])
    assert C.get_config().auto_fallback is True


def test_代次同时覆盖库和文件(cfg, monkeypatch):
    """只盯一个源都会漏: 只盯库 → 改 yaml 不生效; 只盯文件 → 改库不生效."""
    rev = {"v": 1}
    monkeypatch.setattr(C.model_store, "is_enabled", lambda: True)
    monkeypatch.setattr(C.model_store, "revision", lambda: rev["v"])

    s1 = C._config_stamp()
    rev["v"] = 2
    s2 = C._config_stamp()
    assert s1 != s2, "库代次变了, 总代次必须跟着变"

    import time as _t
    _t.sleep(0.01)
    _yaml_with(cfg, "from-yaml", "another")  # 改文件
    s3 = C._config_stamp()
    assert s3 != s2, "yaml 变了, 总代次必须跟着变"


# ── 展示与计价字段 (7/30) ────────────────────────────────────────────


def test_单价颜色图标能从配置读到(cfg, monkeypatch):
    """这三个字段原本硬编码在前端 modelDisplay.ts。

    模型改成能在界面上增删改之后, 硬编码会让客户新加的模型在审计页显示
    "未知模型 ⚪"、成本按兜底价 0.001 算 —— 而真实单价跨度 0.00005~0.0218,
    差 400 倍。所以必须跟着模型配置走。
    """
    row = _row("m")
    row.update({"price_per_1k_tokens": 0.0015, "color": "#7c3aed", "dot_emoji": "🟣"})
    monkeypatch.setattr(C.model_store, "is_enabled", lambda: True)
    monkeypatch.setattr(C.model_store, "revision", lambda: 1)
    monkeypatch.setattr(C.model_store, "read_models", lambda: [row])
    m = C.get_config().models[0]
    assert m.price_per_1k_tokens == 0.0015
    assert m.color == "#7c3aed"
    assert m.dot_emoji == "🟣"


def test_不填这三个字段也能用(cfg, monkeypatch):
    """老配置里没有这几个字段, 不能因为加了字段就让存量配置加载失败."""
    monkeypatch.setattr(C.model_store, "is_enabled", lambda: True)
    monkeypatch.setattr(C.model_store, "revision", lambda: 1)
    monkeypatch.setattr(C.model_store, "read_models", lambda: [_row("m")])
    m = C.get_config().models[0]
    assert m.price_per_1k_tokens is None
    assert m.color is None


def test_catalog_把这三个字段透出去(cfg, monkeypatch):
    """光存进配置不够 —— 前端是从 /v1/catalog 读的, 没透出去等于没做."""
    from catfish_gateway.catalog import build_catalog

    row = _row("m")
    row.update({"price_per_1k_tokens": 0.0015, "color": "#7c3aed", "dot_emoji": "🟣"})
    monkeypatch.setattr(C.model_store, "is_enabled", lambda: True)
    monkeypatch.setattr(C.model_store, "revision", lambda: 1)
    monkeypatch.setattr(C.model_store, "read_models", lambda: [row])

    cat = build_catalog(C.get_config(), user=None)
    entry = next(x for x in cat["models"] if x["id"] == "m")
    assert entry["price_per_1k_tokens"] == 0.0015
    assert entry["color"] == "#7c3aed"
    assert entry["dot_emoji"] == "🟣"


def test_真实配置里的单价都填了(cfg):
    """守护: config/models.yaml 里每个 chat 模型都该有单价.

    漏一个的话审计页对它就按兜底价算, 而且不会有任何报错 —— 只是数字悄悄
    是错的。这正是 7/30 之前 catfish-public-qwen-flash 的表现。
    """
    import os
    from pathlib import Path

    for k in (
        "INTERNAL_LLM_BASE_QWEN_MAIN",
        "INTERNAL_LLM_BASE_QWEN_VISION",
        "INTERNAL_LLM_BASE_BGE_M3",
    ):
        os.environ.setdefault(k, "http://placeholder")

    real = Path(__file__).resolve().parent.parent / "config" / "models.yaml"
    missing = [
        m.name for m in C.load_config(real).models if m.price_per_1k_tokens is None
    ]
    assert not missing, (
        f"这些模型没配 price_per_1k_tokens, 审计页会按兜底价 0.001 估算: {missing}"
    )


# ── ${VAR} 不能被烤进库 (7/30 当天发现并修) ──────────────────────────────
#
# models.yaml 里内网地址是故意写成 ${INTERNAL_LLM_BASE_*} 的 —— 真实地址在
# .env 里, 不进配置文件也不进 git。但把模型搬进库时, 播种用的是**插值之后**
# 的 config.models, 于是整串地址作为字面量进了 gateway_models.payload。
#
# 两个后果, 第二个更狠:
#   1. 隔离没了 —— 地址进库、进 pg_dump、进备份, 还直接显示在「模型」页上
#   2. **改 .env 从此不生效** —— 库里是烤死的旧值, 播种又是 ON CONFLICT
#      DO NOTHING, 重启也不更新。网关继续打老地址, 不报错不提示。


def _yaml_with_placeholder(path) -> None:
    path.write_text(
        "version: 1\n"
        "models:\n"
        "  - name: m1\n"
        "    tier: private\n"
        '    display_name: "m1"\n'
        "    upstream:\n"
        "      model: openai/x\n"
        "      api_base: ${TEST_LLM_BASE}\n"
        "      api_key_env: K\n",
        encoding="utf-8",
    )


def test_读原始_yaml_保留占位符不插值(tmp_path, monkeypatch):
    p = tmp_path / "models.yaml"
    _yaml_with_placeholder(p)
    monkeypatch.setenv("CATFISH_CONFIG", str(p))
    monkeypatch.setenv("TEST_LLM_BASE", "http://10.0.0.1/secret-uuid/v1")

    raw = C.load_raw_models()
    # 这份是要播进库的 —— 必须还是占位符, 不能是真实地址
    assert raw[0]["upstream"]["api_base"] == "${TEST_LLM_BASE}"
    # 对照: 走 load_config 那条路径是插值过的 (给运行时用)
    assert C.load_config().models[0].upstream.api_base == "http://10.0.0.1/secret-uuid/v1"


def test_库里存占位符_读出来时才插值(cfg, monkeypatch):
    monkeypatch.setenv("TEST_LLM_BASE", "http://10.0.0.1/a/v1")
    row = _row("m1")
    row["upstream"]["api_base"] = "${TEST_LLM_BASE}"
    monkeypatch.setattr(C.model_store, "is_enabled", lambda: True)
    monkeypatch.setattr(C.model_store, "read_models", lambda: [row])
    monkeypatch.setattr(C.model_store, "revision", lambda: 1)

    assert C.get_config().models[0].upstream.api_base == "http://10.0.0.1/a/v1"


def test_改_env_后重新组装能拿到新地址(cfg, monkeypatch):
    """这条是整件事的要害: 库里烤死的话, 改 .env 永远不生效。"""
    row = _row("m1")
    row["upstream"]["api_base"] = "${TEST_LLM_BASE}"
    monkeypatch.setattr(C.model_store, "is_enabled", lambda: True)
    monkeypatch.setattr(C.model_store, "read_models", lambda: [row])
    rev = {"n": 1}
    monkeypatch.setattr(C.model_store, "revision", lambda: rev["n"])

    monkeypatch.setenv("TEST_LLM_BASE", "http://old/v1")
    assert C.get_config().models[0].upstream.api_base == "http://old/v1"

    monkeypatch.setenv("TEST_LLM_BASE", "http://new/v1")
    rev["n"] = 2  # IT 改完 .env 重启 → 缓存失效
    C.invalidate_config()
    assert C.get_config().models[0].upstream.api_base == "http://new/v1"


# ── 回迁: 已经烤死的库怎么救 ──────────────────────────────────────────


def test_回迁把烤死的值换回占位符(monkeypatch):
    monkeypatch.setenv("TEST_LLM_BASE", "http://10.0.0.1/uuid/v1")
    raw = {"name": "m1", "upstream": {"api_base": "${TEST_LLM_BASE}", "model": "openai/x"}}
    stored = {"name": "m1", "upstream": {"api_base": "http://10.0.0.1/uuid/v1", "model": "openai/x"}}

    fixed, note = C.restore_placeholders(stored, raw)
    assert fixed["upstream"]["api_base"] == "${TEST_LLM_BASE}"
    assert note is None


def test_回迁换不回时必须报出来而不是静默跳过(monkeypatch):
    """这条是第一版漏掉的要害.

    这个 bug 的发现路径就是"IT 改了 .env 但不生效" —— 到打补丁时库里是旧地址、
    .env 里是新地址, 两者必然对不上。第一版"对不上就当人手填的, 跳过"于是在
    **最需要它的场景里静默失效**。函数没能力区分两者, 那就别猜, 报出来。
    """
    monkeypatch.setenv("TEST_LLM_BASE", "http://新地址/v1")
    raw = {"name": "m1", "upstream": {"api_base": "${TEST_LLM_BASE}"}}
    stored = {"name": "m1", "upstream": {"api_base": "http://旧地址/v1"}}

    fixed, note = C.restore_placeholders(stored, raw)
    assert fixed == stored, "拿不准就别改"
    assert note, "但必须说出来"
    assert "人工确认" in note or "确认" in note


def test_回迁幂等(monkeypatch):
    monkeypatch.setenv("TEST_LLM_BASE", "http://10.0.0.1/uuid/v1")
    raw = {"name": "m1", "upstream": {"api_base": "${TEST_LLM_BASE}"}}
    once, _ = C.restore_placeholders(
        {"name": "m1", "upstream": {"api_base": "http://10.0.0.1/uuid/v1"}}, raw
    )
    twice, note = C.restore_placeholders(once, raw)
    assert twice == once
    assert note is None, "已经是占位符了, 不该再报存疑"


def test_回迁在_env_没设时不动但会报(monkeypatch):
    monkeypatch.delenv("TEST_LLM_BASE", raising=False)
    raw = {"name": "m1", "upstream": {"api_base": "${TEST_LLM_BASE}"}}
    stored = {"name": "m1", "upstream": {"api_base": "http://whatever/v1"}}
    fixed, note = C.restore_placeholders(stored, raw)
    assert fixed == stored
    assert note and "没设" in note


def test_回迁遇到类型对不上不抛(monkeypatch):
    """raw 里是 dict 而库里同 key 是别的类型 —— 老数据/手改坏的数据会这样。"""
    monkeypatch.setenv("TEST_LLM_BASE", "http://a/v1")
    raw = {"name": "m1", "upstream": {"api_base": "${TEST_LLM_BASE}"}}
    for bad in ("字符串", None, [], 42):
        fixed, _ = C.restore_placeholders({"name": "m1", "upstream": bad}, raw)
        assert fixed == {"name": "m1", "upstream": bad}


# ── 单个模型的占位符解析失败, 不能掀翻整份配置 ──────────────────────────


def test_未设的_env_变量不会让整份配置挂掉(cfg, monkeypatch):
    """管理员在界面上把 api_base 填成 ${打错的变量名} ——

    第一版是一路 raise 上去:
      · 冷启动 → lifespan 第一行就是 get_config(), 网关**起不来**, 全站 502
      · 热态   → get_config 沿用旧缓存, **整份配置冻结**, 之后所有编辑都不生效
    一个人打错一个字, 代价是全站。现在按单个模型兜住。
    """
    monkeypatch.delenv("NO_SUCH_VAR", raising=False)
    bad = _row("坏的")
    bad["upstream"]["api_base"] = "${NO_SUCH_VAR}"
    good = _row("好的")
    monkeypatch.setattr(C.model_store, "is_enabled", lambda: True)
    monkeypatch.setattr(C.model_store, "read_models", lambda: [bad, good])
    monkeypatch.setattr(C.model_store, "revision", lambda: 1)

    got = C.get_config()  # 不抛
    assert [m.name for m in got.models] == ["坏的", "好的"], "别的模型必须照常"
    # 出错的那个保留占位符原文 —— 调用时会明显失败, 且错误里带着变量名
    assert got.models[0].upstream.api_base == "${NO_SUCH_VAR}"
    # 而且要能在界面上看到原因, 不能只有服务器日志里一行
    errs = C.model_config_errors()
    assert "坏的" in errs and "NO_SUCH_VAR" in errs["坏的"]
    assert "好的" not in errs


def test_只插值_upstream_api_base(cfg, monkeypatch):
    """display_name 会经 /v1/catalog 发给**未认证**调用方.

    整行递归插值的话, 把 display_name 填成 ${INTERNAL_LLM_KEY} 就能让网关
    把密钥打印出来。models.yaml 里全部占位符都在 upstream.api_base,
    没有别的字段需要这个能力。
    """
    monkeypatch.setenv("SECRET_KEY", "sk-绝密")
    row = _row("m1")
    row["display_name"] = "${SECRET_KEY}"
    monkeypatch.setattr(C.model_store, "is_enabled", lambda: True)
    monkeypatch.setattr(C.model_store, "read_models", lambda: [row])
    monkeypatch.setattr(C.model_store, "revision", lambda: 1)

    assert C.get_config().models[0].display_name == "${SECRET_KEY}", "不该被展开"
