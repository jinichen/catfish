"""11/02: 题库与错题复习 (借 DeepTutor Practice) —— 入库 / 判分 / 排期 / 导入 / 工具入口。"""
from __future__ import annotations

import json
import time

import pytest

from catfish_tool_bridge import practice as P
from catfish_tool_bridge import practice_tool

NOW = time.mktime((2026, 11, 2, 10, 0, 0, 0, 0, -1))
DAY = 86400.0


@pytest.fixture()
def con(tmp_path):
    c = P.connect(tmp_path / "practice.db")
    yield c
    c.close()


QS = [
    {"type": "single_choice", "question": "季度预算谁批?", "options": {"A": "王芳", "B": "李雷"}, "answer": "A",
     "explanation": "财务部王芳", "tags": ["制度"]},
    {"type": "multi_choice", "question": "哪些是质数", "options": ["2", "3", "4"], "answer": "A，B"},
    {"type": "判断", "question": "立项会每周三开", "answer": "对"},
    {"question": "报销要附什么?", "answer": "发票原件和审批单"},
]


def test_入库_规整题型和答案_重复的合并标签(con):
    r = P.add_questions(con, QS, now=NOW)
    assert (r["added"], r["errors"]) == (4, [])
    rows = {row["stem"]: row for row in con.execute("SELECT * FROM questions")}
    assert rows["哪些是质数"]["answer"] == "A,B"
    assert rows["立项会每周三开"]["qtype"] == "true_false" and rows["立项会每周三开"]["answer"] == "true"
    assert rows["报销要附什么?"]["qtype"] == "short_answer"
    r2 = P.add_questions(con, [{**QS[0], "tags": ["新员工"]}], now=NOW)
    assert (r2["added"], r2["merged"]) == (0, 1)
    assert set(json.loads(rows["季度预算谁批?"]["tags"]) + ["新员工"]) == set(
        json.loads(con.execute("SELECT tags FROM questions WHERE stem='季度预算谁批?'").fetchone()[0]))


def test_不合法的题说清楚_不影响别的题(con):
    r = P.add_questions(con, [{"question": "", "answer": "x"}, {"type": "single_choice", "question": "q",
                              "options": {"A": "1", "B": "2"}, "answer": "C"}, QS[0]], now=NOW)
    assert r["added"] == 1
    assert [e["row"] for e in r["errors"]] == [1, 2]
    assert "不在选项" in r["errors"][1]["error"]


def test_排期_跟DeepTutor同一套规则():
    s = P.schedule(None, "again", NOW)
    assert (s["interval"], s["reps"]) == (1.0, 0) and s["due"] == NOW + DAY
    s = P.schedule(s, "good", NOW)
    assert s["interval"] == 3.0                    # 忘过之后第一次记住: 3 天
    s2 = P.schedule(s, "good", NOW)
    assert s2["interval"] == pytest.approx(3.0 * s["ease"])
    h = P.schedule(s, "hard", NOW)
    assert h["interval"] == pytest.approx(3.6) and h["ease"] < s["ease"]
    e = P.schedule(s, "easy", NOW)
    assert e["interval"] > s2["interval"] and e["ease"] > s["ease"]
    big = P.schedule({**s, "interval": 300.0, "reps": 5}, "easy", NOW)
    assert big["interval"] == 365.0               # 最长一年
    lapse = P.schedule(s2, "again", NOW)
    assert lapse["lapses"] == 1 and lapse["interval"] == 1.0


def test_客观题判分_答错进错题_次日复习(con):
    P.add_questions(con, QS, now=NOW)
    qid = con.execute("SELECT id FROM questions WHERE qtype='single_choice'").fetchone()[0]
    r = P.answer(con, qid, "B", now=NOW)
    assert (r["correct"], r["rating"], r["answer"]) == (False, "again", "A")
    assert P.stats(con, now=NOW)["wrong"] == 1
    assert P.next_questions(con, mode="review", now=NOW) == []          # 明天才到期
    due = P.next_questions(con, mode="review", now=NOW + DAY)
    assert [q["id"] for q in due] == [qid] and "answer" not in due[0]  # 出题时不带答案
    r2 = P.answer(con, qid, "a", now=NOW + DAY)                         # 大小写不计
    assert r2["correct"] is True and r2["interval_days"] == 3.0
    assert P.stats(con, now=NOW + DAY)["due_today"] == 0


def test_多选和判断_答案写法宽松(con):
    P.add_questions(con, QS, now=NOW)
    mid = con.execute("SELECT id FROM questions WHERE qtype='multi_choice'").fetchone()[0]
    tid = con.execute("SELECT id FROM questions WHERE qtype='true_false'").fetchone()[0]
    assert P.answer(con, mid, "BA", now=NOW)["correct"] is True
    assert P.answer(con, tid, "错", now=NOW)["correct"] is False


def test_主观题先给参考答案_再按自评排期(con):
    P.add_questions(con, QS, now=NOW)
    sid = con.execute("SELECT id FROM questions WHERE qtype='short_answer'").fetchone()[0]
    r = P.answer(con, sid, "发票", now=NOW)
    assert r["needs_rating"] and r["answer"] == "发票原件和审批单"
    r2 = P.answer(con, sid, "发票", rating="again", now=NOW)
    assert r2["rating"] == "again" and P.stats(con, now=NOW)["wrong"] == 1


def test_新题按入库顺序_做过的不再出(con):
    P.add_questions(con, QS, now=NOW)
    first = P.next_questions(con, n=2, mode="new", now=NOW)
    assert [q["question"] for q in first] == ["季度预算谁批?", "哪些是质数"]
    P.answer(con, first[0]["id"], "A", now=NOW)
    assert P.next_questions(con, n=1, mode="new", now=NOW)[0]["question"] == "哪些是质数"


def test_已掌握不再排_再答错重新开始(con):
    P.add_questions(con, QS[:1], now=NOW)
    qid = con.execute("SELECT id FROM questions").fetchone()[0]
    P.answer(con, qid, "B", now=NOW)
    P.set_mastered(con, qid, True)
    assert P.next_questions(con, now=NOW + 2 * DAY) == []
    P.answer(con, qid, "B", now=NOW + 2 * DAY)
    assert P.stats(con, now=NOW + 3 * DAY)["due_today"] == 1


def test_导入Excel和CSV_中文列名(tmp_path, con):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["题干", "题型", "A", "B", "答案", "解析", "标签"])
    ws.append(["预算谁批", "单选", "王芳", "李雷", "A", "财务部", "制度;财务"])
    ws.append(["", "", "", "", "", "", ""])
    x = tmp_path / "题库.xlsx"
    wb.save(x)
    items = P.read_table(str(x))
    assert len(items) == 1 and items[0]["options"] == {"A": "王芳", "B": "李雷"}
    assert P.add_questions(con, items)["added"] == 1
    c = tmp_path / "q.csv"
    c.write_bytes("题目,题型,答案\n报销要附什么,简答,发票\n".encode("gb18030"))
    assert P.read_table(str(c))[0]["question"] == "报销要附什么"
    with pytest.raises(ValueError):
        P.read_table(str(tmp_path / "x.xls"))


def test_工具入口(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    r = practice_tool.dispatch("catfish_practice", {"action": "add", "questions": QS})
    assert r["added"] == 4
    nxt = practice_tool.dispatch("catfish_practice", {"action": "next", "mode": "new", "n": 1})
    assert nxt["stats"]["new"] == 4 and len(nxt["questions"]) == 1
    assert "error" in practice_tool.dispatch("catfish_practice", {"action": "answer", "id": 999})
    assert "error" in practice_tool.dispatch("catfish_practice", {"action": "bogus"})
    assert (tmp_path / "practice.db").exists()
