"""11/03: 微信对话里的上下文压缩不带员工, 门户配额日志记成 client:hermes-cli。

辅助调用的员工原来只看 CV_CF_USER (Companion 经 api_server 的请求才有)。
"""
from __future__ import annotations

import contextvars
import types

import pytest

import plugin_runtime


@pytest.fixture
def env(monkeypatch):
    cf_user = contextvars.ContextVar("cf_user", default="")
    live = contextvars.ContextVar("runtime_main", default=None)
    registry = {"sess-wx": "alice@corp.cn"}
    monkeypatch.setattr(plugin_runtime, "_ctx", lambda: types.SimpleNamespace(CV_CF_USER=cf_user))
    monkeypatch.setattr(
        plugin_runtime, "_sib",
        lambda name: types.SimpleNamespace(lookup=registry.get) if name == "session_registry" else None,
    )
    monkeypatch.setattr(plugin_runtime, "_is_catfish_gateway_base_url", lambda u: True)
    aux = types.SimpleNamespace(
        _RUNTIME_MAIN_CONTEXT=live,
        _normalize_main_runtime=lambda m: {"base_url": "http://127.0.0.1:8999/v1"},
    )
    return aux, cf_user, live


def _user(aux, task="compression", main_runtime=None):
    return plugin_runtime._aux_identity_headers(aux, {}, task, main_runtime).get("X-Catfish-User")


def test_请求上下文里有员工就用它(env):
    aux, cf_user, live = env
    live.set({"session_id": "sess-wx"})
    cf_user.set("bob@corp.cn")
    assert _user(aux) == "bob@corp.cn"


def test_微信对话压缩_按本轮session查回员工(env):
    aux, _, live = env
    live.set({"session_id": "sess-wx"})
    assert _user(aux) == "alice@corp.cn"


def test_调用方给的main_runtime带了员工(env):
    aux, _, _ = env
    assert _user(aux, main_runtime={"catfish_outgoing_user": "carol@corp.cn"}) == "carol@corp.cn"


def test_都没有就不带_来源照标(env):
    aux, _, _ = env
    h = plugin_runtime._aux_identity_headers(aux, {}, "compression")
    assert "X-Catfish-User" not in h
    assert h["X-Catfish-Source"] == "hermes:aux:compression"
