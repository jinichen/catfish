"""小鲶学习 (11/18, learning.py): 摸底 → 路线图 → 讲解; 先存后算 / 标准答案不外露 / 不合格重交 / 简答判分 / 出错重试。
大模型和查资料都用假的。"""
from __future__ import annotations

import asyncio
import json

import pytest

from catfish_tool_bridge import gateway_llm, learning, learning_store, web_lookup

CHOICE = {"type": "choice", "question": "CCRC 应急处理二级要几个项目?", "options": [{"value": "a", "label": "1 个"},
          {"value": "b", "label": "3 个"}, {"value": "c", "label": "5 个"}], "explanation": "规则 7.2", "answer": ["b"]}
TEACHING = ("二级资质要求近三年内完成至少三个应急处理项目, 每个项目都要有完整的过程记录: 发现、抑制、取证分析、根除、恢复、总结; "
            "审核组看的是这些记录能不能证明你们真的按流程做过, 而不是只写了结论。") * 2
PLAN = {"approach": "先讲分级, 再讲二级要求", "plan": {"nodes": [{"id": "levels", "title": "资质分几级"},
        {"id": "level-2", "title": "二级要求"}], "edges": [["levels", "level-2"]]}}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    learning._RUNNING.clear()

    async def mats(q, ctx, title, call_llm, kw):
        return [{"kind": "知识库", "title": "CCRC 应急处理二级", "where": "wiki/c.md", "text": "二级要求 3 个项目"}]
    monkeypatch.setattr(web_lookup, "gather_items", mats)
    from catfish_tool_bridge import local_lookup
    monkeypatch.setattr(local_lookup, "_wiki", lambda qs, question="": [
        {"kind": "知识库", "title": "二级要求细则", "where": "wiki/r.md", "text": "近三年 3 个项目"}])
    monkeypatch.setattr(local_lookup, "_files", lambda qs: [])


def _llm(monkeypatch, replies: list):
    seen: list[list[dict]] = []

    async def fake(messages, **kw):
        seen.append(messages)
        assert kw["source"] == learning.SOURCE and kw["auth_token"] == "t"
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
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


def test_full_flow_probe_plan_teach(tmp_path, monkeypatch):
    seen = _llm(monkeypatch, [
        {"say": "先看看你对分级熟不熟", "question": CHOICE},
        {"say": "好, 摸清了", "probe_done": {"goal": "答辩讲清二级要求", "strands": [{"strand": "分级", "solid": "知道有三级", "gap": "二级要求"}],
                                          "summary": "从二级要求讲起"}},
        PLAN,
        {"say": "**二级**要 3 个项目 [1]。" + TEACHING, "sources": [1], "question": {**CHOICE, "question": "这个项目要求近几年的?", "role": "check", "node": "level-2"}},
    ])

    async def flow():
        v = await _run(tmp_path, "learn/create", goal="准备 CCRC 答辩", mode="quick")
        sid = v["session"]["id"]
        v = await learning.handle("learn/get", _p(tmp_path, id=sid))
        q = next(e for e in v["events"] if e["kind"] == "question")
        assert "answer" not in q["q"] and "explanation" not in q["q"]          # 没答的题不给界面看答案
        assert v["session"]["pending"]["qid"] == q["qid"] and v["running"] is False
        stored = next(e for e in learning_store.events(learning_store.session_dir(tmp_path, sid)) if e["kind"] == "question")
        right = stored["q"]["answer"]
        assert [o["label"] for o in stored["q"]["options"] if o["value"] in right] == ["3 个"]   # 打乱了, 答案跟着换编号
        await _run(tmp_path, "learn/answer", id=sid, qid=q["qid"], picked=right, confidence=60, thinking="好像是三个")
        v = await learning.handle("learn/get", _p(tmp_path, id=sid))
        assert v["session"]["phase"] == "plan_review" and v["session"]["plan"]["edges"] == [["levels", "level-2"]]
        ans = next(e for e in v["events"] if e["kind"] == "answer")
        assert ans["verdict"] == "correct" and ans["confidence"] == 60
        answered = next(e for e in v["events"] if e["kind"] == "question")
        assert answered["q"]["answer"] == right                                # 答完了才给看
        await _run(tmp_path, "learn/say", id=sid, start=True)
        return await learning.handle("learn/get", _p(tmp_path, id=sid))
    v = asyncio.run(flow())
    s = v["session"]
    assert s["phase"] == "teach" and s["current"] == "level-2" and s["visited"] == ["level-2"]
    say = [e for e in v["events"] if e["kind"] == "say" and e["role"] == "tutor"][-1]
    assert say["sources"] == [{"kind": "知识库", "title": "二级要求细则", "where": "wiki/r.md"}]   # 节点资料排在前面, [1] 是它
    assert "二级要求 3 个项目" in seen[0][1]["content"]                      # 资料进了提示词
    assert "(对) / 把握 60% / 想法: 好像是三个" in seen[1][1]["content"]
    assert "近三年 3 个项目" in seen[3][1]["content"]                          # 当前节点的资料 (直接搜本机)
    assert "【讲解】" in seen[3][0]["content"] and "快速" in seen[3][0]["content"]


def test_invalid_output_is_sent_back_with_reasons(tmp_path, monkeypatch):
    seen = _llm(monkeypatch, [
        "随便说说, 不是 JSON",
        {"question": {**CHOICE, "answer": ["z"]}},
        {"question": {**CHOICE, "options": json.dumps(CHOICE["options"])}},      # 选项写成字符串: 修好再收
    ])
    v = asyncio.run(_run(tmp_path, "learn/create", goal="学 CCRC", mode="quick"))
    sid = v["session"]["id"]
    assert len(seen) == 3
    assert "解析不了" in seen[1][-1]["content"] and "answer 要是选项 value 的列表" in seen[2][-1]["content"]
    assert _get(tmp_path, sid)["session"]["pending"]


def test_teach_question_must_tag_existing_node(tmp_path, monkeypatch):
    q = learning.check_question({**CHOICE, "role": "check", "node": "nope"}, "teach", PLAN["plan"])
    assert q[0] is None and any("节点 id" in e for e in q[1])
    q = learning.check_question({**CHOICE, "node": "levels"}, "teach", PLAN["plan"])
    assert any("predict" in e for e in q[1])
    ok, errs = learning.check_question({**CHOICE, "role": "predict", "node": "levels"}, "teach", PLAN["plan"])
    assert errs == [] and ok["role"] == "predict"


def test_plan_validation():
    assert learning.check_plan(PLAN["plan"])[1] == []
    _, errs = learning.check_plan({"nodes": [{"id": "A B", "title": "x"}, {"id": "b", "title": "y"}], "edges": []})
    assert any("不合格" in e for e in errs)
    _, errs = learning.check_plan({"nodes": [{"id": "a", "title": "x"}, {"id": "b", "title": "y"}], "edges": [["a", "b"], ["b", "a"]]})
    assert any("环" in e for e in errs)
    plan, _ = learning.check_plan(json.dumps({"nodes": [{"id": "a", "title": "x"}, {"id": "b", "title": "y"}],
                                              "edges": [{"from": "a", "to": "b"}]}))
    assert plan["edges"] == [["a", "b"]] and learning.topo_order(plan) == ["a", "b"]


def test_recall_answer_needs_grade_next_step(tmp_path, monkeypatch):
    recall = {"type": "recall", "question": "说说二级要求", "answer": "3 个项目, 近三年"}
    _llm(monkeypatch, [{"question": recall}])
    sid = asyncio.run(_run(tmp_path, "learn/create", goal="学 CCRC", mode="deep"))["session"]["id"]
    d = learning_store.session_dir(tmp_path, sid)
    meta = learning_store.load(d)
    meta.update(phase="teach", plan=PLAN["plan"])
    learning_store.save(d, meta)
    qid = meta["pending"]["qid"]
    seen = _llm(monkeypatch, [
        {"say": TEACHING, "question": {**CHOICE, "question": "近几年?", "role": "check", "node": "levels"}},           # 漏了 grade → 退回
        {"grade": {"verdict": "partial", "feedback": "漏了近三年"}, "say": TEACHING, "question": {**CHOICE, "question": "近几年?", "role": "check", "node": "levels"}},
    ])
    asyncio.run(_run(tmp_path, "learn/answer", id=sid, qid=qid, text="三个项目"))
    assert "grade" in seen[1][-1]["content"] and "深入" in seen[0][0]["content"]
    g = next(e for e in _get(tmp_path, sid)["events"] if e["kind"] == "grade")
    assert g["verdict"] == "partial" and g["qid"] == qid


def test_failure_keeps_input_and_retry(tmp_path, monkeypatch):
    _llm(monkeypatch, [RuntimeError("网关 502")])
    sid = asyncio.run(_run(tmp_path, "learn/create", goal="学 CCRC", mode="quick"))["session"]["id"]
    v = _get(tmp_path, sid)
    assert "网关 502" in v["session"]["last_error"] and v["events"][0]["text"] == "学 CCRC" and not v["session"]["stalled"]
    _llm(monkeypatch, [{"question": CHOICE}])
    asyncio.run(_run(tmp_path, "learn/retry", id=sid))
    v = _get(tmp_path, sid)
    assert v["session"]["last_error"] is None and v["session"]["pending"]


def test_busy_and_answer_checks(tmp_path, monkeypatch):
    _llm(monkeypatch, [{"question": CHOICE}])
    sid = asyncio.run(_run(tmp_path, "learn/create", goal="学 CCRC", mode="quick"))["session"]["id"]
    qid = learning_store.load(learning_store.session_dir(tmp_path, sid))["pending"]["qid"]
    learning._RUNNING.add(sid)
    with pytest.raises(ValueError, match="还在想"):
        asyncio.run(learning.handle("learn/answer", _p(tmp_path, id=sid, qid=qid, picked=["a"])))
    learning._RUNNING.clear()
    for bad, msg in (({"qid": "q_x", "picked": ["a"]}, "已经答过"), ({"qid": qid, "picked": ["z"]}, "选项不对"),
                     ({"qid": qid, "picked": ["a", "b"]}, "只能选一个"), ({"qid": qid}, "选一个")):
        with pytest.raises(ValueError, match=msg):
            asyncio.run(learning.handle("learn/answer", _p(tmp_path, id=sid, **bad)))


def test_say_while_question_open_skips_it_and_stalled_flag(tmp_path, monkeypatch):
    _llm(monkeypatch, [{"question": CHOICE}])
    sid = asyncio.run(_run(tmp_path, "learn/create", goal="学 CCRC", mode="quick"))["session"]["id"]
    d = learning_store.session_dir(tmp_path, sid)
    learning_store.append(d, {"role": "learner", "kind": "answer", "qid": "q_x", "skipped": True})
    learning_store.append(d, {"role": "learner", "kind": "say", "text": "等下, 先问个问题"})
    meta = learning_store.load(d)
    meta["pending"] = None
    learning_store.save(d, meta)
    assert _get(tmp_path, sid)["session"]["stalled"] is True          # 输入存了但没在跑 → 界面给重试
    _llm(monkeypatch, [{"question": CHOICE}])
    sid2 = asyncio.run(_run(tmp_path, "learn/create", goal="学 ITSS", mode="quick"))["session"]["id"]
    qid = learning_store.load(learning_store.session_dir(tmp_path, sid2))["pending"]["qid"]
    _llm(monkeypatch, [{"say": "先回答你", "question": CHOICE}])
    asyncio.run(_run(tmp_path, "learn/say", id=sid2, text="这个规则是哪年的?"))
    evs = _get(tmp_path, sid2)["events"]
    assert any(e.get("skipped") and e["qid"] == qid for e in evs)
    lst = asyncio.run(learning.handle("learn/list", _p(tmp_path)))["sessions"]
    assert {s["id"] for s in lst} == {sid, sid2}
    asyncio.run(learning.handle("learn/delete", _p(tmp_path, id=sid)))
    assert [s["id"] for s in asyncio.run(learning.handle("learn/list", _p(tmp_path)))["sessions"]] == [sid2]


def test_session_id_is_checked(tmp_path):
    for bad in ("../x", "lrn_../../etc", "abc"):
        with pytest.raises(ValueError):
            learning_store.session_dir(tmp_path, bad)


def test_shuffle_options_is_stable_and_remaps_answer():
    q = {**CHOICE, "answer": ["b"]}
    a = learning.shuffle_options(q, "q_1")
    assert [o["value"] for o in a["options"]] == ["a", "b", "c"]
    assert [o["label"] for o in a["options"] if o["value"] in a["answer"]] == ["3 个"]
    assert learning.shuffle_options(q, "q_1") == a
    firsts = {learning.shuffle_options(q, f"q_{i}")["answer"][0] for i in range(30)}
    assert len(firsts) == 3                                   # 对的那个不总在同一个位置
    assert learning.shuffle_options({"type": "recall", "question": "x", "answer": "y"}, "q") == {"type": "recall", "question": "x", "answer": "y"}


def test_probe_no_repeat_and_cap(tmp_path, monkeypatch):
    """11/18 实测: 员工一直答错时同一道题问了五遍、摸底停不下来 → 重复的题退回; 问满 PROBE_MAX 题必须结束。"""
    _llm(monkeypatch, [{"question": CHOICE}])
    sid = asyncio.run(_run(tmp_path, "learn/create", goal="学 CCRC", mode="quick"))["session"]["id"]
    qid = learning_store.load(learning_store.session_dir(tmp_path, sid))["pending"]["qid"]
    seen = _llm(monkeypatch, [{"question": {**CHOICE, "question": "CCRC 应急处理二级要几个项目？"}},   # 只差标点 → 算重复
                              {"question": {**CHOICE, "question": "换个角度: 项目要近几年的?"}}])
    asyncio.run(_run(tmp_path, "learn/answer", id=sid, qid=qid, picked=["a"]))
    assert "前面问过了" in seen[1][-1]["content"]
    d = learning_store.session_dir(tmp_path, sid)
    for i in range(learning.PROBE_MAX):
        learning_store.append(d, {"role": "tutor", "kind": "question", "qid": f"q_{i}", "q": {"type": "choice", "role": "probe", "question": f"题{i}"}})
    meta = learning_store.load(d)
    meta["pending"] = None
    learning_store.save(d, meta)
    seen = _llm(monkeypatch, [{"question": {**CHOICE, "question": "再来一题"}},
                              {"probe_done": {"strands": [{"strand": "分级", "solid": "还没有", "gap": "全部"}]}}, PLAN])
    asyncio.run(_run(tmp_path, "learn/retry", id=sid))
    assert f"摸底已经问了 {learning.PROBE_MAX + 2} 题" in seen[0][1]["content"] and "必须输出 probe_done" in seen[1][-1]["content"]
    assert _get(tmp_path, sid)["session"]["phase"] == "plan_review"


def test_check_needs_teaching_first(tmp_path, monkeypatch):
    """11/18 实测: 快速模式只出检查题不讲 → 退回; 讲够了才收。"""
    recall = {"type": "choice", "question": "开始前", "options": CHOICE["options"], "explanation": "x", "answer": ["a"]}
    _llm(monkeypatch, [{"question": recall}])
    sid = asyncio.run(_run(tmp_path, "learn/create", goal="学 CCRC", mode="quick"))["session"]["id"]
    d = learning_store.session_dir(tmp_path, sid)
    meta = learning_store.load(d)
    meta.update(phase="teach", plan=PLAN["plan"])
    learning_store.save(d, meta)
    teach = TEACHING
    seen = _llm(monkeypatch, [
        {"say": "好, 开始第一步。", "question": {**CHOICE, "question": "根据刚才讲的, 二级要几个?", "role": "check", "node": "levels"}},
        {"say": teach, "question": {**CHOICE, "question": "根据刚才讲的, 二级要几个?", "role": "check", "node": "levels"}},
    ])
    asyncio.run(_run(tmp_path, "learn/answer", id=sid, qid=meta["pending"]["qid"], picked=["a"]))
    assert "不能只出题" in seen[1][-1]["content"]
    assert _get(tmp_path, sid)["session"]["pending"]


def test_heal_option_shapes():
    q, errs = learning.check_question({"type": "choice", "question": "几个?", "options": ["1 个", "3 个"], "answer": "3 个",
                                       "explanation": "规则"}, "probe", None)
    assert errs == [] and q["options"] == [{"value": "a", "label": "1 个"}, {"value": "b", "label": "3 个"}] and q["answer"] == ["b"]
    q, errs = learning.check_question({"type": "choice", "question": "几个?", "options": [{"id": "x", "text": "1 个"}, {"id": "y", "text": "3 个"}],
                                       "answer": 1, "explanation": "规则"}, "probe", None)
    assert errs == [] and q["answer"] == ["y"]


def test_duplicate_is_soft_on_last_attempt(tmp_path, monkeypatch):
    _llm(monkeypatch, [{"question": CHOICE}])
    sid = asyncio.run(_run(tmp_path, "learn/create", goal="学 CCRC", mode="quick"))["session"]["id"]
    qid = learning_store.load(learning_store.session_dir(tmp_path, sid))["pending"]["qid"]
    seen = _llm(monkeypatch, [{"question": CHOICE}] * learning.MAX_ATTEMPTS)
    asyncio.run(_run(tmp_path, "learn/answer", id=sid, qid=qid, picked=["a"]))
    v = _get(tmp_path, sid)
    assert len(seen) == learning.MAX_ATTEMPTS and v["session"]["last_error"] is None and v["session"]["pending"]
    assert "~" not in seen[1][-1]["content"]
