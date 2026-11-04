"""派任务给同事 · 第三步: 多阶段 (11/4, docs/COLLEAGUE-TASK-PLAN.md §3)。

一个流程 = 几段, 按顺序做; 每一段派给一位或几位同事 (几位就是并行, 都交回或发起人不等了才算这一段完)。
**发起人电脑来协调**, 中央不存任何一段的内容:
  · 每一段就是一个普通的「马上做」任务 (colleague_tasks.create, 带 pipeline_id), 同事那边跟平常一样接下 / 交回
  · 这一段的结果先回到发起人; 发起人核对、可以改, 点「放行到下一段」才作为 context 交给下一段的人
    —— 甲的结果不会在发起人不知情时流到乙手里
  · 某一段有人不接 / 做得不行: 在那一段里加人或「请他重做」, 只动这一段
  · 最后一段交回后「完成流程」, 让小鲶把各段结果汇总 (全在本机)

本机账本: ~/.catfish/collab_tasks/pipelines/<pipeline_id>.json
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime
from typing import Any

from . import colleague_tasks as ct
from . import forms

MAX_STAGES = 8
_ID = re.compile(r"p_[0-9]{8}_[0-9a-f]{6,16}")
DONE_STATES = {"已完成", "逾期完成"}
TERMINAL = DONE_STATES | {"不接", "已取消"}


def _dir():
    d = ct.tasks_dir() / "pipelines"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(pid: Any):
    pid = str(pid or "").strip()
    if not _ID.fullmatch(pid):
        raise ValueError(f"流程编号不对: {pid!r}")
    return _dir() / f"{pid}.json"


def load(pid: str) -> dict[str, Any]:
    p = forms._read(_path(pid))
    if not p:
        raise LookupError(f"没有这个流程: {pid}")
    return p


def _save(p: dict[str, Any]) -> None:
    forms._write(_path(p["pipeline_id"]), p)


def _norm_stages(raw: Any, owner: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) < 2:
        raise ValueError("多阶段至少要两段 (只有一段就用普通派任务)")
    if len(raw) > MAX_STAGES:
        raise ValueError(f"最多 {MAX_STAGES} 段")
    out = []
    for i, st in enumerate(raw, 1):
        st = st if isinstance(st, dict) else {}
        text = str(st.get("text") or "").strip()
        if not text:
            raise ValueError(f"第 {i} 段要写做什么")
        if len(text) > ct.TEXT_MAX:
            raise ValueError(f"第 {i} 段说明太长, 最多 {ct.TEXT_MAX} 字")
        people = forms._norm_recipients(st.get("recipients"), owner)
        if not people:
            raise ValueError(f"第 {i} 段要选谁来做")
        if len(people) > ct.MAX_RECIPIENTS:
            raise ValueError(f"第 {i} 段最多 {ct.MAX_RECIPIENTS} 人")
        out.append({"name": str(st.get("name") or "").strip()[:30] or f"第 {i} 段", "text": text,
                    "recipients": people, "deadline": ct.normalize_deadline(st.get("deadline")),
                    "task_id": None, "input": None, "started_at": None, "released_at": None})
    return out


def _start(p: dict[str, Any], k: int, handoff: str | None) -> dict[str, Any]:
    st = p["stages"][k]
    task = ct.create({"title": f"{p['title']} · {st['name']}", "text": st["text"], "deadline": st["deadline"],
                      "recipients": st["recipients"], "context": handoff, "pipeline_id": p["pipeline_id"]})["task"]
    st.update(task_id=task["task_id"], input=handoff, started_at=ct._now())
    p["current"] = k
    _save(p)   # 先记下段落 → 任务, 发送失败也能在那一段里重发
    return ct.send(task["task_id"])


def create(args: dict[str, Any]) -> dict[str, Any]:
    owner = ct.forms_net.me()
    title = str(args.get("title") or "").strip()[:40]
    if not title:
        raise ValueError("给流程起个名字")
    pid = f"p_{datetime.now():%Y%m%d}_{secrets.token_hex(4)}"
    p = {"pipeline_id": pid, "title": title, "owner": owner, "created_at": ct._now(), "status": "open",
         "stages": _norm_stages(args.get("stages"), owner), "current": 0, "notified": []}
    sent = _start(p, 0, None)
    return {"ok": True, "pipeline": load(pid), "sent": sent.get("sent", []), "failed": sent.get("failed", [])}


def _stage_progress(st: dict[str, Any]) -> dict[str, Any] | None:
    if not st.get("task_id"):
        return None
    try:
        return ct.progress(ct.load_outgoing(st["task_id"]))
    except LookupError:
        return None


def _finished(prog: dict[str, Any] | None) -> bool:
    rows = (prog or {}).get("rows") or []
    return bool(rows) and all(r["state"] in TERMINAL for r in rows)


def handoff_text(prog: dict[str, Any] | None) -> str:
    """默认交给下一段的: 这一段每个人交回的结果, 标上是谁。发起人可以改。"""
    parts = [f"【{r['email']}】\n{r['result']}" for r in (prog or {}).get("rows") or []
             if r["state"] in DONE_STATES and r.get("result")]
    return "\n\n".join(parts)


def release(pid: str, text: Any) -> dict[str, Any]:
    """放行到下一段 (最后一段就是完成流程)。这一段还没交回的人会收到取消通知。"""
    p = load(pid)
    if p["status"] != "open":
        raise ValueError("这个流程已经结束了")
    k = p["current"]
    st = p["stages"][k]
    prog = _stage_progress(st)
    handoff = str(text or "").strip()
    last = k == len(p["stages"]) - 1
    if not last:
        if not handoff:
            raise ValueError("交给下一段的内容是空的: 至少写一句上一段做成了什么")
        if len(handoff) > ct.RESULT_MAX:
            raise ValueError(f"交给下一段的内容太长 ({len(handoff)} 字), 最多 {ct.RESULT_MAX} 字: 精简一下")
    cancelled = []
    if st.get("task_id") and not _finished(prog):
        cancelled = [r["email"] for r in prog["rows"] if r["state"] not in TERMINAL] if prog else []
        ct.cancel(st["task_id"])
    st["released_at"] = ct._now()
    if last:
        p["status"], p["done_at"] = "done", ct._now()
        _save(p)
        return {"ok": True, "done": True, "cancelled": cancelled}
    sent = _start(p, k + 1, handoff)
    return {"ok": True, "done": False, "stage": k + 2, "sent": sent.get("sent", []), "failed": sent.get("failed", []),
            "cancelled": cancelled}


def cancel(pid: str) -> dict[str, Any]:
    p = load(pid)
    if p["status"] != "open":
        return {"ok": True, "note": "已经结束了"}
    st = p["stages"][p["current"]]
    if st.get("task_id"):
        ct.cancel(st["task_id"])
    p["status"], p["cancelled_at"] = "cancelled", ct._now()
    _save(p)
    return {"ok": True}


def get(pid: str) -> dict[str, Any]:
    p = load(pid)
    stages = []
    for i, st in enumerate(p["stages"]):
        prog = _stage_progress(st)
        stages.append({**st, "index": i, "progress": prog, "finished": _finished(prog)})
    cur = stages[p["current"]]
    return {"ok": True, "pipeline": {**p, "stages": stages},
            "handoff": handoff_text(cur["progress"]) if p["status"] == "open" else ""}


def list_pipelines() -> list[dict[str, Any]]:
    out = []
    for path in sorted(_dir().glob("*.json")):
        p = forms._read(path)
        if not p:
            continue
        st = p["stages"][p["current"]]
        prog = _stage_progress(st)
        out.append({"pipeline_id": p["pipeline_id"], "title": p["title"], "status": p["status"],
                    "created_at": p.get("created_at"), "stages": len(p["stages"]), "current": p["current"] + 1,
                    "current_name": st["name"], "progress": (prog or {}).get("counts") or {},
                    "waiting_release": p["status"] == "open" and _finished(prog)})
    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return out


def maintain() -> list[dict[str, Any]]:
    """这一段都回了 (交回 / 不接) → 提醒发起人去放行 (每段只提醒一次)。"""
    events = []
    for path in sorted(_dir().glob("*.json")):
        p = forms._read(path)
        if not p or p["status"] != "open" or p["current"] in p.get("notified", []):
            continue
        st = p["stages"][p["current"]]
        if _finished(_stage_progress(st)):
            p.setdefault("notified", []).append(p["current"])
            _save(p)
            events.append({"kind": "stage_done", "task_id": st["task_id"], "from": "", "title": p["title"],
                           "period": p["current"] + 1})
    return events


def pim_handlers(params: dict[str, Any]) -> dict[str, Any]:
    pid = str(params.get("pipeline_id") or "")
    return {
        "pim/ctask_pipeline_create": lambda: create(params),
        "pim/ctask_pipeline_get": lambda: get(pid),
        "pim/ctask_pipeline_release": lambda: release(pid, params.get("text")),
        "pim/ctask_pipeline_cancel": lambda: cancel(pid),
    }
