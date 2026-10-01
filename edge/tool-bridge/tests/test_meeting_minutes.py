"""meeting_minutes 单测 (10/1). 大模型调用用假的 call_llm 替掉。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from catfish_tool_bridge import meeting_minutes as mm
from catfish_tool_bridge.recmode import aggregator


def _meeting(tmp_path: Path, segments: list[dict], names: dict | None = None, mid: str = "mtg_20261001-100000_ab12") -> Path:
    d = tmp_path / "meetings" / mid
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({
        "id": mid, "title": "资质集采周会", "created_at": "2026-10-01T10:00:00+08:00", "attendees": 3,
    }), encoding="utf-8")
    (d / "transcript.json").write_text(json.dumps({"segments": segments}), encoding="utf-8")
    if names is not None:
        (d / "speakers.json").write_text(json.dumps(names), encoding="utf-8")
    return d


SEGS = [
    {"spk": 0, "start": 0.0, "end": 2.0, "text": "我们先过一下资质集采。"},
    {"spk": 0, "start": 2.1, "end": 4.0, "text": "二期材料周五前交。"},
    {"spk": 1, "start": 65.0, "end": 70.0, "text": "好的我来准备。"},
]


def test_transcript_lines_merge_same_speaker_and_apply_names():
    lines = mm.transcript_lines({"segments": SEGS}, {"0": "陈鸿波"})
    assert lines == [
        "[00:00] 陈鸿波: 我们先过一下资质集采。二期材料周五前交。",
        "[01:05] 说话人2: 好的我来准备。",
    ]


def test_long_transcript_is_chunked_without_splitting_lines():
    lines = [f"[00:00] A: {'字' * 100}"] * 50
    chunks = mm.chunk_lines(lines, limit=1000)
    assert len(chunks) > 1
    assert sum(len(c) for c in chunks) == 50


def test_parse_minutes_tolerates_fences_and_drops_bad_dates():
    raw = """好的, 纪要如下:
```json
{"summary": "过了资质集采进度。", "decisions": ["二期周五交"],
 "action_items": [{"owner": "说话人2", "task": "准备二期材料", "due": "2026-10-03"},
                  {"owner": "", "task": "跟进高新", "due": "下周"},
                  {"task": "  "}],
 "open_questions": []}
```"""
    m = mm.parse_minutes(raw)
    assert m["summary"] == "过了资质集采进度。"
    assert m["action_items"] == [
        {"owner": "说话人2", "task": "准备二期材料", "due": "2026-10-03"},
        {"owner": "", "task": "跟进高新", "due": ""},  # "下周" 不是日期
    ]
    with pytest.raises(ValueError):
        mm.parse_minutes("我不会输出 JSON")


def test_render_markdown_has_checkboxes_and_disclaimer():
    md = mm.render_markdown({"title": "周会", "created_at": "2026-10-01T10:00"},
                            {"summary": "s", "decisions": [], "open_questions": ["q"],
                             "action_items": [{"owner": "张三", "task": "交材料", "due": "2026-10-03"}]},
                            {"0": "张三"})
    assert "- [ ] 交材料 (张三 · 截止 2026-10-03)" in md
    assert "## 决议\n\n(无)" in md
    assert "请核对" in md


def _fake_llm(monkeypatch, replies: list[str]):
    calls: list[list[dict]] = []

    async def fake(messages, **kw):
        calls.append(messages)
        assert kw["source"] == "plugin:toolbridge-meeting"
        assert kw["gateway_url"] == "http://gw.test"
        return replies[len(calls) - 1]

    monkeypatch.setattr(aggregator, "call_llm", fake)
    return calls


REPLY = json.dumps({"summary": "s", "decisions": ["d"], "action_items": [], "open_questions": []})


def test_summarize_single_call_writes_both_files(tmp_path, monkeypatch):
    d = _meeting(tmp_path, SEGS, {"0": "陈鸿波"})
    calls = _fake_llm(monkeypatch, [REPLY])
    doc = asyncio.run(mm.summarize(d, gateway_url="http://gw.test", auth_token="t"))
    assert len(calls) == 1
    assert "陈鸿波" in calls[0][1]["content"] and "2026-10-01" in calls[0][1]["content"]
    assert doc["chunks"] == 1 and doc["decisions"] == ["d"]
    assert json.loads((d / "minutes.json").read_text(encoding="utf-8"))["summary"] == "s"
    assert (d / "minutes.md").read_text(encoding="utf-8").startswith("# 资质集采周会")


def test_summarize_long_meeting_maps_then_reduces(tmp_path, monkeypatch):
    long_segs = [{"spk": i % 2, "start": i * 10.0, "end": i * 10.0 + 9, "text": "内容" * 400} for i in range(40)]
    d = _meeting(tmp_path, long_segs)
    chunks = len(mm.chunk_lines(mm.transcript_lines({"segments": long_segs}, {})))
    assert chunks > 1
    calls = _fake_llm(monkeypatch, [REPLY] * (chunks + 1))
    doc = asyncio.run(mm.summarize(d, gateway_url="http://gw.test", auth_token="t"))
    assert len(calls) == chunks + 1
    assert "一部分" in calls[0][1]["content"]
    assert "分段整理" in calls[-1][0]["content"]
    assert doc["chunks"] == chunks


def test_empty_transcript_and_missing_transcript_are_clear_errors(tmp_path):
    d = _meeting(tmp_path, [])
    with pytest.raises(ValueError, match="空"):
        asyncio.run(mm.summarize(d, gateway_url="http://gw.test", auth_token="t"))
    (d / "transcript.json").unlink()
    with pytest.raises(ValueError, match="还没转写"):
        asyncio.run(mm.summarize(d, gateway_url="http://gw.test", auth_token="t"))


def test_handle_summarize_validates_id_and_token(tmp_path):
    _meeting(tmp_path, SEGS)
    base = {"catfish_home": str(tmp_path), "gateway_url": "http://gw.test"}
    for bad in ("../x", "mtg_../../etc", "x"):
        with pytest.raises(ValueError, match="不合法"):
            asyncio.run(mm.handle_summarize({**base, "meeting_id": bad, "auth_token": "t"}))
    with pytest.raises(ValueError, match="不存在"):
        asyncio.run(mm.handle_summarize({**base, "meeting_id": "mtg_nope", "auth_token": "t"}))
    with pytest.raises(RuntimeError, match="没登录"):
        asyncio.run(mm.handle_summarize({**base, "meeting_id": "mtg_20261001-100000_ab12"}))
