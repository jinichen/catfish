"""学习: 校验和修正模型的结构化输出 (11/18 从 learning.py 拆出来)。

每条不合格都要说清楚错在哪 (原样回给模型让它改); 模型常见的写法 (数组写成字符串、选项写成纯文字列表、答案写成选项原文) 先修再校验。
SOFT 开头的是软规则: 前几次照样退回, 最后一次只剩软规则就收下 (learning._call)。
"""
from __future__ import annotations

import json
import re
from typing import Any

_SLUG = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
#: 软规则 (例 别出重复的题): 前几次照样退回让它改, 最后一次只剩软规则没满足就收下 —— 别为这个让整步失败
SOFT = "~"


def _heal(v: Any) -> Any:
    """模型把数组 / 对象写成 JSON 字符串时还原。"""
    if isinstance(v, str) and v.strip()[:1] in "[{":
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def check_question(q: Any, phase: str, plan: dict | None) -> tuple[dict | None, list[str]]:
    if not isinstance(q, dict):
        return None, ["question 必须是对象"]
    q = {k: _heal(v) for k, v in q.items()}
    errs: list[str] = []
    qtype = q.get("type")
    if qtype not in ("choice", "recall", "ask"):
        errs.append("question.type 只能是 choice / recall / ask")
    text = str(q.get("question") or "").strip()
    if not text:
        errs.append("question.question 不能空")
    out: dict[str, Any] = {"type": qtype, "question": text[:2000]}
    if qtype in ("choice", "ask"):
        opts = q.get("options") or []
        if not isinstance(opts, list):
            opts = []
        if opts and all(isinstance(o, str) for o in opts):   # ["1 个", "3 个"] —— 模型常这么写, 按顺序编 a b c
            opts = [{"value": "abcdefgh"[i], "label": o} for i, o in enumerate(opts[:8])]
        opts = [{"value": str(o.get("value") or o.get("id") or o.get("key") or "abcdefgh"[i]), "label": o.get("label") or o.get("text")}
                if isinstance(o, dict) else o for i, o in enumerate(opts)]
        clean, seen = [], set()
        for o in opts:
            if isinstance(o, dict) and str(o.get("value") or "").strip() and str(o.get("label") or "").strip():
                v = str(o["value"]).strip()[:20]
                if v in seen:
                    errs.append(f"选项 value 重复: {v}")
                seen.add(v)
                clean.append({"value": v, "label": str(o["label"]).strip()[:500]})
        if qtype == "choice" and not 2 <= len(clean) <= 6:
            errs.append("choice 要 2–6 个选项, 每个有 value 和 label")
        out["options"] = clean
        out["multi"] = bool(q.get("multi")) if qtype == "choice" else False
    if qtype == "choice":
        ans = q.get("answer")
        ans = [ans] if isinstance(ans, (str, int)) else ans
        by_label = {o["label"]: o["value"] for o in clean}
        values = [o["value"] for o in clean]
        fixed = []
        for a in ans or []:   # 写成选项原文 / 序号的也认
            a = by_label.get(str(a).strip(), a)
            if isinstance(a, int) and not isinstance(a, bool) and 0 <= a < len(values) and str(a) not in seen:
                a = values[a]
            fixed.append(a)
        ans = [str(a).strip() for a in fixed if str(a).strip()]
        if not ans or any(a not in seen for a in ans):
            errs.append("choice 的 answer 要是选项 value 的列表, 例 [\"a\"]")
        elif not out["multi"] and len(ans) != 1:
            errs.append("单选题 answer 只能有一个; 多选要写 \"multi\": true")
        if not str(q.get("explanation") or "").strip():
            errs.append("choice 要写 explanation (为什么对)")
        out["answer"] = ans
        out["explanation"] = str(q.get("explanation") or "").strip()[:2000]
    if qtype == "recall":
        if not str(q.get("answer") or "").strip():
            errs.append("recall 要写 answer (完整标准答案)")
        out["answer"] = str(q.get("answer") or "").strip()[:3000]
    if phase == "probe":
        out["role"] = "ask" if qtype == "ask" else "probe"
    elif qtype == "ask":
        out["role"] = "ask"
        if q.get("node") and plan and q["node"] in {n["id"] for n in plan["nodes"]}:
            out["node"] = q["node"]
    else:
        role = q.get("role")
        if role not in ("predict", "check"):
            errs.append("讲解时的 choice / recall 要写 role: predict (讲之前猜) 或 check (讲完检查)")
        out["role"] = role
        ids = {n["id"] for n in (plan or {}).get("nodes") or []}
        if q.get("node") not in ids:
            errs.append(f"question.node 必须是路线图里的节点 id, 现有: {', '.join(sorted(ids)) or '(还没有路线图)'}")
        out["node"] = q.get("node")
    return (None if errs else out), errs


def check_plan(p: Any) -> tuple[dict | None, list[str]]:
    p = _heal(p)
    if not isinstance(p, dict):
        return None, ["plan 必须是 {nodes, edges}"]
    nodes_in = _heal(p.get("nodes")) or []
    edges_in = _heal(p.get("edges")) or []
    errs: list[str] = []
    nodes, ids = [], []
    for n in nodes_in if isinstance(nodes_in, list) else []:
        nid = str((n or {}).get("id") or "").strip() if isinstance(n, dict) else ""
        title = str((n or {}).get("title") or "").strip() if isinstance(n, dict) else ""
        if not _SLUG.match(nid):
            errs.append(f"节点 id「{nid}」不合格: 只能小写英文字母 / 数字, 用 - 或 _ 连 (例 cert-levels)")
            continue
        if nid in ids:
            errs.append(f"节点 id 重复: {nid}")
            continue
        ids.append(nid)
        nodes.append({"id": nid, "title": (title or nid)[:40]})
    if not 2 <= len(nodes) <= 40:
        errs.append("路线图要 2–40 个节点")
    edges = []
    for e in edges_in if isinstance(edges_in, list) else []:
        if isinstance(e, dict):
            e = [e.get("from"), e.get("to")]
        if not (isinstance(e, list) and len(e) == 2):
            errs.append(f"边要写成 [前置, 后续]: {e}")
            continue
        a, b = str(e[0]), str(e[1])
        if a not in ids or b not in ids:
            errs.append(f"边 [{a}, {b}] 里有不存在的节点")
        elif a == b:
            errs.append(f"边 [{a}, {b}] 自己连自己")
        elif [a, b] not in edges:
            edges.append([a, b])
    if not errs and _has_cycle(ids, edges):
        errs.append("路线图里有环 (A 要先懂 B, B 又要先懂 A), 去掉一条边")
    return (None if errs else {"nodes": nodes, "edges": edges}), errs


def _has_cycle(ids: list[str], edges: list[list[str]]) -> bool:
    indeg = {i: 0 for i in ids}
    nxt: dict[str, list[str]] = {i: [] for i in ids}
    for a, b in edges:
        indeg[b] += 1
        nxt[a].append(b)
    queue = [i for i in ids if indeg[i] == 0]
    seen = 0
    while queue:
        x = queue.pop()
        seen += 1
        for y in nxt[x]:
            indeg[y] -= 1
            if indeg[y] == 0:
                queue.append(y)
    return seen != len(ids)


def topo_order(plan: dict) -> list[str]:
    ids = [n["id"] for n in plan["nodes"]]
    indeg = {i: 0 for i in ids}
    for _, b in plan["edges"]:
        indeg[b] += 1
    order, queue = [], [i for i in ids if indeg[i] == 0]
    while queue:
        x = queue.pop(0)
        order.append(x)
        for a, b in plan["edges"]:
            if a == x:
                indeg[b] -= 1
                if indeg[b] == 0:
                    queue.append(b)
    return order


def _norm(t: str) -> str:
    return re.sub(r"[\s'\"‘’“”「」，,。.？?！!：:]", "", t or "")


def asked_before(q: dict | None, evs: list[dict]) -> bool:
    """题干跟之前出过的一样 (去掉标点空白比)。"""
    if not q:
        return False
    t = _norm(q.get("question", ""))
    return any(ev.get("kind") == "question" and _norm((ev.get("q") or {}).get("question", "")) == t for ev in evs)


def probe_count(evs: list[dict]) -> int:
    return sum(1 for ev in evs if ev.get("kind") == "question" and (ev.get("q") or {}).get("role") == "probe")


def json_obj(raw: str) -> dict:
    raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("没找到 JSON 对象")
    obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise ValueError("要的是一个 JSON 对象")
    return obj



def shuffle_options(q: dict, seed: str) -> dict:
    """选择题选项打乱、重新编 a b c (11/18 实测: 模型八成把对的放第一个, 员工几题下来就知道「选 a」)。
    同一道题 (seed = qid) 打乱结果固定; 标准答案跟着换编号。"""
    if q.get("type") != "choice" or len(q.get("options") or []) < 2:
        return q
    import random  # noqa: PLC0415
    opts = list(q["options"])
    random.Random(seed).shuffle(opts)
    letters = "abcdefgh"
    remap = {o["value"]: letters[i] for i, o in enumerate(opts)}
    return {**q, "options": [{"value": letters[i], "label": o["label"]} for i, o in enumerate(opts)],
            "answer": sorted(remap[a] for a in q.get("answer") or [])}


