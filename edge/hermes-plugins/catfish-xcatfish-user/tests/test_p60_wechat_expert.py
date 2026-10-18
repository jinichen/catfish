"""P60 微信里找专家 (10/18): 「找X」/「回小鲶」/「找谁」命令 + 按聊天改派 profile。

全部虚构; CATFISH_HOME 指到临时目录, hermes 的类用假的。
"""
from __future__ import annotations

import asyncio
import types

import pytest

import plugin_wechat_expert as px


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    for eid, name, enabled in (("qual", "资质专员", True), ("fin", "报销助手", True), ("off", "停用的", False)):
        d = tmp_path / "experts" / eid
        d.mkdir(parents=True)
        (d / "expert.yaml").write_text(f"id: {eid}\nname: {name}\ndescription: 管{name}\nenabled: {str(enabled).lower()}\n",
                                       encoding="utf-8")
    return tmp_path


SERVED = {"expert-qual", "expert-fin", "expert-off"}


def test_找专家_写路由_回小鲶_删掉(home):
    r = px.handle_command("找资质专员", "u1", SERVED)
    assert "交给「资质专员」" in r
    assert px.read_routes()["u1"]["profile"] == "expert-qual"
    assert "现在就是「资质专员」" in px.handle_command("找 资质专员", "u1", SERVED)
    assert "切回小鲶" in px.handle_command("回小鲶", "u1", SERVED)
    assert "u1" not in px.read_routes()
    assert px.handle_command("回小鲶", "u1", SERVED) == "现在就是小鲶在回你。"


def test_不是专家名字的不吃掉_交给agent(home):
    assert px.handle_command("找一下上周的合同", "u1", SERVED) is None
    assert px.handle_command("找停用的", "u1", SERVED) is None, "停用的专家不算"
    assert px.handle_command("帮我找资质专员审一下这三条待办, 今天要", "u1", SERVED) is None, "长句不是命令"
    assert px.handle_command("找资质专员", "u1", {"expert-fin"}) is None, "没在跑的不算"
    assert px.read_routes() == {}


def test_找谁_列出来(home):
    r = px.handle_command("找谁", "u1", SERVED)
    assert "资质专员" in r and "报销助手" in r and "停用的" not in r and "现在是小鲶" in r


def test_找谁_没专家时交给agent(home):
    assert px.handle_command("找谁", "u1", set()) is None


def test_模糊名字_唯一就切_多个就问(home):
    assert "资质专员" in px.handle_command("找资质", "u2", SERVED)
    (home / "experts" / "q2").mkdir()
    (home / "experts" / "q2" / "expert.yaml").write_text("id: q2\nname: 资质审核员\n", encoding="utf-8")
    assert "哪一个" in px.handle_command("找资质", "u3", SERVED | {"expert-q2"})


# ── 改派 profile ─────────────────────────────────────────────────

class _Runner:
    config = types.SimpleNamespace(multiplex_profiles=True)

    def _transport_owner(self, source):
        return source._owner


def _src(chat_id, platform="weixin", owner=None):
    s = types.SimpleNamespace(platform=types.SimpleNamespace(value=platform), chat_id=chat_id, _owner=owner)
    s._transport_adapter_ref = lambda: None
    return s


def test_改派_只对主Bot的微信_目标要在托管集合里(home, monkeypatch):
    monkeypatch.setattr(px, "_served_profiles", lambda runner: {"expert-qual"})
    px.set_route("u1", "expert-qual", "资质专员")
    px.set_route("u2", "expert-gone", "删掉的")
    f = px.wrap_profile_name_for_source(lambda self, source, adapter_profile=None: None)
    r = _Runner()
    assert f(r, _src("u1")) == "expert-qual"
    assert f(r, _src("u2")) is None, "专家删了 → 回主小鲶, 不拒收"
    assert f(r, _src("u3")) is None
    assert f(r, _src("u1", platform="telegram")) is None
    assert f(r, _src("u1", owner=("weixin", "expert-fin"))) is None, "专家自己的微信号不管"
    assert f(r, _src("u1"), adapter_profile="expert-fin") is None


def test_静态路由优先(home, monkeypatch):
    monkeypatch.setattr(px, "_served_profiles", lambda runner: {"expert-qual"})
    px.set_route("u1", "expert-qual")
    f = px.wrap_profile_name_for_source(lambda self, source, adapter_profile=None: "finance")
    assert f(_Runner(), _src("u1")) == "finance"


def test_没开多路复用就不改派(home, monkeypatch):
    monkeypatch.setattr(px, "_served_profiles", lambda runner: {"expert-qual"})
    px.set_route("u1", "expert-qual")
    r = _Runner()
    r.config = types.SimpleNamespace(multiplex_profiles=False)
    assert px.wrap_profile_name_for_source(lambda *a, **k: None)(r, _src("u1")) is None


def test_路由文件坏了_当没有(home):
    (home / px.ROUTES_FILE).write_text("{bad", encoding="utf-8")
    assert px.read_routes() == {}


# ── 收命令 ──────────────────────────────────────────────────────

class _Adapter:
    def __init__(self, owner=None):
        self._owner_profile = owner
        self.gateway_runner = _Runner()
        self.sent = []

    async def send(self, chat_id, text):
        self.sent.append((chat_id, text))


def _event(text, chat_id="u1"):
    return types.SimpleNamespace(text=text, source=types.SimpleNamespace(chat_id=chat_id))


def test_主Bot收到命令_回确认_不进agent(home, monkeypatch):
    monkeypatch.setattr(px, "_served_profiles", lambda runner: SERVED)
    passed = []

    async def orig(self, event):
        passed.append(event.text)

    h = px.wrap_handle_message(orig)
    a = _Adapter()
    asyncio.run(h(a, _event("找资质专员")))
    asyncio.run(h(a, _event("今天天气")))
    assert passed == ["今天天气"]
    assert a.sent and "资质专员" in a.sent[0][1]


def test_专家自己的微信号_命令照常交给agent(home, monkeypatch):
    monkeypatch.setattr(px, "_served_profiles", lambda runner: SERVED)
    passed = []

    async def orig(self, event):
        passed.append(event.text)

    asyncio.run(px.wrap_handle_message(orig)(_Adapter(owner="expert-fin"), _event("找资质专员")))
    assert passed == ["找资质专员"] and px.read_routes() == {}


def test_装两次不叠(monkeypatch):
    orig = lambda self, source, adapter_profile=None: None  # noqa: E731
    w = px.wrap_profile_name_for_source(orig)
    assert w._catfish_p60
