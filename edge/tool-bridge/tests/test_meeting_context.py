"""meeting_context 单测 (11/3): 纪要的背景材料 —— 知识库里会上提到的条目 + 同系列上一次会议。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from catfish_tool_bridge import gateway_llm
from catfish_tool_bridge import meeting_context as mc
from catfish_tool_bridge import meeting_minutes as mm


def _wiki(home: Path, sub: str, name: str, title: str, body: str, aliases: str = "", status: str = "") -> None:
    d = home / "wiki" / sub
    d.mkdir(parents=True, exist_ok=True)
    fm = [f"title: {title}"] + ([f"aliases: [{aliases}]"] if aliases else []) + ([f"ontology_status: {status}"] if status else [])
    (d / f"{name}.md").write_text("---\n" + "\n".join(fm) + "\n---\n" + body, encoding="utf-8")


def _meeting(home: Path, mid: str, title: str, created: str, text: str, minutes: dict | None = None) -> Path:
    d = home / "meetings" / mid
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"id": mid, "title": title, "created_at": created, "attendees": 3,
                                              "hotwords": []}, ensure_ascii=False), encoding="utf-8")
    (d / "transcript.json").write_text(json.dumps({"segments": [{"spk": 0, "start": 0, "end": 2, "text": text}]},
                                                  ensure_ascii=False), encoding="utf-8")
    if minutes is not None:
        (d / "minutes.json").write_text(json.dumps(minutes, ensure_ascii=False), encoding="utf-8")
    return d


def test_series_key_drops_dates_and_counts():
    assert mc.series_key("资质集采周会 10.27") == mc.series_key("资质集采周会（第3次）") == "资质集采周会"
    assert mc.series_key("2026-11-03 项目例会") == mc.series_key("项目例会 第 12 期")
    assert mc.series_key("") == "" and mc.series_key("11.3") == ""


def test_wiki_context_picks_mentioned_entries_only(tmp_path):
    _wiki(tmp_path, "entities", "laoli", "李建国", "福建电信政企部总监, 负责资质集采。", aliases="老李")
    _wiki(tmp_path, "concepts", "csmm", "CSMM", "软件过程能力成熟度评估。")
    _wiki(tmp_path, "entities", "other", "王五", "没被提到的人。")
    _wiki(tmp_path, "entities", "pend", "待确认人", "还没确认。", status="pending")
    ctx = mc.wiki_context(tmp_path, "老李说 CSMM 二级周五交, 待确认人也在")
    assert [w["title"] for w in ctx] == ["CSMM", "李建国"]
    assert ctx[1]["excerpt"].startswith("福建电信政企部总监")
    assert ctx[1]["rel_path"] == "wiki/entities/laoli.md"


def test_previous_meeting_same_series_earlier_with_minutes(tmp_path):
    prev = {"open_questions": ["二期报价没定"], "action_items": [{"owner": "张三", "task": "交二期材料", "due": "2026-10-31"}]}
    _meeting(tmp_path, "mtg_a", "资质集采周会 10.20", "2026-10-20T10:00:00+08:00", "x", {"open_questions": ["旧"]})
    _meeting(tmp_path, "mtg_b", "资质集采周会 10.27", "2026-10-27T10:00:00+08:00", "x", prev)
    _meeting(tmp_path, "mtg_c", "别的会", "2026-10-30T10:00:00+08:00", "x", {"open_questions": ["无关"]})
    _meeting(tmp_path, "mtg_later", "资质集采周会 11.10", "2026-11-10T10:00:00+08:00", "x", {"open_questions": ["以后"]})
    me = _meeting(tmp_path, "mtg_now", "资质集采周会 11.3", "2026-11-03T10:00:00+08:00", "y")
    meta = json.loads((me / "meta.json").read_text(encoding="utf-8"))
    p = mc.previous_meeting(me, meta)
    assert p["meeting_id"] == "mtg_b" and p["open_questions"] == ["二期报价没定"]
    assert p["action_items"][0]["task"] == "交二期材料"


def test_summarize_passes_background_and_notes_references(tmp_path, monkeypatch):
    _wiki(tmp_path, "entities", "laoli", "李建国", "政企部总监。", aliases="老李")
    _meeting(tmp_path, "mtg_b", "资质集采周会 10.27", "2026-10-27T10:00:00+08:00", "x",
             {"open_questions": ["二期报价没定"], "action_items": []})
    me = _meeting(tmp_path, "mtg_now", "资质集采周会 11.3", "2026-11-03T10:00:00+08:00", "老李说二期报价定了")
    calls: list[list[dict]] = []

    async def fake(messages, **kw):
        calls.append(messages)
        return json.dumps({"summary": "s", "decisions": [], "action_items": [], "open_questions": [],
                           "carried_over": ["二期报价已定"]}, ensure_ascii=False)

    monkeypatch.setattr(gateway_llm, "call_llm", fake)
    doc = asyncio.run(mm.summarize(me, gateway_url="http://gw.test", auth_token="t"))
    prompt = calls[0][1]["content"]
    assert "背景材料" in prompt and "李建国: 政企部总监。" in prompt and "二期报价没定" in prompt
    assert doc["carried_over"] == ["二期报价已定"]
    assert doc["references"]["wiki"] == [{"title": "李建国", "rel_path": "wiki/entities/laoli.md"}]
    assert doc["references"]["previous_meeting"]["meeting_id"] == "mtg_b"
    md = (me / "minutes.md").read_text(encoding="utf-8")
    assert "## 上次遗留事项进展" in md and "参考的背景材料: 知识库 「李建国」; 上次会议「资质集采周会 10.27」(2026-10-27)" in md


def test_no_background_keeps_prompt_unchanged(tmp_path):
    me = _meeting(tmp_path, "mtg_now", "", "2026-11-03T10:00:00+08:00", "随便聊聊")
    meta = json.loads((me / "meta.json").read_text(encoding="utf-8"))
    ctx = mc.gather(me, meta, "随便聊聊", {})
    assert ctx == {"wiki": [], "previous": None}
    assert mc.prompt_block(ctx) == "" and mc.references_note(ctx) == ""
