"""BL-MEMORY-BRIDGE-STORE — dispatch memory tool 时 store kwargs 注入.

RCA-MEMORY-PLUMBING-20260516 后立的防火墙 P0:

  hermes 0.13 memory_tool handler 用 `store=kw.get("store")` 拿 MemoryStore.
  catfish tool-bridge 是 stateless RPC 桥接, 如果不主动注入 store, hermes
  静默返 '{"error": "Memory is not available", "success": false}'.
  这是 5/16 整天 6h 摸排的真因.

本测试守住三个不变量, 防回归:
  1. dispatch name="memory" 时, registry.dispatch 收到 kwargs 含 store=<sentinel>
  2. dispatch 别的工具 (read_file / catfish_search_sessions / 等) 时,
     registry.dispatch **不**收 store kwarg (避免污染别的 stateful 工具)
  3. _get_memory_store() 第一次调用初始化失败 → 标记 init_failed,
     后续不再重试 (免无限刷异常 log)

注: 不测真 hermes MemoryStore 写盘行为 (那是 hermes 自家责任, 不归 catfish 守).
   测 catfish 这层是否正确把 store 透传过去.
"""
from __future__ import annotations

import asyncio
import types
from typing import Any, Dict, List, Optional

import pytest

from catfish_tool_bridge import adapter


# ============================================================
# Fake registry — 接 **kwargs 验透传
# ============================================================


class _StoreInjectionFakeRegistry:
    """模拟 hermes registry, 关键差别: dispatch 接 **kwargs 并记下来.

    这样测试就能验 catfish adapter 给 memory 工具调用注入了 store kw.
    """

    def __init__(self) -> None:
        self.dispatch_calls: List[Dict[str, Any]] = []
        # 每次 dispatch 返这个 — 真 hermes memory_tool 成功时返 JSON str,
        # 但本测试不验 hermes 行为, 只验 catfish 注入路径, 所以随便返个值.
        self.return_value = '{"success": true, "message": "fake ok"}'

    def get_all_tool_names(self) -> List[str]:
        # 让 adapter 走 r.dispatch 主分支 (name 在已知列表里)
        return ["memory", "todo", "read_file", "catfish_search_sessions"]

    def get_entry(self, name: str) -> Any:
        return types.SimpleNamespace(description=f"fake {name}")

    def get_schema(self, name: str) -> Dict[str, Any]:
        return {"type": "object"}

    def get_emoji(self, name: str) -> str:
        return ""

    def get_toolset_for_tool(self, name: str) -> str:
        return "memory" if name == "memory" else "general"

    def is_toolset_available(self, _name: str) -> bool:
        return True

    def get_max_result_size(self, _name: str) -> int:
        return 100_000

    def get_registered_toolset_names(self) -> List[str]:
        return ["memory", "general"]

    def dispatch(self, name: str, args: Dict[str, Any], **kwargs: Any) -> Any:
        # sync dispatch — adapter 会 asyncio.to_thread() 包它. 关键: 接 **kwargs.
        # 真 hermes registry.dispatch signature 就是这样 (line 347 of registry.py).
        self.dispatch_calls.append({
            "name": name,
            "args": dict(args),
            "kwargs": dict(kwargs),
        })
        return self.return_value


def _install_fake_registry(monkeypatch: pytest.MonkeyPatch, fake: Any) -> None:
    """把 fake registry 装到 adapter._registry_module."""
    fake_module = types.SimpleNamespace(registry=fake)
    monkeypatch.setattr(adapter, "_registry_module", fake_module)


def _reset_memory_store_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """每次测试前清掉模块级 cache, 防互相污染."""
    monkeypatch.setattr(adapter, "_memory_store_cache", None)
    monkeypatch.setattr(adapter, "_memory_store_init_failed", False)


# ============================================================
# 1. 主路径: memory 工具收 store kwarg
# ============================================================


def test_dispatch_memory_injects_store_kwarg(monkeypatch: pytest.MonkeyPatch) -> None:
    """name='memory' → registry.dispatch 收到 kwargs={'store': <sentinel>}.

    这是 5/16 RCA 主守护: 如果 catfish 又一次忘了注入, hermes memory_tool
    会再次走 store=None 路径返 'Memory is not available' 静默死掉.
    """
    _reset_memory_store_cache(monkeypatch)
    fake = _StoreInjectionFakeRegistry()
    _install_fake_registry(monkeypatch, fake)

    # mock _get_memory_store 返个 sentinel — 不真 import hermes MemoryStore.
    # 因为本测试是验 catfish adapter 透传逻辑, 不验 hermes 内部.
    sentinel_store = types.SimpleNamespace(name="fake-memory-store")
    monkeypatch.setattr(adapter, "_get_memory_store", lambda: sentinel_store)

    result = asyncio.run(adapter.dispatch_tool(
        "memory",
        {"action": "add", "target": "user", "content": "测试守护"},
    ))

    assert result["ok"] is True, f"dispatch 失败: {result}"
    assert len(fake.dispatch_calls) == 1, "fake.dispatch 应被调用 1 次"

    call = fake.dispatch_calls[0]
    assert call["name"] == "memory"
    assert call["args"]["action"] == "add"
    # 这是核心 assert — 5/16 之前这条不成立, hermes 因此返 disabled
    assert "store" in call["kwargs"], (
        f"BL-MEMORY-BRIDGE-STORE 回归: dispatch memory 时没注入 store kwarg. "
        f"hermes memory_tool 会返 'Memory is not available'. 实际 kwargs={call['kwargs']}"
    )
    assert call["kwargs"]["store"] is sentinel_store, (
        "注入的 store 不是 _get_memory_store() 返的实例"
    )


# ============================================================
# 2. 别的工具不收 store (避免污染)
# ============================================================


def test_dispatch_non_memory_tool_no_store_kwarg(monkeypatch: pytest.MonkeyPatch) -> None:
    """name='read_file' (非 memory) → registry.dispatch 收到的 kwargs 不含 store.

    防"全工具都注 store" 的反向 bug — 别的 stateful 工具 (如 todo) 可能将来
    要自己的 store, 这里只针对 memory 注入.
    """
    _reset_memory_store_cache(monkeypatch)
    fake = _StoreInjectionFakeRegistry()
    _install_fake_registry(monkeypatch, fake)

    # 即使 _get_memory_store 返实例, 别的工具也不应该收
    sentinel_store = types.SimpleNamespace(name="fake-memory-store")
    monkeypatch.setattr(adapter, "_get_memory_store", lambda: sentinel_store)

    result = asyncio.run(adapter.dispatch_tool(
        "read_file",
        {"path": "/tmp/x"},
    ))

    assert result["ok"] is True
    call = fake.dispatch_calls[0]
    assert call["name"] == "read_file"
    assert "store" not in call["kwargs"], (
        f"read_file 收到了 store kwargs (污染): {call['kwargs']}"
    )


# ============================================================
# 3. _get_memory_store init 失败兜底
# ============================================================


def test_get_memory_store_init_failure_marks_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """MemoryStore 构造抛 → _memory_store_init_failed=True, 后续返 None 不重试.

    防 hermes-agent 路径错 / sys.path 没插 / venv 不对的情况下, 每次 dispatch
    都重试 import 刷一堆 exception log.
    """
    _reset_memory_store_cache(monkeypatch)

    # mock import 抛 (模拟 sys.path 没 hermes / hermes 版本不对)
    def _fail_import(*_args, **_kwargs):
        raise ImportError("fake: hermes-agent 没装")

    monkeypatch.setattr(
        "catfish_tool_bridge.adapter._read_hermes_memory_config",
        _fail_import,
    )

    store1 = adapter._get_memory_store()
    assert store1 is None
    assert adapter._memory_store_init_failed is True

    # 第二次调用应该直接走 short-circuit, 不再 import
    # 把 _fail_import 换成会抛不同错的版本, 验证它没被再次调用
    call_count = [0]

    def _should_not_be_called(*_a, **_kw):
        call_count[0] += 1
        raise RuntimeError("不应该再被调")

    monkeypatch.setattr(
        "catfish_tool_bridge.adapter._read_hermes_memory_config",
        _should_not_be_called,
    )
    store2 = adapter._get_memory_store()
    assert store2 is None
    assert call_count[0] == 0, "init_failed 后不应重试"


# ============================================================
# 4. _get_memory_store 成功路径 + cache
# ============================================================


# ============================================================
# 5. BL-TODO-BRIDGE-STORE — todo 工具 per-session 注入
# ============================================================


def _reset_todo_store_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter, "_todo_store_cache", {})
    monkeypatch.setattr(adapter, "_todo_store_init_failed", False)


def test_dispatch_todo_injects_store_kwarg_default_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """name='todo' + 无 session_id → 走 __default__ store, kw['store'] 注入."""
    _reset_todo_store_cache(monkeypatch)
    fake = _StoreInjectionFakeRegistry()
    _install_fake_registry(monkeypatch, fake)

    # mock TodoStore class
    class _FakeTodoStore:
        def __init__(self):
            self._items = []

    fake_module = types.SimpleNamespace(TodoStore=_FakeTodoStore)
    monkeypatch.setitem(__import__("sys").modules, "tools.todo_tool", fake_module)

    result = asyncio.run(adapter.dispatch_tool("todo", {"todos": []}))
    assert result["ok"] is True
    call = fake.dispatch_calls[0]
    assert "store" in call["kwargs"]
    assert isinstance(call["kwargs"]["store"], _FakeTodoStore)


def test_dispatch_todo_per_session_isolated_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """两次 dispatch 不同 session_id → 各自独立 TodoStore 实例."""
    _reset_todo_store_cache(monkeypatch)
    fake = _StoreInjectionFakeRegistry()
    _install_fake_registry(monkeypatch, fake)

    class _FakeTodoStore:
        def __init__(self):
            self._items = []

    fake_module = types.SimpleNamespace(TodoStore=_FakeTodoStore)
    monkeypatch.setitem(__import__("sys").modules, "tools.todo_tool", fake_module)

    asyncio.run(adapter.dispatch_tool("todo", {}, session_id="sess-A"))
    asyncio.run(adapter.dispatch_tool("todo", {}, session_id="sess-B"))

    store_a = fake.dispatch_calls[0]["kwargs"]["store"]
    store_b = fake.dispatch_calls[1]["kwargs"]["store"]
    assert store_a is not store_b, "不同 session 应该各自独立 store, 实际共享"


def test_dispatch_todo_same_session_reuses_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """两次 dispatch 同 session_id → 同一 TodoStore 实例 (跨调用记得 todos)."""
    _reset_todo_store_cache(monkeypatch)
    fake = _StoreInjectionFakeRegistry()
    _install_fake_registry(monkeypatch, fake)

    class _FakeTodoStore:
        def __init__(self):
            self._items = []

    fake_module = types.SimpleNamespace(TodoStore=_FakeTodoStore)
    monkeypatch.setitem(__import__("sys").modules, "tools.todo_tool", fake_module)

    asyncio.run(adapter.dispatch_tool("todo", {}, session_id="sess-X"))
    asyncio.run(adapter.dispatch_tool("todo", {}, session_id="sess-X"))

    store_1 = fake.dispatch_calls[0]["kwargs"]["store"]
    store_2 = fake.dispatch_calls[1]["kwargs"]["store"]
    assert store_1 is store_2, "同 session 应该复用 store, 实际新建"


def test_get_memory_store_caches_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """第一次调用 init + load, 第二次直接返 cache 实例 (不重新 load_from_disk)."""
    _reset_memory_store_cache(monkeypatch)

    # mock MemoryStore class — adapter 通过 `from tools.memory_tool import MemoryStore`
    # 拿. 拦 sys.modules 注个 fake.
    load_call_count = [0]

    class _FakeMemoryStore:
        def __init__(self, memory_char_limit: int = 0, user_char_limit: int = 0) -> None:
            self.memory_char_limit = memory_char_limit
            self.user_char_limit = user_char_limit
            self.memory_entries: List[str] = []
            self.user_entries: List[str] = []

        def load_from_disk(self) -> None:
            load_call_count[0] += 1

    fake_module = types.SimpleNamespace(MemoryStore=_FakeMemoryStore)
    monkeypatch.setitem(__import__("sys").modules, "tools.memory_tool", fake_module)
    # config 返空 → 用默认值
    monkeypatch.setattr(
        "catfish_tool_bridge.adapter._read_hermes_memory_config",
        lambda: {},
    )

    s1 = adapter._get_memory_store()
    s2 = adapter._get_memory_store()

    assert s1 is not None
    assert s1 is s2, "cache 失效, 拿到不同实例"
    assert load_call_count[0] == 1, f"load_from_disk 调用了 {load_call_count[0]} 次 (应 1 次)"
