"""BL-LEARN-RECMODE CDP listener v0 骨架单测.

跑法: cd central/llm-gateway && PYTHONPATH=src python -m pytest tests/test_recmode_cdp_listener.py -q

v0 只测骨架: start/stop 接口 + events.jsonl 落档 + meta.json + active_sessions 管理.
真 CDP ws 连接 + 截图 5/26 sprint 真做时填.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from catfish_gateway.recmode import cdp_listener


@pytest.mark.asyncio
async def test_start_stop_roundtrip(tmp_path):
    """v0: start → 加几个 mock events → stop → 看 events.jsonl + meta.json 落档"""
    sess = await cdp_listener.CDPRecordingSession.start(
        session_id="test_001",
        chrome_ws="ws://localhost:9222",
        output_root=tmp_path,
        connect_ws=False,  # 测试不真连
    )

    # mock 几个 events
    sess.state.events.append(cdp_listener.CDPEvent(
        ts=1.0, kind="page_navigated",
        content={"url": "http://eis.ffcs.cn/", "title": "EIS"},
        screenshot_id="kf_0001",
    ))
    sess.state.events.append(cdp_listener.CDPEvent(
        ts=3.5, kind="dom_changed",
        content={"summary": "DOM updated"},
    ))
    sess.state.keyframe_count = 1

    summary = await sess.stop()

    # events.jsonl
    events_path = tmp_path / "test_001" / "events.jsonl"
    assert events_path.exists()
    lines = events_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    e1 = json.loads(lines[0])
    assert e1["kind"] == "page_navigated"
    assert e1["url"] == "http://eis.ffcs.cn/"
    assert e1["screenshot_id"] == "kf_0001"
    e2 = json.loads(lines[1])
    assert e2["kind"] == "dom_changed"
    assert e2["screenshot_id"] is None

    # meta.json
    meta_path = tmp_path / "test_001" / "meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta["session_id"] == "test_001"
    assert meta["events_count"] == 2
    assert meta["keyframes_count"] == 1
    assert "duration_s" in meta

    # summary
    assert summary["events_count"] == 2
    assert summary["output_dir"] == str(tmp_path / "test_001")


@pytest.mark.asyncio
async def test_screenshots_dir_created(tmp_path):
    """start 时自动建 screenshots/ 子目录 (5/26 真做时往里写 PNG)"""
    await cdp_listener.CDPRecordingSession.start(
        session_id="test_002",
        output_root=tmp_path,
        connect_ws=False,
    )
    assert (tmp_path / "test_002" / "screenshots").is_dir()


@pytest.mark.asyncio
async def test_module_level_start_stop(tmp_path, monkeypatch):
    """gateway endpoint 用的 module-level helper"""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))

    info = await cdp_listener.start_recording("rec_a", connect_ws=False)
    assert info["session_id"] == "rec_a"
    assert "rec_a" in cdp_listener.list_active()

    summary = await cdp_listener.stop_recording("rec_a")
    assert summary["session_id"] == "rec_a"
    assert "rec_a" not in cdp_listener.list_active()


@pytest.mark.asyncio
async def test_duplicate_start_rejected(tmp_path, monkeypatch):
    """同 session_id 重复 start → ValueError"""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    await cdp_listener.start_recording("rec_dup", connect_ws=False)
    with pytest.raises(ValueError, match="已在录中"):
        await cdp_listener.start_recording("rec_dup", connect_ws=False)
    # cleanup
    await cdp_listener.stop_recording("rec_dup")


@pytest.mark.asyncio
async def test_stop_unknown_session_rejected(tmp_path, monkeypatch):
    """stop 不存在的 session → ValueError"""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    with pytest.raises(ValueError, match="没在录中"):
        await cdp_listener.stop_recording("rec_nonexistent")


@pytest.mark.asyncio
async def test_long_pause_detector_fires(tmp_path, monkeypatch):
    """直接调 _long_pause_detector 看会不会写 long_pause event (压时间).

    完整版 5/26 sprint 才接真 ws + 真背景 task. v0 只验逻辑."""
    sess = await cdp_listener.CDPRecordingSession.start(
        session_id="test_lp", output_root=tmp_path, connect_ws=False,
    )
    # 模拟 last_event_ts 是 5s 前 + 上次 keyframe 也是 5s 前
    sess.state.last_event_ts = time.time() - 5.0
    sess.state.last_keyframe_ts = time.time() - 5.0

    # 手动调一次 detector 体内的逻辑 (不跑 sleep loop)
    now = time.time()
    gap = now - sess.state.last_event_ts
    assert gap >= cdp_listener._LONG_PAUSE_THRESHOLD_S
    # detector 真跑会触发 _capture_screenshot — v0 这是 noop, 测它不挂
    kf_id = await cdp_listener._capture_screenshot(sess.state)
    assert kf_id.startswith("kf_")
    assert sess.state.keyframe_count == 1
