"""11/18 FSRS 复习排期 (fsrs.py) + 题库迁移 / 学习卡 (practice.py)。"""
from __future__ import annotations

import time

import pytest

from catfish_tool_bridge import fsrs
from catfish_tool_bridge import practice as P

NOW = time.mktime((2026, 11, 18, 10, 0, 0, 0, 0, -1))
DAY = 86400.0


def test_new_card_intervals_follow_default_params():
    days = {r: fsrs.review(None, r, NOW)["interval"] for r in ("hard", "good", "easy")}
    assert days == {"hard": 1.0, "good": 4.0, "easy": 14.0}
    again = fsrs.review(None, "again", NOW)
    assert again["due"] == NOW + 600 and again["state"] == "relearning"


def test_recall_grows_and_forget_shrinks_stability():
    c = fsrs.review(None, "good", NOW)
    later = c["due"]
    good = fsrs.review(c, "good", later)
    assert good["stability"] > c["stability"] * 2 and good["interval"] > c["interval"]
    hard = fsrs.review(c, "hard", later)
    easy = fsrs.review(c, "easy", later)
    assert hard["stability"] < good["stability"] < easy["stability"]
    assert easy["difficulty"] < good["difficulty"] < hard["difficulty"]
    lapse = fsrs.review(good, "again", good["due"])
    assert lapse["stability"] < good["stability"] and lapse["lapses"] == 1
    # 等更久才复习、还想起来了: 涨得更多 (可提取率低时成功的回报大)
    late = fsrs.review(c, "good", later + 20 * DAY)
    assert late["stability"] > good["stability"]


def test_preview_order_and_text():
    c = fsrs.review(None, "good", NOW)
    p = fsrs.preview(c, c["due"])
    assert p["again"] == "10 分钟"
    d = {k: fsrs.ordered_interval(c, k, c["due"]) for k in ("hard", "good", "easy")}
    assert d["hard"] <= d["good"] < d["easy"]
    assert fsrs.human_days(45) == "2 个月" and fsrs.human_days(400) == "1 年"
    with pytest.raises(ValueError):
        fsrs.review(None, "meh", NOW)


def test_old_sm2_rows_replayed_into_fsrs(tmp_path):
    import sqlite3
    db = tmp_path / "practice.db"
    con = P.connect(db)
    P.add_questions(con, [{"question": "季度预算谁批?", "options": {"A": "王芳", "B": "李雷"}, "answer": "A"}], now=NOW)
    qid = con.execute("SELECT id FROM questions").fetchone()[0]
    con.close()
    # 造一份 11/18 之前的库: review_state 只有 SM-2 列 (state 为空), 有两次作答记录
    raw = sqlite3.connect(db)
    raw.execute("INSERT INTO review_state (question_id, due, interval, ease, reps, lapses, first_wrong, last_review)"
                " VALUES (?, ?, 3, 2.3, 1, 0, ?, ?)", (qid, NOW + 4 * DAY, NOW, NOW + DAY))
    raw.executemany("INSERT INTO review_events (question_id, ts, rating, correct) VALUES (?, ?, ?, ?)",
                    [(qid, NOW, "again", 0), (qid, NOW + DAY, "good", 1)])
    raw.commit()
    raw.close()
    con = P.connect(db)
    row = con.execute("SELECT * FROM review_state").fetchone()
    assert row["state"] == "review" and row["stability"] > 0 and 1 <= row["difficulty"] <= 10
    assert row["due"] >= NOW + 4 * DAY                     # 不会一迁移就一堆到期
    con.close()
    assert P.connect(db).execute("SELECT count(*) FROM review_state WHERE state IS NULL").fetchone()[0] == 0


def test_learning_cards_enter_review_tomorrow_and_dedupe(tmp_path):
    con = P.connect(tmp_path / "practice.db")
    cards = [{"point": "二级要三个项目", "question": "应急处理二级要几个项目?", "answer": "近三年 3 个"},
             {"point": "二级要三个项目", "question": "评审问「项目够吗」, 你怎么答?", "answer": "近三年 3 个, 每个有过程记录"},
             {"question": "", "answer": "x"}]
    r = P.add_cards(con, cards, "learn:lrn_1", ["CCRC"], now=NOW)
    assert r["added"] == 2 and r["skipped"] == 1
    assert P.add_cards(con, cards[:1], "learn:lrn_2", [], now=NOW)["added"] == 0     # 同一题干不重复
    assert P.next_questions(con, now=NOW) == []
    due = P.next_questions(con, now=NOW + DAY)
    assert len(due) == 2
    assert P.review_preview(con, due[0]["id"], now=NOW + DAY) == {"again": "10 分钟", "hard": "1 天", "good": "4 天", "easy": "14 天"}
    out = P.answer(con, due[0]["id"], "三个", rating="good", now=NOW + DAY)
    assert out["interval_days"] == 4.0
    st = con.execute("SELECT state, stability FROM review_state WHERE question_id = ?", (due[0]["id"],)).fetchone()
    assert st["state"] == "review" and st["stability"] == pytest.approx(3.7145)
    again = P.answer(con, due[1]["id"], "忘了", rating="again", now=NOW + DAY)
    assert again["interval_days"] == 0 and P.stats(con, now=NOW + DAY)["due_today"] == 1   # 10 分钟后再来
