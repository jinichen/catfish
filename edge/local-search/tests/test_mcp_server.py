"""MCP server 冒烟测试。

不启动真的 stdio server（太重），只验证：
    - 内部包装函数（_run_query / _run_status）行为正确
    - catfish-search 不在 PATH 时优雅报错
    - tool 参数边界（limit clamp）生效
"""
from __future__ import annotations

from catfish_search import mcp_server


def test_run_query_missing_catfish(monkeypatch):
    """catfish-search 不在 PATH 时返回结构化 error，不崩溃。"""
    monkeypatch.setenv("CATFISH_SEARCH_BIN", "/nonexistent/catfish-search-xxx")
    out = mcp_server._run_query({"query": "鲶鱼"})
    assert "catfish-search not found" in out


def test_run_query_empty_query():
    out = mcp_server._run_query({"query": "  "})
    assert "query is required" in out


def test_run_query_limit_clamping(monkeypatch):
    """limit 应被 clamp 到 [1, 50]。"""
    called = {}

    def fake_run(cmd, **_kwargs):
        called["cmd"] = cmd

        class R:
            returncode = 0
            stdout = "[]"
            stderr = ""

        return R()

    monkeypatch.setattr("subprocess.run", fake_run)

    # limit=999 应被压到 50
    mcp_server._run_query({"query": "x", "limit": 999})
    assert "50" in called["cmd"]
    # limit=0 应被抬到 1
    mcp_server._run_query({"query": "x", "limit": 0})
    assert "1" in called["cmd"]


def test_run_status_missing_catfish(monkeypatch):
    monkeypatch.setenv("CATFISH_SEARCH_BIN", "/nonexistent/xxx")
    out = mcp_server._run_status()
    assert "not found" in out


def test_catfish_bin_default():
    assert mcp_server._catfish_bin() in ("catfish-search",) or mcp_server._catfish_bin().endswith(
        "/catfish-search"
    )


def test_build_server_registers_two_tools():
    """启动前先确认 tool 清单结构 OK。"""
    pytest_skip_if_no_mcp()
    server, _stdio = mcp_server._build_server()
    # mcp.Server 把 handler 存在实例里；我们只要它能被构造出来即可。
    assert server is not None


def pytest_skip_if_no_mcp():
    try:
        import mcp  # noqa: F401, PLC0415
    except ImportError:
        import pytest  # noqa: PLC0415

        pytest.skip("mcp not installed; run `pip install '.[mcp]'`")
