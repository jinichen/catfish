"""派任务 · 群里派的那部分 (docs/GROUP-CHAT-PLAN.md 第二 / 三步; 11/5 从 colleague_tasks.py 拆出来, 守 800 行)。

room_for: 派之前核对群和收件人; then: 收件人点按钮后回群状态; auto_post / set_room_auto_post: 定时周期结果自动发群 (特例)。
"""
from __future__ import annotations

import re
from typing import Any

from . import colleague_tasks as ct


def room_for(raw: Any, recipients: list[str]) -> str | None:
    """在群里派的任务 (GROUP-CHAT-PLAN §2): 群在本机、我已加入、收件人都是群里已加入的人。"""
    if not raw:
        return None
    from . import colleague_rooms_tasks  # noqa: PLC0415
    room = colleague_rooms_tasks.joined_room(str(raw))
    outside = [e for e in recipients if e not in colleague_rooms_tasks.joined_members(room)]
    if outside:
        raise ValueError(f"这些人不在群里 (或还没点「加入」), 不能在群里派给他们: {', '.join(outside)}")
    return room["room_id"]


def auto_post(task: dict[str, Any], sender: str, n: int, result: str) -> None:
    """发起人这边收到一期结果: 勾了「每次结果自动发到群里」就转进群 (一个人的一期只转一次)。转不出去不影响收结果。"""
    if not (task.get("room_auto_post") and task.get("room_id")):
        return
    key = f"{sender}#{n}"
    if key in (task.get("auto_posted") or []):
        return
    from . import colleague_rooms_tasks  # noqa: PLC0415
    try:
        out = colleague_rooms_tasks.auto_post_period(task["room_id"], task, sender, n, result)
    except Exception as e:  # noqa: BLE001
        ct.logger.warning("任务 %s 第 %s 次结果自动转群失败: %s", task["task_id"], n, e)
        return
    if out:
        task.setdefault("auto_posted", []).append(key)
        ct._save_outgoing(task)


def set_room_auto_post(task_id: str, on: Any) -> dict[str, Any]:
    """发起人在任务里开 / 关「每次结果自动发到群里」(随时能关; 只有群里派的定时 / 周期任务能开)。"""
    task = ct.load_outgoing(task_id)
    want = bool(on)
    if want and not (task.get("room_id") and task.get("schedule")):
        raise ValueError("只有在群里派的定时 / 周期任务能设「每次结果自动发到群里」")
    task["room_auto_post"] = want
    task["room_auto_post_changed_at"] = ct._now()
    ct._save_outgoing(task)
    return {"ok": True, "room_auto_post": want}


def room_id_or_none(raw: Any) -> str | None:
    s = str(raw or "")
    return s if re.fullmatch(r"r_\d{8}_[0-9a-f]{12}", s) else None


def _echo(task_id: str, state: str, period: Any = None) -> None:
    """收件人在界面上点了「接下 / 交回 / 不接 / 退出」: 群里派的任务, 同一次点击往群里发一条状态 (只有状态, 没有结果)。"""
    try:
        rec = ct.load_incoming(task_id)
    except (ValueError, LookupError):
        return
    rid = rec["task"].get("room_id")
    if rid:
        from . import colleague_rooms_tasks  # noqa: PLC0415
        colleague_rooms_tasks.echo_status(rid, task_id, rec["task"]["title"], state, int(period) if str(period or "").isdigit() else None)


def then(fn, task_id: str, state: str, period: Any = None):
    """点完再回群。重复点 (已接下又点接下、交回后改了再交) 不再往群里发一遍, 免得刷屏。"""
    def run():
        try:
            before = ct.load_incoming(task_id)
        except (ValueError, LookupError):
            before = {}
        again = (state == "accepted" and before.get("accepted_at") and not before.get("declined")) or \
                (state == "done" and period in (None, "") and before.get("done"))
        out = fn()
        if not again:
            _echo(task_id, state, period)
        return out
    return run
