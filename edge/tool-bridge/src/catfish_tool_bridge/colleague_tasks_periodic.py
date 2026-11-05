"""派任务给同事 · 定时 / 周期任务在收件人这边的生命周期 (11/4, docs/COLLEAGUE-TASK-PLAN.md §2)。

  接下 (accept_scheduled)   在本机建 hermes 定时任务; 选了「每期直接交回」就记 auto_return
  每期结果 (record_period)  小鲶到点做完调 catfish_form task_period 存这一期; auto_return 就马上交回, 否则「待核对」
                            有要人确认才能做的步骤 (skipped) → 「待确认」, 不交回; 告诉发起人「等对方确认 (还差 N 步)」;
                            本人回来点「我在了, 接着做」, 小鲶带 period 再调一次 task_period 补完 (11/5)
  核对交回 (submit_period)  本人在「协同」页点交回 (可以先改)
  退出 (quit)               删本机定时任务, 告诉发起人
  对方改了 (reset_for_change) 停掉定时任务, 等重新同意
  同步时 (maintain)          一次性任务错过了补跑一次 / 做满了收尾 / 定时任务没了 / 有要核对的 → 给界面发事件

时间判断都在 colleague_tasks_schedule.py。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import colleague_tasks as ct
from . import colleague_tasks_schedule as sched
from . import forms


# ── 发起人看到的状态 ─────────────────────────────────────────────

def owner_state(task: dict[str, Any], r: dict[str, Any] | None) -> str:
    status = (r or {}).get("status")
    if status == "declined":
        return "不接"
    if status == "quit":
        return "已退出"
    if task["status"] == "cancelled":
        return "已取消"
    if r is None or status is None:
        return "等回应"
    if int(r.get("rev") or 1) < int(task.get("rev") or 1):
        return "等重新同意"
    if any((x or {}).get("waiting") for x in (r.get("periods") or {}).values()):
        return "等对方确认"
    n = len([x for x in (r.get("periods") or {}).values() if (x or {}).get("result")])
    if sched.ended({"task": {"schedule": task["schedule"]}, "periods": [None] * n}):
        return "已完成" if task["schedule"]["kind"] == "once" else "已结束"
    return "进行中" if n else "已安排"


# ── 收件人 ───────────────────────────────────────────────────────

def incoming_state(rec: dict[str, Any]) -> str:
    if rec.get("status") == "cancelled":
        return "已取消"
    if rec.get("quit"):
        return "已退出"
    if rec.get("declined"):
        return "不接"
    if not rec.get("accepted_at"):
        return "要重新同意" if rec.get("changed_at") else "待处理"
    if any(p.get("state") == "待确认" for p in rec.get("periods") or []):
        return "待确认"
    if any(p.get("state") == "待核对" for p in rec.get("periods") or []):
        return "待核对"
    if rec.get("ended_at") or sched.ended(rec):
        return "已结束"
    if rec.get("missed"):
        return "没做成"
    if rec.get("job_lost"):
        return "定时没了"
    return "进行中" if rec.get("periods") else "已安排"


def _save(rec: dict[str, Any]) -> None:
    forms._write(ct._in_path(rec["task_id"]), rec)


def _soon() -> str:
    return (datetime.now() + timedelta(minutes=1)).replace(second=0, microsecond=0).isoformat(timespec="minutes")


def accept_scheduled(rec: dict[str, Any], *, auto_return: bool) -> dict[str, Any]:
    if sched.ended(rec):
        raise ValueError("这个任务已经做完了")
    notify = not rec.get("accepted_at") or bool(rec.pop("declined", None))
    if not sched.job_exists(rec.get("job_id")):
        overdue = sched.overdue_once(rec)
        rec["job_id"] = sched.create_job(rec, when=_soon() if overdue else None)
    rec["auto_return"] = auto_return
    for k in ("job_lost", "missed", "changed_at", "ended_at"):
        rec.pop(k, None)
    if notify:
        at = ct._now()
        ct._reply(rec, {"status": "accepted", "at": at})
        rec["accepted_at"] = at
    _save(rec)
    return {"ok": True, "accepted_at": rec["accepted_at"], "job_id": rec["job_id"],
            "next_run": sched.next_run(rec["job_id"])}


def reset_for_change(rec: dict[str, Any]) -> None:
    """发起人改了任务 / 请他重做: 停掉本机定时任务, 等员工重新接下 (交回过的几期留着; 马上做的任务上次交回的清掉, 草稿留着)。"""
    sched.remove_job(rec.pop("job_id", None))
    if rec.get("accepted_at"):
        rec["changed_at"] = ct._now()
    if rec.get("done") and not rec.get("draft"):   # 重做: 上次交回的当草稿留着, 在上面改
        rec["draft"] = {"result": rec["done"]["result"], "updated_at": rec["done"]["at"]}
    for k in ("accepted_at", "job_lost", "missed", "catchups", "ended_at", "declined", "done"):
        rec.pop(k, None)


def _send_period(rec: dict[str, Any], p: dict[str, Any]) -> None:
    ct._reply(rec, {"status": "period", "period": p["period"], "result": p["result"], "at": p["at"],
                    "late_minutes": p.get("late_minutes") or 0})
    p["state"], p["sent_at"] = "已交回", ct._now()


def _finish_if_ended(rec: dict[str, Any]) -> None:
    if sched.ended(rec):
        sched.remove_job(rec.pop("job_id", None))
        rec.setdefault("ended_at", ct._now())


MAX_SKIPPED = 10


def _norm_skipped(raw: Any) -> list[str]:
    items = raw if isinstance(raw, list) else ([raw] if raw else [])
    return [str(x).strip()[:200] for x in items if str(x or "").strip()][:MAX_SKIPPED]


def record_period(task_id: str, result: Any, skipped: Any = None, period: Any = None) -> dict[str, Any]:
    """小鲶做完一期调 (catfish_form task_period)。立即任务没有「期」, 当草稿存。

    skipped: 要人确认才能做、这次没做的步骤 → 这一期「待确认」, 不交回, 等本人回来接着做。
    period:  接着做时带上 —— 更新那一期 (结果换成补完后的, skipped 换成还剩下的), 不新开一期。
    """
    rec = ct.load_incoming(task_id)
    if not rec["task"].get("schedule"):
        return ct.save_draft(task_id, result)
    if rec.get("status") == "cancelled" or rec.get("quit") or not rec.get("accepted_at"):
        raise ValueError("这个任务已经停了 (对方取消 / 你退出 / 还没接下), 不用再做")
    text = str(result or "").strip()
    if not text:
        raise ValueError("结果是空的")
    if len(text) > ct.RESULT_MAX:
        raise ValueError(f"结果太长 ({len(text)} 字), 最多 {ct.RESULT_MAX} 字: 精简一下")
    steps = _norm_skipped(skipped)
    periods = rec.setdefault("periods", [])
    if period not in (None, ""):
        p = next((x for x in periods if x.get("period") == int(period)), None)
        if not p:
            raise ValueError(f"没有第 {period} 次")
        p.update(result=text, continued_at=ct._now())
    else:
        if sched.ended(rec):
            raise ValueError("这个任务的次数已经做满了")
        p = {"period": len(periods) + 1, "result": text, "at": ct._now(),
             "late_minutes": sched.late_minutes(rec["task"]["schedule"], last_at=periods[-1]["at"] if periods else None)}
        periods.append(p)
    p["skipped"], p["notified"] = steps, False
    if steps:
        p["state"] = "待确认"
        ct._reply(rec, {"status": "waiting", "period": p["period"], "waiting_steps": len(steps), "at": ct._now()})
    else:
        p["state"] = "待核对"
        if rec.get("auto_return"):
            _send_period(rec, p)
            p["notified"] = True
    rec.pop("missed", None)
    _finish_if_ended(rec)
    _save(rec)
    return {"ok": True, "period": p["period"], "state": p["state"], "late_minutes": p.get("late_minutes", 0),
            "skipped": steps}


def submit_period(task_id: str, period: Any, result: Any) -> dict[str, Any]:
    """本人核对后交回某一期 (可以先改; 交回过的改了再交覆盖)。"""
    rec = ct.load_incoming(task_id)
    try:
        n = int(period)
    except (TypeError, ValueError):
        raise ValueError("第几期没写对") from None
    p = next((x for x in rec.get("periods") or [] if x.get("period") == n), None)
    if not p:
        raise ValueError(f"没有第 {n} 期")
    text = str(result if result is not None else p["result"]).strip()
    if not text or len(text) > ct.RESULT_MAX:
        raise ValueError(f"结果不能空, 最多 {ct.RESULT_MAX} 字")
    p["result"] = text
    _send_period(rec, p)
    p["notified"] = True
    _save(rec)
    return {"ok": True, "period": n}


def quit(task_id: str, reason: str) -> dict[str, Any]:
    rec = ct.load_incoming(task_id)
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("退出也说一句原因, 发起人好安排")
    sched.remove_job(rec.pop("job_id", None))
    at = ct._now()
    if rec.get("status") != "cancelled":
        ct._reply(rec, {"status": "quit", "reason": reason[:300], "at": at})
    rec["quit"] = {"reason": reason[:300], "at": at}
    _save(rec)
    return {"ok": True}


def _event(rec: dict[str, Any], kind: str, period: int | None = None) -> dict[str, Any]:
    return {"kind": kind, "task_id": rec["task_id"], "from": rec["owner"], "title": rec["task"]["title"], "period": period}


def _maintain_one(rec: dict[str, Any], events: list[dict[str, Any]]) -> bool:
    """返回改没改 rec。"""
    changed = False
    stopped = rec.get("status") == "cancelled" or rec.get("quit") or rec.get("declined") or not rec.get("accepted_at")
    if stopped or sched.ended(rec):
        if rec.get("job_id"):
            sched.remove_job(rec.pop("job_id"))
            changed = True
        if not stopped and not rec.get("ended_at"):
            rec["ended_at"] = ct._now()
            changed = True
    elif rec["task"]["schedule"]["kind"] == "once":
        if sched.overdue_once(rec) and not sched.job_exists(rec.get("job_id")):
            if not rec.get("catchups"):
                rec["job_id"], rec["catchups"] = sched.create_job(rec, when=_soon()), 1
                changed = True
            elif not rec.get("missed"):
                rec["missed"] = True
                events.append(_event(rec, "missed"))
                changed = True
    elif not sched.job_exists(rec.get("job_id")) and not rec.get("job_lost"):
        rec["job_lost"] = True
        events.append(_event(rec, "job_lost"))
        changed = True
    for p in rec.get("periods") or []:
        if p.get("state") in ("待核对", "待确认") and not p.get("notified"):
            p["notified"] = True
            events.append(_event(rec, "review" if p["state"] == "待核对" else "confirm_needed", p["period"]))
            changed = True
    return changed


def maintain() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in sorted((ct.tasks_dir() / "incoming").glob("*.json")):
        rec = forms._read(Path(path))
        if not rec or not (rec.get("task") or {}).get("schedule"):
            continue
        if _maintain_one(rec, events):
            _save(rec)
    return events
