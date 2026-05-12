"""trace_recorder v2 unit tests (BL-MM9-FREEZE-v2, 5/12).

测覆盖:
- session 生命周期 (start → record → end → freeze 拿 archive)
- record 只在 active session 内写 (核心隔离机制)
- 嵌套 depth_guard 防复用阶段污染
- 大字段截断
- 各种过滤 / 错误恢复
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

# 让 trace_recorder 用临时目录, 不污染真实 ~/.catfish/
@pytest.fixture(autouse=True)
def isolated_trace_dir(tmp_path, monkeypatch):
    """每个 test 一个独立 trace 目录, 避免互相干扰."""
    from catfish_tool_bridge import trace_recorder
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    fake_catfish = fake_home / ".catfish" / "traces"
    monkeypatch.setattr(trace_recorder, "TRACE_DIR", fake_catfish)
    monkeypatch.setattr(trace_recorder, "TRACE_PATH", fake_catfish / "active.jsonl")
    monkeypatch.setattr(trace_recorder, "STATE_PATH", fake_catfish / "_state.json")
    monkeypatch.setattr(trace_recorder, "LAST_COMPLETED_PATH", fake_catfish / "_last_completed.json")
    monkeypatch.setattr(trace_recorder, "ARCHIVE_DIR", fake_catfish)
    # reset module-level seq
    trace_recorder._SEQ = 0
    yield fake_catfish


# ─── start_session / end_session 状态机 ──────────────────────────────


def test_start_session_creates_state():
    from catfish_tool_bridge import trace_recorder
    state = trace_recorder.start_session(name="test-skill", description="测试")
    assert state["name"] == "test-skill"
    assert state["description"] == "测试"
    assert "session_id" in state
    assert "started_at" in state
    # state 持久化
    assert trace_recorder.STATE_PATH.exists()
    assert trace_recorder.is_session_active()


def test_start_session_default_name():
    """name 为空 / None → 'unnamed' (不抛)."""
    from catfish_tool_bridge import trace_recorder
    state = trace_recorder.start_session(name="", description=None)
    assert state["name"] == "unnamed"
    assert state["description"] == ""


def test_start_session_auto_closes_old():
    """老 session 未 end, start 新 → 自动 end 老的."""
    from catfish_tool_bridge import trace_recorder
    trace_recorder.start_session(name="old")
    trace_recorder.record("catfish_browser_goto", {"url": "http://a"}, {"ok": True}, True, 100)
    # 没 end_session 直接 start 新的
    trace_recorder.start_session(name="new")
    # 老 session 被自动归档
    last = trace_recorder.get_last_completed_session()
    assert last is not None
    assert last["name"] == "old"
    # 新 session 是 active
    active = trace_recorder.get_active_session()
    assert active is not None
    assert active["name"] == "new"


def test_start_session_rotates_stale_active_jsonl(isolated_trace_dir):
    """没 active state, 但 active.jsonl 有残留 → 搬到 stale_*.jsonl."""
    from catfish_tool_bridge import trace_recorder
    # 手动塞残留
    trace_recorder.TRACE_DIR.mkdir(parents=True, exist_ok=True)
    trace_recorder.TRACE_PATH.write_text('{"residue":true}\n', encoding="utf-8")
    trace_recorder.start_session(name="fresh")
    # 残留搬走了
    stales = list(trace_recorder.TRACE_DIR.glob("stale_*.jsonl"))
    assert len(stales) == 1
    # active.jsonl 应该空 / 不存在
    assert not trace_recorder.TRACE_PATH.exists() or trace_recorder.TRACE_PATH.stat().st_size == 0


def test_end_session_no_active():
    """没 active session 时 end → ok=False, 不抛."""
    from catfish_tool_bridge import trace_recorder
    r = trace_recorder.end_session()
    assert r["ok"] is False
    assert "没有 active" in r["error"]


def test_end_session_archives_and_clears():
    from catfish_tool_bridge import trace_recorder
    trace_recorder.start_session(name="eis-login")
    trace_recorder.record("catfish_browser_goto", {"url": "x"}, {"ok": True}, True, 100)
    trace_recorder.record("catfish_browser_fill", {"selector": "#a"}, {"ok": True}, True, 100)
    r = trace_recorder.end_session(reason="done")
    assert r["ok"] is True
    assert r["step_count"] == 2
    assert r["name"] == "eis-login"
    assert "session_eis-login_" in r["archive_path"]
    # active 清干净
    assert not trace_recorder.is_session_active()
    assert not trace_recorder.TRACE_PATH.exists()
    # last_completed 写入
    last = trace_recorder.get_last_completed_session()
    assert last["name"] == "eis-login"
    assert last["step_count"] == 2


# ─── 核心: record 只在 active 时写 (隔离机制) ────────────────────────


def test_record_no_session_silently_skips():
    """没 active session → record 静默跳过, 不写文件."""
    from catfish_tool_bridge import trace_recorder
    trace_recorder.record("catfish_browser_goto", {"url": "http://dirty"}, {"ok": True}, True, 100)
    trace_recorder.record("catfish_browser_fill", {"selector": "#x", "text": "y"}, {"ok": True}, True, 100)
    assert not trace_recorder.TRACE_PATH.exists()


def test_record_after_end_silently_skips():
    """end 之后再 record → 不写 (复用阶段污染防护)."""
    from catfish_tool_bridge import trace_recorder
    trace_recorder.start_session(name="x")
    trace_recorder.record("catfish_browser_goto", {"url": "a"}, {"ok": True}, True, 100)
    trace_recorder.end_session()
    # 模拟复用阶段
    trace_recorder.record("catfish_browser_goto", {"url": "dirty"}, {"ok": True}, True, 100)
    trace_recorder.record("catfish_browser_fill", {"selector": "wrong"}, {"ok": True}, True, 100)
    # active.jsonl 应该没了 (end 时 rename 走了), 新 record 不该创建
    assert not trace_recorder.TRACE_PATH.exists()


def test_record_in_session_writes_with_metadata():
    from catfish_tool_bridge import trace_recorder
    state = trace_recorder.start_session(name="t1", description="d")
    trace_recorder.record("catfish_browser_goto", {"url": "http://x"}, {"ok": True}, True, 123)
    assert trace_recorder.TRACE_PATH.exists()
    lines = trace_recorder.TRACE_PATH.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["tool"] == "catfish_browser_goto"
    assert entry["args"] == {"url": "http://x"}
    assert entry["ok"] is True
    assert entry["duration_ms"] == 123
    assert entry["session_id"] == state["session_id"]
    assert entry["session_name"] == "t1"
    assert entry["seq"] == 1


def test_record_unrecorded_tool_skipped():
    """非白名单 tool → 不录."""
    from catfish_tool_bridge import trace_recorder
    trace_recorder.start_session(name="x")
    # catfish_remember 不在 RECORDED_TOOLS
    trace_recorder.record("catfish_remember", {"fact": "a"}, {"ok": True}, True, 100)
    trace_recorder.record("catfish_run_skill", {"skill_path": "x"}, {"ok": True}, True, 100)
    assert not trace_recorder.TRACE_PATH.exists() or trace_recorder.TRACE_PATH.stat().st_size == 0


def test_seq_increments_monotonically():
    from catfish_tool_bridge import trace_recorder
    trace_recorder.start_session(name="t")
    for i in range(5):
        trace_recorder.record("catfish_browser_goto", {"url": f"http://{i}"}, {"ok": True}, True, 10)
    lines = trace_recorder.TRACE_PATH.read_text(encoding="utf-8").strip().split("\n")
    seqs = [json.loads(l)["seq"] for l in lines]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == 5  # 严格递增 + 唯一


# ─── 嵌套 depth_guard ─────────────────────────────────────────────────


def test_depth_guard_outermost_returns_true():
    from catfish_tool_bridge import trace_recorder
    with trace_recorder.record_depth_guard():
        assert trace_recorder.is_outermost() is True


def test_depth_guard_nested_returns_false():
    from catfish_tool_bridge import trace_recorder
    with trace_recorder.record_depth_guard():
        with trace_recorder.record_depth_guard():
            # 第二层
            assert trace_recorder.is_outermost() is False
        # 退出第二层后回到第一层
        assert trace_recorder.is_outermost() is True


def test_depth_guard_thread_local_state():
    """退出 with 后 depth 归零."""
    from catfish_tool_bridge import trace_recorder
    with trace_recorder.record_depth_guard():
        pass
    assert trace_recorder.is_outermost() is True  # 默认 depth=0, 也算 outermost


# ─── 大字段截断 ──────────────────────────────────────────────────────


def test_truncate_long_string():
    from catfish_tool_bridge import trace_recorder
    trace_recorder.start_session(name="t")
    huge = "x" * 20000  # > _MAX_FIELD_BYTES (8000)
    trace_recorder.record(
        "catfish_browser_snapshot", {"max": 100}, {"ok": True, "dom": huge}, True, 100
    )
    entry = json.loads(trace_recorder.TRACE_PATH.read_text(encoding="utf-8").strip())
    assert len(entry["result"]["dom"]) < 9000  # 截断了
    assert "truncated" in entry["result"]["dom"]


def test_truncate_long_list():
    from catfish_tool_bridge import trace_recorder
    trace_recorder.start_session(name="t")
    big_list = [{"i": i} for i in range(100)]
    trace_recorder.record(
        "catfish_browser_snapshot", {}, {"ok": True, "items": big_list}, True, 100
    )
    entry = json.loads(trace_recorder.TRACE_PATH.read_text(encoding="utf-8").strip())
    # 取前 50 + 1 个 marker
    assert len(entry["result"]["items"]) == 51


# ─── read_session_traces / read_traces ───────────────────────────────


def test_read_session_traces_filters_failed():
    from catfish_tool_bridge import trace_recorder
    trace_recorder.start_session(name="t")
    trace_recorder.record("catfish_browser_goto", {"url": "ok"}, {"ok": True}, True, 100)
    trace_recorder.record("catfish_browser_fill", {"selector": "#a"}, {"ok": False, "error": "x"}, False, 100)
    trace_recorder.record("catfish_browser_click", {"selector": "#b"}, {"ok": True}, True, 100)
    info = trace_recorder.end_session()
    only_ok = trace_recorder.read_session_traces(info["archive_path"], only_ok=True)
    assert len(only_ok) == 2  # 跳过 ok=False
    all_steps = trace_recorder.read_session_traces(info["archive_path"], only_ok=False)
    assert len(all_steps) == 3


def test_read_session_traces_missing_file():
    """文件不存在 → 返空列表, 不抛."""
    from catfish_tool_bridge import trace_recorder
    out = trace_recorder.read_session_traces("/nonexistent/path.jsonl")
    assert out == []


def test_session_summary_no_active():
    from catfish_tool_bridge import trace_recorder
    s = trace_recorder.session_summary()
    assert s["active_session"] is None
    assert s["last_completed"] is None


def test_session_summary_with_active():
    from catfish_tool_bridge import trace_recorder
    trace_recorder.start_session(name="ongoing")
    trace_recorder.record("catfish_browser_goto", {"url": "x"}, {"ok": True}, True, 100)
    s = trace_recorder.session_summary()
    assert s["active_session"]["name"] == "ongoing"
    assert s["active_file"]["lines"] == 1
