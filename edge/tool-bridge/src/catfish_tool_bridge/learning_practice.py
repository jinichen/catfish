"""学习里的【练习】(11/18, docs/LEARNING-PLAN.md §2 第 4 条 / 第 2 步)。

讲解后的检查题问的是「刚讲的」(还在脑子里); 练习要的是隔了一阵、没人提醒时自己想得起来、用得对。方法借 LearnHarness 的练习部分,
文字按公司场景自己写 (learning_prompts.PRACTICE)。

    learn/practice  {id}               → 进入练习, 小鲶出第一题
    learn/attempt   {id, text, thinking?}  交答案 → 小鲶判 (做对结束这题 / 指出哪步断了让你再想)
    learn/hint      {id}               要提示 → 一次只升一级 (R1 指方向 … R5 几乎整个解法)
    learn/give_up   {id}               不做了 → 这题结束 (算 movedOn), 给看标准解法; 不调模型
    learn/next      {id}               下一题 (做完一题停下, 员工点了才出下一题)
    learn/teach     {id}               回去接着学

记录 (events.jsonl):
    problem {pid, p: {statement, targets, mode, solution}}   solution 结束前不给界面
    attempt {pid, text, thinking?} / hint_request {pid} / give_up {pid}
    hint {pid, rung, text} / conclude {pid, outcome, verdict, rung, miss, retest}
"""
from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from . import learning_prompts as P
from . import learning_store as store

MODES = ("recall", "discrimination", "composition")
OUTCOMES = ("solved", "solvedWithHints", "movedOn")
MISSES = ("cue", "discrimination", "execution", "careless", "structural")
RUNGS = ("R1", "R2", "R3", "R4", "R5")
MISS_LABEL = {"cue": "想不起该用哪条", "discrimination": "跟另一条搞混", "execution": "用对了但做错",
              "careless": "粗心", "structural": "没理解那条"}


def active(meta: dict) -> str | None:
    return (meta.get("problem") or {}).get("pid")


def _ok(p: Any) -> bool:
    return isinstance(p, dict) and isinstance(p.get("statement"), str) and isinstance(p.get("targets"), list)


def problems(evs: list[dict]) -> list[dict]:
    """记录里完整的练习题 (11/18 实测出过一道残题: 读到坏的一行跳过, 不让整个学习打不开)。"""
    return [ev for ev in evs if ev.get("kind") == "problem" and _ok(ev.get("p"))]


def _last_input(evs: list[dict], pid: str | None) -> str:
    """当前这题员工最后做了什么: attempt / hint_request / say / 没有。"""
    for ev in reversed(evs):
        if ev.get("role") != "learner":
            continue
        if ev.get("kind") in ("attempt", "hint_request") and ev.get("pid") == pid:
            return ev["kind"]
        if ev.get("kind") == "say":
            return "say"
        return ""
    return ""


def last_rung(evs: list[dict], pid: str) -> int:
    rs = [RUNGS.index(ev["rung"]) + 1 for ev in evs if ev.get("kind") == "hint" and ev.get("pid") == pid and ev.get("rung") in RUNGS]
    return max(rs, default=0)


def check_problem(p: Any, plan: dict | None, evs: list[dict]) -> tuple[dict | None, list[str]]:
    from .learning import SOFT, _heal, _norm  # noqa: PLC0415
    p = _heal(p)
    if not isinstance(p, dict):
        return None, ["problem 必须是对象"]
    errs: list[str] = []
    statement = str(p.get("statement") or "").strip()
    solution = str(p.get("solution") or "").strip()
    targets = _heal(p.get("targets")) or []
    targets = [targets] if isinstance(targets, str) else targets
    ids = {n["id"] for n in (plan or {}).get("nodes") or []}
    targets = [str(t) for t in targets if str(t)]
    if not statement:
        errs.append("problem.statement (题目) 不能空")
    if not solution:
        errs.append("problem.solution 要写标准解法 (每一步用到哪条), 出题前先自己做一遍")
    if not targets or any(t not in ids for t in targets):
        errs.append(f"problem.targets 要是路线图里的节点 id 列表, 现有: {', '.join(sorted(ids))}")
    mode = p.get("mode")
    if mode not in MODES:
        errs.append("problem.mode 只能是 recall / discrimination / composition")
    if statement and any(_norm(ev["p"]["statement"]) == _norm(statement) for ev in problems(evs)):
        errs.append(SOFT + "这道题前面出过了, 换个情境出新题")
    recent = [set(ev["p"]["targets"]) for ev in problems(evs)[-2:]]
    if targets and len(recent) == 2 and all(r == set(targets) for r in recent):
        errs.append(SOFT + "连着三题都考同一个知识点了, 换着练: 这题换一个知识点")
    out = {"statement": statement[:4000], "solution": solution[:4000], "targets": targets, "mode": mode}
    # 只剩软规则 (重复 / 没换着练) 时题本身是完整的, 照样给出去 —— _call 最后一次会收下
    return (None if any(not e.startswith(SOFT) for e in errs) else out), errs


def check_conclude(c: Any) -> list[str]:
    from .learning import _heal  # noqa: PLC0415
    c = _heal(c)
    if not isinstance(c, dict):
        return ["conclude 必须是对象"]
    errs = []
    if c.get("outcome") not in OUTCOMES:
        errs.append("conclude.outcome 只能是 solved / solvedWithHints / movedOn")
    if not str(c.get("verdict") or "").strip():
        errs.append("conclude.verdict 要写一句: 对在哪、断在哪")
    if c.get("rung") not in (None, *RUNGS):
        errs.append("conclude.rung 只能是 R1–R5 或 null")
    if c.get("miss") not in (None, *MISSES):
        errs.append("conclude.miss 只能是 cue / discrimination / execution / careless / structural 或 null")
    if c.get("outcome") in ("solved", "solvedWithHints") and c.get("miss"):
        errs.append("做对了 miss 写 null")
    if c.get("outcome") == "movedOn" and not c.get("miss"):
        errs.append("没做对要写 miss (没做对的原因)")
    return errs


def render_lines(ev: dict) -> list[str]:
    k = ev.get("kind")
    if k == "problem":
        p = ev.get("p")
        if not _ok(p):
            return []
        return [f"小鲶出练习题 [{p.get('mode')} · 考 {', '.join(map(str, p['targets']))}]: {p['statement']}",
                f"  标准解法: {p.get('solution', '')}"]
    if k == "attempt":
        return [f"员工交答案: {ev.get('text', '')}" + (f" / 想法: {ev['thinking']}" if ev.get("thinking") else "")]
    if k == "hint_request":
        return ["员工要提示"]
    if k == "hint":
        return [f"小鲶给提示 {ev.get('rung')}: {ev.get('text', '')}"]
    if k == "give_up":
        return ["员工这题不做了 (看了标准解法)"]
    if k == "conclude":
        return [f"这题结束: {ev.get('outcome')} / 提示 {ev.get('rung') or '没用'} / 原因 {ev.get('miss') or '-'} — {ev.get('verdict', '')}"]
    return []


def summary(evs: list[dict]) -> dict[str, Any]:
    """这次练了几题、各种结果 / 错因各多少 (界面上显示)。"""
    out: dict[str, Any] = {"total": 0, "solved": 0, "solvedWithHints": 0, "movedOn": 0, "misses": {}}
    for ev in evs:
        if ev.get("kind") == "conclude":
            out["total"] += 1
            out[ev["outcome"]] = out.get(ev["outcome"], 0) + 1
            if ev.get("miss"):
                label = MISS_LABEL.get(ev["miss"], ev["miss"])
                out["misses"][label] = out["misses"].get(label, 0) + 1
    return out


async def step(d: Path, meta: dict, evs: list[dict], llm_kw: dict) -> None:
    """练习的一步: 没有进行中的题 → 出题; 有 → 按员工最后做的 (交答案 / 要提示 / 插话) 回应。"""
    from .learning import _call, _heal, _materials, _say, build_messages  # noqa: PLC0415
    pid = active(meta)
    kind = _last_input(evs, pid) if pid else ""
    plan = meta.get("plan")
    mats = await _materials(d, meta, None, llm_kw)

    def validate(o: dict) -> list[str]:
        if not pid:
            q, errs = check_problem(o.get("problem"), plan, evs)
            o["_p"] = q
            return errs
        if kind == "attempt":
            if "conclude" in o:
                return check_conclude(o["conclude"])
            if o.get("keep") and str(o.get("say") or "").strip():
                return []
            hint = (" 不要在这一步出新题 (做完一题要停下来, 员工点「下一题」才出);" if "problem" in o else "")
            return ["员工交了答案, 只能二选一:" + hint + " ① 这题结束: {\"conclude\": {\"outcome\": \"movedOn\", \"verdict\": \"…\", "
                    "\"rung\": null, \"miss\": \"cue\", \"retest\": true}} (他说不会 / 想不起来就是这种, miss 按原因填); "
                    "② 让他再想: {\"say\": \"指出哪一步断了 (不给答案)\", \"keep\": true}"]
        if kind == "hint_request":
            h = _heal(o.get("hint"))
            if not (isinstance(h, dict) and h.get("rung") in RUNGS and str(h.get("text") or "").strip()):
                return ["员工要提示: 输出 {hint: {rung: R1–R5, text}}"]
            if RUNGS.index(h["rung"]) + 1 <= last_rung(evs, pid):
                return [f"提示要比上一级高一级 (已经给到 R{last_rung(evs, pid)})"]
            return []
        return [] if str(o.get("say") or "").strip() else ["员工插话了: 输出 {say: 回答}"]

    msgs = build_messages(meta, evs, "practice", mats)
    msgs[-1]["content"] += "\n" + situation(evs, pid, kind)
    out = await _call(msgs, validate, llm_kw)
    if not pid:
        new = f"p_{secrets.token_hex(4)}"
        _say(d, str(out.get("say") or ""))
        store.append(d, {"role": "tutor", "kind": "problem", "pid": new, "p": out["_p"]})
        meta["problem"] = {"pid": new}
    elif kind == "attempt" and "conclude" in out:
        _say(d, str(out.get("say") or ""))
        conclude(d, meta, pid, _heal(out["conclude"]))
    elif kind == "hint_request":
        h = _heal(out["hint"])
        store.append(d, {"role": "tutor", "kind": "hint", "pid": pid, "rung": h["rung"], "text": str(h["text"]).strip()[:3000]})
    else:
        _say(d, str(out.get("say") or ""))
    store.save(d, meta)


def situation(evs: list[dict], pid: str | None, kind: str) -> str:
    """明说现在是哪一步、这一步该输出什么 —— 11/18 实测不说的话模型分不清有没有进行中的题, 员工交了答案它去出新题。"""
    if not pid:
        done = [ev for ev in evs if ev.get("kind") == "conclude"]
        last = done[-1] if done else None
        tail = ""
        if last:
            tail = (f" 上一题结果: {last.get('outcome')}" + (f" (原因 {last['miss']})" if last.get("miss") else "")
                    + "; 按难度规则调整, 同一知识点连着没做对就换知识点。")
        asked = "; ".join(f"「{ev['p']['statement'][:40]}」" for ev in problems(evs)[-6:])
        return (f"【现在】没有进行中的题, 出一道新题 (problem)。{tail}"
                + (f" 已经出过的 (不要再出一样的): {asked}" if asked else ""))
    cur = next((ev for ev in reversed(problems(evs)) if ev.get("pid") == pid), None)
    head = f"【现在】正在做的题: 「{(cur or {}).get('p', {}).get('statement', '')[:80]}」。"
    if kind == "attempt":
        return head + "员工刚交了答案 (见上面最后一条)。这一步只能 conclude 这题, 或者 {say, keep: true} 让他再想; 不要出新题。"
    if kind == "hint_request":
        return head + "员工要提示。输出 {hint: {rung, text}}, 比上一级高一级。"
    return head + "员工插话了, 输出 {say} 回答他 (不泄露解法), 不要出新题。"


def conclude(d: Path, meta: dict, pid: str, c: dict) -> None:
    store.append(d, {"role": "tutor", "kind": "conclude", "pid": pid, "outcome": c["outcome"],
                     "verdict": str(c.get("verdict") or "").strip()[:2000], "rung": c.get("rung"), "miss": c.get("miss"),
                     "retest": bool(c.get("retest"))})
    meta["problem"] = None


def give_up(d: Path, meta: dict) -> None:
    pid = active(meta)
    if not pid:
        raise ValueError("现在没有在做的题")
    store.append(d, {"role": "learner", "kind": "give_up", "pid": pid})
    rung = last_rung(store.events(d), pid)
    conclude(d, meta, pid, {"outcome": "movedOn", "verdict": "员工这题不做了, 看了标准解法。", "rung": RUNGS[rung - 1] if rung else None,
                            "miss": None, "retest": True})
    store.save(d, meta)


def build_system(meta: dict) -> tuple[str, str]:
    return P.PRACTICE, P.FORMAT_PRACTICE
