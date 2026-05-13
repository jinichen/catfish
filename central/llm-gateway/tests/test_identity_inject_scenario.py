"""BL-SOUL-SCENARIO P2 (5/13 鸿波"完成 SOUL 优化 P2") — gateway 按 tools 候选
注入 SOUL_<scenario>.md (BROWSER / EXECUTE_CODE / 等), 不再永远全量灌.

设计:
- tools=None / 空 → 不注入场景段 (跟现有 chat 不调工具的兼容)
- tools 含 catfish_browser_* → 注入 SOUL_BROWSER.md
- tools 含 execute_code → 注入 SOUL_EXECUTE_CODE.md
- 多场景 → 多段注入 (顺序跟 SCENARIO_RULES 一致)
- 文件不存在 → 静默跳过, 不抛
"""
from __future__ import annotations

from pathlib import Path

import pytest

from catfish_gateway import identity_inject


@pytest.fixture
def fake_hermes_home(tmp_path: Path, monkeypatch):
    home = tmp_path / "fake_hermes"
    home.mkdir()
    monkeypatch.setattr(identity_inject, "_hermes_home", lambda: home)
    # 清掉模块级 cache (上轮测试可能污染)
    identity_inject._cache._store.clear()
    yield home


def _write_files(home: Path):
    """通用 fixture 写法: 主 SOUL + 2 个场景 SOUL."""
    (home / "SOUL.md").write_text("# CORE\n通用铁律", encoding="utf-8")
    (home / "SOUL_BROWSER.md").write_text(
        "# BROWSER\n找按钮优先 find_by_text(role='button')",
        encoding="utf-8",
    )
    (home / "SOUL_EXECUTE_CODE.md").write_text(
        "# EXECUTE_CODE\nsandbox 拿不到 browser session",
        encoding="utf-8",
    )


# ─── _detect_scenarios ──────────────────────────────────────


def test_detect_scenarios_none_returns_empty():
    assert identity_inject._detect_scenarios(None) == []


def test_detect_scenarios_empty_list_returns_empty():
    assert identity_inject._detect_scenarios([]) == []


def test_detect_scenarios_browser_tool():
    tools = [
        {"function": {"name": "catfish_browser_goto"}},
    ]
    out = identity_inject._detect_scenarios(tools)
    assert ("BROWSER", "SOUL_BROWSER.md") in out


def test_detect_scenarios_execute_code():
    tools = [{"function": {"name": "execute_code"}}]
    out = identity_inject._detect_scenarios(tools)
    assert ("EXECUTE_CODE", "SOUL_EXECUTE_CODE.md") in out


def test_detect_scenarios_both_triggered():
    tools = [
        {"function": {"name": "catfish_browser_click"}},
        {"function": {"name": "execute_code"}},
    ]
    out = identity_inject._detect_scenarios(tools)
    labels = [t[0] for t in out]
    assert "BROWSER" in labels
    assert "EXECUTE_CODE" in labels


def test_detect_scenarios_unrelated_tools_no_trigger():
    """普通 chat 工具 (read_file / write_file) 不该触发场景段."""
    tools = [
        {"function": {"name": "read_file"}},
        {"function": {"name": "search_sessions"}},
    ]
    assert identity_inject._detect_scenarios(tools) == []


def test_detect_scenarios_handles_malformed_tools():
    """tools 里有非 dict / 缺 function / name 缺 → 不抛."""
    tools = [
        "not_a_dict",
        {"no_function": True},
        {"function": {"name": None}},
        {"function": {}},
        {"function": {"name": "execute_code"}},  # 这个该触发
    ]
    out = identity_inject._detect_scenarios(tools)
    assert ("EXECUTE_CODE", "SOUL_EXECUTE_CODE.md") in out


# ─── build_identity_content with tools ──────────────────────


def test_no_tools_no_scenario_inject(fake_hermes_home: Path):
    """tools=None → 只 CORE SOUL, 不载场景段 (省 token)."""
    _write_files(fake_hermes_home)
    content = identity_inject.build_identity_content(tools=None)
    assert "通用铁律" in content
    assert "找按钮优先" not in content
    assert "sandbox 拿不到" not in content


def test_browser_tool_loads_browser_scenario(fake_hermes_home: Path):
    _write_files(fake_hermes_home)
    tools = [{"function": {"name": "catfish_browser_screenshot"}}]
    content = identity_inject.build_identity_content(tools=tools)
    assert "通用铁律" in content
    assert "找按钮优先" in content
    assert "Identity (SOUL_BROWSER — 场景纪律)" in content
    # 没 execute_code 工具 → 不载 EXECUTE_CODE 段
    assert "sandbox 拿不到" not in content


def test_execute_code_tool_loads_execute_code_scenario(fake_hermes_home: Path):
    _write_files(fake_hermes_home)
    tools = [{"function": {"name": "execute_code"}}]
    content = identity_inject.build_identity_content(tools=tools)
    assert "通用铁律" in content
    assert "sandbox 拿不到" in content
    assert "Identity (SOUL_EXECUTE_CODE — 场景纪律)" in content
    assert "找按钮优先" not in content


def test_both_tools_load_both_scenarios(fake_hermes_home: Path):
    _write_files(fake_hermes_home)
    tools = [
        {"function": {"name": "catfish_browser_goto"}},
        {"function": {"name": "execute_code"}},
    ]
    content = identity_inject.build_identity_content(tools=tools)
    assert "通用铁律" in content
    assert "找按钮优先" in content
    assert "sandbox 拿不到" in content


def test_scenario_file_missing_silent_skip(fake_hermes_home: Path):
    """SOUL_BROWSER.md 没装 → 不抛, 主 SOUL 仍注入."""
    (fake_hermes_home / "SOUL.md").write_text("# CORE", encoding="utf-8")
    # 不写 SOUL_BROWSER.md
    tools = [{"function": {"name": "catfish_browser_goto"}}]
    content = identity_inject.build_identity_content(tools=tools)
    assert "# CORE" in content
    # 应该不含 BROWSER label (文件没装就没有)
    assert "Identity (SOUL_BROWSER" not in content


# ─── inject_identity_if_needed 透传 tools ────────────────────


def test_inject_passes_tools_through(fake_hermes_home: Path):
    _write_files(fake_hermes_home)
    messages: list = [{"role": "user", "content": "登录 EIS"}]
    tools = [{"function": {"name": "catfish_browser_goto"}}]
    out = identity_inject.inject_identity_if_needed(messages, tools=tools)
    # 第一条应该是注入的 system
    assert out[0]["role"] == "system"
    assert "找按钮优先" in out[0]["content"]


def test_inject_no_tools_argument_compat(fake_hermes_home: Path):
    """老调用方没传 tools → 兼容, 不抛, 不注场景段."""
    _write_files(fake_hermes_home)
    messages: list = [{"role": "user", "content": "你好"}]
    out = identity_inject.inject_identity_if_needed(messages)
    assert out[0]["role"] == "system"
    assert "通用铁律" in out[0]["content"]
    assert "找按钮优先" not in out[0]["content"]


# ─── token 节省验证 ────────────────────────────────────────


def test_no_tools_content_is_shorter_than_with_tools(fake_hermes_home: Path):
    """简单 chat (无 tools) 的注入长度 < 调浏览器 chat 的注入长度.
    这是 P2 优化的核心收益验证."""
    _write_files(fake_hermes_home)
    short = identity_inject.build_identity_content(tools=None)
    long_ = identity_inject.build_identity_content(
        tools=[{"function": {"name": "catfish_browser_goto"}}]
    )
    assert len(short) < len(long_), (
        "tools=None 时注入应该更短 (没载 SOUL_BROWSER 场景段)"
    )
