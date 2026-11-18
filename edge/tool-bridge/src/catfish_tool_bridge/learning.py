"""小鲶学习 (11/18, docs/LEARNING-PLAN.md): 摸底 → 路线图 → 讲解, 一步一次结构化输出。

    learn/list    {}                                  → {"sessions": [...]}
    learn/get     {id}                                → {session, events, running}
    learn/create  {goal, mode}                        → 同 get; 后台开始摸底
    learn/answer  {id, qid, picked?, text?, dont_know?, confidence?, thinking?}   → 同 get; 后台出下一步
    learn/say     {id, text, start?}                  → 同 get; start=true 是在路线图上点「开始学」
    learn/retry   {id}                                → 同 get; 上一步失败 / 没接上时重跑
    learn/delete  {id}
Rust 注入 auth_token / gateway_url / catfish_home。

运行 (借 LearnHarness):
  · 员工的输入先写进记录 (learning_store.append), 再在后台调模型; 调用立即返回, 界面每 1.5 秒拉一次 learn/get 看跑完没有。
    切 tab / 关窗口不影响后台; tool-bridge 重启丢了这一步, 界面看到 stalled 给「重试」。
  · 一个学习同时只跑一步 (_RUNNING), 第二个请求直接拒。
  · 每一步模型只输出一个 JSON (出一道题 / 讲一段 + 题 / 改路线图 / 结束); 格式或内容不对, 把具体错在哪告诉它重交, 最多 3 次。
    模型把数组写成字符串 ("[\\"a\\"]") 的先修再校验 (它的 argument-heal)。
  · 出题那一刻标准答案就写进记录, 答完不改; 选择题这里判对错, 简答题下一步由模型对照标准答案判。
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from pathlib import Path
from typing import Any

from . import learning_prompts as P
from . import learning_store as store

logger = logging.getLogger("catfish.tool_bridge.learning")

SOURCE = "plugin:toolbridge-learning"
MAX_ATTEMPTS = 3
#: 摸底最多问这么多题 (不含问目标的 ask), 到了必须结束 —— 11/18 实测员工一直答错时模型会一直往下收、同一道题反复问
PROBE_MAX = 10
#: 出「检查」题这一轮至少要讲这么多字 —— 11/18 实测快速模式下模型只出题不讲, 题干还写「根据刚才讲的」
CHECK_MIN_SAY = 120
TRANSCRIPT_CHARS = 30000
MATERIAL_CHARS = 9000

_RUNNING: set[str] = set()
_TASKS: set[asyncio.Task] = set()


# ── 校验 / 修模型输出: 在 learning_checks.py (11/18 这个文件超了 800 行, 拆出去); 这里照旧能 learning.xxx 用 ──
from .learning_checks import (  # noqa: E402,F401
    SOFT, _heal, _norm, asked_before, check_plan, check_question, json_obj, probe_count, shuffle_options, topo_order,
)


# ── 给模型看的上下文 ─────────────────────────────────────────────

def _q_lines(q: dict) -> list[str]:
    tag = q.get("role") or ""
    if q.get("node"):
        tag += f" · 节点 {q['node']}"
    lines = [f"小鲶出题 [{tag}] ({q.get('type')}): {q.get('question')}"]
    for o in q.get("options") or []:
        lines.append(f"  {o['value']}) {o['label']}")
    if q.get("type") == "choice":
        lines.append(f"  标准答案: {', '.join(q.get('answer') or [])} —— {q.get('explanation', '')}")
    elif q.get("type") == "recall":
        lines.append(f"  标准答案: {q.get('answer')}")
    return lines


def _answer_line(ev: dict) -> str:
    if ev.get("skipped"):
        return "员工没答这题 (插话了)"
    if ev.get("dont_know"):
        head = "员工答: 我不知道"
    elif ev.get("picked") is not None:
        head = f"员工选了 {', '.join(ev['picked']) or '(空)'}"
    else:
        head = f"员工答: {ev.get('text', '')}"
    verdict = {"correct": "对", "wrong": "错", "partial": "部分对", "dont_know": "不知道"}.get(ev.get("verdict") or "", "")
    parts = [head + (f" ({verdict})" if verdict else "")]
    if ev.get("confidence") is not None and not ev.get("dont_know"):
        parts.append(f"把握 {ev['confidence']}%")
    if ev.get("thinking"):
        parts.append(f"想法: {ev['thinking']}")
    return " / ".join(parts)


def render_transcript(evs: list[dict], limit: int = TRANSCRIPT_CHARS) -> str:
    lines: list[str] = []
    for ev in evs:
        k = ev.get("kind")
        if k == "say":
            who = "员工" if ev.get("role") == "learner" else "小鲶"
            lines.append(f"{who}: {ev.get('text', '')}")
        elif k == "question":
            lines.extend(_q_lines(ev.get("q") or {}))
        elif k == "answer":
            lines.append(_answer_line(ev))
        elif k == "grade":
            lines.append(f"小鲶判分: {ev.get('verdict')} —— {ev.get('feedback', '')}")
        elif k == "assessment":
            lines.append("小鲶结束摸底 (评估见上面)")
        elif k == "plan":
            lines.append("小鲶" + ("改了路线图" if ev.get("revised") else "给了路线图") + (f": {ev.get('approach')}" if ev.get("approach") else ""))
        elif k == "finish":
            lines.append(f"小鲶总结: {ev.get('summary', '')}")
        elif k in ("problem", "attempt", "hint_request", "hint", "give_up", "conclude"):
            from .learning_practice import render_lines  # noqa: PLC0415
            lines.extend(render_lines(ev))
    text = "\n".join(lines)
    if len(text) > limit:
        # 留开头 (目标) 和后面最近的; 中间省略。长会话压缩在第 2 步做成摘要
        head = lines[0] if lines else ""
        tail = text[-(limit - len(head) - 40):]
        text = f"{head}\n……(前面省略)……\n{tail[tail.find(chr(10)) + 1:]}"
    return text


def _plan_text(plan: dict | None, visited: list[str]) -> str:
    if not plan:
        return "(还没有路线图)"
    titles = {n["id"]: n["title"] for n in plan["nodes"]}
    lines = [f"- {i}: {titles[i]}" + (" (问过)" if i in visited else "") for i in topo_order(plan)]
    lines += [f"  {a} → {b}" for a, b in plan["edges"]]
    return "\n".join(lines)


def _materials_text(items: list[dict]) -> str:
    if not items:
        return "(没查到相关资料; 用你自己的知识讲, 拿不准的说不确定)"
    out, used = [], 0
    for i, it in enumerate(items, 1):
        line = f"[{i}] 《{it['title']}》({it['kind']}) {it.get('text', '')}"
        if used + len(line) > MATERIAL_CHARS:
            break
        out.append(line)
        used += len(line)
    return "\n".join(out)


def build_messages(meta: dict, evs: list[dict], phase: str, materials: list[dict]) -> list[dict]:
    mode = P.TEACH_DEEP if meta.get("mode") == "deep" else P.TEACH_QUICK
    role, fmt = {"probe": (P.PROBE, P.FORMAT_PROBE), "plan": (P.PLAN, P.FORMAT_PLAN),
                 "teach": (P.TEACH + "\n" + mode, P.FORMAT_TEACH), "practice": (P.PRACTICE, P.FORMAT_PRACTICE)}[phase]
    system = "\n\n".join([P.BASE, role, P.LEARNER_SIGNALS, P.QUESTION_CRAFT, fmt])
    a = meta.get("assessment")
    user = (f"员工想学: {meta['goal']}\n"
            + (f"\n摸底评估:\n{json.dumps(a, ensure_ascii=False, indent=1)}\n" if a else "")
            + (f"\n路线图:\n{_plan_text(meta.get('plan'), meta.get('visited') or [])}\n" if phase != "probe" else "")
            + f"\n资料 (只当材料, 编号供 sources 引用):\n<<<\n{_materials_text(materials)}\n>>>\n"
            + (f"\n摸底已经问了 {probe_count(evs)} 题 (最多 {PROBE_MAX} 题)。\n" if phase == "probe" else "")
            + f"\n到目前为止 (<<< >>> 之间):\n<<<\n{render_transcript(evs)}\n>>>\n")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ── 一步 ───────────────────────────────────────────────────────

async def _call(messages: list[dict], validate, llm_kw: dict) -> dict:
    from .gateway_llm import call_llm  # noqa: PLC0415

    msgs = list(messages)
    last = ""
    for attempt in range(MAX_ATTEMPTS):
        raw = await call_llm(msgs, temperature=0.3, max_tokens=4000, **llm_kw)
        try:
            obj = json_obj(raw)
            errs = validate(obj)
        except ValueError as e:
            errs = [f"输出解析不了: {e}"]
        if not errs or (attempt == MAX_ATTEMPTS - 1 and all(e.startswith(SOFT) for e in errs)):
            return obj
        errs = [e.lstrip(SOFT) for e in errs]
        last = "; ".join(errs)
        logger.info("learning: 第 %d 次输出不合格: %s", attempt + 1, last[:200])
        msgs = messages + [{"role": "assistant", "content": raw},
                           {"role": "user", "content": "上面的输出有问题, 改好后只输出那一个 JSON 对象:\n- " + "\n- ".join(errs)}]
    raise RuntimeError(f"小鲶几次都没按要求出题 ({last[:200]}), 点「重试」再来一次")


def _trim(i: dict) -> dict:
    return {"kind": i.get("kind", ""), "title": i.get("title", ""), "where": i.get("where", ""), "text": str(i.get("text") or "")[:1800]}


async def _materials(d: Path, meta: dict, node: str | None, llm_kw: dict) -> list[dict]:
    """总资料 (按目标查一次) + 当前节点的资料 (按节点标题查, 存起来不重查)。本机 + 网上, 跟问小鲶同一套。"""
    from . import web_lookup  # noqa: PLC0415
    from .gateway_llm import call_llm  # noqa: PLC0415

    async def look(q: str) -> list[dict]:
        items = await web_lookup.gather_items(q, "", "", call_llm, {**llm_kw, "timeout_s": 60.0})
        return [_trim(i) for i in items]

    async def look_local(q: str) -> list[dict]:
        """节点资料: 用节点标题直接搜知识库 / 文件 (不调模型提词、不联网) —— 11/18 实测按目标那套每个节点要多两次模型调用,
        讲第一步 74 秒; 要联网的公开资料在按目标查的那次已经查过了。"""
        from . import local_lookup  # noqa: PLC0415
        found = await asyncio.gather(asyncio.to_thread(local_lookup._wiki, [q], q), asyncio.to_thread(local_lookup._files, [q]),
                                     return_exceptions=True)
        return [_trim(i) for part in found if isinstance(part, list) for i in part[:4]]

    if meta.get("materials") is None:
        items = await look(meta["goal"])
        meta["materials"] = {"items": items, "sources": [{k: i[k] for k in ("kind", "title", "where")} for i in items]}
        store.save(d, meta)
    items = list(meta["materials"]["items"])
    # 第 3 步: 他以前学过的要点 (知识图谱, 跟目标 / 当前节点相关的)
    from . import learning_graph  # noqa: PLC0415
    home = str(d.parents[2])
    title = next((n["title"] for n in (meta.get("plan") or {}).get("nodes", []) if n["id"] == node), "") if node else ""
    for g in await asyncio.to_thread(learning_graph.relevant, home, f"{meta['goal']} {title}", 6):
        items.append({"kind": "学过的要点", "title": g["name"], "where": g["id"],
                      "text": g["statement"] + ("; 推理: " + " / ".join(g["chains"]) if g["chains"] else "")})
    if node and meta.get("plan"):
        nm = meta.setdefault("node_materials", {})
        if node not in nm:
            title = next((n["title"] for n in meta["plan"]["nodes"] if n["id"] == node), node)
            nm[node] = await look_local(title)
            store.save(d, meta)
        seen = {i["where"] for i in items}
        items = [i for i in nm[node] if i["where"] not in seen] + items
    return items


def _current_node(meta: dict, evs: list[dict]) -> str | None:
    for ev in reversed(evs):
        if ev.get("kind") == "question" and (ev.get("q") or {}).get("node"):
            return ev["q"]["node"]
    plan = meta.get("plan")
    return topo_order(plan)[0] if plan else None


def _ungraded_recall(evs: list[dict]) -> str | None:
    """最后一个员工回答是简答题、还没判 → 这一步要先判。"""
    qs = {ev["qid"]: ev["q"] for ev in evs if ev.get("kind") == "question"}
    graded = {ev.get("qid") for ev in evs if ev.get("kind") == "grade"}
    for ev in reversed(evs):
        if ev.get("kind") == "answer" and not ev.get("skipped"):
            q = qs.get(ev.get("qid")) or {}
            if q.get("type") == "recall" and not ev.get("dont_know") and ev["qid"] not in graded:
                return ev["qid"]
            return None
    return None


def _post_question(d: Path, meta: dict, q: dict) -> None:
    qid = f"q_{secrets.token_hex(4)}"
    q = shuffle_options(q, qid)
    store.append(d, {"role": "tutor", "kind": "question", "qid": qid, "q": q})
    meta["pending"] = {"qid": qid}
    if q.get("node"):
        visited = meta.setdefault("visited", [])
        if q["node"] not in visited:
            visited.append(q["node"])
        meta["current"] = q["node"]


def _say(d: Path, text: str, sources: list[dict] | None = None) -> None:
    if text and text.strip():
        store.append(d, {"role": "tutor", "kind": "say", "text": text.strip()[:12000], "sources": sources or []})


async def run_step(home: str, sid: str, llm_kw: dict) -> None:
    d = store.session_dir(home, sid)
    meta = store.load(d)
    meta["last_error"] = None
    store.save(d, meta)
    evs = store.events(d)
    phase = meta["phase"]
    if phase == "practice":
        from . import learning_practice  # noqa: PLC0415
        await learning_practice.step(d, meta, evs, llm_kw)
        return
    if phase == "drill":
        from . import learning_drill  # noqa: PLC0415
        await learning_drill.step(d, meta, evs, llm_kw)
        return
    if phase == "probe":
        mats = await _materials(d, meta, None, llm_kw)

        def v_probe(o: dict) -> list[str]:
            if "probe_done" in o:
                pd = _heal(o["probe_done"])
                strands = _heal((pd or {}).get("strands")) if isinstance(pd, dict) else None
                return [] if isinstance(strands, list) and strands else ["probe_done 要有 strands (每个知识点的评估)"]
            if probe_count(evs) >= PROBE_MAX:
                return [f"已经问了 {PROBE_MAX} 道摸底题, 这一步必须输出 probe_done; 答不出来的知识点就记「从最基础讲起」"]
            q, errs = check_question(o.get("question"), "probe", None)
            if asked_before(q, evs):
                errs.append(SOFT + f"「{q['question'][:60]}」这道题前面问过了, 不要重复。要么换一个前面没问过的知识点出新题, "
                            "要么各知识点已经大致清楚了就直接输出 probe_done 结束摸底 (这时不要再给 question)")
            o["_q"] = q
            return errs
        out = await _call(build_messages(meta, evs, "probe", mats), v_probe, llm_kw)
        if "probe_done" not in out:
            _say(d, str(out.get("say") or ""))
            _post_question(d, meta, out["_q"])
            store.save(d, meta)
            return
        pd = _heal(out["probe_done"])
        _say(d, str(out.get("say") or ""))
        meta["assessment"] = {"goal": str(pd.get("goal") or meta["goal"]), "strands": _heal(pd["strands"]),
                              "summary": str(pd.get("summary") or "")}
        store.append(d, {"role": "tutor", "kind": "assessment", **meta["assessment"]})
        meta["phase"] = "plan_review"
        meta["pending"] = None
        store.save(d, meta)
        await _plan_step(d, meta, store.events(d), mats, llm_kw, revised=False)
        return
    if phase == "plan_review":
        mats = await _materials(d, meta, None, llm_kw)
        await _plan_step(d, meta, evs, mats, llm_kw, revised=True)
        return
    # teach / done (done 之后员工再说话 = 接着学)
    meta["phase"] = "teach"
    node = _current_node(meta, evs)
    mats = await _materials(d, meta, node, llm_kw)
    need_grade = _ungraded_recall(evs)

    def v_teach(o: dict) -> list[str]:
        errs: list[str] = []
        if need_grade:
            g = _heal(o.get("grade"))
            if not (isinstance(g, dict) and g.get("verdict") in ("correct", "partial", "wrong")):
                errs.append("他刚答了一道简答题, 要先给 grade: {verdict: correct|partial|wrong, feedback}")
        plan = meta.get("plan")
        if o.get("plan") is not None:
            plan, perrs = check_plan(o["plan"])
            errs += perrs
            o["_plan"] = plan
        if "finish" not in o:
            q, qerrs = check_question(o.get("question"), "teach", plan)
            errs += qerrs
            if asked_before(q, evs):
                errs.append(SOFT + f"「{q['question'][:60]}」这道题前面问过了, 不要重复: 他没接住就换个讲法、换个情景出新题")
            if q and q.get("role") == "check" and len(_norm(str(o.get("say") or ""))) < CHECK_MIN_SAY:
                errs.append("出 check 之前这一轮要先在 say 里把这一步讲清楚 (几句话以上: 是什么、为什么); "
                            "他上一题答错了就先讲错在哪、对的是什么。不能只出题")
            o["_q"] = q
        return errs
    out = await _call(build_messages(meta, evs, "teach", mats), v_teach, llm_kw)
    if need_grade:
        g = _heal(out["grade"])
        store.append(d, {"role": "tutor", "kind": "grade", "qid": need_grade, "verdict": g["verdict"],
                         "feedback": str(g.get("feedback") or "")[:2000]})
    if out.get("_plan"):
        meta["plan"] = out["_plan"]
        store.append(d, {"role": "tutor", "kind": "plan", "plan": out["_plan"], "revised": True})
    cited = [mats[i - 1] for i in (_heal(out.get("sources")) or []) if isinstance(i, int) and 1 <= i <= len(mats)]
    _say(d, str(out.get("say") or ""), [{"kind": c["kind"], "title": c["title"], "where": c["where"]} for c in cited])
    if "finish" in out:
        fin = _heal(out["finish"])
        store.append(d, {"role": "tutor", "kind": "finish", "summary": str((fin or {}).get("summary") or "")[:3000]})
        meta["phase"] = "done"
        meta["pending"] = None
        store.save(d, meta)
        await _auto_cards(home, sid, llm_kw)
        meta = store.load(d)
    else:
        _post_question(d, meta, out["_q"])
    store.save(d, meta)


async def _auto_cards(home: str, sid: str, llm_kw: dict) -> None:
    """学完自动做复习卡 (第 2 步) + 把讲过的沉淀成要点 / 推理链 (第 3 步); 做不成不影响这一步,
    员工可以在界面上点「做成复习卡」「沉淀要点」再来。"""
    from . import learning_cards  # noqa: PLC0415
    try:
        await learning_cards.session_cards(home, sid, llm_kw)
    except Exception as e:  # noqa: BLE001
        logger.info("learning: %s 自动做复习卡失败: %s", sid, e)
    try:
        await ingest_session(home, sid, llm_kw)
    except Exception as e:  # noqa: BLE001
        logger.info("learning: %s 沉淀要点失败: %s", sid, e)


async def ingest_session(home: str, sid: str, llm_kw: dict) -> dict:
    """学习会话里小鲶讲过的 → 要点 / 推理链 (learning_graph)。只用讲解, 不用员工答了什么。"""
    from . import learning_graph  # noqa: PLC0415
    d = store.session_dir(home, sid)
    meta = store.load(d)
    text = "\n\n".join(ev["text"] for ev in store.events(d) if ev.get("kind") == "say" and ev.get("role") == "tutor")
    if not text.strip():
        raise ValueError("还没讲什么, 没东西可沉淀")
    r = await learning_graph.ingest(home, f"learn:{sid}", f"学习: {meta['goal']}", "learn", text, llm_kw)
    store.append(d, {"role": "tutor", "kind": "graph", **{k: r[k] for k in ("added", "linked", "chains")}})
    return r


async def _plan_step(d: Path, meta: dict, evs: list[dict], mats: list[dict], llm_kw: dict, revised: bool) -> None:
    def v_plan(o: dict) -> list[str]:
        plan, errs = check_plan(o.get("plan"))
        o["_plan"] = plan
        if not str(o.get("approach") or "").strip():
            errs.append("要写 approach (按什么顺序讲、为什么)")
        return errs
    out = await _call(build_messages(meta, evs, "plan", mats), v_plan, llm_kw)
    meta["plan"] = out["_plan"]
    store.append(d, {"role": "tutor", "kind": "plan", "plan": out["_plan"], "approach": str(out["approach"]).strip()[:3000],
                     "revised": revised})
    store.save(d, meta)


# ── RPC ────────────────────────────────────────────────────────

def _ctx(params: dict) -> tuple[str, dict]:
    home = str(params.get("catfish_home") or "").strip()
    if not home:
        raise ValueError("catfish_home 必填")
    token = str(params.get("auth_token") or "").strip()
    gw = str(params.get("gateway_url") or "").strip().rstrip("/")
    from .gateway_llm import resolve_model  # noqa: PLC0415
    return home, {"model": resolve_model(params.get("model") or None), "gateway_url": gw, "auth_token": token,
                  "source": SOURCE, "timeout_s": 180.0}


def _view(home: str, sid: str) -> dict:
    d = store.session_dir(home, sid)
    return store.public_view(store.load(d), store.events(d), sid in _RUNNING)


def _kick(home: str, sid: str, llm_kw: dict, runner=None) -> None:
    if not llm_kw.get("auth_token"):
        raise RuntimeError("没登录 (拿不到访问令牌), 登录后再学")
    if not llm_kw.get("gateway_url"):
        raise ValueError("gateway_url 必填")
    if sid in _RUNNING:
        raise ValueError("小鲶还在想上一步, 等它出来再说")
    _RUNNING.add(sid)

    async def go() -> None:
        try:
            await (runner or run_step)(home, sid, llm_kw)
        except Exception as e:  # noqa: BLE001 —— 任何失败都记下来给界面, 员工的输入已经存了
            logger.warning("learning: %s 这一步失败: %s", sid, e)
            try:
                d = store.session_dir(home, sid)
                meta = store.load(d)
                meta["last_error"] = str(e)[:500] if isinstance(e, (RuntimeError, ValueError)) else f"{type(e).__name__}: {e}"[:500]
                store.save(d, meta)
            except Exception:  # noqa: BLE001
                pass
        finally:
            _RUNNING.discard(sid)

    t = asyncio.get_running_loop().create_task(go())
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)


def grade_choice(q: dict, picked: list[str]) -> str:
    return "correct" if set(picked) == set(q.get("answer") or []) else "wrong"


async def handle(method: str, params: dict) -> dict:
    home, llm_kw = _ctx(params)
    if method == "learn/list":
        return {"sessions": store.list_all(home)}
    if method.startswith("learn/graph_"):
        from . import learning_graph_rpc  # noqa: PLC0415
        return await learning_graph_rpc.handle(method, params, home, llm_kw)
    if method.startswith("learn/review_"):
        from . import learning_cards  # noqa: PLC0415
        return learning_cards.review(method, params, home)
    if method == "learn/create":
        meta = store.create(home, str(params.get("goal") or ""), str(params.get("mode") or "quick"))
        _kick(home, meta["id"], llm_kw)
        return _view(home, meta["id"])
    sid = str(params.get("id") or "")
    if method == "learn/get":
        return _view(home, sid)
    if method == "learn/delete":
        if sid in _RUNNING:
            raise ValueError("小鲶还在想, 等这一步出来再删")
        store.delete(home, sid)
        from . import learning_graph  # noqa: PLC0415
        learning_graph.delete_source(home, f"learn:{sid}")   # 别的材料也提到的要点留着
        return {"ok": True}
    d = store.session_dir(home, sid)
    if sid in _RUNNING:
        raise ValueError("小鲶还在想上一步, 等它出来再说")
    meta = store.load(d)
    if method == "learn/answer":
        pend = (meta.get("pending") or {}).get("qid")
        qid = str(params.get("qid") or "")
        if not pend or qid != pend:
            raise ValueError("这道题已经答过了 (或者不是当前这道)")
        q = next(ev["q"] for ev in store.events(d) if ev.get("kind") == "question" and ev.get("qid") == qid)
        ev: dict[str, Any] = {"role": "learner", "kind": "answer", "qid": qid}
        conf = params.get("confidence")
        if isinstance(conf, (int, float)) and 0 <= conf <= 100:
            ev["confidence"] = int(conf)
        thinking = str(params.get("thinking") or "").strip()[:3000]
        if thinking:
            ev["thinking"] = thinking
        if params.get("dont_know") and q["type"] != "ask":
            ev.update(dont_know=True, verdict="dont_know")
        elif q["type"] == "recall":
            text = str(params.get("text") or "").strip()[:3000]
            if not text:
                raise ValueError("写一下你的答案 (不会可以点「我不知道」)")
            ev["text"] = text
        else:
            picked = [str(p) for p in params.get("picked") or [] if str(p)]
            text = str(params.get("text") or "").strip()[:1000]
            valid = {o["value"] for o in q.get("options") or []}
            if any(p not in valid for p in picked):
                raise ValueError("选项不对")
            if q["type"] == "choice":
                if not picked:
                    raise ValueError("选一个 (不会可以点「我不知道」)")
                if not q.get("multi") and len(picked) > 1:
                    raise ValueError("这题只能选一个")
                ev.update(picked=picked, verdict=grade_choice(q, picked))
            else:
                if not picked and not text:
                    raise ValueError("选一个或者写几句")
                ev["picked"] = picked
                if text:
                    ev["text"] = text
        store.append(d, ev)
        meta["pending"] = None
        store.save(d, meta)
        _kick(home, sid, llm_kw)
        return _view(home, sid)
    if method == "learn/say":
        if meta["phase"] == "drill":
            raise ValueError("答辩陪练时直接回答评审的问题 (可以🎤口头回答); 想跟小鲶学, 先点「回去接着学」")
        text = str(params.get("text") or "").strip()[:3000]
        start = bool(params.get("start"))
        if start:
            if meta["phase"] != "plan_review":
                raise ValueError("现在不在看路线图")
            text = text or "开始吧"
            meta["phase"] = "teach"
        if not text:
            raise ValueError("说点什么")
        pend = (meta.get("pending") or {}).get("qid")
        if pend:   # 有题没答就插话: 这题算跳过, 小鲶先回应再接着出题
            store.append(d, {"role": "learner", "kind": "answer", "qid": pend, "skipped": True})
            meta["pending"] = None
        store.append(d, {"role": "learner", "kind": "say", "text": text})
        store.save(d, meta)
        _kick(home, sid, llm_kw)
        return _view(home, sid)
    if method in ("learn/practice", "learn/next"):
        if not meta.get("plan"):
            raise ValueError("先学完摸底、有了路线图再练")
        from . import learning_practice as lp  # noqa: PLC0415
        if lp.active(meta):
            raise ValueError("这道题还没做完 (可以交答案、要提示或者点「不做了」)")
        if method == "learn/practice" and meta["phase"] != "practice":
            meta["practice_from"] = meta["phase"]
            meta["pending"] = None
            store.append(d, {"role": "learner", "kind": "say", "text": "开始练习"})
        meta["phase"] = "practice"
        store.save(d, meta)
        _kick(home, sid, llm_kw)
        return _view(home, sid)
    if method in ("learn/attempt", "learn/hint", "learn/give_up"):
        from . import learning_practice as lp  # noqa: PLC0415
        pid = lp.active(meta)
        if meta["phase"] != "practice" or not pid:
            raise ValueError("现在没有在做的练习题")
        if method == "learn/give_up":
            lp.give_up(d, meta)
            return _view(home, sid)
        if method == "learn/hint":
            if lp.last_rung(store.events(d), pid) >= len(lp.RUNGS):
                raise ValueError("提示已经给到最后一级了, 可以点「不做了」看标准解法")
            store.append(d, {"role": "learner", "kind": "hint_request", "pid": pid})
        else:
            text = str(params.get("text") or "").strip()[:4000]
            if not text:
                raise ValueError("写一下你的答案 (卡住了可以要提示)")
            ev: dict[str, Any] = {"role": "learner", "kind": "attempt", "pid": pid, "text": text}
            thinking = str(params.get("thinking") or "").strip()[:3000]
            if thinking:
                ev["thinking"] = thinking
            store.append(d, ev)
        _kick(home, sid, llm_kw)
        return _view(home, sid)
    if method == "learn/teach":
        if meta["phase"] not in ("practice", "drill"):
            raise ValueError("现在不在练习 / 答辩陪练")
        from . import learning_practice as lp  # noqa: PLC0415
        if meta["phase"] == "practice" and lp.active(meta):
            lp.give_up(d, meta)
            meta = store.load(d)
        came = meta.get("practice_from") if meta["phase"] == "practice" else meta.get("drill_from")
        if not meta.get("plan"):   # 直接开的答辩陪练没有路线图: 回去从摸底开始学
            meta["phase"] = "probe"
        else:
            meta["phase"] = "done" if came == "done" else "teach"
        store.append(d, {"role": "learner", "kind": "say", "text": "回去接着学"})
        store.save(d, meta)
        if meta["phase"] == "done":
            return _view(home, sid)
        _kick(home, sid, llm_kw)
        return _view(home, sid)
    if method.startswith("learn/drill"):
        from . import learning_drill as dr  # noqa: PLC0415
        if method == "learn/drill":
            if meta["phase"] != "drill":
                meta["drill_from"] = meta["phase"]
                meta["phase"] = "drill"
                meta["pending"] = None
                store.append(d, {"role": "learner", "kind": "say", "text": "开始答辩陪练"})
                store.save(d, meta)
            if (meta.get("drill") or {}).get("questions") and not meta["drill"].get("done"):
                return _view(home, sid)   # 题库已经有了, 接着答
            if (meta.get("drill") or {}).get("done"):
                meta["drill"] = None      # 上一轮问完了: 再来一轮, 重新出题
                store.save(d, meta)
            _kick(home, sid, llm_kw)
            return _view(home, sid)
        if meta["phase"] != "drill" or not (meta.get("drill") or {}).get("questions"):
            raise ValueError("现在不在答辩陪练")
        if method == "learn/drill_answer":
            dr.answer(d, meta, str(params.get("text") or ""), params.get("secs"))
            _kick(home, sid, llm_kw)
        elif method == "learn/drill_skip":
            dr.skip(d, meta)
        elif method == "learn/drill_next":
            res = dr.next_question(d, meta)
            if res and res["cards"]:
                from . import learning_cards, practice  # noqa: PLC0415
                con = learning_cards._practice_con(home)
                try:
                    practice.add_cards(con, [{**c, "source": f"答辩陪练: {meta['goal'][:60]}"} for c in res["cards"]],
                                       f"learn:{sid}", [meta["goal"][:20]])
                finally:
                    con.close()
        else:
            raise ValueError(f"不认识的操作: {method}")
        return _view(home, sid)
    if method == "learn/ingest":
        async def ing(h: str, s_: str, kw: dict) -> None:
            await ingest_session(h, s_, kw)
        _kick(home, sid, llm_kw, runner=ing)
        return _view(home, sid)
    if method == "learn/cards":
        from . import learning_cards  # noqa: PLC0415

        async def cards(h: str, s_: str, kw: dict) -> None:
            await learning_cards.session_cards(h, s_, kw)
        _kick(home, sid, llm_kw, runner=cards)
        return _view(home, sid)
    if method == "learn/retry":
        if meta.get("pending"):
            raise ValueError("有题等你答, 不用重试")
        _kick(home, sid, llm_kw)
        return _view(home, sid)
    raise ValueError(f"不认识的学习操作: {method}")
