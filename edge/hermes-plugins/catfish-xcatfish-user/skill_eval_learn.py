"""技能验证题: 从员工原话出题 / 跟着员工改口径更新题 / 模型判分 / 两道不用题的检查 (11/27)。

skill_eval_gate.py 的帮手, 换个员工也能用 —— 不靠人手写题 (11/27 鸿波「换个人用, 不是现在的数据, 技能也不一样,
鲁棒性是不是比较弱」)。第一版的 47 道题是照鸿波两个月的对话手挑的, 判分要点也是照他的回答调的; 新员工一道题都没有。

  · 出题 learn(): 员工这一轮说了口径 (「记住 / 以后 / 不对, 应该 …」) 而且小鲶改了技能 → 照员工原话出题。
    每道题必须带一句员工原话 (quote), 代码核对这句话真在员工消息里 —— 模型编的口径进不来。
    新题是「待确认」(Companion 首页「技能验证题」卡片里点确认), 确认了才拦改动。
    员工这一轮改了口径 → 旧题的标准答案照新原话更新 (留历史), 不会把员工要的改动拦回去。
    定时任务 / 系统触发的轮次没有员工的话, 不出题也不改题, 只按已有的题严格拦。
  · 判分 judge(): 有要点 (must) 的先按要点, 命中就过; 没命中或没要点 → 员工 picker 选的模型对着标准答案判。
  · 不用题的检查 (新员工第一天就有):
      conflicts()  改技能新加的内容, 跟技能里员工定过的规则 (「员工 X/X 定」这类行 + 验证题答案) 直接矛盾 → 撤回;
                   员工这一轮自己改了口径的不算。11/02 「集成交付运营部是一级部」那条就是这么进来的。
      missing()    删 / 并技能: 原技能里员工定的规则, 并进去的那个技能里没有 → 拦。6/30 那次「并进」什么都没并。
  · mine(): 老员工冷启动 —— 扫 state.db 里「员工说了口径、同一轮小鲶改了技能」的历史, 批量出待确认的题。

全部走员工 picker 选的模型 (不换别的模型兜底), catfish_source=plugin:skill-eval。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable, Optional

#: 技能正文里像「员工定的规则」的行
#: 技能正文里「员工定的规则」的行: 写明是员工 / 员工原话 / 员工口径的。
#: 11/27 验证: 连「必须 / 不要 / 禁止」都算的话, 小鲶自己记的技术笔记 (哪个页面取市值、patch 的语义) 也成了「规则」,
#: 30 次真实改动误报 10 次; 只认员工的之后见 tests 和 docs。
RULE_LINE = re.compile(r"员工|原话")
#: 员工消息里拼进来的机器内容, 从这里往后截掉
_TAIL = re.compile(r"\n?\s*(<memory-context>|\[System note:|=== 附件:|=== 微信聊天记录:|\[共 \d+ 个文件|\[screenshot\]|"
                   r"--- preview|\[📄)")
_MACHINE_HEAD = ("[", "#", "<", "以下是", "员工之前对这些发件人", "现有定时任务", "You ", "Continue from")
#: Companion 按钮发的任务说明 (「官网核查」「整理属性」…) 也存成 role=user, 但不是员工的话 ——
#: 11/27 从历史出题: 「官网核查」模板里的「有不一致的先给我看」盖掉了员工 11/12「以后查询后就直接更新数据」
_MACHINE_BODY = re.compile(r"catfish_[a-z_]{3,}|params=|rel_path=|\{\\?\"_help|这条的属性：|按下面的步骤")
_RULES_MAX = 8000
_CHANGE_MAX = 4000

Chat = Callable[[list[dict[str, str]], int], str]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").replace("**", "").replace("`", "")
    return re.sub(r"[\s，。、；：,.;:!?！？\"'“”‘’「」『』（）()]+", "", s)


def _json(text: str) -> Any:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("模型没回 JSON: " + (text or "")[:120])
    return json.loads(m.group(0))


# ── 员工这一轮说的话 ─────────────────────────────────────────────────

def employee_words(text: str) -> str:
    """一条 role=user 消息里员工自己写的部分 (机器拼的附件 / 记忆 / 截图标记截掉); 整条是机器消息 → ""。"""
    t = (text or "").strip()
    if t.startswith(" json:") or t.startswith("json:"):
        try:
            parts = json.loads(t.split(":", 1)[1])
            t = " ".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text").strip()
        except (ValueError, AttributeError):
            return ""
    if not t or t.startswith(_MACHINE_HEAD) or _MACHINE_BODY.search(t):
        return ""
    m = _TAIL.search(t)
    return (t[: m.start()] if m else t).strip()[:1500]


def recent_employee_words(state_db: Path, session_id: str, n: int = 3) -> list[str]:
    """这个会话最近 n 条员工的话 (新的在前)。定时任务 / 后台会话没有员工的话 → []。"""
    if not session_id or not state_db.is_file():
        return []
    try:
        con = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True, timeout=3)
        try:
            src = con.execute("select source from sessions where id=?", (session_id,)).fetchone()
            if src and str(src[0]) in ("cron", "scheduler"):
                return []
            rows = con.execute("select content from messages where session_id=? and role='user' and display_kind is null "
                               "order by id desc limit 12", (session_id,)).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    out = [w for w in (employee_words(r[0] or "") for r in rows) if w]
    return out[:n]


def substantive(words: list[str]) -> bool:
    """员工最近的话里至少有一句不是「是 / 继续 / 好的」这种 (口径常在前一句说, 这一句只回「是」)。
    11/27 用关键词先筛过一版, 漏了「我不是和你说过集训地点在…」「…是注册地址」这类 —— 出题一次几千 token, 不再筛。"""
    return any(len(_norm(w)) >= 6 for w in words)


def quote_ok(quote: str, words: list[str]) -> bool:
    q = _norm(quote)
    return len(q) >= 4 and any(q in _norm(w) for w in words)


# ── 出题 / 改题 ───────────────────────────────────────────────────

LEARN_SYSTEM = """你帮鲶鱼的 AI 同事「小鲶」维护技能验证题: 员工定下的口径写成题, 以后小鲶改技能时用来检查有没有把口径改丢、改反。
只根据【员工原话】出题, 不根据技能改动里小鲶自己写的内容出题。
- 只收员工明确定下、以后还会用到的口径 / 规则 / 固定信息 (如「换证不算新增」「用 xx 邮箱发」「网址是 …」「比例向下取整」)。
  先问自己: 换一份文件、换一个月, 这条还适用吗? 不适用就不出 ——
  一次性的事 (这次的日期 / 某张证书的编号 / 这份文件第几项是什么 / 这次建几条提醒 / 某件事现在办到哪了)、
  空话 (「要记住」「要能展现出来」)、闲聊、问进度, 都不出题。宁可少出, 不要凑。
- 每道题: q 是员工以后可能会问小鲶的一句话 (不提「员工说过」); gold 是标准答案, 一两句, 自己能看懂;
  kind = "S" 直接问规则, "C" 给一个具体场景让小鲶套规则 (算数 / 判断), 一条口径最多出一道 S 一道 C;
  quote 是员工原话里一字不差的一段 (8~60 字), 这道题就是从这句来的。
- 员工这次的话改了下面【已有的题】里某道题的口径 → 写进 update (id + 新 gold + quote), 不要另出新题。
- 没有可出的题就回空数组。只输出 JSON: {"add": [{"q","gold","kind","quote"}], "update": [{"id","gold","quote"}]}"""


def learn(chat: Chat, skill: str, words: list[str], change: str, questions: list[dict[str, Any]]) -> dict[str, list]:
    """→ {"add": [...], "update": [...]}, 每条的 quote 都核过真在员工原话里。"""
    if not substantive(words):
        return {"add": [], "update": []}
    existing = "\n".join(f"- {q['id']}: {q['q']} → {q.get('gold', '')}" for q in questions) or "(还没有)"
    user = (f"技能: {skill}\n\n【员工原话】(最新的在前)\n" + "\n".join(f"- {w}" for w in words) +
            f"\n\n【小鲶这次对技能的改动】\n{(change or '')[:_CHANGE_MAX]}\n\n【已有的题】\n{existing}")
    out = _json(chat([{"role": "system", "content": LEARN_SYSTEM}, {"role": "user", "content": user}], 1200))
    ids = {q["id"] for q in questions}
    add = [a for a in out.get("add") or [] if isinstance(a, dict) and a.get("q") and a.get("gold")
           and quote_ok(str(a.get("quote") or ""), words)]
    upd = [u for u in out.get("update") or [] if isinstance(u, dict) and u.get("id") in ids and u.get("gold")
           and quote_ok(str(u.get("quote") or ""), words)]
    for a in add:
        a["kind"] = "C" if str(a.get("kind")).upper() == "C" else "S"
    return {"add": add[:4], "update": upd}


def apply_learned(data: dict[str, Any], learned: dict[str, list], *, origin: str, session_id: str) -> list[str]:
    """把出的题 / 改的题写进 data (不落盘)。→ 一句句说明, 给工具结果和日志用。"""
    notes = []
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    qs = data.setdefault("questions", [])
    by_id = {q["id"]: q for q in qs}
    for u in learned.get("update") or []:
        q = by_id[u["id"]]
        if _norm(q.get("gold", "")) == _norm(u["gold"]):
            continue
        q.setdefault("history", []).append({"gold": q.get("gold", ""), "until": now})
        q.update(gold=u["gold"], source=f"员工原话: {u['quote']}", updated_at=now, session_id=session_id)
        q.pop("must", None)
        q.pop("must_not", None)  # 要点是照旧答案写的, 口径改了就只靠模型对着新答案判
        notes.append(f"按员工这次的话更新了验证题「{q['q']}」")
    have = {_norm(q["q"]) for q in qs}
    used = " ".join(_norm(q.get("source", "")) for q in qs)
    for a in learned.get("add") or []:
        if _norm(a["q"]) in have or _norm(a.get("quote", "")) in used:
            continue  # 同一句原话出过题了 (口径在前一句说, 后面几轮都会再看到它)
        qid = f"auto-{int(time.time() * 1000) % 10**10}-{len(qs)}"
        qs.append({"id": qid, "kind": a["kind"], "q": a["q"], "gold": a["gold"], "status": "pending", "origin": origin,
                   "source": f"员工原话: {a['quote']}", "created_at": now, "session_id": session_id})
        have.add(_norm(a["q"]))
        notes.append(f"从员工原话新出了验证题「{a['q']}」(待员工在首页确认)")
    return notes


# ── 判分 ─────────────────────────────────────────────────────────

JUDGE_SYSTEM = """你是判分员: 对照员工定的标准答案, 判断 AI 同事的回答对不对。
- 关键结论跟标准答案一致就算对, 措辞不同、多写了不冲突的细节都算对;
- 关键结论错了、跟标准答案冲突、漏了标准答案的要点、说「技能里没有 / 不知道」, 算错。
只输出 JSON: {"ok": true 或 false, "why": "一句话"}"""


def judge(chat: Chat, q: dict[str, Any], answer: str) -> tuple[bool, str]:
    out = _json(chat([{"role": "system", "content": JUDGE_SYSTEM},
                      {"role": "user", "content": f"问题: {q['q']}\n标准答案: {q.get('gold', '')}\n回答: {answer[:3000]}"}], 200))
    return bool(out.get("ok")), str(out.get("why") or "")[:200]


# ── 不用题的两道检查 ─────────────────────────────────────────────────

def rule_lines(skill_text: str, questions: list[dict[str, Any]]) -> list[str]:
    """员工定过的规则: 验证题的标准答案 + 技能正文里像规则的行 (总长封顶)。"""
    out = [f"{q['q']} → {q.get('gold', '')}" for q in questions if q.get("gold")]
    body = re.sub(r"^---\n.*?\n---\n", "", skill_text or "", count=1, flags=re.S)
    lines = [ln.strip() for ln in body.splitlines() if ln.strip() and RULE_LINE.search(ln)]
    lines.sort(key=lambda ln: 0 if "员工" in ln else 1)
    total = sum(len(x) for x in out)
    for ln in lines:
        if total + len(ln) > _RULES_MAX:
            break
        out.append(ln[:400])
        total += len(ln)
    return out


def added_text(before: str, after: str) -> str:
    """改完多出来的行 (按行比, 够判矛盾用)。"""
    old = set((before or "").splitlines())
    return "\n".join(ln for ln in (after or "").splitlines() if ln.strip() and ln not in old)[:_CHANGE_MAX]


CONFLICT_SYSTEM = """检查 AI 同事「小鲶」这次往技能里新加的内容, 有没有跟员工定过的规则直接矛盾 (说法相反、数字不同、把员工否掉的做法写成要做)。
- 只报明确的矛盾; 补充细节、换个说法、规则没提到的新内容, 都不算;
- 【员工这一轮的原话】里员工自己改了口径的, 按员工新说法, 不算矛盾;
- 规则之间先后不同时, 以写明日期较新的那条为准; 新加内容只是把同一条规则补充得更细, 不算矛盾。
只输出 JSON: {"conflicts": [{"rule": "员工定的那条", "added": "新加内容里矛盾的那句", "why": "一句话"}]}"""


def conflicts(chat: Chat, rules: list[str], added: str, words: list[str]) -> list[dict[str, str]]:
    if not rules or not added.strip():
        return []
    user = ("【员工定过的规则】\n" + "\n".join(f"- {r}" for r in rules) +
            "\n\n【员工这一轮的原话】\n" + ("\n".join(f"- {w}" for w in words) or "(这一轮没有员工的话)") +
            f"\n\n【小鲶新加的内容】\n{added}")
    out = _json(chat([{"role": "system", "content": CONFLICT_SYSTEM}, {"role": "user", "content": user}], 600))
    cands = [c for c in out.get("conflicts") or [] if isinstance(c, dict) and c.get("rule") and c.get("added")
             and _norm(str(c["added"]))[:12] in _norm(added)]
    # 第二道: 每条单独复核「照新内容做, 是不是一定违反这条规则」(11/27 验证: 只过一道, 30 次真实改动误报 6 次)
    said = "\n".join(f"- {w}" for w in words) or "(这一轮没有员工的话)"
    return [c for c in cands[:5] if _confirm(chat, str(c["rule"]), str(c["added"]), said)]


CONFIRM_SYSTEM = """判断一句新写进技能的话, 是不是跟员工定的一条规则直接矛盾。
只有「照新写的这句去做, 就一定会违反这条规则」才算矛盾; 两句说的不是同一件事、新句只是补充细节或者更具体、
员工这一轮的原话已经改了这条规则, 都不算。只输出 JSON: {"contradict": true 或 false, "why": "一句话"}"""


def _confirm(chat: Chat, rule: str, added: str, said: str) -> bool:
    try:
        out = _json(chat([{"role": "system", "content": CONFIRM_SYSTEM}, {"role": "user", "content":
                          f"【员工定的规则】{rule}\n\n【新写的话】{added}\n\n【员工这一轮的原话】\n{said}"}], 200))
    except (ValueError, KeyError):
        return True  # 复核没回成, 按第一道的结论 (宁可撤回让模型说清楚)
    return bool(out.get("contradict"))


MISSING_SYSTEM = """一个技能要删掉、内容并进另一个技能。逐条检查原技能里员工定的规则, 在目标技能里还在不在 (意思在就算在, 不要求原文)。
只输出 JSON: {"missing": ["目标技能里找不到的那条规则", ...]}"""


def missing(chat: Chat, rules: list[str], target_text: str) -> list[str]:
    if not rules:
        return []
    user = "【原技能里员工定的规则】\n" + "\n".join(f"- {r}" for r in rules) + f"\n\n【目标技能全文】\n{target_text}"
    out = _json(chat([{"role": "system", "content": MISSING_SYSTEM}, {"role": "user", "content": user}], 800))
    return [str(m) for m in out.get("missing") or [] if m][:10]


# ── 老员工冷启动: 从历史里挖 ────────────────────────────────────────────

def history_items(state_db: Path) -> list[dict[str, Any]]:
    """state.db 里「员工说了像口径的话、同一轮小鲶改了某个技能」的每一处 → [{skill, words, change, session_id}]。"""
    con = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True, timeout=5)
    try:
        rows = con.execute("select m.id, m.session_id, m.tool_calls, s.source from messages m join sessions s on s.id=m.session_id "
                           "where m.tool_calls like '%skill_manage%' order by m.id").fetchall()
        out, seen = [], set()
        for mid, sid, tc, src in rows:
            if str(src) in ("cron", "scheduler"):
                continue
            try:
                calls = json.loads(tc)
            except ValueError:
                continue
            for c in calls:
                f = c.get("function") or {}
                if f.get("name") != "skill_manage":
                    continue
                try:
                    a = json.loads(f.get("arguments") or "{}")
                except ValueError:
                    continue
                for o in (a.get("operations") if isinstance(a.get("operations"), list) else [a]):
                    if not isinstance(o, dict) or o.get("action") not in ("patch", "edit", "create"):
                        continue
                    skill = str(o.get("name") or a.get("name") or "").split("/")[-1]
                    us = con.execute("select id, content from messages where session_id=? and id<? and role='user' "
                                     "order by id desc limit 12", (sid, mid)).fetchall()
                    words = [w for w in (employee_words(u[1] or "") for u in us) if w][:3]
                    if not skill or not substantive(words) or (skill, words[0]) in seen:
                        continue
                    seen.add((skill, words[0]))
                    out.append({"skill": skill, "words": words, "session_id": sid,
                                "change": str(o.get("new_string") or o.get("content") or "")[:_CHANGE_MAX]})
        return out
    finally:
        con.close()


def default_state_db() -> Path:
    env = os.environ.get("HERMES_HOME", "").strip()
    return (Path(env).expanduser() if env else Path.home() / ".hermes") / "state.db"


def mine(chat: Chat, load: Callable[[str], Optional[dict]], save: Callable[[str, dict], None],
         state_db: Optional[Path] = None, *, only: Optional[set[str]] = None, workers: int = 4) -> dict[str, int]:
    """把历史里挖出来的题写成待确认 (同一技能按时间顺序走, 后说的口径更新先出的题)。→ {技能: 新增几道}。"""
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415
    items = [i for i in history_items(state_db or default_state_db()) if not only or i["skill"] in only]
    by_skill: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        by_skill.setdefault(it["skill"], []).append(it)

    def one(skill: str) -> tuple[str, int]:
        data = load(skill) or {"skill": skill, "questions": []}
        before = len(data["questions"])
        for it in by_skill[skill]:
            try:
                got = learn(chat, skill, it["words"], it["change"], data["questions"])
            except Exception:  # noqa: BLE001 — 一处挖失败不影响别的
                continue
            apply_learned(data, got, origin="mined", session_id=it["session_id"])
        if len(data["questions"]) != before or any(q.get("updated_at") for q in data["questions"]):
            save(skill, data)
        return skill, len(data["questions"]) - before
    with ThreadPoolExecutor(max(1, workers)) as ex:
        return dict(ex.map(one, sorted(by_skill)))
