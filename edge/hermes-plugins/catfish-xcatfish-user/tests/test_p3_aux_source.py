"""P3 (10/25): hermes 的辅助调用 (看图 / 上下文压缩 / 起标题) 带 X-Catfish-Source: hermes:aux:<task>。

门户配额日志原来把这些显示成「未标来源」。只发给 catfish 网关, 第三方端点不带。
"""
from __future__ import annotations

import contextvars
import sys
import types

import pytest

import plugin_runtime


@pytest.fixture
def aux(monkeypatch):
    seen = []
    mod = types.SimpleNamespace(
        _plan_aux_call=lambda *a, **kw: seen.append((a, kw)),
        _normalize_main_runtime=lambda _x: {"base_url": "http://127.0.0.1:8999/v1"},
    )
    agent_pkg = types.ModuleType("agent")
    agent_pkg.auxiliary_client = mod
    monkeypatch.setitem(sys.modules, "agent", agent_pkg)
    monkeypatch.setitem(sys.modules, "agent.auxiliary_client", mod)
    monkeypatch.setattr(plugin_runtime, "_sib", lambda name: types.SimpleNamespace(patch=lambda a: None))
    user = contextvars.ContextVar("cf_user", default="")
    monkeypatch.setattr(plugin_runtime, "_ctx", lambda: types.SimpleNamespace(CV_CF_USER=user))
    monkeypatch.setattr(plugin_runtime, "_is_catfish_gateway_base_url", lambda u: "8999" in u)
    plugin_runtime._patch_p3_auxiliary_client()
    return mod, seen, user


def _headers(seen):
    return seen[-1][1].get("extra_headers") or {}


def test_看图_压缩_起标题_各自标来源(aux):
    mod, seen, _ = aux
    mod._plan_aux_call("vision", async_mode=True, messages=[])
    assert _headers(seen)["X-Catfish-Source"] == "hermes:aux:vision"
    mod._plan_aux_call(task="compression", messages=[])
    assert _headers(seen)["X-Catfish-Source"] == "hermes:aux:compression"
    mod._plan_aux_call(None, messages=[])
    assert _headers(seen)["X-Catfish-Source"] == "hermes:aux"


def test_没有员工上下文也标来源_有就带员工(aux):
    mod, seen, user = aux
    mod._plan_aux_call("title_generation", messages=[])
    assert "X-Catfish-User" not in _headers(seen)
    tok = user.set("alice@x.com")
    try:
        mod._plan_aux_call("title_generation", messages=[])
    finally:
        user.reset(tok)
    assert _headers(seen) == {"X-Catfish-User": "alice@x.com", "X-Catfish-Source": "hermes:aux:title_generation"}


def test_调用方自己给的来源不覆盖_第三方端点不带(aux):
    mod, seen, _ = aux
    mod._plan_aux_call("vision", messages=[], extra_headers={"X-Catfish-Source": "plugin:x"})
    assert _headers(seen)["X-Catfish-Source"] == "plugin:x"
    mod._plan_aux_call("vision", messages=[], base_url="https://api.openai.com/v1")
    assert "X-Catfish-Source" not in _headers(seen)
