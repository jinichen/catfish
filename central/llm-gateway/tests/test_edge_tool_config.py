"""BL-EDGE-TOOL-KEY (5/24 鸿波): edge_tool_config 模块单测.

测的是 module-level registry + build_response 的纯逻辑 (3 个分支: 404 / 503 / 200).
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
    """web_search / web_extract / web_crawl 必须 tool_group=='web' (CLI 用 group dedupe).

    不一致会让 CLI 拉 3 次 endpoint 写 3 次同样的 .env 不浪费 (无害)
    但日志会刷, 而且日后扩展 group 概念也会乱.
    """
    for name in ("web_search", "web_extract", "web_crawl"):
        cfg = etc.EDGE_TOOL_REGISTRY[name]
        assert cfg.tool_group == "web", f"{name} 不在 'web' group: {cfg.tool_group}"
        assert cfg.env_var_name == "TAVILY_API_KEY"
        assert cfg.provider == "tavily"


def test_web_yaml_block_uses_single_backend():
    """yaml_block 必须是 {web: {backend: tavily}} — hermes 0.14 'single backend' 模式.

    如果哪天我们改成 per-capability (search_backend/extract_backend), CLI 那边
    yaml merge 的 sibling 保留逻辑也要对照改, 这个测帮我们捕到偏移.
    """
    cfg = etc.EDGE_TOOL_REGISTRY["web_search"]
    assert cfg.yaml_block == {"web": {"backend": "tavily"}}


# ── get_env_value ────────────────────────────────────────────────


def test_get_env_value_missing_returns_none(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert etc.get_env_value("web_search") is None


def test_get_env_value_empty_string_returns_none(monkeypatch: pytest.MonkeyPatch):
    """空白 / 只有空格 应该当作"没配", 不发坏 key 给员工."""
    monkeypatch.setenv("TAVILY_API_KEY", "")
    assert etc.get_env_value("web_search") is None
    monkeypatch.setenv("TAVILY_API_KEY", "   ")
    assert etc.get_env_value("web_search") is None


def test_get_env_value_real_key_returned_trimmed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TAVILY_API_KEY", "  tvly-abc123  \n")
    assert etc.get_env_value("web_search") == "tvly-abc123"


def test_get_env_value_unknown_tool_returns_none(monkeypatch: pytest.MonkeyPatch):
    """不在 registry → 直接 None (不去读任何 env), build_response 上层会返 404."""
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-abc")
    assert etc.get_env_value("nonexistent_tool") is None


# ── build_response ───────────────────────────────────────────────


def test_build_response_unknown_tool_returns_404(monkeypatch: pytest.MonkeyPatch):
    status, body = etc.build_response("image_generate")
    assert status == 404
    assert "supported" in body
    assert "web_search" in body["supported"]


def test_build_response_no_env_returns_503(monkeypatch: pytest.MonkeyPatch):
    """admin 还没配 .env → 503, 不是 500. 503 表示 "服务还没准备好"."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    status, body = etc.build_response("web_search")
    assert status == 503
    assert "TAVILY_API_KEY" in body["error"]
    assert body["tool_name"] == "web_search"
    assert body["provider"] == "tavily"


def test_build_response_success_shape(monkeypatch: pytest.MonkeyPatch):
    """200 路径返完整 payload — CLI 直接 merge env_vars + yaml_block."""
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-secret-xyz")
    status, body = etc.build_response("web_search")
    assert status == 200
    assert body["tool_name"] == "web_search"
    assert body["tool_group"] == "web"
    assert body["provider"] == "tavily"
    assert body["env_vars"] == {"TAVILY_API_KEY": "tvly-secret-xyz"}
    assert body["yaml_block"] == {"web": {"backend": "tavily"}}


def test_build_response_extract_and_search_share_env(monkeypatch: pytest.MonkeyPatch):
    """web_search 和 web_extract 必须返同样的 env_vars + yaml_block (共用 Tavily)."""
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-shared")
    _, body_search = etc.build_response("web_search")
    _, body_extract = etc.build_response("web_extract")
    assert body_search["env_vars"] == body_extract["env_vars"]
    assert body_search["yaml_block"] == body_extract["yaml_block"]
    assert body_search["tool_group"] == body_extract["tool_group"] == "web"


def test_build_response_does_not_leak_other_envs(monkeypatch: pytest.MonkeyPatch):
    """gateway 进程环境里也可能有 FIRECRAWL_API_KEY 等 — 不该出现在 web_search 响应里."""
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-x")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-secret-NOT-FOR-CALLER")
    _, body = etc.build_response("web_search")
    assert body["env_vars"] == {"TAVILY_API_KEY": "tvly-x"}
    # Firecrawl key 不该在响应里
    assert "FIRECRAWL_API_KEY" not in body["env_vars"]
    # 也不该意外泄进 yaml_block
    assert "fc-secret-NOT-FOR-CALLER" not in str(body)
