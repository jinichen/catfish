"""请同事帮忙 · 派任务给多位同事 (11/4, docs/COLLEAGUE-TASK-PLAN.md 第一步: 立即完成的任务)。

发起人写一张任务单 (要做什么 + 截止 + 发给谁) → 每位同事各自决定接不接 → 接下后在**同事自己的电脑上**
由他的小鲶 (或他本人) 做 → 他核对结果后点「交回」→ 结果只回到发起人这台电脑汇总。
中央只当邮筒 (跟一起填表同一个, 取走即清空); 工具审批 / 对外动作的确认照旧在同事那边弹。

本机账本 (`~/.catfish/collab_tasks/`):
  outgoing/<task_id>/task.json             我发出的: 任务 + 收件人 + 发 / 催时间
  outgoing/<task_id>/replies/<邮箱>.json    同事的回复 (接下 → 交回 / 不接, 后到覆盖, 接下时间保留)
  incoming/<task_id>.json                  别人派给我的: 任务 + 我接没接 + 结果草稿 + 交回了什么

邮筒里的信封 (JSON, 中央看不懂也不看):
  task_invite  {v, task_id, rev, task: {title, text, deadline, schedule}}   —— 不含别的收件人, 不含别人的结果
  task_reply   {v, task_id, rev, status: accepted|done|declined|period|quit, result?, reason?, period?, late_minutes?, at}

第二步 (定时 / 周期, schedule 不空): 接下 = 在同事电脑上建 hermes 定时任务; 每期结果 period 交回;
发起人改了任务 (rev+1) 同事要重新同意; 同事可以退出 (quit)。细节在 colleague_tasks_periodic.py / _schedule.py。
第三步 (多阶段): 每一段就是一个普通任务 (带 pipeline_id 和 context = 发起人放行的上一段结果), 流程在
colleague_tasks_pipeline.py; 「请他重做」= 只给这一个人发 rev+1 的任务单 (redo_note 写要改什么)。
  task_remind  {v, task_id, text?}
  task_cancel  {v, task_id}
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from . import colleague_tasks_schedule as sched
from . import forms, forms_net, wiki_files

ENVELOPE_VERSION = 1
MAX_RECIPIENTS = 20
TEXT_MAX = 2000
RESULT_MAX = 15000   # 邮筒单封 64KB: 中文一个字 3 字节, 留 JSON 余量
_ID = re.compile(r"t_[0-9]{8}_[0-9a-f]{6,16}")


# ── 本机账本 ─────────────────────────────────────────────────────

def tasks_dir() -> Path:
    d = wiki_files._catfish_home() / "collab_tasks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_id(task_id: Any) -> str:
    tid = str(task_id or "").strip()
    if not _ID.fullmatch(tid):
        raise ValueError(f"任务编号不对: {task_id!r}")
    return tid


def _out_dir(task_id: str) -> Path:
    return tasks_dir() / "outgoing" / _safe_id(task_id)


def _in_path(task_id: str) -> Path:
    return tasks_dir() / "incoming" / f"{_safe_id(task_id)}.json"


def _reply_path(task_id: str, email: str) -> Path:
    return _out_dir(task_id) / "replies" / f"{re.sub(r'[^0-9A-Za-z@._-]', '_', email)}.json"


def load_outgoing(task_id: str) -> dict[str, Any]:
    task = forms._read(_out_dir(task_id) / "task.json")
    if not task:
        raise LookupError(f"没有这个我发出的任务: {task_id}")
    return task


def load_incoming(task_id: str) -> dict[str, Any]:
    rec = forms._read(_in_path(task_id))
    if not rec:
        raise LookupError(f"没有这个收到的任务: {task_id}")
    return rec


def _save_outgoing(task: dict[str, Any]) -> None:
    forms._write(_out_dir(task["task_id"]) / "task.json", task)


def replies_of(task_id: str) -> dict[str, dict[str, Any]]:
    d = _out_dir(task_id) / "replies"
    out = {}
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        r = forms._read(p)
        if r and r.get("from"):
            out[r["from"]] = r
    return out


# ── 截止 ─────────────────────────────────────────────────────────

def normalize_deadline(raw: Any) -> str | None:
    """截止: 「2026-11-05」或「2026-11-05T18:00」(本机时间)。空 = 不限。"""
    s = str(raw or "").strip()
    if not s:
        return None
    if len(s) == 10:
        return date.fromisoformat(s).isoformat()
    try:
        return datetime.fromisoformat(s).replace(second=0, microsecond=0, tzinfo=None).isoformat(timespec="minutes")
    except ValueError:
        raise ValueError(f"截止时间格式不对: {raw!r} (要 年-月-日 或 年-月-日T时:分)") from None


def _deadline_at(deadline: str | None) -> datetime | None:
    if not deadline:
        return None
    try:
        if len(deadline) == 10:
            return datetime.combine(date.fromisoformat(deadline), time(23, 59, 59)).astimezone()
        return datetime.fromisoformat(deadline).astimezone()
    except ValueError:
        return None


def is_late(deadline: str | None, at: str | None = None) -> bool:
    """at 晚于截止 (at 不给 = 现在)。"""
    end = _deadline_at(deadline)
    if end is None:
        return False
    try:
        when = datetime.fromisoformat(at).astimezone() if at else datetime.now().astimezone()
    except ValueError:
        return False
    return when > end


def _ttl(deadline: str | None, **kw: Any) -> int:
    return forms_net.ttl_seconds(deadline[:10] if deadline else None, **kw)


# ── 发起人 ───────────────────────────────────────────────────────

def _title_from(text: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    return first[:30] + ("…" if len(first) > 30 else "")


def _room_for(raw: Any, recipients: list[str]) -> str | None:
    """在群里派的任务 (GROUP-CHAT-PLAN §2): 群在本机、我已加入、收件人都是群里已加入的人。"""
    if not raw:
        return None
    from . import colleague_rooms  # noqa: PLC0415
    room = colleague_rooms.joined_room(str(raw))
    outside = [e for e in recipients if e not in colleague_rooms.joined_members(room)]
    if outside:
        raise ValueError(f"这些人不在群里 (或还没点「加入」), 不能在群里派给他们: {', '.join(outside)}")
    return room["room_id"]


def create(args: dict[str, Any]) -> dict[str, Any]:
    """建任务 (还没发)。room_id: 在群里派的, 发出后任务卡发到群里, 收件人的状态也回群。"""
    text = str(args.get("text") or "").strip()
    if not text:
        raise ValueError("写一下要同事做什么")
    if len(text) > TEXT_MAX:
        raise ValueError(f"任务说明太长 ({len(text)} 字), 最多 {TEXT_MAX} 字")
    owner = forms_net.me()
    recipients = forms._norm_recipients(args.get("recipients"), owner)
    if len(recipients) > MAX_RECIPIENTS:
        raise ValueError(f"一次最多派给 {MAX_RECIPIENTS} 位同事 (现在 {len(recipients)} 位)")
    task_id = f"t_{datetime.now():%Y%m%d}_{secrets.token_hex(4)}"
    schedule = sched.normalize(args.get("schedule"))
    context = str(args.get("context") or "").strip()
    if len(context) > RESULT_MAX:
        raise ValueError(f"转给同事的材料太长 ({len(context)} 字), 最多 {RESULT_MAX} 字")
    room_id = _room_for(args.get("room_id"), recipients)
    room_text = str(args.get("room_text") or "").strip()[:4000] or None if room_id else None
    task = {"task_id": task_id, "context": context or None, "pipeline_id": args.get("pipeline_id"), "room_id": room_id, "room_text": room_text,
            "owner": owner, "created_at": _now(), "status": "draft", "rev": 1,
            "title": (str(args.get("title") or "").strip() or _title_from(text))[:60], "text": text,
            "deadline": None if schedule and schedule["kind"] == "cron" else normalize_deadline(args.get("deadline")),
            "schedule": schedule,
            "recipients": {e: {"invited_at": None, "reminded_at": None} for e in recipients}}
    _save_outgoing(task)
    return {"ok": True, "task": task}


def add_recipients(task_id: str, raw: Any) -> dict[str, Any]:
    task = load_outgoing(task_id)
    if task["status"] == "cancelled":
        raise ValueError("这个任务已经取消了")
    for e in forms._norm_recipients(raw, task["owner"]):
        task["recipients"].setdefault(e, {"invited_at": None, "reminded_at": None})
    if len(task["recipients"]) > MAX_RECIPIENTS:
        raise ValueError(f"一个任务最多派给 {MAX_RECIPIENTS} 位同事")
    _save_outgoing(task)
    return {"ok": True, "task": task}


def _post_each(task: dict[str, Any], emails: list[str], kind: str, env: dict[str, Any], stamp: str | None):
    done, failed = [], []
    ttl = _ttl(task.get("deadline"))
    for e in emails:
        try:
            forms_net.post(e, kind, env, ttl)
            if kind == "task_invite":
                task["recipients"][e]["rev"] = int(task.get("rev", 1))
            if stamp:
                task["recipients"][e][stamp] = _now()
            done.append(e)
        except ValueError as err:
            failed.append({"to": e, "error": str(err)})
    return done, failed


def send(task_id: str) -> dict[str, Any]:
    """给还没发过的人投任务单。一个人失败不影响别人, 失败的再点发送会重试。"""
    task = load_outgoing(task_id)
    if task["status"] == "cancelled":
        raise ValueError("这个任务已经取消了")
    todo = [e for e, v in task["recipients"].items() if not v.get("invited_at")]
    if not todo:
        raise ValueError("没有要发的人 (都发过了, 或还没加同事)")
    sent, failed = _post_each(task, todo, "task_invite", _invite_env(task), "invited_at")
    if sent:
        task["status"] = "open"
    _save_outgoing(task)
    room_failed = None
    if sent and task.get("room_id") and not task.get("room_card_at"):
        from . import colleague_rooms  # noqa: PLC0415
        try:
            colleague_rooms.post_card(task["room_id"], task, sched.describe(task["schedule"]) if task.get("schedule") else None)
            task["room_card_at"] = _now()
            _save_outgoing(task)
        except (ValueError, LookupError) as e:
            room_failed = f"任务发出去了, 但任务卡没发到群里: {e}"
    return {"ok": bool(sent), "sent": sent, "failed": failed, **({"room_notice": room_failed} if room_failed else {})}


def _invite_env(task: dict[str, Any], redo_note: str | None = None) -> dict[str, Any]:
    env = {"v": ENVELOPE_VERSION, "task_id": task["task_id"], "rev": task.get("rev", 1),
           "task": {k: task.get(k) for k in ("title", "text", "deadline", "schedule", "context", "room_id")}}
    if redo_note is not None:
        env["redo_note"] = redo_note
    return env


def redo(task_id: str, email: str, note: str) -> dict[str, Any]:
    """请某一位同事重做 (马上做的任务): 只给他发 rev+1 的任务单, 他要重新接下; 别人的结果不动。"""
    task = load_outgoing(task_id)
    if task["status"] != "open" or task.get("schedule"):
        raise ValueError("只有进行中、马上做的任务能请人重做")
    email = str(email or "").strip().lower()
    if email not in task["recipients"] or not task["recipients"][email].get("invited_at"):
        raise ValueError(f"{email} 不在这个任务里")
    note = str(note or "").strip()
    if not note:
        raise ValueError("说一下要怎么改, 对方好重做")
    task["rev"] = int(task.get("rev", 1)) + 1
    sent, failed = _post_each(task, [email], "task_invite", _invite_env(task, note[:500]), None)
    _save_outgoing(task)
    if failed:
        raise ValueError(failed[0]["error"])
    return {"ok": True, "sent": sent}


def update(task_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """改任务 (内容 / 截止 / 什么时候做): 重新发给还在参与的人, 他们要重新同意; 已交回的结果留着。"""
    task = load_outgoing(task_id)
    if task["status"] != "open":
        raise ValueError("只有进行中的任务能改")
    if "text" in args:
        text = str(args.get("text") or "").strip()
        if not text or len(text) > TEXT_MAX:
            raise ValueError(f"任务说明要写, 最多 {TEXT_MAX} 字")
        task["text"] = text
    if args.get("title"):
        task["title"] = str(args["title"]).strip()[:60]
    if "schedule" in args:
        task["schedule"] = sched.normalize(args.get("schedule"))
    if "deadline" in args:
        task["deadline"] = normalize_deadline(args.get("deadline"))
    if task.get("schedule") and task["schedule"]["kind"] == "cron":
        task["deadline"] = None
    task["rev"] = int(task.get("rev", 1)) + 1
    replies = replies_of(task_id)
    todo = [e for e, v in task["recipients"].items()
            if v.get("invited_at") and (replies.get(e) or {}).get("status") not in ("declined", "quit")]
    sent, failed = _post_each(task, todo, "task_invite", _invite_env(task), None)
    task["updated_at"] = _now()
    _save_outgoing(task)
    return {"ok": True, "sent": sent, "failed": failed, "task": task}


def _unfinished(task: dict[str, Any]) -> list[str]:
    """还在参与、需要通知 (催 / 取消) 的人。周期任务交回过几期也还没完。"""
    replies = replies_of(task["task_id"])
    stop = ("declined", "quit") if task.get("schedule") else ("done", "declined")
    return [e for e, v in task["recipients"].items()
            if v.get("invited_at") and (replies.get(e) or {}).get("status") not in stop]


def remind(task_id: str, text: str = "") -> dict[str, Any]:
    task = load_outgoing(task_id)
    if task["status"] != "open":
        raise ValueError("只有进行中的任务能催")
    todo = _unfinished(task)
    if task.get("schedule"):   # 定时 / 周期: 只催还没同意 (或改了还没重新同意) 的
        replies = replies_of(task_id)
        todo = [e for e in todo if (replies.get(e) or {}).get("status") is None
                or int((replies.get(e) or {}).get("rev") or 1) < int(task.get("rev", 1))]
    if not todo:
        return {"ok": True, "reminded": [], "failed": [], "note": "都交回了 (或回了不接), 不用催"}
    env = {"v": ENVELOPE_VERSION, "task_id": task_id, "text": str(text or "").strip()[:200]}
    done, failed = _post_each(task, todo, "task_remind", env, "reminded_at")
    _save_outgoing(task)
    return {"ok": bool(done), "reminded": done, "failed": failed}


def cancel(task_id: str) -> dict[str, Any]:
    """取消: 通知还没交回的人别做了。已经交回的结果留着。"""
    task = load_outgoing(task_id)
    if task["status"] == "cancelled":
        return {"ok": True, "failed": [], "note": "已经取消了"}
    _, failed = _post_each(task, _unfinished(task), "task_cancel", {"v": ENVELOPE_VERSION, "task_id": task_id}, None)
    task["status"], task["cancelled_at"] = "cancelled", _now()
    _save_outgoing(task)
    return {"ok": True, "failed": failed}


def _owner_state(task: dict[str, Any], v: dict[str, Any], r: dict[str, Any] | None) -> str:
    if not v.get("invited_at"):
        return "未发"
    if task.get("schedule"):
        from .colleague_tasks_periodic import owner_state  # noqa: PLC0415
        return owner_state(task, r)
    status = (r or {}).get("status")
    if r and int(r.get("rev") or 1) < int(v.get("rev") or 1):   # 请他重做了, 还没重新回
        return "已取消" if task["status"] == "cancelled" else "等回应"
    if status == "done":
        return "逾期完成" if is_late(task.get("deadline"), r.get("done_at")) else "已完成"
    if status == "declined":
        return "不接"
    if task["status"] == "cancelled":
        return "已取消"
    if status == "accepted":
        return "超时未交" if is_late(task.get("deadline")) else "在做"
    return "超时未回" if is_late(task.get("deadline")) else "等回应"


def progress(task: dict[str, Any]) -> dict[str, Any]:
    replies = replies_of(task["task_id"])
    rows = []
    for e, v in task["recipients"].items():
        r = replies.get(e)
        rows.append({"email": e, "state": _owner_state(task, v, r), "invited_at": v.get("invited_at"),
                     "reminded_at": v.get("reminded_at"), "accepted_at": (r or {}).get("accepted_at"),
                     "done_at": (r or {}).get("done_at"), "result": (r or {}).get("result"),
                     "reason": (r or {}).get("reason"),
                     "periods": sorted(((r or {}).get("periods") or {}).values(), key=lambda x: x.get("period", 0))})
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return {"rows": rows, "counts": counts, "total": len(rows)}


def results(task_id: str) -> dict[str, Any]:
    """给小鲶读的: 任务 + 每个人的状态和交回的结果。"""
    task = load_outgoing(task_id)
    p = progress(task)
    people = []
    for r in p["rows"]:
        row = {"同事": r["email"], "状态": r["state"], "交回时间": r["done_at"], "结果": r["result"], "不接原因": r["reason"]}
        if r["periods"]:
            row["每期结果"] = [{"第几期": x.get("period"), "交回时间": x.get("at"), "晚了分钟": x.get("late_minutes") or 0,
                            "结果": x.get("result"), "还差几步等对方确认": x.get("waiting") or 0} for x in r["periods"]]
        people.append(row)
    return {"ok": True, "title": task["title"], "text": task["text"], "deadline": task.get("deadline"),
            "schedule": sched.describe(task.get("schedule")), "status": task["status"], "counts": p["counts"],
            "people": people}


# ── 收件人 ───────────────────────────────────────────────────────

def _reply(rec: dict[str, Any], env: dict[str, Any]) -> None:
    ttl = _ttl(rec["task"].get("deadline"), minimum=forms_net.MIN_RESPONSE_TTL_SECONDS)
    forms_net.post(rec["owner"], "task_reply",
                   {"v": ENVELOPE_VERSION, "task_id": rec["task_id"], "rev": rec.get("rev", 1), **env}, ttl)


def _open_rec(task_id: str) -> dict[str, Any]:
    rec = load_incoming(task_id)
    if rec.get("status") == "cancelled":
        raise ValueError("发起人已经取消了这个任务")
    return rec


def accept(task_id: str, auto_return: bool = False) -> dict[str, Any]:
    """接下。告诉发起人「在做了」。重复接下不重复通知。定时 / 周期的: 在本机建 hermes 定时任务。"""
    rec = _open_rec(task_id)
    if rec["task"].get("schedule"):
        from .colleague_tasks_periodic import accept_scheduled  # noqa: PLC0415
        return accept_scheduled(rec, auto_return=bool(auto_return))
    if rec.get("done"):
        return {"ok": True, "accepted_at": rec.get("accepted_at")}
    was_declined = bool(rec.pop("declined", None))
    if not rec.get("accepted_at") or was_declined:
        at = _now()
        _reply(rec, {"status": "accepted", "at": at})
        rec["accepted_at"] = at
    forms._write(_in_path(task_id), rec)
    return {"ok": True, "accepted_at": rec["accepted_at"]}


def save_draft(task_id: str, result: Any) -> dict[str, Any]:
    """结果草稿 (小鲶做完存的 / 本人改了一半)。不发出去。"""
    rec = load_incoming(task_id)
    text = str(result or "").strip()
    if len(text) > RESULT_MAX:
        raise ValueError(f"结果太长 ({len(text)} 字), 最多 {RESULT_MAX} 字: 精简一下, 长材料用邮件发")
    rec["draft"] = {"result": text, "updated_at": _now()}
    forms._write(_in_path(task_id), rec)
    return {"ok": True, "chars": len(text)}


def submit(task_id: str, result: Any) -> dict[str, Any]:
    """本人点了「交回」才调 (Companion 按钮; 小鲶的工具里没有这个动作)。可以改了再交, 后交的覆盖。"""
    rec = _open_rec(task_id)
    text = str(result or "").strip()
    if not text:
        raise ValueError("结果是空的: 写一下做了什么 / 结果是什么")
    if len(text) > RESULT_MAX:
        raise ValueError(f"结果太长 ({len(text)} 字), 最多 {RESULT_MAX} 字: 精简一下, 长材料用邮件发")
    at = _now()
    _reply(rec, {"status": "done", "result": text, "at": at})
    rec["accepted_at"] = rec.get("accepted_at") or at
    rec["done"] = {"result": text, "at": at}
    rec.pop("declined", None)
    forms._write(_in_path(task_id), rec)
    return {"ok": True, "done_at": at, "late": is_late(rec["task"].get("deadline"), at)}


def decline(task_id: str, reason: str) -> dict[str, Any]:
    rec = _open_rec(task_id)
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("不接也说一句原因, 发起人好安排")
    if rec.get("done"):
        raise ValueError("已经交回过结果了, 不能再回不接")
    at = _now()
    _reply(rec, {"status": "declined", "reason": reason[:300], "at": at})
    rec["declined"] = {"reason": reason[:300], "at": at}
    forms._write(_in_path(task_id), rec)
    return {"ok": True}


# ── 收邮筒 ───────────────────────────────────────────────────────

def _apply_reply(tid: str, sender: str, env: dict[str, Any]) -> str | None:
    task = forms._read(_out_dir(tid) / "task.json")
    if not task or sender not in task.get("recipients", {}):
        return None   # 不是这个任务的收件人: 不收
    status, at = env.get("status"), str(env.get("at") or _now())
    old = forms._read(_reply_path(tid, sender)) or {}
    r = {**old, "from": sender, "received_at": _now()}
    if status == "accepted":
        if old.get("status") == "done":
            return None   # 晚到的「接下」不盖掉结果 (回了不接之后又接下的, 照常改成在做)
        r.update(status="accepted", accepted_at=old.get("accepted_at") or at)
    elif status == "done":
        r.update(status="done", accepted_at=old.get("accepted_at") or at, done_at=at,
                 result=str(env.get("result") or "")[:RESULT_MAX], reason=None)
    elif status == "declined":
        r.update(status="declined", reason=str(env.get("reason") or "")[:300], declined_at=at)
    elif status == "period" and task.get("schedule"):
        try:
            n = int(env.get("period"))
        except (TypeError, ValueError):
            return None
        periods = dict(old.get("periods") or {})
        periods[str(n)] = {"period": n, "result": str(env.get("result") or "")[:RESULT_MAX], "at": at,
                           "late_minutes": int(env.get("late_minutes") or 0)}   # 交回了: 「等确认」自然清掉
        r.update(status="accepted" if old.get("status") != "quit" else "quit", periods=periods,
                 accepted_at=old.get("accepted_at") or at, done_at=at)
    elif status == "waiting" and task.get("schedule"):
        try:
            n = int(env.get("period"))
        except (TypeError, ValueError):
            return None
        periods = dict(old.get("periods") or {})
        prev = periods.get(str(n)) or {}
        periods[str(n)] = {**prev, "period": n, "waiting": max(1, int(env.get("waiting_steps") or 1)), "waiting_at": at}
        r.update(periods=periods, accepted_at=old.get("accepted_at") or at)
        if not old.get("status"):   # 「接下」那封没到 / 晚到: 都在做了, 当已接下
            r.update(status="accepted", rev=int(env.get("rev") or 1))
    elif status == "quit":
        r.update(status="quit", reason=str(env.get("reason") or "")[:300], quit_at=at)
    else:
        return None
    if status not in ("period", "waiting"):
        r["rev"] = int(env.get("rev") or 1)
    forms._write(_reply_path(tid, sender), r)
    return status


def _room_id_or_none(raw: Any) -> str | None:
    s = str(raw or "")
    return s if re.fullmatch(r"r_\d{8}_[0-9a-f]{12}", s) else None


def _echo(task_id: str, state: str, period: Any = None) -> None:
    """收件人在界面上点了「接下 / 交回 / 不接 / 退出」: 群里派的任务, 同一次点击往群里发一条状态 (只有状态, 没有结果)。"""
    try:
        rec = load_incoming(task_id)
    except (ValueError, LookupError):
        return
    rid = rec["task"].get("room_id")
    if rid:
        from . import colleague_rooms  # noqa: PLC0415
        colleague_rooms.echo_status(rid, task_id, rec["task"]["title"], state, int(period) if str(period or "").isdigit() else None)


def _then(fn, task_id: str, state: str, period: Any = None):
    def run():
        out = fn()
        _echo(task_id, state, period)
        return out
    return run


def _apply(msg: dict[str, Any]) -> str | None:
    env, kind, sender = msg["env"], msg["kind"], msg["from"]
    try:
        tid = _safe_id(env.get("task_id"))
    except ValueError:
        return None
    if kind == "task_invite":
        t = env.get("task") or {}
        text = str(t.get("text") or "").strip()[:TEXT_MAX]
        if not text:
            return None
        old = forms._read(_in_path(tid)) or {}
        if old and old.get("owner") != sender:
            return None   # 同编号别人发的: 不覆盖 (防冒名)
        try:
            deadline = normalize_deadline(t.get("deadline"))
            schedule = sched.normalize(t.get("schedule"), strict=False) if t.get("schedule") else None
        except ValueError:
            return None
        rev = int(env.get("rev") or 1)
        if old and rev <= int(old.get("rev") or 1) and old.get("task"):
            return None   # 重复 / 更旧的一版
        body = {"title": str(t.get("title") or _title_from(text))[:60], "text": text, "deadline": deadline,
                "schedule": schedule, "context": str(t.get("context") or "")[:RESULT_MAX] or None,
                "room_id": _room_id_or_none(t.get("room_id"))}
        rec = {**old, "task_id": tid, "owner": sender, "received_at": old.get("received_at") or _now(),
               "status": "open", "rev": rev, "task": body}
        rec.pop("redo_note", None)
        if env.get("redo_note"):
            rec["redo_note"] = str(env["redo_note"])[:500]
        if old.get("task"):   # 发起人改了任务: 要重新同意 (定时任务先停掉)
            from .colleague_tasks_periodic import reset_for_change  # noqa: PLC0415
            reset_for_change(rec)
        forms._write(_in_path(tid), rec)
        return "changed" if old.get("task") else "invite"
    if kind in ("task_remind", "task_cancel"):
        rec = forms._read(_in_path(tid))
        if not rec or rec.get("owner") != sender:
            return None
        if kind == "task_remind":
            rec["reminded_at"], rec["remind_text"] = _now(), str(env.get("text") or "")[:200]
        else:
            rec["status"], rec["cancelled_at"] = "cancelled", _now()
            sched.remove_job(rec.pop("job_id", None))
        forms._write(_in_path(tid), rec)
        return "remind" if kind == "task_remind" else "cancel"
    if kind == "task_reply":
        return _apply_reply(tid, sender, env)
    return None


def sync() -> dict[str, Any]:
    counts = {"invite": 0, "changed": 0, "remind": 0, "cancel": 0, "accepted": 0, "done": 0, "declined": 0,
              "period": 0, "quit": 0, "waiting": 0}
    events = []
    for msg in forms_net.take(forms_net.TASK_KINDS):
        got = _apply(msg)
        if got:
            counts[got] += 1
            tid = msg["env"].get("task_id")
            title = ((msg["env"].get("task") or {}).get("title")
                     or _title_of(tid))
            events.append({"kind": got, "task_id": tid, "from": msg["from"], "title": title,
                           "period": msg["env"].get("period")})
    from .colleague_tasks_periodic import maintain  # noqa: PLC0415
    events += maintain()   # 本机定时任务的补跑 / 收尾 / 要核对的提醒
    from .colleague_tasks_pipeline import maintain as pipeline_maintain  # noqa: PLC0415
    events += pipeline_maintain()   # 多阶段: 这一段都交回了 → 提醒发起人放行下一段
    return {"ok": True, **counts, "events": events}


def _title_of(task_id: Any) -> str:
    for path in (lambda t: _in_path(t), lambda t: _out_dir(t) / "task.json"):
        try:
            rec = forms._read(path(task_id))
        except ValueError:
            return ""
        if rec:
            return str((rec.get("task") or rec).get("title") or "")
    return ""


# ── 列表 / 详情 ──────────────────────────────────────────────────

def incoming_state(rec: dict[str, Any]) -> str:
    if rec["task"].get("schedule"):
        from .colleague_tasks_periodic import incoming_state as scheduled_state  # noqa: PLC0415
        return scheduled_state(rec)
    if rec.get("done"):
        return "已交回"
    if rec.get("declined"):
        return "不接"
    if rec.get("status") == "cancelled":
        return "已取消"
    return "在做" if rec.get("accepted_at") else "待处理"


def list_all() -> dict[str, Any]:
    incoming = []
    for p in sorted((tasks_dir() / "incoming").glob("*.json")):
        rec = forms._read(p)
        if rec and rec.get("task"):
            incoming.append({"task_id": rec["task_id"], "title": rec["task"]["title"], "owner": rec["owner"],
                             "deadline": rec["task"].get("deadline"), "state": incoming_state(rec),
                             "late": is_late(rec["task"].get("deadline")), "received_at": rec.get("received_at"),
                             "reminded_at": rec.get("reminded_at"),
                             "schedule": sched.describe(rec["task"].get("schedule")) if rec["task"].get("schedule") else None,
                             "to_review": sum(1 for x in rec.get("periods") or [] if x.get("state") == "待核对"),
                             "to_confirm": sum(1 for x in rec.get("periods") or [] if x.get("state") == "待确认")})
    outgoing = []
    for p in sorted((tasks_dir() / "outgoing").glob("*/task.json")):
        task = forms._read(p)
        if task and not task.get("pipeline_id"):   # 多阶段里的每一段在流程里显示
            outgoing.append({"task_id": task["task_id"], "title": task["title"], "status": task["status"],
                             "deadline": task.get("deadline"), "created_at": task.get("created_at"),
                             "schedule": sched.describe(task.get("schedule")) if task.get("schedule") else None,
                             "progress": progress(task)["counts"], "recipients": len(task["recipients"])})
    incoming.sort(key=lambda r: r.get("received_at") or "", reverse=True)
    outgoing.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    from .colleague_tasks_pipeline import list_pipelines  # noqa: PLC0415
    return {"ok": True, "incoming": incoming, "outgoing": outgoing, "pipelines": list_pipelines()}


def get(task_id: str) -> dict[str, Any]:
    if (_out_dir(task_id) / "task.json").exists():
        task = load_outgoing(task_id)
        return {"ok": True, "role": "owner", "task": task, "progress": progress(task),
                "schedule_text": sched.describe(task.get("schedule")) if task.get("schedule") else None}
    rec = load_incoming(task_id)
    extra = {}
    if rec["task"].get("schedule"):
        extra = {"schedule_text": sched.describe(rec["task"]["schedule"]), "next_run": sched.next_run(rec.get("job_id"))}
    return {"ok": True, "role": "recipient", "record": rec, "state": incoming_state(rec), **extra}


def _pipeline():
    from . import colleague_tasks_pipeline  # noqa: PLC0415
    return colleague_tasks_pipeline


def _periodic():
    from . import colleague_tasks_periodic  # noqa: PLC0415
    return colleague_tasks_periodic


def pim_handlers(params: dict[str, Any]) -> dict[str, Any]:
    """Companion 界面 (Rust commands/colleague_tasks.rs → pim/ctask_*)。按钮本身就是员工的确认;
    接下 / 交回 / 不接只走这里, 小鲶的工具里没有。"""
    tid = str(params.get("task_id") or "")
    return {
        "pim/ctask_list": list_all,
        "pim/ctask_get": lambda: get(tid),
        "pim/ctask_sync": sync,
        "pim/ctask_create": lambda: create(params),
        "pim/ctask_add": lambda: add_recipients(tid, params.get("recipients")),
        "pim/ctask_send": lambda: send(tid),
        "pim/ctask_remind": lambda: remind(tid, str(params.get("text") or "")),
        "pim/ctask_cancel": lambda: cancel(tid),
        "pim/ctask_accept": _then(lambda: accept(tid, bool(params.get("auto_return"))), tid, "accepted"),
        "pim/ctask_update": lambda: update(tid, params),
        "pim/ctask_period_submit": _then(lambda: _periodic().submit_period(tid, params.get("period"), params.get("result")),
                                         tid, "done", params.get("period")),
        "pim/ctask_quit": _then(lambda: _periodic().quit(tid, str(params.get("reason") or "")), tid, "quit"),
        "pim/ctask_redo": lambda: redo(tid, str(params.get("email") or ""), str(params.get("note") or "")),
        **_pipeline().pim_handlers(params),
        "pim/ctask_draft": lambda: save_draft(tid, params.get("result")),
        "pim/ctask_submit": _then(lambda: submit(tid, params.get("result")), tid, "done"),
        "pim/ctask_decline": _then(lambda: decline(tid, str(params.get("reason") or "")), tid, "declined"),
    }


def tool_dispatch(action: str, args: dict[str, Any]) -> dict[str, Any]:
    """catfish_form 的 task_* action (工具数已到网关上限, 合进填表那一格)。
    派出 / 催 / 取消会给同事发消息: 不带 confirmed 只返回提案。没有接下 / 交回 / 不接。"""
    tid = str(args.get("task_id") or "").strip()
    if action == "task_list":
        return list_all()
    if action == "task_get":
        return get(tid)
    if action == "task_results":
        return results(tid)
    if action == "task_create":
        return create(args)
    if action == "task_draft":
        return save_draft(tid, args.get("result"))
    if action == "task_period":
        return _periodic().record_period(tid, args.get("result"), args.get("skipped"), args.get("period"))
    if action in ("task_send", "task_remind", "task_cancel"):
        task = load_outgoing(tid)
        if not args.get("confirmed"):
            who = ([e for e, v in task["recipients"].items() if not v.get("invited_at")]
                   if action == "task_send" else _unfinished(task))
            when = sched.describe(task.get("schedule")) if task.get("schedule") else f"截止 {task.get('deadline') or '不限'}"
            what = {"task_send": f"把任务「{task['title']}」({when}) 派给",
                    "task_remind": f"催任务「{task['title']}」还没交回的", "task_cancel": f"取消任务「{task['title']}」, 通知"}[action]
            return {"ok": True, "needs_confirmation": True, "action": action, "task_id": tid,
                    "proposal": f"{what} {len(who)} 人: {'、'.join(who) or '(没有人)'}",
                    "note": "念给员工听, 员工同意后带 confirmed=true 再调"}
        if action == "task_send":
            return send(tid)
        if action == "task_remind":
            return remind(tid, str(args.get("text") or ""))
        return cancel(tid)
    raise ValueError("任务 action 只能是 task_list / task_get / task_results / task_create / task_send / "
                     "task_remind / task_cancel / task_draft / task_period")
