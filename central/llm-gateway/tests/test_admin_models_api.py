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
    d = r.json()["detail"]
    # 断言实质内容而不是某个词 —— 文案会改, 但"必须点名是谁在引用"和
    # "必须给出下一步"这两件事不该因为改文案就丢掉。
    assert "main" in d, "要点名是哪个模型在引用, 否则得自己一个个翻"
    assert "失败切换" in d, "要说清楚是哪一块配置"
    assert "移除" in d or "去掉" in d, "要给出可执行的下一步"


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


# ── 库出错时的报错质量 (7/30) ────────────────────────────────────────


def test_迁移没跑时的报错要能指导下一步(client, monkeypatch):
    """原本这种情况只返 "HTTP 500: Internal Server Error"。

    管理界面上就显示那一行 —— 对着屏幕的人不知道发生了什么, 更不知道
    下一步该做什么, 而真正的原因埋在 gateway 日志里 (点保存的人未必有
    服务器日志权限)。
    """
    from catfish_gateway import model_store as MS

    def _boom(*a, **kw):
        raise RuntimeError('relation "gateway_models" does not exist')

    c, *_ = client
    monkeypatch.setattr(MS, "upsert_model", _boom)
    r = c.put("/api/admin/models/m", json=_model("m"))
    assert r.status_code == 500
    d = r.json()["detail"]
    assert "迁移" in d, "要说清楚是迁移没跑"
    assert "alembic upgrade head" in d, "要给出具体命令"
    assert "does not exist" in d, "原始错误也要留着, 便于排查别的成因"


def test_其它库错误也不返空洞的_500(client, monkeypatch):
    from catfish_gateway import model_store as MS

    def _boom(*a, **kw):
        raise RuntimeError("connection refused")

    c, *_ = client
    monkeypatch.setattr(MS, "upsert_model", _boom)
    r = c.put("/api/admin/models/m", json=_model("m"))
    assert r.status_code == 500
    assert "connection refused" in r.json()["detail"]


def test_删除时库出错不会假装删成功(client, monkeypatch):
    """静默成功的话, 界面显示已删除但模型还在, 刷新又出现 —— 最迷惑的一种."""
    from catfish_gateway import model_store as MS

    c, *_ = client
    c.put("/api/admin/models/m1", json=_model("m1"))
    c.put("/api/admin/models/m2", json=_model("m2"))
    monkeypatch.setattr(
        MS, "delete_model", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("db down"))
    )
    r = c.delete("/api/admin/models/m1")
    assert r.status_code == 500
    assert "db down" in r.json()["detail"]


# ── 失败切换 (fallback) 相关 (7/30) ──────────────────────────────────


def test_内网模型不许因_500_切公网(client):
    """保密红线, 不是风格问题。

    内网返 500 触发 fallback → 内网 prompt 落到公网模型 = 内网内容出公司。

    **必须在接口上拦**: tests/test_fallback_500_policy.py 读的是
    config/models.yaml, 而模型改成可在界面上增删改之后, 库里的模型完全
    不在那个测试的视野内 —— 通过界面加一个内网模型填 500, 没有任何测试会红。
    """
    c, *_ = client
    r = c.put(
        "/api/admin/models/priv",
        json=_model(
            "priv",
            tier="private",
            fallback={"on_errors": [429, 500, 503], "chain": ["other"]},
        ),
    )
    assert r.status_code == 400
    assert "保密" in r.json()["detail"]
    assert "500" in r.json()["detail"]


def test_公网模型可以含_500(client):
    """公网之间切换不涉及跨边界, 500 是允许的 (而且体验上应该有)."""
    c, *_ = client
    r = c.put(
        "/api/admin/models/pub",
        json=_model(
            "pub",
            tier="public",
            fallback={"on_errors": [429, 500, 503], "chain": ["other"]},
        ),
    )
    assert r.status_code == 200


def test_内网模型没有_fallback_不受影响(client):
    c, *_ = client
    r = c.put("/api/admin/models/priv2", json=_model("priv2", tier="private"))
    assert r.status_code == 200


def test_列表要告诉界面_fallback_全局开没开(client, monkeypatch):
    """默认是关的。不告诉界面的话, 管理员会认真配一条永不执行的链 ——
    配置了不生效且无任何提示, 是最难查的一类。"""
    c, *_ = client
    monkeypatch.delenv("CATFISH_AUTO_FALLBACK", raising=False)
    body = c.get("/api/admin/models").json()
    assert "auto_fallback" in body
    assert body["auto_fallback"] is False, "models.yaml 没开且无 env → 应为 False"

    monkeypatch.setenv("CATFISH_AUTO_FALLBACK", "1")
    assert c.get("/api/admin/models").json()["auto_fallback"] is True


# ── 默认模型接任只能挑对话模型 (7/30 二修) ──────────────────────────────
#
# 原来是 `rest[0]` —— 剩下列表的第一个, 不看 mode。按 models.yaml 的顺序
# (main, vision, embed, ...), 删掉 main 之后接任的是**视觉模型**, 再删一个
# 就轮到 **embedding 模型**。而 default_model() (config.py) 也不筛 mode,
# 只看 default 标记。
#
# 结果: 全公司默认对话模型变成一个 embedding 模型, 每个员工一开口就报错,
# 而界面上完全看不出哪里不对 —— 「模型」页只会显示 embed 那行挂着「默认」。
# 鸿波 7/30 的库里就正好停在这个状态的前一步 (默认已经落到视觉模型上)。


def test_接任默认时跳过向量模型(client):
    c, store, *_ = client
    c.put("/api/admin/models/主力", json=_model("主力", default=True))
    # 顺序上排在前面, 但它是 embedding —— 不能让它接任
    c.put("/api/admin/models/embed", json=_model("embed", mode="embedding"))
    c.put("/api/admin/models/对话", json=_model("对话"))

    r = c.delete("/api/admin/models/主力")
    assert r.status_code == 200
    assert r.json()["promoted_default"] == "对话", "接任的必须是对话模型"

    defaults = [m["name"] for m in c.get("/api/admin/models").json()["models"] if m["default"]]
    assert defaults == ["对话"]


def test_没有别的对话模型时拒绝删(client):
    """能删成功但删完全公司聊不了天 —— 这种操作不该让它成功."""
    c, store, *_ = client
    c.put("/api/admin/models/唯一对话", json=_model("唯一对话", default=True))
    c.put("/api/admin/models/embed", json=_model("embed", mode="embedding"))

    r = c.delete("/api/admin/models/唯一对话")
    assert r.status_code == 400
    # 关键: 拦住之后模型必须还在, 不能"删了但没指定新默认"
    assert "唯一对话" in store, "拦截必须发生在真删之前"


def test_删的不是默认模型也要挡住(client):
    """守卫的判据是"删完还剩不剩对话模型", 不是"删的是不是默认".

    只看 was_default 的话, 同一个洞从旁边就能走进去 ——
    [chatA(非默认), embedB(默认)] 删掉 chatA, 剩一个 embedding 挂着「默认」,
    全公司默认对话模型变成向量模型, 员工一开口就报错。
    """
    c, store, *_ = client
    c.put("/api/admin/models/chatA", json=_model("chatA"))
    c.put("/api/admin/models/embedB", json=_model("embedB", mode="embedding", default=True))

    r = c.delete("/api/admin/models/chatA")
    assert r.status_code == 400, "删完就没有对话模型了, 不管它是不是默认"
    assert "chatA" in store


def test_删到没有默认时会补一个(client):
    """两个都没标 default 时, default_model() 退化成 models[0] —— 取决于排序."""
    c, store, *_ = client
    c.put("/api/admin/models/chatC", json=_model("chatC"))
    c.put("/api/admin/models/chatD", json=_model("chatD"))
    r = c.delete("/api/admin/models/chatC")
    assert r.status_code == 200
    assert r.json()["promoted_default"] == "chatD"
    assert store["chatD"]["default"] is True


def test_删非默认模型不受这条影响(client):
    c, store, *_ = client
    c.put("/api/admin/models/m1", json=_model("m1", default=True))
    c.put("/api/admin/models/embed", json=_model("embed", mode="embedding"))
    r = c.delete("/api/admin/models/embed")
    assert r.status_code == 200
    assert r.json()["promoted_default"] is None
    assert "embed" not in store


# ── 管理接口必须走库里的原样, 不能走 cfg.models (7/30 三修) ──────────────
#
# cfg.models 是**插值后**的运行时配置 (${VAR} 已换成真实地址)。界面拿到什么
# 就会在保存时原样传回来 —— 于是:
#   · GET 返回插值后的值 → 管理员随便编辑一次就把占位符烤成字面量,
#     把回迁当场撤销 (而且不报错)
#   · 删除时的"自动接任默认"同理, 把接任者的占位符也烤了
#   · 界面上"这是环境变量占位符"那条提示永远不匹配, 是死代码


def test_GET_返回库里的原样而不是插值后的值(client, monkeypatch):
    monkeypatch.setenv("TEST_BASE", "http://10.0.0.1/绝密uuid/v1")
    c, store, *_ = client
    m = _model("m1")
    m["upstream"]["api_base"] = "${TEST_BASE}"
    c.put("/api/admin/models/m1", json=m)

    got = c.get("/api/admin/models").json()["models"][0]
    assert got["upstream"]["api_base"] == "${TEST_BASE}", "不能把真实地址发给界面"


def test_界面往返一次不会把占位符烤死(client, monkeypatch):
    """GET → 改个无关字段 → PUT, 占位符必须还在.

    这正是"回迁跑完之后管理员第一次编辑就撤销了"的路径。
    """
    monkeypatch.setenv("TEST_BASE", "http://10.0.0.1/uuid/v1")
    c, store, *_ = client
    m = _model("m1")
    m["upstream"]["api_base"] = "${TEST_BASE}"
    c.put("/api/admin/models/m1", json=m)

    got = c.get("/api/admin/models").json()["models"][0]
    got["display_name"] = "改个名字"      # 界面上只动了一个无关字段
    c.put("/api/admin/models/m1", json=got)

    assert store["m1"]["upstream"]["api_base"] == "${TEST_BASE}"


def test_自动接任默认不会烤死接任者的占位符(client, monkeypatch):
    monkeypatch.setenv("TEST_BASE", "http://10.0.0.1/uuid/v1")
    c, store, *_ = client
    c.put("/api/admin/models/主力", json=_model("主力", default=True))
    heir = _model("备用")
    heir["upstream"]["api_base"] = "${TEST_BASE}"
    c.put("/api/admin/models/备用", json=heir)

    assert c.delete("/api/admin/models/主力").json()["promoted_default"] == "备用"
    assert store["备用"]["default"] is True
    assert store["备用"]["upstream"]["api_base"] == "${TEST_BASE}", (
        "接任时把占位符烤死了 —— 跟界面往返是同一个错"
    )


def test_解析不了的_env_变量会在接口上说明(client, monkeypatch):
    """否则"这个模型为什么不工作"在界面上没有任何线索, 只有服务器日志里一行."""
    monkeypatch.delenv("NO_SUCH", raising=False)
    c, store, *_ = client
    m = _model("坏的")
    m["upstream"]["api_base"] = "${NO_SUCH}"
    c.put("/api/admin/models/坏的", json=m)

    r = c.get("/api/admin/models").json()
    assert "坏的" in r["config_errors"]
    assert "NO_SUCH" in r["config_errors"]["坏的"]


# ── 播种必须用 yaml 原文 (7/30 头号 bug 的回归防护) ─────────────────────
#
# 这条之前**一行防护都没有** —— fixture 用的是 TestClient(app) 而不是
# with TestClient(app), lifespan 从来没被执行过, 把播种改回 config.models
# 全部测试照样绿。现在把那段抽成了 _seed_and_migrate_models() 直接测。


def test_播种进库的是_yaml_原文而不是插值后的值(tmp_path, monkeypatch):
    from catfish_gateway import app as A
    from catfish_gateway import config as C
    from catfish_gateway import model_store as MS

    p = tmp_path / "models.yaml"
    p.write_text(
        "version: 1\nmodels:\n"
        "  - name: m1\n    tier: private\n"
        '    display_name: "m1"\n'
        "    upstream:\n      model: openai/x\n"
        "      api_base: ${SEED_TEST_BASE}\n      api_key_env: K\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CATFISH_CONFIG", str(p))
    monkeypatch.setenv("SEED_TEST_BASE", "http://10.0.0.1/绝密uuid/v1")
    C._CACHE = None
    C._DB_EVER_SERVED = False

    seeded: list[dict] = []
    monkeypatch.setattr(MS, "is_enabled", lambda: True)
    monkeypatch.setattr(MS, "seed_from_yaml", lambda ms: (seeded.extend(ms), len(ms))[1])
    monkeypatch.setattr(MS, "restore_env_placeholders", lambda ms: ([], {}))

    A._seed_and_migrate_models()

    assert seeded, "没播种"
    assert seeded[0]["upstream"]["api_base"] == "${SEED_TEST_BASE}", (
        "播种用了插值后的 config.models —— 真实地址被烤进库, "
        "而且此后改 .env 不再生效"
    )


# ── default 必须全局唯一 (7/30 五修) ────────────────────────────────────
#
# Config.default_model() 返回的是列表里**第一个** default=True 的 ——
# 两个的话就取决于排序, 员工下次开聊用哪个模型不可预测, 而界面上看起来
# 只是"两行都挂着「默认」徽章", 不点进去没人会意识到这意味着什么。
#
# 鸿波 7/30 的库里就撞出了这个状态: catfish-private-main 被删过又加回来,
# 重启时播种按 models.yaml 插入它 (yaml 里 default: true), 而库里
# catfish-private-vision 已经因为"删默认后自动接任"挂着默认了。
# 播种只管 ON CONFLICT DO NOTHING, **完全不检查已经有没有默认模型**。


def test_播种不会带进来第二个默认(monkeypatch):
    """播种是"补齐缺失的出厂模型", 不该改变当前的默认."""
    from catfish_gateway import model_store as MS

    store = {"已有": {"name": "已有", "default": True}}
    inserted: list[dict] = []

    class _Cur:
        def __init__(self):
            self._r = None

        def execute(self, sql, args=None):
            if "count(*)" in sql:
                self._r = [sum(1 for v in store.values() if v.get("default"))]
            elif "INSERT" in sql:
                import json as _j

                m = _j.loads(args[1])
                inserted.append(m)
                if m["name"] not in store:
                    store[m["name"]] = m
                self.rowcount = 1
            else:
                self._r = [1]

        def fetchone(self):
            return self._r

        rowcount = 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Conn:
        def cursor(self):
            return _Cur()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(MS, "is_enabled", lambda: True)
    monkeypatch.setattr(MS, "_conn", lambda: _Conn())
    monkeypatch.setattr(MS, "_bump_revision", lambda cur: None)

    MS.seed_from_yaml([{"name": "新来的", "default": True}])

    assert inserted, "没播种"
    assert inserted[0]["default"] is False, (
        "库里已经有默认模型了, 播种不该再带进来一个 —— "
        "两个 default 时 default_model() 取决于排序"
    )


def test_库里已经有两个默认时清到只剩一个(monkeypatch):
    """光在播种时防住不够 —— 已经撞出来的库要能修回来."""
    from catfish_gateway import model_store as MS

    rows = [
        ("main", {"name": "main", "default": True}),
        ("vision", {"name": "vision", "default": True}),
    ]
    updated: dict[str, dict] = {}

    class _Cur:
        def execute(self, sql, args=None):
            if sql.startswith("SELECT"):
                self._r = rows
            else:
                import json as _j

                updated[args[1]] = _j.loads(args[0])

        def fetchall(self):
            return self._r

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Conn:
        def cursor(self):
            return _Cur()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(MS, "is_enabled", lambda: True)
    monkeypatch.setattr(MS, "_conn", lambda: _Conn())
    monkeypatch.setattr(MS, "_bump_revision", lambda cur: None)

    cleared = MS.enforce_single_default()
    assert cleared == ["vision"], "留 sort_order 最靠前的那个 (main)"
    assert updated["vision"]["default"] is False
    assert "main" not in updated, "第一个不该被动"
