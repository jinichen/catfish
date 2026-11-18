"""题库与错题复习 (11/02, 借 DeepTutor 的 Practice; docs/BACKLOG 「借鉴 DeepTutor」)。

# 做什么

新员工培训、制度 / 产品知识学习、考前刷题: 小鲶从知识库或资料里出题 (或员工导入 Excel 题库),
在对话里一题一题考, 客观题这里判对错, 主观题员工自评。答错的进「错题」, 按间隔重复排每天的复习,
早安简报 (Companion) 提醒「今天有 N 道要复习」。

数据全在员工本机 `~/.catfish/practice.db`, 不出这台电脑。

# 复习怎么排

11/18 起换成 FSRS (fsrs.py, Anki 新版同款; 学习方案第 2 步): 每张卡记难度 / 稳定性, 排在「想得起来的概率掉到 90%」那天。
老库第一次打开时, 按 review_events 把每道错题的作答记录用 FSRS 重放一遍, 接着排 (_migrate_fsrs)。
学习 (learning.py) 学完自动做的复习卡、会议纪要「记住这几条」做的卡: 入库就进复习 (第二天到期), 不用先答错。
interval / ease 两列照旧写 (老版本 Companion 读数)。下面是原来 SM-2 的规则 (11/18 前), 留作历史:

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


_ADD_COLUMNS = (
    ("questions", "point", "TEXT NOT NULL DEFAULT ''"),      # 这张卡练的是哪条要点 (学习 / 纪要做的卡)
    ("questions", "origin", "TEXT NOT NULL DEFAULT ''"),     # learn:<学习编号> / meeting:<会议编号>
    ("review_state", "stability", "REAL"),
    ("review_state", "difficulty", "REAL"),
    ("review_state", "state", "TEXT"),                       # NULL = 老 SM-2 行, 还没迁; new / review / relearning
)


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(_SCHEMA)
    for table, col, decl in _ADD_COLUMNS:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    _migrate_fsrs(con)
    return con


def _migrate_fsrs(con: sqlite3.Connection) -> None:
    """老 SM-2 复习行 (state 为空) → 按作答记录用 FSRS 重放一遍。没有记录的按原来的间隔估一个稳定性。"""
    from . import fsrs  # noqa: PLC0415
    rows = con.execute("SELECT * FROM review_state WHERE state IS NULL").fetchall()
    for row in rows:
        evs = con.execute("SELECT ts, rating FROM review_events WHERE question_id = ? AND ts >= ? ORDER BY ts",
                          (row["question_id"], row["first_wrong"] - 1)).fetchall()
        st: dict | None = None
        for ev in evs:
            if ev["rating"] in fsrs.RATINGS:
                st = fsrs.review(st, ev["rating"], ev["ts"])
        if st is None:
            st = {"stability": max(0.5, float(row["interval"])), "difficulty": 5.0, "state": "review", "due": row["due"]}
        con.execute("UPDATE review_state SET stability = ?, difficulty = ?, state = ?, due = ? WHERE question_id = ?",
                    (st["stability"], st["difficulty"], st["state"], max(st["due"], row["due"]) if evs else row["due"],
                     row["question_id"]))
    if rows:
        con.commit()


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


def _fsrs_step(state: dict[str, Any] | None, rating: str, now: float) -> dict[str, Any]:
    """FSRS 排一次; 间隔跟界面按钮上写的一致 (fsrs.ordered_interval)。ease 列照旧写 (老版本 Companion 读)。"""
    from . import fsrs  # noqa: PLC0415
    card = None
    if state and state.get("state") not in (None, "new"):
        card = {k: state.get(k) for k in ("stability", "difficulty", "reps", "lapses", "last_review")}
    elif state:
        card = {"reps": state.get("reps"), "lapses": state.get("lapses")}
    st = fsrs.review(card, rating, now)
    if rating != "again":
        st["interval"] = fsrs.ordered_interval(card, rating, now)
        st["due"] = now + st["interval"] * DAY
    st["ease"] = round(max(MIN_EASE, 3.0 - st["difficulty"] / 5), 3)
    return st


def review_preview(con: sqlite3.Connection, qid: int, now: float | None = None) -> dict[str, str]:
    """四个按钮各排到哪天 (「10 分钟」「4 天」…)。"""
    from . import fsrs  # noqa: PLC0415
    now = now or time.time()
    row = con.execute("SELECT * FROM review_state WHERE question_id = ?", (int(qid),)).fetchone()
    card = None
    if row and row["state"] not in (None, "new"):
        card = {k: row[k] for k in ("stability", "difficulty", "reps", "lapses", "last_review")}
    return fsrs.preview(card, now)


def add_cards(con: sqlite3.Connection, cards: list[dict[str, Any]], origin: str, tags: list[str],
              now: float | None = None) -> dict[str, Any]:
    """学习 / 纪要做的复习卡 {point, question, answer}: 当简答题入库, 直接进复习 (明天到期)。同一题干不重复入库。"""
    now = now or time.time()
    added, skipped = [], 0
    for c in cards[:MAX_IMPORT]:
        q, a = str(c.get("question") or "").strip(), str(c.get("answer") or "").strip()
        if not q or not a:
            skipped += 1
            continue
        key = json.dumps(["card", q], ensure_ascii=False)
        if con.execute("SELECT 1 FROM questions WHERE dedup_key = ? OR (stem = ? AND origin != '')", (key, q)).fetchone():
            skipped += 1
            continue
        cur = con.execute(
            "INSERT INTO questions (qtype, stem, options, answer, explanation, tags, source, dedup_key, created_at, point, origin)"
            " VALUES ('short_answer', ?, '{}', ?, ?, ?, ?, ?, ?, ?, ?)",
            (q[:1000], a[:2000], str(c.get("point") or "")[:500], json.dumps(tags[:10], ensure_ascii=False),
             str(c.get("source") or origin)[:200], key, now, str(c.get("point") or "")[:500], origin[:200]))
        qid = cur.lastrowid
        con.execute("INSERT INTO review_state (question_id, due, interval, ease, reps, lapses, first_wrong, last_review, state)"
                    " VALUES (?, ?, 0, ?, 0, 0, ?, NULL, 'new')", (qid, _start_of_tomorrow(now), DEFAULT_EASE, now))
        added.append(qid)
    con.commit()
    return {"added": len(added), "skipped": skipped, "ids": added}


def _start_of_tomorrow(now: float) -> float:
    return _end_of_today(now) + 1


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
    if in_review or rating == "again":
        st = _fsrs_step(dict(state_row) if in_review else None, rating, now)
        if not in_review:   # 刚答错进错题: 跟原来一样明天复习 (复习时再忘才是 10 分钟后再来)
            st["due"], st["interval"] = _start_of_tomorrow(now), 1.0
        if in_review:
            con.execute("UPDATE review_state SET due = ?, interval = ?, ease = ?, reps = ?, lapses = ?, last_review = ?,"
                        " stability = ?, difficulty = ?, state = ? WHERE question_id = ?",
                        (st["due"], st["interval"], st["ease"], st["reps"], st["lapses"], now, st["stability"],
                         st["difficulty"], st["state"], row["id"]))
        else:
            con.execute("INSERT INTO review_state (question_id, due, interval, ease, reps, lapses, first_wrong, last_review,"
                        " stability, difficulty, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (row["id"], st["due"], st["interval"], st["ease"], st["reps"], st["lapses"], now, now,
                         st["stability"], st["difficulty"], st["state"]))
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
        out["next_review"] = time.strftime("%Y-%m-%d %H:%M" if st["interval"] < 1 else "%Y-%m-%d", time.localtime(st["due"]))
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
