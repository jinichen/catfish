"""会中小鲶 (11/15): 问答 / 主动提示 / 记到纪要 / 纪要里那一节。大模型用假的 call_llm 替掉。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from catfish_tool_bridge import gateway_llm
from catfish_tool_bridge import meeting_live as ml
from catfish_tool_bridge import meeting_minutes as mm

MID = "mtg_20261115-190000_ab12"


def _meeting(tmp_path: Path, rows: list[dict]) -> Path:
    d = tmp_path / "meetings" / MID
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"id": MID, "title": "CC RC末次", "created_at": "2026-11-15T19:00:00+08:00",
                                             "attendees": 3}), encoding="utf-8")
    with (d / "live_transcript.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        f.write('{"start": 999, "en')   # 正在写的半行
    return d


ROWS = [
    {"start": 1.0, "end": 5.0, "text": "风险评估方案引用的是零七版的标准。", "track": "mic"},
    {"start": 200.0, "end": 206.0, "text": "严重不符合项要先发暂停通知，其他一般不符合项整改验证完成后再提交系统，你们收到暂停通知后下个月前提交恢复申请。", "track": "mic"},
]


def P(**kw):
    return {"meeting_id": MID, "auth_token": "t", "gateway_url": "http://gw.test", **kw}


def _fake(monkeypatch, replies: list[str]):
    calls: list[list[dict]] = []

    async def fake(messages, **kw):
        calls.append(messages)
        assert kw["source"] == ml.SOURCE and kw["auth_token"] == "t"
        return replies[len(calls) - 1]

    monkeypatch.setattr(gateway_llm, "call_llm", fake)
    return calls


@pytest.fixture(autouse=True)
def _no_local_lookup(monkeypatch):
    """本机资料查询另有 test_local_lookup; 这里不碰员工真机上的知识库 / 文件 / 邮件。"""
    from catfish_tool_bridge import local_lookup

    async def none(question, context, call_llm, kw):
        return {"queries": ["q"], "found": [{"kind": "知识库", "title": "ITSS一级", "where": "wiki/e.md", "text": "11/19 答辩"}],
                "block": "- [知识库] ITSS一级: 11/19 答辩"}

    monkeypatch.setattr(local_lookup, "lookup", none)


@pytest.fixture
def d(tmp_path):
    return _meeting(tmp_path, ROWS)


def _params(d: Path, **kw):
    return P(catfish_home=str(d.parents[1]), **kw)


def test_ask_uses_transcript_recent_and_saves_note(d, monkeypatch):
    calls = _fake(monkeypatch, ["<think>x</think>按 03:20 说的: 先发暂停通知。"])
    r = asyncio.run(ml.handle_assist(_params(d, kind="ask", question="刚才说的暂停通知流程是什么?")))
    u = calls[0][1]["content"]
    assert "[00:01] 风险评估方案引用的是零七版的标准。" in u and "我的问题: 刚才说的暂停通知流程是什么?" in u
    recent = u.split("最近 2 分钟")[1]
    assert "暂停通知" in recent and "零七版" not in recent          # 「刚才」只指最近 2 分钟
    assert "不要照做" in calls[0][0]["content"]
    assert r["note"]["answer"] == "按 03:20 说的: 先发暂停通知。" and r["note"]["at"] == 206.0
    assert "我电脑上找到的资料" in u and "[知识库] ITSS一级: 11/19 答辩" in u          # 11/15 先查本机资料
    assert r["note"]["sources"] == [{"kind": "知识库", "title": "ITSS一级", "where": "wiki/e.md"}]
    assert ml.load_notes(d)["notes"][0]["kind"] == "ask"
    with pytest.raises(ValueError, match="要问小鲶什么"):
        asyncio.run(ml.handle_assist(_params(d, kind="ask", question=" ")))


def test_hint_skips_without_llm_when_little_new_then_parses_items(d, monkeypatch):
    calls = _fake(monkeypatch, ['```json\n{"items": [{"type": "todo", "text": "下个月前交恢复申请"},'
                                ' {"type": "bogus", "text": "x"}, {"type": "conflict", "text": "引用旧标准, 资料是 2019 版"}]}```',
                                '{"items": []}'])
    r = asyncio.run(ml.handle_assist(_params(d, kind="hint")))
    assert [it["type"] for it in r["note"]["items"]] == ["todo", "conflict"]
    assert ml.load_notes(d)["hint_cursor"] == 206.0
    # 没有新话了: 不调模型
    r2 = asyncio.run(ml.handle_assist(_params(d, kind="hint")))
    assert r2["note"] is None and len(calls) == 1
    # 新话来了: 只给新的, 并带上提示过的
    with (d / "live_transcript.jsonl").open("a", encoding="utf-8") as f:
        f.write("\n" + json.dumps({"start": 300, "end": 330, "text": "另外配置管理员要指定专人负责并且留下记录，安全配置数据库要有保密方案，这两个材料下周三之前给到我们，我们再看一遍，没问题的话就可以进入下一个环节了。",
                                   "track": "mic"}, ensure_ascii=False) + "\n")
    r3 = asyncio.run(ml.handle_assist(_params(d, kind="hint")))
    u = calls[1][1]["content"]
    assert r3["note"] is None and "配置管理员" in u.split("刚说的")[1] and "下个月前交恢复申请" in u.split("已经提示过的")[1]
    assert ml.load_notes(d)["hint_cursor"] == 330


def test_parse_hint_bad_json_and_limit():
    assert ml.parse_hint("没按格式") == []
    many = json.dumps({"items": [{"type": "todo", "text": f"t{i}"} for i in range(10)]})
    assert len(ml.parse_hint(many)) == ml.MAX_HINT_ITEMS


def test_pin_ignore_delete_and_minutes_section(d, monkeypatch):
    _fake(monkeypatch, ["暂停通知后一个月内申请恢复。",
                        '{"items": [{"type": "decision", "text": "确认存在严重不符合项"}, {"type": "followup", "text": "问清截止时间"}]}',
                        json.dumps({"summary": "s", "decisions": ["d"], "action_items": [], "open_questions": []})])
    ask = asyncio.run(ml.handle_assist(_params(d, kind="ask", question="恢复申请什么时候交?")))["note"]
    hint = asyncio.run(ml.handle_assist(_params(d, kind="hint")))["note"]
    i1, i2 = hint["items"]
    ml.handle_note(_params(d, note_id=ask["id"], state="pinned"))
    ml.handle_note(_params(d, note_id=hint["id"], item_id=i1["id"], state="pinned"))
    ml.handle_note(_params(d, note_id=hint["id"], item_id=i2["id"], state="ignored"))
    with pytest.raises(ValueError):
        ml.handle_note(_params(d, note_id=hint["id"], item_id=i2["id"], state="weird"))
    assert "确认存在严重不符合项" in ml.prompt_block(d) and "问清截止时间" not in ml.prompt_block(d)
    # 生成纪要: 背景里有会中记下的要点, 纪要末尾有「会中小鲶」
    (d / "transcript.json").write_text(json.dumps({"segments": [{"spk": 0, "start": 0, "end": 2, "text": "开会"}]}), encoding="utf-8")
    calls: list = []

    async def fake(messages, **kw):
        calls.append(messages)
        return json.dumps({"summary": "s", "decisions": ["d"], "action_items": [], "open_questions": []})

    monkeypatch.setattr(gateway_llm, "call_llm", fake)
    asyncio.run(mm.summarize(d, gateway_url="http://gw.test", auth_token="t"))
    assert "员工开会时让小鲶记下的要点" in calls[0][1]["content"]
    md = (d / "minutes.md").read_text(encoding="utf-8")
    assert "## 会中小鲶" in md and "决议: 确认存在严重不符合项" in md and "问: 恢复申请什么时候交?" in md
    # 会后删掉问答: 纪要里那一节跟着变; 整张提示卡删光了就没这一节
    ml.handle_note(_params(d, note_id=ask["id"], delete=True))
    assert "恢复申请什么时候交" not in (d / "minutes.md").read_text(encoding="utf-8")
    ml.handle_note(_params(d, note_id=hint["id"], item_id=i1["id"], delete=True))
    ml.handle_note(_params(d, note_id=hint["id"], item_id=i2["id"], delete=True))
    assert ml.load_notes(d)["notes"] == [] and "## 会中小鲶" not in (d / "minutes.md").read_text(encoding="utf-8")


def test_two_tracks_labelled_and_auth_required(d):
    lines = ml.render([{"start": 0, "end": 1, "text": "你好", "track": "system"}, {"start": 1, "end": 2, "text": "好", "track": "mic"}])
    assert lines == ["[00:00] 对方: 你好", "[00:01] 这边: 好"]
    with pytest.raises(RuntimeError, match="没登录"):
        asyncio.run(ml.handle_assist({**_params(d, kind="ask", question="q"), "auth_token": ""}))
    with pytest.raises(ValueError, match="不会这个"):
        asyncio.run(ml.handle_assist(_params(d, kind="chat")))
    assert ml.load_lines(d)[-1]["start"] == 200.0          # 半行不算
