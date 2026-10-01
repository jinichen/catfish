"""BL-EDGE-TOOL-KEY (5/24 鸿波): edge_tool_config 模块单测.

测的是 module-level registry + build_response 的纯逻辑 (404 / 503 / 200; 10/2 起 backend 可配, 默认 parallel).
endpoint 装饰那层 (FastAPI Depends + HTTPException 包装) 不在这里测 —
那走的是 e2e curl 验证 (DEPLOYMENT-RUNBOOK §15 第 6 步).
"""
from __future__ import annotations

import pytest

from catfish_gateway import edge_tool_config as etc


# ── registry 完整性 ─────────────────────────────────────────────


def test_supported_tools_contains_web_search_and_extract():
    """5/24 first ship scope = web_search + web_extract (+ web_crawl 共用 backend)."""
    supported = etc.list_supported_tools()
    assert "web_search" in supported
    assert "web_extract" in supported
    # web_crawl 在 registry (跟 search/extract 共用 backend), 即使 RBAC 未 seed 也算"中央可派发"
    assert "web_crawl" in supported


def test_supported_tools_returns_sorted():
    """CLI 依赖稳定顺序 (虽然内部 dedupe, 调用顺序仍想 deterministic)."""
    supported = etc.list_supported_tools()
    assert supported == sorted(supported)


def test_is_supported_tool_unknown_returns_false():
    """不在 registry 的 tool → false → endpoint 后续返 404."""
    assert etc.is_supported_tool("image_generate") is False
    assert etc.is_supported_tool("session_search") is False
    assert etc.is_supported_tool("") is False


def test_web_tool_group_consistent():
    """web_search / web_extract / web_crawl 必须 tool_group=='web' (CLI 用 group dedupe)."""
    for name in ("web_search", "web_extract", "web_crawl"):
        assert etc.EDGE_TOOL_REGISTRY[name].tool_group == "web"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    """开发机 / CI 的 gateway env 里可能真有这些, 每个用例从干净状态开始。"""
    monkeypatch.delenv("CATFISH_WEB_BACKEND", raising=False)
    for b in etc.WEB_BACKENDS.values():
        monkeypatch.delenv(b.key_env, raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)


def test_tavily_never_offered():
    """10/2: hermes 已经没有 tavily 插件, 中央再发它 = 每 50 分钟把员工 web 工具全打挂。"""
    assert "tavily" not in etc.WEB_BACKENDS
    assert etc.DEFAULT_WEB_BACKEND in etc.WEB_BACKENDS
    assert etc.WEB_BACKENDS[etc.DEFAULT_WEB_BACKEND].keyless


# ── 默认: parallel 免费通道 ───────────────────────────────────────


def test_default_is_parallel_keyless_200():
    status, body = etc.build_response("web_search")
    assert status == 200
    assert body["provider"] == "parallel"
    assert body["env_vars"] == {}
    assert body["yaml_block"] == {"web": {"backend": "parallel"}}


def test_stale_tavily_key_in_gateway_env_is_ignored(monkeypatch: pytest.MonkeyPatch):
    """gateway .env 里还留着 5/24 的 TAVILY_API_KEY —— 不能再发下去。"""
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-old")
    status, body = etc.build_response("web_search")
    assert status == 200
    assert "tavily" not in str(body).lower()


def test_paid_key_sent_when_configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "  par-123 \n")
    status, body = etc.build_response("web_extract")
    assert status == 200
    assert body["env_vars"] == {"PARALLEL_API_KEY": "par-123"}


# ── admin 换 backend ─────────────────────────────────────────────


def test_backend_switch_via_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CATFISH_WEB_BACKEND", " EXA ")
    monkeypatch.setenv("EXA_API_KEY", "exa-k")
    status, body = etc.build_response("web_search")
    assert status == 200
    assert body["yaml_block"] == {"web": {"backend": "exa"}}
    assert body["env_vars"] == {"EXA_API_KEY": "exa-k"}


def test_keyed_backend_without_key_returns_503(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CATFISH_WEB_BACKEND", "brave-free")
    status, body = etc.build_response("web_search")
    assert status == 503
    assert "BRAVE_SEARCH_API_KEY" in body["error"]
    assert "yaml_block" not in body


def test_unknown_backend_returns_503_not_pushed(monkeypatch: pytest.MonkeyPatch):
    """admin 把 .env 写成 tavily (或拼错) → 503, CLI 什么都不写, 不会把员工配置刷坏。"""
    monkeypatch.setenv("CATFISH_WEB_BACKEND", "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-x")
    status, body = etc.build_response("web_search")
    assert status == 503
    assert "yaml_block" not in body and "env_vars" not in body
    assert "parallel" in body["error"]


# ── 其它 ─────────────────────────────────────────────────────────


def test_build_response_unknown_tool_returns_404():
    status, body = etc.build_response("image_generate")
    assert status == 404
    assert "web_search" in body["supported"]


def test_search_and_extract_share_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "par-shared")
    _, a = etc.build_response("web_search")
    _, b = etc.build_response("web_extract")
    assert a["env_vars"] == b["env_vars"] and a["yaml_block"] == b["yaml_block"]


def test_does_not_leak_other_backend_keys(monkeypatch: pytest.MonkeyPatch):
    """gateway 进程里别家的 key 不该跟着发下去。"""
    monkeypatch.setenv("PARALLEL_API_KEY", "par-x")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-secret-NOT-FOR-CALLER")
    _, body = etc.build_response("web_search")
    assert body["env_vars"] == {"PARALLEL_API_KEY": "par-x"}
    assert "fc-secret-NOT-FOR-CALLER" not in str(body)


def test_group_provider_metadata(monkeypatch: pytest.MonkeyPatch):
    assert etc.group_provider("web") == ("parallel", "PARALLEL_API_KEY")
    monkeypatch.setenv("CATFISH_WEB_BACKEND", "nope")
    assert etc.group_provider("web") == ("nope", "")
