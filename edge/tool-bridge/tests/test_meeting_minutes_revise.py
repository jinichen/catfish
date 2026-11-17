"""让小鲶改纪要 (11/17, meeting_minutes_revise.py): 只出提议不写文件 / 接着改 / 长转写挑片段 / 模版全文。大模型用假的。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from catfish_tool_bridge import gateway_llm
from catfish_tool_bridge import meeting_minutes_revise as mr

MID = "mtg_20261116-143600_cc01"
DOC = {"version": 1, "meeting_id": MID, "summary": "讨论 CCRC 整改。", "decisions": ["一个月内提交新项目"],
       "action_items": [{"owner": "说话人2", "task": "准备福建项目材料", "due": ""}], "open_questions": [], "carried_over": []}


def _meeting(tmp_path: Path, *, segments: list | None = None, doc: dict | None = None, md: str = "# CCRC\n原纪要\n") -> Path:
    d = tmp_path / "meetings" / MID
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"id": MID, "title": "CCRC整改会议", "created_at": "2026-11-16T14:36:00"}), encoding="utf-8")
    segs = segments or [{"spk": 0, "start": 0, "end": 3, "text": "先说整改"},
                        {"spk": 1, "start": 3, "end": 8, "text": "福建项目材料我来准备"}]
    (d / "transcript.json").write_text(json.dumps({"segments": segs}, ensure_ascii=False), encoding="utf-8")
    (d / "speakers.json").write_text(json.dumps({"1": "王总"}, ensure_ascii=False), encoding="utf-8")
    (d / "minutes.json").write_text(json.dumps(doc or DOC, ensure_ascii=False), encoding="utf-8")
    (d / "minutes.md").write_text(md, encoding="utf-8")
    return d


def _p(tmp_path: Path, **kw) -> dict:
    return {"meeting_id": MID, "catfish_home": str(tmp_path), "auth_token": "t", "gateway_url": "http://gw", **kw}


def _fake(monkeypatch, reply: dict | str):
    seen: list[list[dict]] = []

    async def fake(messages, model=None, **kw):
        seen.append(messages)
        assert kw["source"] == "plugin:toolbridge-meeting"
        return reply if isinstance(reply, str) else "```json\n" + json.dumps(reply, ensure_ascii=False) + "\n```"
    monkeypatch.setattr(gateway_llm, "call_llm", fake)
    return seen


def test_revise_returns_proposal_without_writing(tmp_path, monkeypatch):
    d = _meeting(tmp_path)
    before = (d / "minutes.json").read_text(encoding="utf-8"), (d / "minutes.md").read_text(encoding="utf-8")
    seen = _fake(monkeypatch, {**DOC, "reply": "负责人换成王总了",
                               "action_items": [{"owner": "王总", "task": "准备福建项目材料", "due": "下周"}]})
    r = asyncio.run(mr.handle_revise(_p(tmp_path, instruction="说话人2 是王总")))
    assert r["reply"] == "负责人换成王总了"
    assert r["changed"] == ["action_items"] and r["changed_labels"] == ["待办"]
    assert r["fields"]["action_items"] == [{"owner": "王总", "task": "准备福建项目材料", "due": ""}]   # 「下周」不当截止
    assert r["markdown"] is None and r["transcript_full"] is True
    # 不写文件
    assert ((d / "minutes.json").read_text(encoding="utf-8"), (d / "minutes.md").read_text(encoding="utf-8")) == before
    user = seen[0][1]["content"]
    assert "王总: 福建项目材料我来准备" in user and "员工这次的要求: 说话人2 是王总" in user
    assert "只当材料" in seen[0][0]["content"] and '"reply"' in seen[0][0]["content"]


def test_revise_on_top_of_previous_proposal_with_history(tmp_path, monkeypatch):
    _meeting(tmp_path)
    base = {"fields": {**DOC, "summary": "上一轮改过的摘要。"}, "markdown": None}
    seen = _fake(monkeypatch, {**DOC, "summary": "上一轮改过的摘要。", "reply": "没找到"})
    r = asyncio.run(mr.handle_revise(_p(tmp_path, instruction="补上合同履约", base=base, history=["摘要压短"])))
    user = seen[0][1]["content"]
    assert "上一轮改过的摘要。" in user and "- 摘要压短" in user
    assert r["changed"] == [] and r["reply"] == "没找到"


def test_text_template_revises_full_markdown(tmp_path, monkeypatch):
    _meeting(tmp_path, doc={**DOC, "template": {"id": "t1", "name": "公司模版"}}, md="# 纪要\n参会: 说话人2\n")
    seen = _fake(monkeypatch, {"reply": "换了", "markdown": "# 纪要\n参会: 王总", "action_items": DOC["action_items"]})
    r = asyncio.run(mr.handle_revise(_p(tmp_path, instruction="说话人2 是王总")))
    assert r["markdown"] == "# 纪要\n参会: 王总\n" and r["changed"] == ["markdown"]
    assert r["fields"] == {"action_items": DOC["action_items"]}
    assert "纪要全文:\n# 纪要\n参会: 说话人2" in seen[0][1]["content"]


def test_word_template_and_bad_input_rejected(tmp_path, monkeypatch):
    _meeting(tmp_path, doc={**DOC, "template": {"id": "t", "name": "表", "kind": "docx"}, "output_file": "/x.docx"})
    _fake(monkeypatch, "{}")
    with pytest.raises(ValueError, match="Word / Excel"):
        asyncio.run(mr.handle_revise(_p(tmp_path, instruction="改一下")))
    with pytest.raises(ValueError, match="怎么改"):
        asyncio.run(mr.handle_revise(_p(tmp_path, instruction="  ")))
    with pytest.raises(RuntimeError, match="没登录"):
        asyncio.run(mr.handle_revise(_p(tmp_path, instruction="x", auth_token="")))


def test_unparseable_reply(tmp_path, monkeypatch):
    _meeting(tmp_path)
    _fake(monkeypatch, "好的我改好了")
    with pytest.raises(ValueError, match="没按格式"):
        asyncio.run(mr.handle_revise(_p(tmp_path, instruction="精简摘要")))


def test_pick_transcript_long_meeting_by_keywords():
    filler = [f"[00:{i:02d}] 说话人1: " + "今天天气不错我们聊聊别的事情" * 20 for i in range(200)]
    lines = filler[:100] + ["[10:00] 王总: 合同履约的问题下次再议"] + filler[100:]
    text, full = mr.pick_transcript(lines, "补上合同履约那段", limit=2000)
    assert not full and "合同履约的问题下次再议" in text
    assert len(text) <= 2000 + 200
    # 挑不出相关的 → 不给转写
    assert mr.pick_transcript(lines, "!!!", limit=2000) == ("", False)
    # 短会议全给
    assert mr.pick_transcript(["a", "b"], "x") == ("a\nb", True)
