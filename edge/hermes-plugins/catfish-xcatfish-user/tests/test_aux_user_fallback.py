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


# ── 后台压缩 (P61 空闲压缩 / P64 会话卫生) 按消息来源补员工 ──

class _Platform:
    value = "weixin"


def _src(uid="o9cq@im.wechat"):
    return types.SimpleNamespace(user_id=uid, platform=_Platform())


@pytest.fixture
def resolved(monkeypatch):
    cv = contextvars.ContextVar("cf_user2", default="")
    seen = []

    def resolve(agent):
        seen.append((agent._user_id, agent.platform))
        return "alice@corp.cn"

    monkeypatch.setattr(plugin_runtime, "_ctx", lambda: types.SimpleNamespace(CV_CF_USER=cv))
    monkeypatch.setattr(plugin_runtime, "_resolver", lambda: types.SimpleNamespace(resolve_for_agent=resolve))
    return cv, seen


def test_消息来源换成员工_走同一条resolver链(resolved):
    _, seen = resolved
    assert plugin_runtime.user_for_source(_src()) == "alice@corp.cn"
    assert seen == [("o9cq@im.wechat", "weixin")]


def test_没有用户id不去兜底默认员工(resolved):
    _, seen = resolved
    assert plugin_runtime.user_for_source(_src("")) == ""
    assert seen == []


def test_P61_后台压缩的context里带上员工(resolved):
    cv, _ = resolved
    ctx = contextvars.copy_context()
    plugin_runtime.bind_user_in_context(ctx, _src())
    assert ctx.run(cv.get) == "alice@corp.cn"
    assert cv.get() == "", "不能漏到当前 context"


def test_P64_会话卫生压缩期间带员工_完了复原(resolved, monkeypatch):
    import asyncio
    import sys

    cv, _ = resolved
    got = []

    class GatewayTurnMixin:
        async def _hmwa_hygiene_detached_attempt(self, attempt, hs, plan, history, _hyg_msgs, _hyg_model,
                                                 _hyg_runtime, source, session_entry, session_key, _quick_key,
                                                 run_generation):
            got.append(contextvars.copy_context().run(cv.get))

    monkeypatch.setitem(sys.modules, "gateway.run_turn", types.SimpleNamespace(GatewayTurnMixin=GatewayTurnMixin))
    # CI 里没有 hermes: run_agent 不在, P64b 装不上也不能连累 P64 (11/03 CI 就是这么挂的)
    monkeypatch.setitem(sys.modules, "run_agent", None)
    plugin_runtime._patch_p64_hygiene_compression_user()
    asyncio.run(GatewayTurnMixin()._hmwa_hygiene_detached_attempt(
        None, None, None, [], [], "m", {}, _src(), None, "k", "q", 1))
    assert got == ["alice@corp.cn"]
    assert cv.get() == ""


def test_P64b_对话中途压缩从活agent取员工(resolved, monkeypatch):
    import sys

    cv, seen = resolved
    got = []

    class AIAgent:
        def __init__(self, uid, platform):
            self._user_id, self.platform, self._catfish_outgoing_user = uid, platform, ""

        def _compress_context(self, messages, system_message, **kw):
            got.append(cv.get())
            return messages, system_message

    monkeypatch.setitem(sys.modules, "run_agent", types.SimpleNamespace(AIAgent=AIAgent))
    plugin_runtime._patch_p64b_compress_context_user()
    AIAgent("o9cq@im.wechat", "weixin")._compress_context([], "", trigger="post_tool")
    assert got == ["alice@corp.cn"] and cv.get() == ""

    # 临时压缩 agent 没有平台用户: 不去兜底默认员工
    seen.clear()
    AIAgent("", "gateway_hygiene")._compress_context([], "")
    assert got[-1] == "" and seen == []

    # 已有员工 (Companion / P61 / P64 设的) 不覆盖
    token = cv.set("bob@corp.cn")
    try:
        AIAgent("o9cq@im.wechat", "weixin")._compress_context([], "")
    finally:
        cv.reset(token)
    assert got[-1] == "bob@corp.cn"
