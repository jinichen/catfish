"""学习第 4 步 (11/18, learning_drill.py): 答辩陪练 —— 出题库 (评分点不给界面) / 口头回答 → 对照评分点 / 追问一次 /
跳过 / 下一题 / 问完出总结, 漏的点做成复习卡。大模型用假的。"""
from __future__ import annotations

import asyncio
import json

import pytest

from catfish_tool_bridge import gateway_llm, learning, learning_drill, learning_store, practice, web_lookup

BANK = {"intro": "我们开始吧, 先请你介绍一下。", "questions": [
    {"question": "你们应急响应的分级标准是怎么定的?", "points": ["有正式发布的分级管理办法", "按影响范围分级", "报告写明依据哪条"]},
    {"question": "二级要几个项目?", "points": ["近三年", "至少 3 个"]},
    {"question": "q3", "points": ["a", "b"]}, {"question": "q4", "points": ["a", "b"]}]}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    learning._RUNNING.clear()

    async def mats(*a, **k):
        return [{"kind": "知识库", "title": "CCRC", "where": "w", "text": "二级要近三年 3 个项目"}]
    monkeypatch.setattr(web_lookup, "gather_items", mats)


def _llm(monkeypatch, replies: list):
    seen: list = []

    async def fake(messages, **kw):
        seen.append(messages)
        r = replies.pop(0)
        return r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
    monkeypatch.setattr(gateway_llm, "call_llm", fake)
    return seen


def _p(tmp_path, **kw):
    return {"catfish_home": str(tmp_path), "auth_token": "t", "gateway_url": "http://gw", "model": "m", **kw}


async def _run(tmp_path, method, **kw):
    r = await learning.handle(method, _p(tmp_path, **kw))
    await asyncio.gather(*list(learning._TASKS))
    return r


def _get(tmp_path, sid):
    return asyncio.run(learning.handle("learn/get", _p(tmp_path, id=sid)))


def test_defense_flow_eval_followup_summary_cards(tmp_path, monkeypatch):
    seen = _llm(monkeypatch, [BANK])
    sid = asyncio.run(_run(tmp_path, "learn/create", goal="CCRC 应急处理二级现场答辩", mode="defense"))["session"]["id"]
    v = _get(tmp_path, sid)
    assert v["session"]["phase"] == "drill" and v["session"]["drill"] == {"idx": 0, "total": 4, "done": False}
    assert "points" not in json.dumps(v["session"]) and "分级管理办法" not in json.dumps(v)        # 评分点不给界面
    assert [e["kind"] for e in v["events"]][-2:] == ["say", "examiner_q"] and "评审老师" in seen[0][0]["content"]
    # 第 1 题: 讲到一点 → 点评 + 追问; 追问后合起来判, 不再追问
    seen = _llm(monkeypatch, [{"covered": [0], "missed": [1, 2], "wrong": [{"said": "看情况定", "fix": "按办法定"}],
                               "feedback": "漏了依据", "followup": "那报告里怎么体现?"},
                              {"covered": [0, 2], "missed": [1], "wrong": [], "feedback": "好多了", "followup": "再追问"}])
    asyncio.run(_run(tmp_path, "learn/drill_answer", id=sid, text="我们有管理办法, 看情况定级", secs=12.5))
    v = _get(tmp_path, sid)
    ev = [e for e in v["events"] if e["kind"] == "drill_eval"][-1]
    assert ev["covered"] == [0] and ev["missed"] == [1, 2] and ev["points"][0] == "有正式发布的分级管理办法"
    assert v["events"][-1]["kind"] == "examiner_q" and v["events"][-1]["followup"] is True
    assert "评分点 (编号从 0 开始):\n0. 有正式发布的分级管理办法" in seen[0][1]["content"]
    asyncio.run(_run(tmp_path, "learn/drill_answer", id=sid, text="报告里写依据第几条"))
    v = _get(tmp_path, sid)
    last = [e for e in v["events"] if e["kind"] == "drill_eval"][-1]
    assert last["followup"] is None and v["events"][-1]["kind"] == "drill_eval"                    # 只追问一次
    assert "追问后的回答" in seen[1][1]["content"] and "员工答: 我们有管理办法" in seen[1][1]["content"]
    with pytest.raises(ValueError, match="先等评审老师问"):
        asyncio.run(learning.handle("learn/drill_answer", _p(tmp_path, id=sid, text="x")))
    # 下一题跳过, 剩下的也跳过, 问完出总结
    for _ in range(3):
        asyncio.run(_run(tmp_path, "learn/drill_next", id=sid))
        asyncio.run(_run(tmp_path, "learn/drill_skip", id=sid))
    asyncio.run(_run(tmp_path, "learn/drill_next", id=sid))
    v = _get(tmp_path, sid)
    summ = v["events"][-1]
    assert summ["kind"] == "drill_summary" and summ["score"] == 2 and summ["total"] == 9 and v["session"]["drill"]["done"]
    assert summ["items"][0]["missed"] == ["按影响范围分级"] and summ["cards"] == 4
    con = practice.connect(tmp_path / "practice.db")
    a = con.execute("SELECT answer FROM questions WHERE stem LIKE '%分级标准%'").fetchone()[0]
    assert "【上次漏了】按影响范围分级" in a and "有正式发布的分级管理办法" in a
    con.close()
    with pytest.raises(ValueError, match="直接回答评审"):
        asyncio.run(learning.handle("learn/say", _p(tmp_path, id=sid, text="hi")))


def test_eval_validation():
    assert learning_drill.check_eval({"covered": [0], "missed": [0, 1], "feedback": "x"}, 2) == ["编号 [0] 同时在 covered 和 missed 里"]
    assert "每个都要放进" in learning_drill.check_eval({"covered": [0], "missed": [], "feedback": "x"}, 2)[0]
    assert learning_drill.check_eval({"covered": "[0]", "missed": "[1]", "feedback": "x", "wrong": []}, 2) == []
    assert learning_drill._check_bank({"questions": [{"question": "q", "points": ["a"]}] * 5}) == ["第 1, 2, 3, 4, 5 题要有 question 和 2–8 条 points"]


def test_drill_from_existing_session_and_back(tmp_path, monkeypatch):
    meta = learning_store.create(tmp_path, "准备 CCRC 答辩", "quick")
    d = learning_store.session_dir(tmp_path, meta["id"])
    meta.update(phase="done", plan={"nodes": [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}], "edges": [["a", "b"]]},
                materials={"items": [], "sources": []})
    learning_store.save(d, meta)
    _llm(monkeypatch, [BANK])
    asyncio.run(_run(tmp_path, "learn/drill", id=meta["id"]))
    assert learning_store.load(d)["phase"] == "drill" and learning_store.load(d)["drill_from"] == "done"
    v = asyncio.run(_run(tmp_path, "learn/teach", id=meta["id"]))
    assert v["session"]["phase"] == "done"
