"""audit.py 单测.

覆盖:
  - write_event 写出来的 jsonl 字段格式对
  - 截断 (大入参 / 长错误消息) 不超过 _MAX_PREVIEW_CHARS
  - args 序列化失败兜底 repr
  - 写文件失败永远不抛 (audit 不能影响主流程)
  - read_events 时间过滤 / tool 过滤 / limit
  - 顺序: 最新在前
  - 多线程并发写不互相破坏 (jsonl 一行一事件不串)
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from catfish_tool_bridge import audit


@pytest.fixture(autouse=True)
def _isolate_audit_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """每个测试用独立的 audit 文件, 避免互相污染."""
    monkeypatch.setattr(audit, "_audit_path", tmp_path / ".catfish_audit.jsonl")
    yield
    # cleanup 不需要, tmp_path 自动清


# ============================================================
# write_event 基础
# ============================================================


def test_write_event_creates_file() -> None:
    """第一次 write_event 自动建文件 + 写一行"""
    audit.write_event("read_file", ok=True, args={"path": "/tmp/x"}, latency_ms=1.5)
    p = audit.audit_path()
    assert p.is_file()
    lines = p.read_text().strip().split("\n")
    assert len(lines) == 1


def test_write_event_appends_multiple() -> None:
    """连续多次 write_event → append 多行, 每行独立 jsonl"""
    for i in range(3):
        audit.write_event(f"tool_{i}", ok=True, args={"i": i})
    p = audit.audit_path()
    lines = p.read_text().strip().split("\n")
    assert len(lines) == 3
    # 每行都是合法 json
    events = [json.loads(line) for line in lines]
    assert [e["tool"] for e in events] == ["tool_0", "tool_1", "tool_2"]


def test_write_event_fields() -> None:
    """事件字段完整 + 类型对"""
    audit.write_event(
        "catfish_screenshot",
        ok=False,
        args={"mode": "fullscreen", "reason": "test"},
        error="some error",
        latency_ms=23.5,
    )
    events = audit.read_events()
    assert len(events) == 1
    e = events[0]
    assert e["tool"] == "catfish_screenshot"
    assert e["ok"] is False
    assert e["error"] == "some error"
    assert e["latency_ms"] == 23.5
    assert "mode" in e["args_preview"]
    assert "fullscreen" in e["args_preview"]
    # ts 是 ISO-8601 格式
    assert "T" in e["ts"]
    assert e["ts"].endswith("+00:00") or "Z" in e["ts"]


def test_write_event_ok_no_error() -> None:
    """ok=True 时 error 字段是 null (json) / None (python)"""
    audit.write_event("read_file", ok=True, args={"path": "/x"})
    events = audit.read_events()
    assert events[0]["error"] is None


# ============================================================
# 截断: 入参 / 错误消息 太长不能撑爆 audit 文件
# ============================================================


def test_long_args_truncated() -> None:
    """超长 args (例: 整张 base64 PNG) 被截到 _MAX_PREVIEW_CHARS"""
    big = {"image": "x" * 10000}  # 模拟大 base64
    audit.write_event("catfish_screenshot", ok=True, args=big)
    events = audit.read_events()
    preview = events[0]["args_preview"]
    # 截后应该有提示
    assert "more chars" in preview
    # 截短后整体长度受控
    assert len(preview) < 1000


def test_long_error_truncated() -> None:
    """超长 error 消息也截"""
    long_err = "stack trace " * 200
    audit.write_event("read_file", ok=False, error=long_err)
    events = audit.read_events()
    err = events[0]["error"]
    assert "more chars" in err
    assert len(err) < 1000


def test_args_serialization_fallback_repr() -> None:
    """非 json 序列化的 args (例: 自定义对象) 走 repr 兜底, 不抛"""
    class Weird:
        def __repr__(self) -> str:
            return "<Weird object>"
    audit.write_event("test_tool", ok=True, args={"obj": Weird()})
    events = audit.read_events()
    # default=str 让 json 也能 dump 类似对象, 实测 json.dumps + default=str OK
    # 这条主要是确认不抛异常
    assert len(events) == 1


# ============================================================
# 写失败永远不抛 (audit 不能影响主流程)
# ============================================================


def test_write_to_nonexistent_parent_creates_dir(tmp_path: Path) -> None:
    """audit 文件父目录不存在 → 自动建"""
    deep = tmp_path / "nested" / "deeper" / ".audit.jsonl"
    audit._audit_path = deep
    audit.write_event("test", ok=True)
    assert deep.is_file()


def test_write_to_readonly_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """audit 文件不可写 (磁盘满 / 权限错) → log warning 不抛"""
    # 模拟 OSError: monkeypatch open 抛
    real_open = Path.open

    def fake_open(self: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if str(self).endswith(".audit.jsonl"):
            raise OSError("disk full")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fake_open)
    # 不应该抛
    audit.write_event("test", ok=True)


def test_write_args_none_safe() -> None:
    """args=None 不抛, args_preview 是空字符串"""
    audit.write_event("test", ok=True, args=None)
    events = audit.read_events()
    assert events[0]["args_preview"] == ""


# ============================================================
# read_events 过滤 + 排序
# ============================================================


def test_read_events_returns_newest_first() -> None:
    """read_events 倒序 (最新在前)"""
    for i in range(5):
        audit.write_event(f"tool_{i}", ok=True)
        time.sleep(0.01)  # 微小间隔确保 ts 不同
    events = audit.read_events()
    assert len(events) == 5
    tools = [e["tool"] for e in events]
    assert tools == ["tool_4", "tool_3", "tool_2", "tool_1", "tool_0"]


def test_read_events_limit() -> None:
    """limit 参数控制返回数量"""
    for i in range(20):
        audit.write_event(f"tool_{i}", ok=True)
    events = audit.read_events(limit=5)
    assert len(events) == 5


def test_read_events_filter_by_tool() -> None:
    """tool_filter 只返回特定 tool 名的事件"""
    audit.write_event("read_file", ok=True)
    audit.write_event("write_file", ok=True)
    audit.write_event("read_file", ok=True)
    events = audit.read_events(tool_filter="read_file")
    assert len(events) == 2
    assert all(e["tool"] == "read_file" for e in events)


def test_read_events_since_iso() -> None:
    """since_iso 只返回这个时间之后的事件"""
    audit.write_event("old_event", ok=True)
    time.sleep(0.05)
    cutoff = "9999-01-01"  # 未来时间
    audit.write_event("new_event", ok=True)
    events = audit.read_events(since_iso=cutoff)
    # 都比 9999 早, 都不返回
    assert len(events) == 0
    # 反之, 给个很早的时间, 全返回
    events_all = audit.read_events(since_iso="1970-01-01")
    assert len(events_all) == 2


def test_read_events_empty_file_returns_empty() -> None:
    """audit 文件不存在 → 空列表 不抛"""
    events = audit.read_events()
    assert events == []


def test_read_events_skips_malformed_lines(tmp_path: Path) -> None:
    """audit 文件里偶尔有半行 / 损坏行 → skip 不抛"""
    p = audit._audit_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        '{"ts":"2026-04-28T00:00:00Z","tool":"a","ok":true,"error":null,"latency_ms":1,"args_preview":""}\n'
        'BROKEN GARBAGE LINE NOT JSON\n'
        '{"ts":"2026-04-28T00:00:01Z","tool":"b","ok":true,"error":null,"latency_ms":1,"args_preview":""}\n'
    )
    events = audit.read_events()
    assert len(events) == 2  # 损坏的那行被跳过
    tools = {e["tool"] for e in events}
    assert tools == {"a", "b"}


# ============================================================
# 多线程并发写
# ============================================================


def test_concurrent_writes_no_partial_lines() -> None:
    """5 个线程并发写 100 次 → 总 500 行, 每行都是合法 json (不串)"""
    def hammer(tool: str) -> None:
        for _ in range(100):
            audit.write_event(tool, ok=True, args={"x": 1})

    threads = [
        threading.Thread(target=hammer, args=(f"tool_{i}",))
        for i in range(5)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 读所有行验证: 每行独立解析成功
    p = audit.audit_path()
    lines = p.read_text().strip().split("\n")
    assert len(lines) == 500
    parsed = [json.loads(line) for line in lines]
    # 5 个 tool 各 100 次
    from collections import Counter
    counts = Counter(e["tool"] for e in parsed)
    for i in range(5):
        assert counts[f"tool_{i}"] == 100
