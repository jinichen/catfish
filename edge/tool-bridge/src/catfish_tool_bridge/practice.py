"""题库与错题复习 (11/02, 借 DeepTutor 的 Practice; docs/BACKLOG 「借鉴 DeepTutor」)。

# 做什么

新员工培训、制度 / 产品知识学习、考前刷题: 小鲶从知识库或资料里出题 (或员工导入 Excel 题库),
在对话里一题一题考, 客观题这里判对错, 主观题员工自评。答错的进「错题」, 按间隔重复排每天的复习,
早安简报 (Companion) 提醒「今天有 N 道要复习」。

数据全在员工本机 `~/.catfish/practice.db`, 不出这台电脑。

# 复习怎么排 (跟 DeepTutor 一样, SM-2 风格的冷启动规则, 不训练个人遗忘曲线)

    反馈        下次
    again       约 1 天后, 记忆系数降 0.2           (客观题答错只能是 again)
    hard        当前间隔 × 1.2, 记忆系数降 0.15
    good        初次 / 忘过之后约 3 天; 之后 × 记忆系数
    easy        初次约 4 天; 之后 × 记忆系数 × 1.3, 记忆系数升 0.15
记忆系数默认 2.5, 最低 1.3; 间隔最长 365 天。

错题从答错那一刻进复习队列 (次日到期); 标「已掌握」停排, 再答错重新开始。
没错过的新题不进每日复习 (「练新题」时按顺序出)。

# 表

    questions      题 (题型 / 题干 / 选项 / 答案 / 解析 / 标签 / 来源); 题干+题型+选项+答案相同的去重合并标签
    review_state   错题的复习进度 (到期时间 / 间隔 / 记忆系数 / 次数)
    review_events  每次作答 (留着以后换调度算法时回放)
"""
from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

QTYPES = ("single_choice", "multi_choice", "true_false", "fill_blank", "short_answer")
OBJECTIVE = ("single_choice", "multi_choice", "true_false")
RATINGS = ("again", "hard", "good", "easy")
DAY = 86400.0
DEFAULT_EASE = 2.5
MIN_EASE = 1.3
MAX_INTERVAL = 365.0
MAX_IMPORT = 500

_QTYPE_ALIASES = {
    "单选": "single_choice", "单选题": "single_choice", "single": "single_choice",
    "多选": "multi_choice", "多选题": "multi_choice", "multiple": "multi_choice",
    "判断": "true_false", "判断题": "true_false", "truefalse": "true_false",
    "填空": "fill_blank", "填空题": "fill_blank",
    "简答": "short_answer", "简答题": "short_answer", "问答": "short_answer",
}
_TRUE = {"true", "t", "对", "正确", "是", "√", "yes", "y"}
_FALSE = {"false", "f", "错", "错误", "否", "×", "no", "n"}
_COL_ALIASES = {
    "question": ("question", "题目", "题干"),
    "question_type": ("question_type", "题型", "type"),
    "correct_answer": ("correct_answer", "答案", "正确答案", "answer"),
    "explanation": ("explanation", "解析"),
    "tags": ("tags", "标签", "分类"),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qtype TEXT NOT NULL, stem TEXT NOT NULL, options TEXT NOT NULL DEFAULT '{}',
    answer TEXT NOT NULL, explanation TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT '', dedup_key TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL, answered INTEGER NOT NULL DEFAULT 0, mastered INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS review_state (
    question_id INTEGER PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
    due REAL NOT NULL, interval REAL NOT NULL, ease REAL NOT NULL,
    reps INTEGER NOT NULL DEFAULT 0, lapses INTEGER NOT NULL DEFAULT 0,
    first_wrong REAL NOT NULL, last_review REAL
);
CREATE TABLE IF NOT EXISTS review_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, question_id INTEGER NOT NULL,
    ts REAL NOT NULL, rating TEXT NOT NULL, correct INTEGER, user_answer TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS review_state_due ON review_state(due);
"""


def db_path() -> Path:
    home = Path(os.environ["CATFISH_HOME"]) if os.environ.get("CATFISH_HOME") else Path.home() / ".catfish"
    return home / "practice.db"


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(_SCHEMA)
    return con


# ── 题 ────────────────────────────────────────────────────

def _norm_qtype(v: Any) -> str:
    s = str(v or "").strip()
    if s in QTYPES:
        return s
    return _QTYPE_ALIASES.get(s.lower().replace(" ", "").replace("_", ""), _QTYPE_ALIASES.get(s, ""))


def _norm_options(v: Any) -> dict[str, str]:
    if isinstance(v, dict):
        items = v.items()
    elif isinstance(v, list):
        items = ((chr(ord("A") + i), x) for i, x in enumerate(v))
    else:
        return {}
    return {str(k).strip().upper(): str(x).strip() for k, x in items if str(x or "").strip()}


def _norm_answer(qtype: str, ans: Any, options: dict[str, str]) -> str:
    if isinstance(ans, list):
        ans = ",".join(str(a) for a in ans)
    s = str(ans if ans is not None else "").strip()
    if qtype == "true_false":
        low = s.lower()
        if low in _TRUE:
            return "true"
        if low in _FALSE:
            return "false"
        raise ValueError(f"判断题答案要写对/错 (true/false), 收到「{s}」")
    if qtype in ("single_choice", "multi_choice"):
        raw = s.upper().replace("，", ",").replace("、", ",").replace(";", ",").replace(" ", "")
        keys = sorted({p for p in (raw.split(",") if "," in raw else list(raw)) if p})
        if not keys or any(k not in options for k in keys):
            raise ValueError(f"答案「{s}」不在选项 {sorted(options)} 里")
        if qtype == "single_choice" and len(keys) != 1:
            raise ValueError("单选题只能有一个答案")
        return ",".join(keys)
    if not s:
        raise ValueError("答案不能空")
    return s


def normalize(q: dict[str, Any]) -> dict[str, Any]:
    """一道题 → 规整好的 dict; 不合法抛 ValueError (说清楚哪不对)。"""
    stem = str(q.get("question") or q.get("stem") or "").strip()
    if not stem:
        raise ValueError("题干不能空")
    qtype = _norm_qtype(q.get("type") or q.get("question_type"))
    options = _norm_options(q.get("options"))
    if not qtype:
        qtype = "single_choice" if options else "short_answer"
    if qtype in ("single_choice", "multi_choice") and len(options) < 2:
        raise ValueError("选择题至少两个选项")
    if qtype == "true_false":
        options = {}
    answer = _norm_answer(qtype, q.get("answer") if q.get("answer") is not None else q.get("correct_answer"), options)
    tags = q.get("tags") or []
    if isinstance(tags, str):
        tags = [t for t in (x.strip() for x in tags.replace("；", ",").replace(";", ",").replace("，", ",").split(",")) if t]
    return {
        "qtype": qtype, "stem": stem, "options": options, "answer": answer,
        "explanation": str(q.get("explanation") or "").strip(),
        "tags": [str(t).strip() for t in tags if str(t).strip()][:10],
        "source": str(q.get("source") or "").strip()[:200],
    }


def _dedup_key(n: dict[str, Any]) -> str:
    return json.dumps([n["qtype"], n["stem"], n["options"], n["answer"]], ensure_ascii=False, sort_keys=True)


def add_questions(con: sqlite3.Connection, items: list[dict[str, Any]], now: float | None = None) -> dict[str, Any]:
    """入库; 一题不合法不影响别的题, 返回每题的结果。重复题合并标签。"""
    now = now or time.time()
    added, merged, errors = [], [], []
    for i, raw in enumerate(items[:MAX_IMPORT], 1):
        try:
            n = normalize(raw)
        except ValueError as e:
            errors.append({"row": i, "error": str(e)})
            continue
        key = _dedup_key(n)
        row = con.execute("SELECT id, tags FROM questions WHERE dedup_key = ?", (key,)).fetchone()
        if row:
            tags = sorted(set(json.loads(row["tags"])) | set(n["tags"]))
            con.execute("UPDATE questions SET tags = ? WHERE id = ?", (json.dumps(tags, ensure_ascii=False), row["id"]))
            merged.append(row["id"])
            continue
        cur = con.execute(
            "INSERT INTO questions (qtype, stem, options, answer, explanation, tags, source, dedup_key, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (n["qtype"], n["stem"], json.dumps(n["options"], ensure_ascii=False), n["answer"], n["explanation"],
             json.dumps(n["tags"], ensure_ascii=False), n["source"], key, now),
        )
        added.append(cur.lastrowid)
    con.commit()
    return {"added": len(added), "merged": len(merged), "errors": errors[:20], "ids": added}


def read_table(path: str) -> list[dict[str, Any]]:
    """Excel (.xlsx 当前工作表) / CSV / TSV / JSON → 题目列表 (列名中英文都认, 选项列 A…J / 选项A…)"""
    p = Path(path).expanduser()
    if not p.is_file():
        raise ValueError(f"找不到文件 {p}")
    if p.stat().st_size > 5 * 1024 * 1024:
        raise ValueError("文件超过 5MB")
    suf = p.suffix.lower()
    if suf == ".json":
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        if not isinstance(data, list):
            raise ValueError("JSON 要是题目数组")
        return data
    if suf in (".csv", ".tsv"):
        raw = p.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("gb18030")
        rows = list(csv.reader(io.StringIO(text), delimiter="\t" if suf == ".tsv" else ","))
    elif suf == ".xlsx":
        from openpyxl import load_workbook  # noqa: PLC0415

        ws = load_workbook(p, read_only=True, data_only=True).active
        rows = [["" if c is None else str(c) for c in r] for r in ws.iter_rows(values_only=True)]
    else:
        raise ValueError("只支持 .xlsx / .csv / .tsv / .json (老 .xls 先另存为 .xlsx)")
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    out = []
    for r in rows[1:]:
        cell = {header[i]: (r[i].strip() if i < len(r) else "") for i in range(len(header))}
        if not any(cell.values()):
            continue
        q: dict[str, Any] = {}
        for key, names in _COL_ALIASES.items():
            for nm in names:
                if cell.get(nm):
                    q[key] = cell[nm]
                    break
        opts = {}
        for i in range(10):
            letter = chr(ord("A") + i)
            v = cell.get(letter) or cell.get(f"选项{letter}") or ""
            if v:
                opts[letter] = v
        q["options"] = opts
        q["source"] = f"导入: {p.name}"
        out.append(q)
    return out


# ── 判分 + 排期 ───────────────────────────────────────────

def grade(qtype: str, answer: str, user_answer: Any) -> bool | None:
    """客观题判对错; 主观题返回 None (员工自评)。"""
    if qtype not in OBJECTIVE:
        return None
    try:
        got = _norm_answer(qtype, user_answer, {k: k for k in "ABCDEFGHIJ"})
    except ValueError:
        return False
    return got == answer


def schedule(state: dict[str, Any] | None, rating: str, now: float) -> dict[str, Any]:
    """一次反馈 → 新的复习进度 (纯函数)。state 为 None = 刚成错题。"""
    s = dict(state or {"interval": 0.0, "ease": DEFAULT_EASE, "reps": 0, "lapses": 0, "first_wrong": now})
    iv, ease, reps = float(s["interval"]), float(s["ease"]), int(s["reps"])
    if rating == "again":
        iv, ease, reps = 1.0, max(MIN_EASE, ease - 0.2), 0
        s["lapses"] = int(s["lapses"]) + (1 if state else 0)
    elif rating == "hard":
        iv, ease, reps = max(1.0, iv * 1.2), max(MIN_EASE, ease - 0.15), reps + 1
    elif rating == "good":
        iv, reps = (3.0 if reps == 0 else iv * ease), reps + 1
    elif rating == "easy":
        iv, ease, reps = (4.0 if reps == 0 else iv * ease * 1.3), ease + 0.15, reps + 1
    else:
        raise ValueError(f"rating 只能是 {RATINGS}")
    iv = min(MAX_INTERVAL, iv)
    s.update(interval=round(iv, 3), ease=round(ease, 3), reps=reps, due=now + iv * DAY, last_review=now)
    return s


def _public(row: sqlite3.Row, with_answer: bool = False) -> dict[str, Any]:
    q = {"id": row["id"], "type": row["qtype"], "question": row["stem"],
         "options": json.loads(row["options"]), "tags": json.loads(row["tags"])}
    if with_answer:
        q.update(answer=row["answer"], explanation=row["explanation"], source=row["source"])
    return q


def _end_of_today(now: float) -> float:
    lt = time.localtime(now)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 23, 59, 59, 0, 0, -1))


def next_questions(con: sqlite3.Connection, n: int = 5, mode: str = "review", tag: str = "",
                   now: float | None = None) -> list[dict[str, Any]]:
    """review: 今天到期 / 逾期的错题 (最早到期的先); new: 还没做过的题 (先入库的先)。不带答案。"""
    now = now or time.time()
    n = max(1, min(int(n or 5), 20))
    like = f'%"{tag}"%' if tag else "%"
    if mode == "new":
        rows = con.execute(
            "SELECT * FROM questions WHERE answered = 0 AND tags LIKE ? ORDER BY id LIMIT ?", (like, n)).fetchall()
    else:
        rows = con.execute(
            "SELECT q.* FROM review_state r JOIN questions q ON q.id = r.question_id"
            " WHERE q.mastered = 0 AND r.due <= ? AND q.tags LIKE ? ORDER BY r.due LIMIT ?",
            (_end_of_today(now), like, n)).fetchall()
    return [_public(r) for r in rows]


def answer(con: sqlite3.Connection, qid: int, user_answer: Any = None, rating: str = "",
           now: float | None = None) -> dict[str, Any]:
    """作答: 客观题这里判 (答错只能 again); 主观题要带员工自评 rating。返回对错 + 参考答案 + 解析 + 下次复习时间。"""
    now = now or time.time()
    row = con.execute("SELECT * FROM questions WHERE id = ?", (int(qid),)).fetchone()
    if row is None:
        raise ValueError(f"没有编号 {qid} 的题")
    correct = grade(row["qtype"], row["answer"], user_answer)
    if correct is False:
        rating = "again"
    elif correct is True and rating not in RATINGS:
        rating = "good"
    elif correct is None and rating not in RATINGS:
        # 主观题: 先把参考答案给出去, 员工对照后再带 rating 回来
        return {"needs_rating": True, **_public(row, with_answer=True),
                "hint": "主观题: 把参考答案给员工看, 问他自己觉得答得怎样 (again/hard/good/easy) 再调一次 answer"}
    state_row = con.execute("SELECT * FROM review_state WHERE question_id = ?", (row["id"],)).fetchone()
    in_review = state_row is not None
    if not in_review and rating == "again":
        st = schedule(None, "again", now)
        con.execute("INSERT INTO review_state (question_id, due, interval, ease, reps, lapses, first_wrong, last_review)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (row["id"], st["due"], st["interval"], st["ease"], st["reps"], st["lapses"], now, now))
        con.execute("UPDATE questions SET mastered = 0 WHERE id = ?", (row["id"],))
    elif in_review:
        st = schedule(dict(state_row), rating, now)
        con.execute("UPDATE review_state SET due = ?, interval = ?, ease = ?, reps = ?, lapses = ?, last_review = ?"
                    " WHERE question_id = ?",
                    (st["due"], st["interval"], st["ease"], st["reps"], st["lapses"], now, row["id"]))
        if rating == "again":
            con.execute("UPDATE questions SET mastered = 0 WHERE id = ?", (row["id"],))
    else:
        st = None
    con.execute("UPDATE questions SET answered = 1 WHERE id = ?", (row["id"],))
    con.execute("INSERT INTO review_events (question_id, ts, rating, correct, user_answer) VALUES (?, ?, ?, ?, ?)",
                (row["id"], now, rating, None if correct is None else int(correct), str(user_answer or "")[:500]))
    con.commit()
    out = {**_public(row, with_answer=True), "correct": correct, "rating": rating}
    if st:
        out["next_review"] = time.strftime("%Y-%m-%d", time.localtime(st["due"]))
        out["interval_days"] = st["interval"]
    return out


def set_mastered(con: sqlite3.Connection, qid: int, mastered: bool) -> dict[str, Any]:
    cur = con.execute("UPDATE questions SET mastered = ? WHERE id = ?", (int(bool(mastered)), int(qid)))
    con.commit()
    return {"ok": cur.rowcount == 1}


def delete(con: sqlite3.Connection, qid: int) -> dict[str, Any]:
    cur = con.execute("DELETE FROM questions WHERE id = ?", (int(qid),))
    con.execute("DELETE FROM review_events WHERE question_id = ?", (int(qid),))
    con.commit()
    return {"ok": cur.rowcount == 1}


def stats(con: sqlite3.Connection, now: float | None = None) -> dict[str, Any]:
    now = now or time.time()
    lt = time.localtime(now)
    start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    one = lambda sql, *a: con.execute(sql, a).fetchone()[0]  # noqa: E731
    return {
        "total": one("SELECT count(*) FROM questions"),
        "new": one("SELECT count(*) FROM questions WHERE answered = 0"),
        "wrong": one("SELECT count(*) FROM review_state r JOIN questions q ON q.id = r.question_id WHERE q.mastered = 0"),
        "due_today": one("SELECT count(*) FROM review_state r JOIN questions q ON q.id = r.question_id"
                         " WHERE q.mastered = 0 AND r.due <= ?", _end_of_today(now)),
        "reviewed_today": one("SELECT count(DISTINCT question_id) FROM review_events WHERE ts >= ?", start),
        "mastered": one("SELECT count(*) FROM questions WHERE mastered = 1"),
    }
