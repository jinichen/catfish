"""11/03: 绑了员工的微信, 门户配额日志里还是 openid —— resolver (b) 调的 get_email 现在的 hermes 没有。"""
from __future__ import annotations

import sys
import types

import pytest

import resolver

OPENID = "o9cq807yh8QQuk8jp4IEWb_900RE@im.wechat"


@pytest.fixture
def fake_pairing(monkeypatch):
    approved = {"weixin": {OPENID: {"user_name": "", "catfish_email": "alice@corp.cn"}, "other@im.wechat": {}}}

    class PairingStore:  # 跟现在的 hermes 一样: 没有 get_email
        def _load_approved(self, platform):
            return approved.get(platform, {})

    mod = types.SimpleNamespace(
        PairingStore=PairingStore,
        _matching_ids=lambda platform, d, uid: [k for k in d if k == uid],
    )
    pkg = types.ModuleType("gateway")
    pkg.pairing = mod
    monkeypatch.setitem(sys.modules, "gateway", pkg)
    monkeypatch.setitem(sys.modules, "gateway.pairing", mod)
    return approved


def _agent(uid, platform="weixin"):
    return types.SimpleNamespace(_catfish_outgoing_user="", _user_id=uid, platform=platform)


def test_绑了员工的微信用员工邮箱(fake_pairing):
    assert resolver.resolve_for_agent(_agent(OPENID)) == "alice@corp.cn"


def test_没绑的还是openid(fake_pairing):
    assert resolver.resolve_for_agent(_agent("other@im.wechat")) == "other@im.wechat"


def test_有get_email的老hermes照旧用(fake_pairing, monkeypatch):
    mod = sys.modules["gateway.pairing"]
    monkeypatch.setattr(mod.PairingStore, "get_email", lambda self, p, u: "bob@corp.cn", raising=False)
    assert resolver.resolve_for_agent(_agent(OPENID)) == "bob@corp.cn"


def test_hermes里没有pairing模块也不炸(monkeypatch):
    monkeypatch.setitem(sys.modules, "gateway", None)
    assert resolver.resolve_for_agent(_agent("u1", "telegram")) == "u1@im.telegram"
