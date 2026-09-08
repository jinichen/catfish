"""adapter 单测 —— 重点是 native tool 不依赖 hermes registry 也能 dispatch。

不打实际 hermes —— 用一个 fake registry 模拟。
"""
from __future__ import annotations

import asyncio
import types
from pathlib import Path
from typing import Any, Dict, List

import pytest

from catfish_tool_bridge import adapter, catfish_tools, tool_availability


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


def test_list_tools_marks_platform_specific_tools_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows 终端不能把 macOS Reminders/Calendar 宣称为可用。"""
    _install_fake_registry(monkeypatch, [])
    monkeypatch.setattr(tool_availability.platform, "system", lambda: "Windows")

    by_name = {tool["name"]: tool for tool in adapter.list_tools()}

    reminder = by_name["catfish_list_reminders"]
    assert reminder["available"] is False
    assert reminder["supported"] is False
    assert reminder["reason_code"] == "unsupported_platform"


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


def test_dispatch_rejects_platform_unsupported_native_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """即使调用方绕过工具列表直接点名，也不能执行不支持的平台工具。"""
    monkeypatch.setattr(tool_availability.platform, "system", lambda: "Windows")
    monkeypatch.setattr(adapter, "_registry_module", None)

    result = asyncio.run(adapter.dispatch_tool("catfish_list_reminders", {}))

    assert result["ok"] is False
    assert result["reason_code"] == "unsupported_platform"
    assert result["error"] == "当前终端不支持此工具"


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


def test_dispatch_native_does_not_truncate_for_no_max_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """native dispatch 不走 hermes 的 truncate —— 它返回的就是 dict, 大小可控"""
    # 这个断言测 IPC truncate，不测本机大结果归档。显式关掉归档，避免读取
    # 开发机 ~/.catfish 配置和已有 archive 状态造成测试污染。
    monkeypatch.setenv("CATFISH_TOOL_ARCHIVE_ENABLED", "false")
    # 这个测试主要是确认 native 的 result 字段直接是 dict, 不是 _truncated 包裹
    result = asyncio.run(
        adapter.dispatch_tool("catfish_today_summary", {})
    )
    assert isinstance(result["result"], dict)
    assert "_truncated" not in result["result"]


@pytest.mark.parametrize("tool_name", ["catfish_list_tasks", "catfish_list_reminders"])
def test_structured_task_queries_are_not_archived(
    monkeypatch: pytest.MonkeyPatch, tool_name: str
) -> None:
    """Companion 要直接解析任务查询 envelope，不能被大结果归档成字符串。"""
    monkeypatch.setenv("CATFISH_TOOL_ARCHIVE_ENABLED", "true")
    result = {"ok": True, "tool": tool_name, "result": {"tasks": ["x" * 5000]}}

    archived = adapter._maybe_archive_oversized_result(result)

    assert archived["result"] is result["result"]
    assert isinstance(archived["result"], dict)


# ---------- execute_code 误用守卫 ----------


def test_execute_code_with_catfish_browser_rejected() -> None:
    """模型在 execute_code 里写脚本调 catfish_browser_* → 立即拒绝, 别让它死等"""
    result = asyncio.run(adapter.dispatch_tool("execute_code", {
        "code": "import catfish_tool_bridge\nresult = catfish_browser_goto(url='http://x')",
    }))
    assert result["ok"] is False
    assert "catfish" in result["error"].lower()
    assert "execute_code" in result["error"] or "沙箱" in result["error"]


def test_execute_code_with_catfish_screenshot_rejected() -> None:
    result = asyncio.run(adapter.dispatch_tool("execute_code", {
        "code": "x = catfish_screenshot()",
    }))
    assert result["ok"] is False


def test_execute_code_with_pure_python_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """纯计算 / 不调 catfish 工具的脚本 — 不拦. 应该走到 hermes registry."""
    _install_fake_registry(monkeypatch, ["execute_code"])
    result = asyncio.run(adapter.dispatch_tool("execute_code", {
        "code": "print(sum(range(100)))",
    }))
    # 关键: 即使最后失败 (fake registry 不真跑), 错误也不该是 "catfish 工具调用" misuse
    if result["ok"] is False:
        assert "catfish 工具调用" not in (result.get("error") or "")


def test_shell_exec_with_catfish_also_rejected() -> None:
    """守卫覆盖 shell_exec / python / bash 等同义工具名"""
    result = asyncio.run(adapter.dispatch_tool("shell_exec", {
        "command": "python -c 'import catfish_tool_bridge; print(1)'",
    }))
    assert result["ok"] is False


def test_non_execute_tools_not_affected() -> None:
    """普通 tool (例如 catfish_today_summary native) 不受守卫干扰"""
    result = asyncio.run(adapter.dispatch_tool("catfish_today_summary", {}))
    # 不该撞 misuse 守卫 (那是给 execute_code 类的)
    if result["ok"] is False:
        assert "catfish 工具调用" not in (result.get("error") or "")


# ---------- health ----------

def test_health_includes_native_count(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_registry(monkeypatch, ["alpha_tool", "beta_tool"])
    h = adapter.health()
    assert h["ok"] is True
    assert h["tool_count"] == 2 + len(catfish_tools.CATFISH_NATIVE_TOOLS)
    assert h["native_tool_count"] == len(catfish_tools.CATFISH_NATIVE_TOOLS)
    assert "catfish_native" in h["toolsets"]


# ---------- 品牌脱敏 (BL-D9 五一 sprint 5/3) ----------


def test_scrub_brand_leaks_replaces_tilde_path() -> None:
    """~/.hermes/memories/x.md → 鲶鱼本机存储 (路径整体被替换, 防 LLM 引用)"""
    out = adapter._scrub_brand_leaks("Saved to ~/.hermes/memories/foo.md")
    assert "hermes" not in out.lower()
    assert "鲶鱼本机存储" in out


def test_scrub_brand_leaks_replaces_absolute_user_path() -> None:
    """/Users/alice/.hermes/... 也得替换 (LLM 看到的 toolresponse 经常是绝对路径)"""
    out = adapter._scrub_brand_leaks("file path: /Users/alice/.hermes/memories/k.md done")
    assert ".hermes" not in out
    assert "鲶鱼本机存储" in out


def test_scrub_brand_leaks_replaces_home_linux_path() -> None:
    """/home/bob/.hermes/... (Linux 路径) 也得替换"""
    out = adapter._scrub_brand_leaks("dir: /home/bob/.hermes/state.json ok")
    assert ".hermes" not in out
    assert "鲶鱼本机存储" in out


def test_scrub_brand_leaks_replaces_standalone_word() -> None:
    """独立的 'hermes' 词 → 鲶鱼 (大小写都换, \\b 词边界)"""
    out = adapter._scrub_brand_leaks("hermes is not initialized; Hermes can't run")
    assert "hermes" not in out.lower()
    assert "鲶鱼" in out


def test_scrub_brand_leaks_keeps_tool_name_with_underscore() -> None:
    """hermes_xxx 这种 tool 名字不能误伤 (\\b 词边界保证)"""
    # 注意: \b 在 hermes_browser 处仍把 hermes 单独识别为词 (因为 _ 不是 \w 边界, 它是 \w)
    # 所以实际上 hermes_browser 里的 hermes 是词的一部分, 不会被替换. 这是预期.
    out = adapter._scrub_brand_leaks("call hermes_browser_goto next")
    # 工具名整体保留
    assert "hermes_browser_goto" in out


def test_scrub_brand_in_result_skips_non_target_tools() -> None:
    """非 hermes memory_* tool 的响应不动, 避免误伤 catfish_skill_install 真路径"""
    raw = {"path": "/Users/x/.hermes/foo", "msg": "saved hermes record"}
    out = adapter.scrub_brand_in_result("catfish_skill_install", raw)
    assert out == raw  # 完全不动


def test_scrub_brand_in_result_str_target_tool() -> None:
    """memory_save 返回纯字符串, 直接脱敏"""
    out = adapter.scrub_brand_in_result(
        "memory_save", "Saved to ~/.hermes/memories/x.md by hermes"
    )
    assert "hermes" not in out.lower()
    assert ".hermes" not in out
    assert "鲶鱼" in out


def test_scrub_brand_in_result_dict_target_tool() -> None:
    """memory_save 返回 dict 时, 递归把字符串字段都脱敏, 非字符串原样"""
    raw = {
        "path": "/Users/alice/.hermes/memories/k.md",
        "size": 123,
        "msg": "hermes ok",
    }
    out = adapter.scrub_brand_in_result("memory_save", raw)
    assert ".hermes" not in out["path"]
    assert "hermes" not in out["msg"].lower()
    assert out["size"] == 123  # 数字字段不动


def test_scrub_brand_in_result_nested_dict() -> None:
    """嵌套 dict 也得递归脱敏"""
    raw = {
        "outer": {
            "inner": {
                "p": "~/.hermes/state",
                "x": [1, 2],
            },
        },
    }
    out = adapter.scrub_brand_in_result("memory_save", raw)
    assert ".hermes" not in out["outer"]["inner"]["p"]
    assert out["outer"]["inner"]["x"] == [1, 2]


def test_scrub_brand_in_result_list_target_tool() -> None:
    """memory_search 通常返回 list[dict], 每条都得脱敏"""
    raw = [
        {"name": "k1", "path": "/Users/a/.hermes/memories/k1.md"},
        {"name": "k2", "path": "/Users/a/.hermes/memories/k2.md"},
    ]
    out = adapter.scrub_brand_in_result("memory_search", raw)
    for item in out:
        assert ".hermes" not in item["path"]


def test_dispatch_tool_scrubs_brand_for_hermes_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """整链路 dispatch: hermes memory_save 返回含 hermes 路径, dispatch 后给 LLM 的 result 已脱敏.

    5/7 BL-D14.5 起, memory_save 走 BL-MM3 wrapper, hermes 真返回嵌套到
    result.raw_save_response. 这里同时验:
      1. wrapper 包了一层 (含 previous_value / overwrite 等 BL-MM2 字段)
      2. raw_save_response 里的 hermes path / 字眼仍被 scrub
    """

    class _MemFakeRegistry(_FakeRegistry):
        async def dispatch(self, name: str, args: Dict[str, Any]) -> Any:  # type: ignore[override]
            if name == "memory_recall":
                # 旧值不存在 → BL-MM3 wrapper 走"首次记"路径
                return None
            # 模拟 hermes memory_save 真实返回
            return {
                "ok": True,
                "path": "/Users/alice/.hermes/memories/k.md",
                "msg": "saved hermes record",
            }

    fake = _MemFakeRegistry(["memory_save", "memory_recall"])
    fake_module = types.SimpleNamespace(registry=fake)
    monkeypatch.setattr(adapter, "_registry_module", fake_module)

    result = asyncio.run(adapter.dispatch_tool("memory_save", {"name": "k", "content": "v"}))
    assert result["ok"] is True
    inner = result["result"]
    # BL-MM3 wrapper 包了一层
    assert inner["overwrite"] is False  # 首次记
    assert inner["previous_value"] is None
    # 关键: raw_save_response 里 hermes path / 字眼已脱敏
    raw = inner["raw_save_response"]
    assert ".hermes" not in raw["path"]
    assert "hermes" not in raw["msg"].lower()
    assert "鲶鱼" in raw["msg"] or "鲶鱼本机存储" in raw["path"]


def test_dispatch_tool_does_not_scrub_other_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非 _HERMES_TOOLS_NEEDS_BRAND_SCRUB 的工具响应原样保留, 防误伤"""

    class _PathFakeRegistry(_FakeRegistry):
        async def dispatch(self, name: str, args: Dict[str, Any]) -> Any:  # type: ignore[override]
            # 假设这是某个真要返回 hermes 路径的非 memory 工具
            return {"path": "/Users/alice/.hermes/foo"}

    fake = _PathFakeRegistry(["some_other_tool"])
    fake_module = types.SimpleNamespace(registry=fake)
    monkeypatch.setattr(adapter, "_registry_module", fake_module)

    result = asyncio.run(adapter.dispatch_tool("some_other_tool", {}))
    assert result["ok"] is True
    # 非目标工具, 路径保持原样
    assert result["result"]["path"] == "/Users/alice/.hermes/foo"


def test_dispatch_tool_scrubs_error_field_for_hermes_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """memory_save 失败时, error 字段里的 hermes 字眼也得脱敏 (LLM 会读 error 给员工解释)"""

    class _ErrFakeRegistry(_FakeRegistry):
        async def dispatch(self, name: str, args: Dict[str, Any]) -> Any:  # type: ignore[override]
            raise RuntimeError("hermes memory backend at ~/.hermes/memories not initialized")

    fake = _ErrFakeRegistry(["memory_save"])
    fake_module = types.SimpleNamespace(registry=fake)
    monkeypatch.setattr(adapter, "_registry_module", fake_module)

    result = asyncio.run(adapter.dispatch_tool("memory_save", {}))
    assert result["ok"] is False
    err = result["error"] or ""
    assert "hermes" not in err.lower()
    assert ".hermes" not in err
