"""派任务 · 截止时间 (11/07, docs/GROUP-CHAT-PLAN.md §4.10): 截止前提醒本人、过了截止提醒发起人、延期。

鸿波: 派出去以后要一个个去问「做了没」。改成:
  · 收件人这边: 截止前 2 小时还没交回 (没接 / 在做 / 小鲶做好了还没核对) → 提醒本人一次 (事件 due_soon)。
    记 due_soon_for = 当时的截止, 延期后截止变了会再提醒一次。
  · 发起人这边: 过了截止还有人没交 (超时未回 / 超时未交) → 提醒发起人一次 (事件 overdue, 带名单), 界面放进输入框上方的提醒条,
    上面能「催一下」「延期」。记 overdue_for = 当时的截止。
  · 延期 (extend): 只改截止时间, 发给还没交的人一封带新截止的「催」(task_remind + deadline)。不升 rev ——
    改任务 (update) 要大家重新同意、会停掉小鲶已经在做的活, 延期不该这样; 老版本收到也只当一次「催」, 说明里写着新截止。
    群里派的再往群里发一条「截止改到 …」(room_msg.deadline), 大家的任务卡和任务面板跟着变。
都只看一次性任务 (定时 / 周期的没有截止, 每期按点跑)。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from . import colleague_tasks as ct
from . import forms

LATE_STATES = ("超时未回", "超时未交")


def _now_dt() -> datetime:
    return datetime.now().astimezone()


def _watch_due(events: list[dict[str, Any]]) -> None:
    now = _now_dt()
    for p in sorted((ct.tasks_dir() / "incoming").glob("*.json")):
        rec = forms._read(p)
        if not rec or not rec.get("task") or rec["task"].get("schedule"):
            continue
        dl = rec["task"].get("deadline")
        end = ct._deadline_at(dl)
        if end is None or rec.get("done") or rec.get("declined") or rec.get("status") == "cancelled":
            continue
        if rec.get("due_soon_for") == dl or not (end - ct.DUE_SOON <= now < end):
            continue
        rec["due_soon_for"] = dl
        forms._write(p, rec)
        events.append({"kind": "due_soon", "task_id": rec["task_id"], "from": rec["owner"], "title": rec["task"]["title"],
                       "deadline": dl, "state": ct.incoming_state(rec)})


def _watch_overdue(events: list[dict[str, Any]]) -> None:
    for p in sorted((ct.tasks_dir() / "outgoing").glob("*/task.json")):
        task = forms._read(p)
        if not task or task.get("status") != "open" or task.get("schedule") or not task.get("deadline"):
            continue
        if task.get("overdue_for") == task["deadline"] or not ct.is_late(task["deadline"]):
            continue
        late = [r["email"] for r in ct.progress(task)["rows"] if r["state"] in LATE_STATES]
        if not late:
            continue
        task["overdue_for"] = task["deadline"]
        forms._write(p, task)
        events.append({"kind": "overdue", "task_id": task["task_id"], "from": "", "title": task["title"],
                       "deadline": task["deadline"], "people": late})


def watch(events: list[dict[str, Any]]) -> None:
    """取邮筒那一轮顺手看 (每 20 秒)。各自只提醒一次。"""
    _watch_due(events)
    _watch_overdue(events)


def apply_extend(rec: dict[str, Any], env: dict[str, Any]) -> bool:
    """收件人收到带新截止的「催」: 改本机任务的截止 (定时 / 周期的不管)。老版本收到只当一次「催」, 说明里写着新截止。"""
    if not env.get("deadline") or rec["task"].get("schedule"):
        return False
    try:
        dl = ct.normalize_deadline(env.get("deadline"))
    except ValueError:
        return False
    if not dl:
        return False
    rec["task"]["deadline"] = dl
    rec.pop("due_soon_for", None)
    return True


def extend(task_id: str, raw: Any) -> dict[str, Any]:
    """发起人延期: 只改截止, 通知还没交的人; 群里派的往群里说一声。"""
    task = ct.load_outgoing(task_id)
    if task["status"] != "open" or task.get("schedule"):
        raise ValueError("只有进行中、马上做的任务能延期")
    dl = ct.normalize_deadline(raw)
    end = ct._deadline_at(dl)
    if end is None:
        raise ValueError("说一下延到什么时候")
    if end <= _now_dt():
        raise ValueError("新的截止时间已经过了")
    task["deadline"] = dl
    task.pop("overdue_for", None)
    todo = ct._unfinished(task)
    words = dl.replace("T", " ")
    env = {"v": ct.ENVELOPE_VERSION, "task_id": task_id, "text": f"截止改到 {words}", "deadline": dl}
    sent, failed = ct._post_each(task, todo, "task_remind", env, None)
    task["extended_at"] = ct._now()
    ct._save_outgoing(task)
    notice = None
    if task.get("room_id") and task.get("room_card_at"):
        from . import colleague_rooms_tasks  # noqa: PLC0415
        try:
            colleague_rooms_tasks.post_deadline(task["room_id"], task)
        except (ValueError, LookupError) as e:
            notice = f"截止改好了, 但群里没说上: {e}"
    return {"ok": True, "deadline": dl, "sent": sent, "failed": failed, **({"room_notice": notice} if notice else {})}
