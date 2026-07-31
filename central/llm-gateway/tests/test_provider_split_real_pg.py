"""供应商拆分 —— **真 PG 上跑真 SQL** 的验证 (8/1).

## 为什么单独一个文件

同目录下 test_provider_split.py 和 test_admin_models_api.py 把 model_store /
provider_store 整个 mock 成内存字典。那样能验业务判断, 但验不了这两件事:

  1. `migrate_models_to_providers` 的整段 SQL —— 它读 JSONB、按
     (api_base, api_key_env) 去重、UPDATE 回写, 全在一个事务里。
     内存字典替身把"SQL 到底把什么写进去了"这一层整个跳过了。
  2. 界面存一次再读回来 —— PUT 走 pydantic 校验 + model_dump + JSONB
     round-trip。**pydantic 默认静默丢掉未声明的字段**, 而 `provider`
     恰恰是 8/1 才加上的。少了它的话每次在界面上保存模型都会把
     供应商引用抹掉, 而且不报错。

这两个 bug 都是**只有真库才看得见**的。

## 怎么跑

    # 任意一个能连的空库
    export CATFISH_TEST_PG_URL="postgresql://catfish@/catfish?host=/tmp&port=55432"
    alembic upgrade head        # 表要先建好, 尤其是 008
    pytest tests/test_provider_split_real_pg.py -v

没设 CATFISH_TEST_PG_URL 就整体 skip —— CI 的沙箱里没有 PG, 但**跳过这件事
本身要在输出里看得见**, 所以是 skip 不是"悄悄通过"。

⚠ 会清空 gateway_models / gateway_providers 两张表。别指着生产库跑。
"""

from __future__ import annotations

import os

import pytest

PG_URL = os.environ.get("CATFISH_TEST_PG_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not PG_URL,
    reason="没设 CATFISH_TEST_PG_URL —— 这个文件要真 PG, 内存替身验不了这两件事",
)


@pytest.fixture()
def db(monkeypatch):
    """指向真库, 并且每个用例开始前清干净这两张表。"""
    monkeypatch.setenv("CATFISH_DB_URL", PG_URL)

    from catfish_gateway import config as C
    from catfish_gateway import model_store

    with model_store._conn() as conn, conn.cursor() as cur:
        # gateway_models 先删 —— 它引用 provider。
        cur.execute("DELETE FROM gateway_models")
        cur.execute("DELETE FROM gateway_providers")
        conn.commit()

    # 配置缓存必须清, 否则读到的是上一个用例装配好的那份
    C._CACHE = None
    C._CACHE_STAMP = None
    C._CACHE_CHECKED_AT = 0.0
    C._DB_EVER_SERVED = False
    monkeypatch.setenv("CATFISH_CONFIG_TTL", "0")
    yield
    C._CACHE = None
    C._DB_EVER_SERVED = False


def _seed_from_yaml() -> list[dict]:
    """把 config/models.yaml 的**原始行**播进库 (保留 ${VAR} 占位符).

    用 load_raw_models 而不是 config.models —— 后者已经把 ${VAR} 解析成
    真值了, 播进去等于把当时的环境变量烤死在库里 (7/30 踩过)。
    """
    from catfish_gateway import model_store
    from catfish_gateway.config import load_raw_models

    raw = load_raw_models()
    model_store.seed_from_yaml(raw)
    return raw


def _upstream_of(name: str):
    """装配后的某个模型的 upstream —— 走的是 gateway 真正在用的那条路径。"""
    from catfish_gateway.config import get_config

    for m in get_config().models:
        if m.name == name:
            return m.upstream
    raise AssertionError(f"装配后的配置里没有 {name}")


# ════════════════════════════════════════════════════════════════════
#  (a) 拆分不能悄悄改掉任何一个模型的 timeout
# ════════════════════════════════════════════════════════════════════


def _effective(name: str) -> dict:
    """一个模型**装配完之后**真正会拿去发请求的那几个值。

    ⚠ 必须比装配后的, 不能拿 yaml 里的原始行去比装配后的 —— 那是两套口径:
    yaml 里 catfish-private-embed 压根没写 timeout, 而 UpstreamConfig 的默认
    值是 60。第一版这条测试就是这么写的, 于是它报了个"timeout 被拆分从
    None 改成了 60"的假问题。
    """
    u = _upstream_of(name)
    return {
        "model": u.model,
        "timeout": u.timeout,
        "api_base": u.api_base,
        "api_key_env": u.api_key_env,
    }


def test_拆分前后每个模型的有效配置逐一相同(db):
    """黄金对照: 拆分**前**装配一次, 拆分**后**再装配一次, 7 个模型逐字段比。

    这条盯的是一类很难看出来的 bug: 两个模型共用一家供应商而 timeout 不同时,
    "这个 timeout 跟供应商默认值一样, 可以省掉"这个判断如果拿模型自己重新算
    出来的值去比, 结论永远是"一样", 于是它的 timeout **静默变成先建那个
    模型的值**。

    实盘里就有这么一对: gemini-pro 180 / gemini-flash 60, api_base 都为空、
    key 变量都是 GEMINI_API_KEY —— 去重后是同一家。第一版代码会把 flash
    从 60 抬到 180, 而配置上看不出少了什么东西。

    比整个 upstream 而不只比 timeout: 端点和 key 变量名是从供应商行合并
    回来的, 合并错了同样只在真调用时才发作。
    """
    from catfish_gateway import model_store, provider_store
    from catfish_gateway.config import invalidate_config

    raw = _seed_from_yaml()
    names = [r["name"] for r in raw]
    # 播完还没拆 —— 这时库里是老形态, merge_provider 原样放行, 装配出来的
    # 就是"拆分前"的有效配置。
    before = {n: _effective(n) for n in names}

    provider_store.migrate_models_to_providers(by="test")
    invalidate_config()
    after = {n: _effective(n) for n in names}

    for n in names:
        assert after[n] == before[n], (
            f"{n} 的有效上游配置被拆分改了:\n"
            f"  拆分前 {before[n]}\n"
            f"  拆分后 {after[n]}\n"
            f"timeout 变了的话, 多半是判断'能不能省掉 timeout'时拿模型自己算"
            f"出来的值去比了, 而不是跟**供应商实际存的**那个比。"
        )


def test_共用一家供应商但timeout不同的两个模型各自保留(db):
    """上一条的针对性版本: 直接钉住 gemini 那一对。

    上一条是全量对照, 这条说明**为什么**要有上一条 —— 只看得到"某个数变了"
    的话, 下一个人很可能以为是数据问题而不是逻辑问题。
    """
    from catfish_gateway import model_store, provider_store

    _seed_from_yaml()
    provider_store.migrate_models_to_providers(by="test")

    pro = _upstream_of("catfish-public-gemini-pro")
    flash = _upstream_of("catfish-public-gemini-flash")

    # 前提: 它们确实被判成同一家 —— 不然这条测的就不是它想测的东西了
    assert pro.provider == flash.provider != None  # noqa: E711
    assert pro.timeout == 180
    assert flash.timeout == 60


def test_七个模型拆成六个供应商(db):
    """去重是按 (api_base, api_key_env) 做的, 不是按模型个数。

    7 个模型里 gemini 那两个共用一家 → 6 家。这个数字写在设计文档和迁移的
    docstring 里, 钉一下免得三处各说各的。
    """
    from catfish_gateway import model_store, provider_store

    _seed_from_yaml()
    created = provider_store.migrate_models_to_providers(by="test")
    assert len(created) == 6, f"应该建 6 家供应商, 实际 {len(created)}: {created}"

    rows = provider_store.read_providers()
    assert rows is not None, "表不在 —— alembic 008 没跑?"
    assert len(rows) == 6


def test_重复跑迁移不会建出重复供应商(db):
    """幂等。启动时每次都会跑, 不幂等的话开一次机多一批 dashscope-2/3/4。"""
    from catfish_gateway import model_store, provider_store

    _seed_from_yaml()
    first = provider_store.migrate_models_to_providers(by="test")
    second = provider_store.migrate_models_to_providers(by="test")

    assert len(first) == 6
    assert second == [], f"第二次不该再建: {second}"
    assert len(provider_store.read_providers() or {}) == 6


# ════════════════════════════════════════════════════════════════════
#  (b) 界面上存一次, provider 不能丢
# ════════════════════════════════════════════════════════════════════


@pytest.fixture()
def client(db, monkeypatch):
    import logging

    logging.disable(logging.CRITICAL)
    from fastapi.testclient import TestClient

    from catfish_gateway.app import app
    from catfish_gateway.auth import User, get_current_user

    async def _sysadmin() -> User:
        return User(sub="admin@x.com", role="sysadmin", department="d")

    app.dependency_overrides[get_current_user] = _sysadmin
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_界面保存模型后provider还在(client):
    """PUT → 读回来 → provider 仍然是原来那个。

    这条挡的是 pydantic 的默认行为: **未声明的字段被静默丢弃**。
    `provider` 是 8/1 才加到 UpstreamConfig 上的, 如果当时只改了业务代码
    没加这个字段, PUT 的链路是

        body(含 provider) → ModelConfig.model_validate  ← 这一步丢掉
                          → model_dump(mode="json")     ← 已经没有了
                          → 写进 JSONB

    结果: 在界面上改一次显示名, 这个模型的供应商引用就没了, 于是 api_base
    和 key 都拿不到 —— 它还在列表里, 但每次调用都失败。全程零报错。
    """
    from catfish_gateway import model_store, provider_store

    _seed_from_yaml()
    provider_store.migrate_models_to_providers(by="test")

    name = "catfish-public-gemini-flash"
    before = _upstream_of(name)
    pid = before.provider
    assert pid, "前提不成立: 迁移后这个模型应该已经引用某家供应商"

    # 取库里的原始行 (跟界面拿到的是同一份), 只改显示名, 别的原样送回
    raw = next(r for r in model_store.read_models() if r["name"] == name)
    body = {**raw, "display_name": "改过的显示名"}

    r = client.put(f"/api/admin/models/{name}", json=body)
    assert r.status_code == 200, r.text

    from catfish_gateway.config import invalidate_config

    invalidate_config()
    after = _upstream_of(name)

    assert after.provider == pid, (
        f"保存一次之后 provider 从 {pid!r} 变成了 {after.provider!r} —— "
        f"多半是 UpstreamConfig 上没有 provider 这个字段, pydantic 把它丢了。"
    )
    # 顺带确认合并回来的端点和 key 变量名还在 (provider 丢了的话这两个也会空)
    assert after.api_key_env == "GEMINI_API_KEY"
    assert after.timeout == 60, "timeout 是模型自己的, 存一次不该被供应商默认值盖掉"


def test_公网模型任何时候都不能落到内网key上(client):
    """比"关联丢了"更重的一条: **内网 key 被发到公网端点**。

    `UpstreamConfig.api_key_env` 的默认值是 "INTERNAL_LLM_KEY"。所以 provider
    一旦在保存链路上被丢掉, 一个 public 模型的 key 变量名不是变成空,
    而是**落回内网集群那个 key** —— gateway 会拿它去请求
    generativelanguage.googleapis.com。

    也就是说这个 bug 的后果不是"这个模型用不了"(那还能被发现), 而是
    "公司内网的凭据被送出去了", 而且调用本身照常返回 401, 看起来只是配错了。

    这跟仓里已有的 fallback-500 保密拦截是同一类红线 (内网的东西不许出公司),
    但那条只管内容, 不管凭据。这条补上凭据这一半。
    """
    from catfish_gateway import model_store, provider_store
    from catfish_gateway.config import get_config, invalidate_config

    _seed_from_yaml()
    provider_store.migrate_models_to_providers(by="test")

    # 把每个模型都在界面上存一遍 (只改显示名), 然后整体检查
    for row in list(model_store.read_models() or []):
        r = client.put(
            f"/api/admin/models/{row['name']}",
            json={**row, "display_name": f"{row.get('display_name') or row['name']} "},
        )
        assert r.status_code == 200, f"{row['name']}: {r.text}"

    invalidate_config()
    offenders = [
        (m.name, m.upstream.api_key_env)
        for m in get_config().models
        if m.tier == "public" and m.upstream.api_key_env == "INTERNAL_LLM_KEY"
    ]
    assert not offenders, (
        f"这些 public 模型的 key 变量名落到内网 key 上了: {offenders}。\n"
        f"gateway 会拿内网集群的凭据去请求公网端点 —— 凭据出公司。\n"
        f"多半是保存链路把 upstream.provider 丢了, 于是 api_key_env 落回了"
        f"UpstreamConfig 的默认值。"
    )


def test_保存时改掉provider会真的生效(client):
    """反向: 确认上一条不是因为"provider 压根没参与保存"才通过的。

    只验"改完还在"是不够的 —— 如果保存链路整个忽略 provider, 那条也会绿。
    这里改成另一家, 必须真的变过去。
    """
    from catfish_gateway import model_store, provider_store

    _seed_from_yaml()
    provider_store.migrate_models_to_providers(by="test")

    name = "catfish-public-gemini-flash"
    old = _upstream_of(name).provider
    other = next(p for p in (provider_store.read_providers() or {}) if p != old)

    raw = next(r for r in model_store.read_models() if r["name"] == name)
    body = {**raw, "upstream": {**raw["upstream"], "provider": other}}

    r = client.put(f"/api/admin/models/{name}", json=body)
    assert r.status_code == 200, r.text

    from catfish_gateway.config import invalidate_config

    invalidate_config()
    assert _upstream_of(name).provider == other


def test_保存到不存在的供应商会被拒(client):
    """引用一家不存在的供应商 = 这个模型每次调用都失败, 但列表里看着正常。

    所以要在保存时就拦掉, 而不是等员工发现"这个模型用不了"。
    """
    from catfish_gateway import model_store, provider_store

    _seed_from_yaml()
    provider_store.migrate_models_to_providers(by="test")

    name = "catfish-public-gemini-flash"
    raw = next(r for r in model_store.read_models() if r["name"] == name)
    body = {**raw, "upstream": {**raw["upstream"], "provider": "根本没有这一家"}}

    r = client.put(f"/api/admin/models/{name}", json=body)
    assert r.status_code == 400
    assert "供应商" in r.text
