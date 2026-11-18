"""学习第 2 步 (11/18): 练习 (出题 / 交答案 / 提示一级一级 / 不做了 / 换着练) + 复习卡 (学完自动做 / 纪要做卡) + 复习。
大模型用假的。"""
from __future__ import annotations

import asyncio
import json

import pytest

from catfish_tool_bridge import gateway_llm, learning, learning_practice, learning_store, practice, web_lookup

PLAN = {"nodes": [{"id": "levels", "title": "资质分几级"}, {"id": "level-2", "title": "二级要求"}], "edges": [["levels", "level-2"]]}
PROB = {"statement": "某公司近三年做了 2 个应急项目, 能申请二级吗?", "targets": ["level-2"], "mode": "recall",
        "solution": "二级要近三年 3 个项目 → 2 个不够 → 不能"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    learning._RUNNING.clear()

    async def mats(*a, **k):
        return []
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


def _session(tmp_path, phase="teach"):
    meta = learning_store.create(tmp_path, "准备 CCRC 答辩", "quick")
    d = learning_store.session_dir(tmp_path, meta["id"])
    meta.update(phase=phase, plan=PLAN, materials={"items": [], "sources": []})
    learning_store.save(d, meta)
    learning_store.append(d, {"role": "tutor", "kind": "say", "text": "二级要求近三年 3 个应急处理项目。"})
    return meta["id"], d


def test_practice_flow_attempt_hint_conclude(tmp_path, monkeypatch):
    sid, d = _session(tmp_path)
    seen = _llm(monkeypatch, [{"problem": PROB}])
    v = asyncio.run(_run(tmp_path, "learn/practice", id=sid))
    v = asyncio.run(learning.handle("learn/get", _p(tmp_path, id=sid)))
    prob = next(e for e in v["events"] if e["kind"] == "problem")
    assert "solution" not in prob["p"] and v["session"]["phase"] == "practice"            # 做完前不给看解法
    assert "【练习】" in seen[0][0]["content"] and "不点名" in seen[0][0]["content"]
    assert "【现在】没有进行中的题" in seen[0][1]["content"]
    _llm(monkeypatch, [{"hint": {"rung": "R1", "text": "想想二级对项目数量有没有要求"}}])
    asyncio.run(_run(tmp_path, "learn/hint", id=sid))
    seen = _llm(monkeypatch, [{"hint": {"rung": "R1", "text": "再说一遍"}}, {"hint": {"rung": "R2", "text": "看二级要求那条"}}])
    asyncio.run(_run(tmp_path, "learn/hint", id=sid))
    assert "比上一级高一级" in seen[1][-1]["content"]                                        # 提示只能往上升
    _llm(monkeypatch, [{"say": "数量那步断了", "keep": True}])
    asyncio.run(_run(tmp_path, "learn/attempt", id=sid, text="能, 有项目就行"))
    assert learning_practice.active(learning_store.load(d))                                 # 没做对: 这题还在
    _llm(monkeypatch, [{"conclude": {"outcome": "solvedWithHints", "verdict": "数量判断对了", "rung": "R2", "miss": None, "retest": False}}])
    asyncio.run(_run(tmp_path, "learn/attempt", id=sid, text="不能, 要 3 个", thinking="看了提示"))
    v = asyncio.run(learning.handle("learn/get", _p(tmp_path, id=sid)))
    assert learning_practice.active(learning_store.load(d)) is None
    prob = next(e for e in v["events"] if e["kind"] == "problem")
    assert prob["p"]["solution"].startswith("二级要")                                       # 结束了才给看
    assert v["session"]["practice"] == {"total": 1, "solved": 0, "solvedWithHints": 1, "movedOn": 0, "misses": {}}
    with pytest.raises(ValueError, match="没有在做"):
        asyncio.run(learning.handle("learn/attempt", _p(tmp_path, id=sid, text="x")))


def test_conclude_and_problem_validation():
    assert learning_practice.check_conclude({"outcome": "movedOn", "verdict": "x", "rung": None, "miss": None}) \
        == ["没做对要写 miss (没做对的原因)"]
    assert "做对了 miss 写 null" in learning_practice.check_conclude({"outcome": "solved", "verdict": "x", "miss": "cue"})
    _, errs = learning_practice.check_problem({**PROB, "targets": ["nope"], "solution": ""}, PLAN, [])
    assert any("targets" in e for e in errs) and any("solution" in e for e in errs)
    evs = [{"kind": "problem", "p": {**PROB, "statement": f"题{i}"}} for i in range(2)]
    _, errs = learning_practice.check_problem({**PROB, "statement": "题3"}, PLAN, evs)
    assert errs and all(e.startswith(learning.SOFT) for e in errs) and "换着练" in errs[0]   # 软规则: 最后一次可以放行


def test_give_up_reveals_and_teach_returns(tmp_path, monkeypatch):
    sid, d = _session(tmp_path)
    _llm(monkeypatch, [{"problem": PROB}])
    asyncio.run(_run(tmp_path, "learn/practice", id=sid))
    v = asyncio.run(_run(tmp_path, "learn/give_up", id=sid))
    c = next(e for e in v["events"] if e["kind"] == "conclude")
    assert c["outcome"] == "movedOn" and c["retest"] is True
    assert next(e for e in v["events"] if e["kind"] == "problem")["p"]["solution"]
    _llm(monkeypatch, [{"say": "接着讲二级。" * 30, "question": {"type": "choice", "role": "check", "node": "level-2", "question": "二级几个项目?",
                       "options": [{"value": "a", "label": "1"}, {"value": "b", "label": "3"}], "explanation": "规则", "answer": ["b"]}}])
    asyncio.run(_run(tmp_path, "learn/teach", id=sid))
    assert learning_store.load(d)["phase"] == "teach" and learning_store.load(d)["pending"]


def test_cards_from_session_enter_review(tmp_path, monkeypatch):
    sid, d = _session(tmp_path, phase="done")
    seen = _llm(monkeypatch, [{"cards": [{"point": "二级要三个项目", "question": "应急处理二级要几个项目?", "answer": "近三年 3 个"},
                                         {"point": "二级要三个项目", "question": "评审问「项目够吗」怎么答?", "answer": "近三年 3 个且有过程记录"}]}])
    asyncio.run(_run(tmp_path, "learn/cards", id=sid))
    assert "二级要求近三年 3 个应急处理项目" in seen[0][1]["content"] and "情境" in seen[0][0]["content"]
    v = asyncio.run(learning.handle("learn/get", _p(tmp_path, id=sid)))
    assert v["session"]["cards"] == 2 and any(e["kind"] == "cards" and e["added"] == 2 for e in v["events"])
    con = practice.connect(tmp_path / "practice.db")
    rows = con.execute("SELECT origin, point, tags FROM questions").fetchall()
    assert {r["origin"] for r in rows} == {f"learn:{sid}"} and json.loads(rows[0]["tags"]) == ["准备 CCRC 答辩"]
    con.close()
    st = asyncio.run(learning.handle("learn/review_stats", _p(tmp_path)))
    assert st["total"] == 2 and st["wrong"] == 2


def test_finish_makes_cards_automatically(tmp_path, monkeypatch):
    sid, d = _session(tmp_path)
    _llm(monkeypatch, [{"say": "总结一下。", "finish": {"summary": "学会了二级要求"}},
                       {"cards": [{"point": "p", "question": "二级几个项目?", "answer": "3 个"}]}])
    asyncio.run(_run(tmp_path, "learn/say", id=sid, text="差不多了"))
    v = asyncio.run(learning.handle("learn/get", _p(tmp_path, id=sid)))
    assert v["session"]["phase"] == "done" and v["session"]["cards"] == 1


def test_finish_survives_card_failure(tmp_path, monkeypatch):
    sid, d = _session(tmp_path)
    _llm(monkeypatch, [{"say": "总结一下。", "finish": {"summary": "学会了"}}, "不是 JSON", "还不是", "也不是"])
    asyncio.run(_run(tmp_path, "learn/say", id=sid, text="差不多了"))
    v = asyncio.run(learning.handle("learn/get", _p(tmp_path, id=sid)))
    assert v["session"]["phase"] == "done" and v["session"]["last_error"] is None


def test_review_flow(tmp_path, monkeypatch):
    con = practice.connect(tmp_path / "practice.db")
    import time
    practice.add_cards(con, [{"point": "p", "question": "二级几个项目?", "answer": "3 个"}], "learn:x", [], now=time.time() - 2 * 86400)
    con.close()
    r = asyncio.run(learning.handle("learn/review_next", _p(tmp_path)))
    q = r["questions"][0]
    assert "answer" not in q and r["previews"][str(q["id"])]["good"] == "4 天" and r["stats"]["due_today"] == 1
    look = asyncio.run(learning.handle("learn/review_answer", _p(tmp_path, qid=q["id"], user_answer="三个")))
    assert look["needs_rating"] and look["answer"] == "3 个" and look["previews"]["easy"] == "14 天"
    done = asyncio.run(learning.handle("learn/review_answer", _p(tmp_path, qid=q["id"], user_answer="三个", rating="good")))
    assert done["interval_days"] == 4.0
    assert asyncio.run(learning.handle("learn/review_next", _p(tmp_path)))["questions"] == []


def test_meeting_cards(tmp_path, monkeypatch):
    from catfish_tool_bridge import learning_cards
    mid = "mtg_20261116-143600_cc01"
    md = tmp_path / "meetings" / mid
    md.mkdir(parents=True)
    (md / "meta.json").write_text(json.dumps({"id": mid, "title": "CCRC整改会议"}, ensure_ascii=False), encoding="utf-8")
    (md / "minutes.json").write_text(json.dumps({"summary": "讨论整改", "decisions": ["一个月内提交整改方案"],
                                                 "action_items": [{"owner": "金智达", "task": "交材料", "due": "2026-12-01"}]},
                                                ensure_ascii=False), encoding="utf-8")
    seen = _llm(monkeypatch, [{"cards": [{"point": "整改期限", "question": "CCRC 整改方案多久内交?", "answer": "一个月内"}]}])
    r = asyncio.run(learning_cards.meeting_cards({"meeting_id": mid, **_p(tmp_path)}))
    assert r["added"] == 1 and "决议: 一个月内提交整改方案" in seen[0][1]["content"] and "金智达" in seen[0][1]["content"]
    con = practice.connect(tmp_path / "practice.db")
    assert con.execute("SELECT origin FROM questions").fetchone()[0] == f"meeting:{mid}"
    con.close()
    (md / "minutes.json").unlink()
    with pytest.raises(ValueError, match="还没有纪要"):
        asyncio.run(learning_cards.meeting_cards({"meeting_id": mid, **_p(tmp_path)}))


def test_attempt_new_problem_is_sent_back(tmp_path, monkeypatch):
    """11/18 实测: 员工答「不清楚」, 模型直接换一道简单的题 → 退回说清两条路; 第二次给 conclude 收下。"""
    sid, d = _session(tmp_path)
    _llm(monkeypatch, [{"problem": PROB}])
    asyncio.run(_run(tmp_path, "learn/practice", id=sid))
    seen = _llm(monkeypatch, [{"problem": {**PROB, "statement": "简单点: 二级几个项目?"}},
                              {"conclude": {"outcome": "movedOn", "verdict": "没想起来", "rung": None, "miss": "cue", "retest": True}}])
    asyncio.run(_run(tmp_path, "learn/attempt", id=sid, text="不清楚"))
    assert "不要在这一步出新题" in seen[1][-1]["content"] and "movedOn" in seen[1][-1]["content"]
    assert "【现在】正在做的题" in seen[0][1]["content"] and "员工刚交了答案" in seen[0][1]["content"]
    v = asyncio.run(learning.handle("learn/get", _p(tmp_path, id=sid)))
    assert v["session"]["practice"]["misses"] == {"想不起该用哪条": 1}
    assert sum(1 for e in v["events"] if e["kind"] == "problem") == 1


def test_soft_only_problem_is_posted_complete(tmp_path, monkeypatch):
    """11/18 实测: 最后一次只剩「重复 / 没换着练」时题被收下, 但 check_problem 返回了 None, 存进去一道残题。"""
    q, errs = learning_practice.check_problem(PROB, PLAN, [{"kind": "problem", "p": PROB}])
    assert errs and q == {**PROB}
    sid, d = _session(tmp_path)
    learning_store.append(d, {"role": "tutor", "kind": "problem", "pid": "p0", "p": PROB})
    learning_store.append(d, {"role": "tutor", "kind": "conclude", "pid": "p0", "outcome": "movedOn", "verdict": "x", "rung": None,
                              "miss": "cue", "retest": True})
    _llm(monkeypatch, [{"problem": PROB}] * learning.MAX_ATTEMPTS)
    asyncio.run(_run(tmp_path, "learn/practice", id=sid))
    last = [e for e in learning_store.events(d) if e["kind"] == "problem"][-1]
    assert last["p"]["mode"] == "recall" and last["p"]["solution"]


def test_broken_problem_row_is_skipped(tmp_path, monkeypatch):
    sid, d = _session(tmp_path)
    learning_store.append(d, {"role": "tutor", "kind": "problem", "pid": "bad", "p": None})
    learning_store.append(d, {"role": "tutor", "kind": "problem", "pid": "bad2", "p": {"statement": "x"}})
    v = asyncio.run(learning.handle("learn/get", _p(tmp_path, id=sid)))
    assert not any(e["kind"] == "problem" for e in v["events"])
    _llm(monkeypatch, [{"problem": PROB}])
    asyncio.run(_run(tmp_path, "learn/practice", id=sid))
    assert learning_store.load(d)["last_error"] is None and learning_practice.active(learning_store.load(d))


def test_cards_again_sees_existing_and_may_add_none(tmp_path, monkeypatch):
    sid, d = _session(tmp_path, phase="done")
    _llm(monkeypatch, [{"cards": [{"point": "p", "question": "应急处理二级要几个项目?", "answer": "3 个"}]}])
    asyncio.run(_run(tmp_path, "learn/cards", id=sid))
    seen = _llm(monkeypatch, [{"cards": []}])
    asyncio.run(_run(tmp_path, "learn/cards", id=sid))
    assert "已经做过的卡" in seen[0][1]["content"] and "应急处理二级要几个项目?" in seen[0][1]["content"]
    v = asyncio.run(learning.handle("learn/get", _p(tmp_path, id=sid)))
    assert v["session"]["cards"] == 1 and v["session"]["last_error"] is None
