"""adapter 单测 —— 重点是 native tool 不依赖 hermes registry 也能 dispatch。

不打实际 hermes —— 用一个 fake registry 模拟。
"""
from __future__ import annotations

import asyncio
import types
from pathlib import Path
from typing import Any, Dict, List

import pytest

from catfish_tool_bridge import adapter, catfish_tools


# ---------- fake hermes registry ----------

class _FakeEntry:
    def __init__(self, description: str = "fake desc") -> None:
        self.description = description


class _FakeRegistry:
    """模仿 hermes registry 接口的最小子集。"""

    def __init__(self, names: List[str]) -> None:
        self._names = names

    def get_all_tool_names(self) -> List[str]:
        return list(self._names)

    def get_entry(self, name: str) -> _FakeEntry:
        return _FakeEntry(f"hermes tool {name}")

    def get_schema(self, name: str) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def get_emoji(self, name: str) -> str:
        return "🔧"

    def get_toolset_for_tool(self, name: str) -> str:
        return "fake_toolset"

    def is_toolset_available(self, name: str) -> bool:
        return True

    def get_max_result_size(self, name: str) -> int:
        return 100_000

    def get_registered_toolset_names(self) -> List[str]:
        return ["fake_toolset"]

    async def dispatch(self, name: str, args: Dict[str, Any]) -> Any:
        return {"echo": name, "args": args}


def _install_fake_registry(monkeypatch: pytest.MonkeyPatch, names: List[str]) -> None:
    """注入一个 fake registry 到 adapter, 供 list_tools/dispatch_tool 用"""
    fake = _FakeRegistry(names)
    fake_module = types.SimpleNamespace(registry=fake)
    monkeypatch.setattr(adapter, "_registry_module", fake_module)


# ---------- list_tools ----------

def test_list_tools_native_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """native tool 一定在最前面, 后面跟 hermes 工具按字母序"""
    _install_fake_registry(monkeypatch, ["zebra_tool", "alpha_tool"])
    out = adapter.list_tools()
    names = [t["name"] for t in out]
    # native tools 全在最前面 (按 CATFISH_NATIVE_TOOLS 列表顺序)
    n_native = len(catfish_tools.CATFISH_NATIVE_TOOLS)
    expected_native = [t["name"] for t in catfish_tools.CATFISH_NATIVE_TOOLS]
    assert names[:n_native] == expected_native
    # hermes 部分按字母排
    rest = names[n_native:]
    assert rest == sorted(rest)
    assert "alpha_tool" in rest
    assert "zebra_tool" in rest


def test_list_tools_native_collision_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """假如 hermes 也有 catfish_today_summary, 我们的 native 优先, hermes 那个跳过"""
    _install_fake_registry(monkeypatch, ["catfish_today_summary", "other"])
    out = adapter.list_tools()
    today_count = sum(1 for t in out if t["name"] == "catfish_today_summary")
    assert today_count == 1
    # 而且这个 catfish_today_summary 应该是我们 native 的版本(检查 toolset)
    today = next(t for t in out if t["name"] == "catfish_today_summary")
    assert today["toolset"] == "catfish_native"


# ---------- dispatch_tool ----------

def test_dispatch_native_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """native tool 可调通, 没装 registry 都行"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("USERPROFILE", raising=False)
    # 故意把 registry 留空, 验证 native 不依赖它
    monkeypatch.setattr(adapter, "_registry_module", None)

    result = asyncio.run(adapter.dispatch_tool("catfish_today_summary", {}))
    assert result["ok"] is True
    assert result["tool"] == "catfish_today_summary"
    assert "summary" in result["result"]


def test_dispatch_hermes_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """hermes tool 走 fake registry"""
    _install_fake_registry(monkeypatch, ["alpha_tool"])
    result = asyncio.run(adapter.dispatch_tool("alpha_tool", {"x": 1}))
    assert result["ok"] is True
    assert result["result"] == {"echo": "alpha_tool", "args": {"x": 1}}


def test_dispatch_unknown_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """既不是 native 也不在 registry → 报 unknown tool"""
    _install_fake_registry(monkeypatch, ["alpha_tool"])
    result = asyncio.run(adapter.dispatch_tool("does_not_exist", {}))
    assert result["ok"] is False
    assert "unknown tool" in result["error"]


def test_dispatch_native_does_not_truncate_for_no_max_size() -> None:
    """native dispatch 不走 hermes 的 truncate —— 它返回的就是 dict, 大小可控"""
    # 这个测试主要是确认 native 的 result 字段直接是 dict, 不是 _truncated 包裹
    result = asyncio.run(
        adapter.dispatch_tool("catfish_today_summary", {})
    )
    assert isinstance(result["result"], dict)
    assert "_truncated" not in result["result"]


# ---------- health ----------

def test_health_includes_native_count(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_registry(monkeypatch, ["alpha_tool", "beta_tool"])
    h = adapter.health()
    assert h["ok"] is True
    assert h["tool_count"] == 2 + len(catfish_tools.CATFISH_NATIVE_TOOLS)
    assert h["native_tool_count"] == len(catfish_tools.CATFISH_NATIVE_TOOLS)
    assert "catfish_native" in h["toolsets"]
