"""P10 (10/22): 没有 inbound 来源时, 按 hermes 的 platform 标 X-Catfish-Source。

门户「配额日志」原来对 hermes 自己的定时任务 / 技能整理只显示 client:hermes-cli。
"""
from __future__ import annotations

import contextvars
import sys
import types

import pytest

import plugin_runtime


@pytest.fixture
def agent_cls(monkeypatch):
    class AIAgent:
        def __init__(self, platform=""):
            self.platform = platform
            self._client_kwargs = {}

        def _apply_client_headers_for_base_url(self, base_url, *a, **kw):
            pass

    monkeypatch.setitem(sys.modules, "run_agent", types.SimpleNamespace(AIAgent=AIAgent))
    cv = contextvars.ContextVar("cf_source", default="")
    monkeypatch.setattr(plugin_runtime, "_ctx", lambda: types.SimpleNamespace(CV_CF_SOURCE=cv))
    monkeypatch.setattr(plugin_runtime, "_resolver", lambda: types.SimpleNamespace(resolve_for_agent=lambda a: None))
    monkeypatch.setattr(plugin_runtime, "_is_catfish_gateway_base_url", lambda u: True)
    plugin_runtime._patch_p10_apply_client_headers_localhost()
    return AIAgent, cv


def _source(agent):
    return (agent._client_kwargs.get("default_headers") or {}).get("X-Catfish-Source")


def test_定时任务没有来源时标_hermes_cron(agent_cls):
    AIAgent, _ = agent_cls
    a = AIAgent("cron")
    a._apply_client_headers_for_base_url("http://127.0.0.1:8999/v1")
    assert _source(a) == "hermes:cron"


def test_外面带来的来源优先(agent_cls):
    AIAgent, cv = agent_cls
    a = AIAgent("api_server")
    token = cv.set("companion-advisor")
    try:
        a._apply_client_headers_for_base_url("http://127.0.0.1:8999/v1")
    finally:
        cv.reset(token)
    assert _source(a) == "companion-advisor"


def test_没有_platform_就不标(agent_cls):
    AIAgent, _ = agent_cls
    a = AIAgent("")
    a._apply_client_headers_for_base_url("http://127.0.0.1:8999/v1")
    assert _source(a) is None


def test_后台分类器请求关思考_聊天不关(agent_cls):
    """10/31: 邮件分诊 / 钓鱼复审 (P44 白名单) 经 hermes 开着思考一批 60–126 秒, 超时全挂。"""
    AIAgent, cv = agent_cls
    for source, expect in (("companion-email-scheduler", "off"), ("companion-phishing-scan", "off"),
                           ("companion-chat", None)):
        a = AIAgent("api_server")
        token = cv.set(source)
        try:
            a._apply_client_headers_for_base_url("http://127.0.0.1:8999/v1")
        finally:
            cv.reset(token)
        assert (a._client_kwargs.get("default_headers") or {}).get("X-Catfish-Thinking") == expect, source
