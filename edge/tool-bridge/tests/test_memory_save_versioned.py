"""BL-MM3 hermes memory_save 包版本化 — 单元测试.

覆盖:
  - 首次写 (memory_recall 返空) → previous_value=None, overwrite=False
  - 覆盖写 (memory_recall 返旧) → previous_value=old, overwrite=True, content 含 inline 备注
  - 同值再写 → no_change=True, content 不加 inline 块 (避免污染)
  - read 失败兜底 → read_old_ok=False, 仍调 memory_save (按首次记)
  - write 失败 → ok=False, error 字段填上
  - args 兼容 {key,value} 跟 {name,content} 两种字段名
  - 上一轮 inline 块剥掉, 不重叠
  - dispatch_tool 整链路: memory_save → wrapper → 真 memory_save → scrub_brand
  - CATFISH_DISABLE_MM3=1 时跳过 wrapper, 直打 hermes
"""
from __future__ import annotations

import asyncio
import types
from typing import Any, Dict, List, Optional

import pytest

from catfish_tool_bridge import adapter


# ---------- fake hermes registry: 可控 memory_recall / memory_save ----------


class _MM3FakeRegistry:
    """模拟 hermes registry, 按 name 路由 memory_recall / memory_save 行为.

    - recall_returns: 每次 memory_recall 返回什么 (None=没找到 / dict / str / list / Exception)
    - save_returns: memory_save 返回什么 (默认 dict OK; Exception → raise)
    - save_calls: 累计每次 memory_save 收到的 args (验 content 含 inline 备注)
    """

    def __init__(
        self,
        recall_returns: Any = None,
        save_returns: Any = None,
        recall_available: bool = True,
    ) -> None:
        self.recall_returns = recall_returns
        self.save_returns = save_returns if save_returns is not None else {
            "ok": True, "path": "/Users/x/.hermes/memories/x.md",
        }
        self._names = ["memory_save", "memory_recall"] if recall_available else ["memory_save"]
        self.recall_calls: List[Dict[str, Any]] = []
        self.save_calls: List[Dict[str, Any]] = []

    def get_all_tool_names(self) -> List[str]:
        return list(self._names)

    def get_entry(self, name: str) -> Any:
        return types.SimpleNamespace(description=f"fake {name}")

    def get_schema(self, name: str) -> Dict[str, Any]:
        return {"type": "object"}

    def get_emoji(self, name: str) -> str:
        return ""

    def get_toolset_for_tool(self, name: str) -> str:
        return "memory"

    def is_toolset_available(self, name: str) -> bool:
        return True

    def get_max_result_size(self, name: str) -> int:
        return 100_000

    def get_registered_toolset_names(self) -> List[str]:
        return ["memory"]

    async def dispatch(self, name: str, args: Dict[str, Any]) -> Any:
        if name == "memory_recall":
            self.recall_calls.append(dict(args))
            if isinstance(self.recall_returns, Exception):
                raise self.recall_returns
            return self.recall_returns
        if name == "memory_save":
            self.save_calls.append(dict(args))
            if isinstance(self.save_returns, Exception):
                raise self.save_returns
            return self.save_returns
        raise RuntimeError(f"unexpected tool: {name}")


def _install(monkeypatch: pytest.MonkeyPatch, fake: _MM3FakeRegistry) -> None:
    fake_module = types.SimpleNamespace(registry=fake)
    monkeypatch.setattr(adapter, "_registry_module", fake_module)


# ============================================================
# 1. 首次写 (memory_recall 返空)
# ============================================================


def test_first_write_no_old_value(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _MM3FakeRegistry(recall_returns=None)
    _install(monkeypatch, fake)

    out = asyncio.run(adapter._memory_save_versioned({
        "name": "leader_preference",
        "content": "戴明利领导喜欢早上 8 点收周报",
    }))

    assert out["ok"] is True
    payload = out["result"]
    assert payload["overwrite"] is False
    assert payload["no_change"] is False
    assert payload["previous_value"] is None
    assert payload["read_old_ok"] is True
    assert "首次" in payload["summary"]

    # 真 memory_save 收到的 content 没有 inline 块 (首次)
    assert len(fake.save_calls) == 1
    saved = fake.save_calls[0]
    assert saved["name"] == "leader_preference"
    assert saved["content"] == "戴明利领导喜欢早上 8 点收周报"
    assert "BL-MM3 上次值" not in saved["content"]


# ============================================================
# 2. 覆盖写: memory_recall 返旧 → inline 备注
# ============================================================


def test_overwrite_with_old_value_adds_inline_note(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _MM3FakeRegistry(recall_returns={"content": "戴明利喜欢早上 8 点"})
    _install(monkeypatch, fake)

    out = asyncio.run(adapter._memory_save_versioned({
        "name": "leader_preference",
        "content": "戴明利喜欢早上 9 点",
    }))

    assert out["ok"] is True
    payload = out["result"]
    assert payload["overwrite"] is True
    assert payload["no_change"] is False
    assert payload["previous_value"] == "戴明利喜欢早上 8 点"
    assert "上次值" in payload["summary"]
    assert "quote 旧值" in payload["summary"]

    # 真 memory_save 收到的 content 含 inline 备注块, 旧值 truncated
    saved_content = fake.save_calls[0]["content"]
    assert "戴明利喜欢早上 9 点" in saved_content  # 新值
    assert "BL-MM3 上次值" in saved_content
    assert "戴明利喜欢早上 8 点" in saved_content  # 旧值在 inline 里
    assert "---" in saved_content  # 分隔线


# ============================================================
# 3. 同值再写: no-op, 不加 inline 块
# ============================================================


def test_same_value_no_change(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _MM3FakeRegistry(recall_returns={"content": "保持原样"})
    _install(monkeypatch, fake)

    out = asyncio.run(adapter._memory_save_versioned({
        "name": "k",
        "content": "保持原样",
    }))
    assert out["ok"] is True
    payload = out["result"]
    assert payload["no_change"] is True
    assert payload["overwrite"] is True  # 旧值存在
    # content 不加 inline 块 (同值不污染)
    assert "BL-MM3 上次值" not in fake.save_calls[0]["content"]
    assert fake.save_calls[0]["content"] == "保持原样"


# ============================================================
# 4. read 失败兜底 → 按首次记
# ============================================================


def test_read_failure_falls_back_to_first_write(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _MM3FakeRegistry(recall_returns=RuntimeError("hermes index 锁了"))
    _install(monkeypatch, fake)

    out = asyncio.run(adapter._memory_save_versioned({
        "name": "k",
        "content": "新值",
    }))
    assert out["ok"] is True
    payload = out["result"]
    assert payload["read_old_ok"] is False
    assert payload["overwrite"] is False
    assert "兜底" in payload["summary"]
    # 仍调 memory_save
    assert len(fake.save_calls) == 1
    assert "BL-MM3 上次值" not in fake.save_calls[0]["content"]


def test_recall_unavailable_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """memory_recall 工具不在 registry 里 (老版本 hermes / disabled toolset)"""
    fake = _MM3FakeRegistry(recall_returns=None, recall_available=False)
    _install(monkeypatch, fake)

    out = asyncio.run(adapter._memory_save_versioned({
        "name": "k",
        "content": "v",
    }))
    assert out["ok"] is True
    # recall 没装就跳过, 不抛
    assert len(fake.recall_calls) == 0
    assert len(fake.save_calls) == 1


# ============================================================
# 5. write 失败
# ============================================================


def test_write_failure_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _MM3FakeRegistry(
        recall_returns=None,
        save_returns=RuntimeError("disk full"),
    )
    _install(monkeypatch, fake)

    out = asyncio.run(adapter._memory_save_versioned({
        "name": "k",
        "content": "v",
    }))
    assert out["ok"] is False
    assert "disk full" in (out["error"] or "")


# ============================================================
# 6. args 字段兼容: {key, value} 跟 {name, content}
# ============================================================


def test_args_compat_key_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """老调用习惯传 key/value, 不应失败"""
    fake = _MM3FakeRegistry(recall_returns=None)
    _install(monkeypatch, fake)

    out = asyncio.run(adapter._memory_save_versioned({
        "key": "leader",
        "value": "戴明利",
    }))
    assert out["ok"] is True
    # wrapper 转成 name/content 喂给 hermes
    saved = fake.save_calls[0]
    assert saved["name"] == "leader"
    assert saved["content"] == "戴明利"
    assert "key" not in saved and "value" not in saved


def test_missing_name_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _MM3FakeRegistry()
    _install(monkeypatch, fake)
    out = asyncio.run(adapter._memory_save_versioned({"content": "v"}))
    assert out["ok"] is False
    assert "name" in (out["error"] or "")


def test_missing_content_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _MM3FakeRegistry()
    _install(monkeypatch, fake)
    out = asyncio.run(adapter._memory_save_versioned({"name": "k"}))
    assert out["ok"] is False
    assert "content" in (out["error"] or "")


# ============================================================
# 7. 上一轮 inline 块剥掉, 不重叠
# ============================================================


def test_prev_inline_block_stripped_before_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    """memory_recall 返回的旧值已含上一轮 inline 块 → 剥掉后跟新值比.

    防止 inline 块越积越长 (每次写都把上轮 inline 当新值的一部分塞进新 inline).
    """
    old_with_inline = (
        "戴明利喜欢早上 8 点\n"
        "\n"
        "---\n"
        "_(BL-MM3 上次值, 已废, ts=2026-05-06 09:00: 戴明利喜欢早上 7 点)_"
    )
    fake = _MM3FakeRegistry(recall_returns={"content": old_with_inline})
    _install(monkeypatch, fake)

    out = asyncio.run(adapter._memory_save_versioned({
        "name": "leader",
        "content": "戴明利喜欢早上 9 点",
    }))
    assert out["ok"] is True
    payload = out["result"]
    # previous_value 应该是剥掉 inline 后的值 ("戴明利喜欢早上 8 点"), 不是含 inline 的版本
    assert payload["previous_value"] == "戴明利喜欢早上 8 点"

    # 新写的 content 里 inline 块只有一份, 不递归累积
    saved_content = fake.save_calls[0]["content"]
    inline_count = saved_content.count("BL-MM3 上次值")
    assert inline_count == 1, f"期望只 1 个 inline 块, 实际 {inline_count}: {saved_content!r}"


def test_same_value_after_strip_treated_as_no_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧值带上轮 inline 块, 剥掉后 == 新值 → no_change, 不加新 inline"""
    old_with_inline = (
        "v1\n\n---\n"
        "_(BL-MM3 上次值, 已废, ts=2026-05-06 09:00: v0)_"
    )
    fake = _MM3FakeRegistry(recall_returns={"content": old_with_inline})
    _install(monkeypatch, fake)

    out = asyncio.run(adapter._memory_save_versioned({"name": "k", "content": "v1"}))
    assert out["result"]["no_change"] is True
    # 新写的 content 不含 inline 块
    assert "BL-MM3 上次值" not in fake.save_calls[0]["content"]


# ============================================================
# 8. 长旧值 truncate 到 _MM3_INLINE_MAX_OLD_LEN
# ============================================================


def test_long_old_value_truncated_in_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    long_old = "x" * 500
    fake = _MM3FakeRegistry(recall_returns={"content": long_old})
    _install(monkeypatch, fake)

    out = asyncio.run(adapter._memory_save_versioned({"name": "k", "content": "y"}))
    saved_content = fake.save_calls[0]["content"]
    assert "BL-MM3 上次值" in saved_content
    # 旧值原长 500, 截到 _MM3_INLINE_MAX_OLD_LEN (200), 不应出现 201 个连续 x
    assert "x" * (adapter._MM3_INLINE_MAX_OLD_LEN + 1) not in saved_content
    # 截到的旧值前缀应该 == _MM3_INLINE_MAX_OLD_LEN 个 x (后面跟省略号)
    assert "x" * adapter._MM3_INLINE_MAX_OLD_LEN in saved_content
    assert "…" in saved_content  # 有省略号
    # 但 previous_value 字段还是返完整旧值 (用于 LLM quote, 不截)
    assert out["result"]["previous_value"] == long_old


# ============================================================
# 9. dispatch_tool 整链路: memory_save → wrapper → scrub_brand
# ============================================================


def test_dispatch_tool_routes_memory_save_through_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """通过 dispatch_tool 进 memory_save, 走 wrapper, 返回经品牌脱敏"""
    fake = _MM3FakeRegistry(
        recall_returns={"content": "old"},
        save_returns={
            "ok": True,
            "path": "/Users/alice/.hermes/memories/k.md",
            "msg": "saved by hermes",
        },
    )
    _install(monkeypatch, fake)

    result = asyncio.run(adapter.dispatch_tool("memory_save", {
        "name": "k", "content": "new",
    }))
    assert result["ok"] is True
    payload = result["result"]
    # 走了 wrapper: 含 previous_value / overwrite / summary
    assert payload["previous_value"] == "old"
    assert payload["overwrite"] is True
    # 真 memory_save 经过, 收到了 inline content
    assert len(fake.save_calls) == 1
    assert "BL-MM3 上次值" in fake.save_calls[0]["content"]
    # raw_save_response 经过 scrub_brand 脱敏 (path 不含 .hermes)
    raw = payload["raw_save_response"]
    assert ".hermes" not in raw["path"]
    assert "hermes" not in raw["msg"].lower()


def test_dispatch_tool_disable_mm3_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """CATFISH_DISABLE_MM3=1 → 跳过 wrapper, 直打 hermes (回退测试 / 调试用)"""
    fake = _MM3FakeRegistry(
        recall_returns={"content": "old"},
        save_returns={"ok": True, "path": "/Users/x/.hermes/k.md"},
    )
    _install(monkeypatch, fake)
    monkeypatch.setenv("CATFISH_DISABLE_MM3", "1")

    result = asyncio.run(adapter.dispatch_tool("memory_save", {
        "name": "k", "content": "new",
    }))
    assert result["ok"] is True
    # 没走 wrapper: memory_recall 没被调
    assert len(fake.recall_calls) == 0
    # memory_save 收到原 content (不带 inline 块)
    assert "BL-MM3 上次值" not in fake.save_calls[0]["content"]


# ============================================================
# 10. _extract_recall_text helper
# ============================================================


@pytest.mark.parametrize("raw,expected", [
    (None, ""),
    ("", ""),
    ("plain text", "plain text"),
    ({"content": "from dict"}, "from dict"),
    ({"text": "alt key"}, "alt key"),
    ({"value": "another"}, "another"),
    ({"unrelated": "x"}, ""),
    ([], ""),
    (["first item"], "first item"),
    ([{"content": "list dict"}], "list dict"),
    ([{"unrelated": "x"}], ""),
    (123, ""),  # 非常规类型不抛
])
def test_extract_recall_text(raw: Any, expected: str) -> None:
    assert adapter._extract_recall_text(raw) == expected


# ============================================================
# 11. _strip_old_inline_block helper
# ============================================================


def test_strip_old_inline_block_preserves_clean_content() -> None:
    assert adapter._strip_old_inline_block("plain") == "plain"
    assert adapter._strip_old_inline_block("") == ""


def test_strip_old_inline_block_removes_inline_section() -> None:
    content = (
        "real value here\n"
        "with multiple lines\n\n"
        "---\n"
        "_(BL-MM3 上次值, 已废, ts=2026-05-07 10:00: 旧的 X)_"
    )
    stripped = adapter._strip_old_inline_block(content)
    assert "BL-MM3" not in stripped
    assert stripped == "real value here\nwith multiple lines"
