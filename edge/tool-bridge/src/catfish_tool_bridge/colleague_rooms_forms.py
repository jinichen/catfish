"""同事群聊 · 群里发表 (11/09, 鸿波「填表能力和群聊的能力有没重叠?」—— 重叠的是底座, 差的就是这一步)。

表本身照旧走填表 (forms.py): 字段定义发给每个人、回答只回发起人、中央只转交。群里只多两样, 跟「群里派任务」一个做法:
  表卡  form         {form_id, title, deadline, fields, recipients[], owner}   发起人发出后发到群里, 收件人算被 @
  状态  form_status  {form_id, title, state: submitted | declined | closed}      收件人点「提交 / 不填」、发起人点「结束收集」时
                                                                                   同一次点击发到群里; 只有状态, 填的内容不进群
老版本收到这两种信只当普通消息 (正文是「请填《…》」「张三交了《…》」), 能看懂。
"""
from __future__ import annotations

import re
from typing import Any

from . import colleague_rooms as cr
from . import forms_net

STATE_WORDS = {"submitted": "交了", "declined": "不填", "closed": "结束收集了"}
_FORM_ID = re.compile(r"f_\d{8}_[0-9a-f]{6,16}")


def room_for(raw: Any, recipients: list[str]) -> str | None:
    """发表前核对: 群在本机、我已加入、收件人都是群里已加入的人 (跟群里派任务同一条规矩)。"""
    from .colleague_tasks_room import room_for as _room_for  # noqa: PLC0415
    return _room_for(raw, recipients)


def card_text(form: dict[str, Any]) -> str:
    dl = form.get("deadline")
    head = f"📋 请填《{form['title']}》({len(form.get('fields') or [])} 项{f', {dl[5:].replace(chr(45), chr(47))} 截止' if dl else ''})"
    note = str(form.get("note") or "").strip()
    return (head + ("\n" + note if note else ""))[: cr.TEXT_MAX]


def post_card(room_id: str, form: dict[str, Any]) -> dict[str, Any]:
    """发起人发出表后, 把表卡发到群里 (界面「发出去」那一次点击的一部分)。"""
    from . import colleague_rooms_tasks as rt  # noqa: PLC0415
    me = forms_net.me()
    room = rt.joined_room(room_id)
    recipients = [e for e in form.get("recipients") or {} if e != me]
    card = {"form_id": form["form_id"], "title": form["title"][:80], "deadline": form.get("deadline"),
            "fields": len(form.get("fields") or []), "recipients": recipients, "owner": me}
    return cr._broadcast(room, me, card_text(form), [e for e in recipients if e in cr._active(room)], {"form": card}, kind="form")


def echo_status(room_id: str | None, form_id: str, title: str, state: str) -> dict[str, Any] | None:
    """收件人点了「提交 / 不填」、发起人点了「结束收集」: 同一次点击往群里发一条只有状态的消息。
    不在群里了就不发, 发不出去也不影响表本身。"""
    if not room_id or state not in STATE_WORDS:
        return None
    from . import colleague_rooms_tasks as rt  # noqa: PLC0415
    try:
        me = forms_net.me()
        room = rt.joined_room(room_id)
    except (ValueError, LookupError):
        return None
    body = f"{cr._label(room, me)} {STATE_WORDS[state]}《{title}》"
    status = {"form_id": form_id, "title": title[:80], "state": state}
    try:
        return cr._broadcast(room, me, body, [], {"form_status": status}, kind="form_status")
    except ValueError:
        return None


def _clean_form(raw: Any, sender: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or str(raw.get("owner") or "").lower() != sender:
        return None   # 表卡只认发起人自己发的
    fid = str(raw.get("form_id") or "")
    if not _FORM_ID.fullmatch(fid):
        return None
    people = [str(x).lower() for x in raw.get("recipients") or [] if isinstance(x, str) and "@" in x][: cr.MAX_MEMBERS]
    try:
        n = max(0, int(raw.get("fields") or 0))
    except (TypeError, ValueError):
        n = 0
    return {"form_id": fid, "title": str(raw.get("title") or "")[:80], "deadline": str(raw.get("deadline") or "")[:10] or None,
            "fields": n, "recipients": people, "owner": sender}


def _clean_form_status(raw: Any, rid: str, sender: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or raw.get("state") not in STATE_WORDS:
        return None
    fid = str(raw.get("form_id") or "")
    card = cr.store.find_form_card(rid, fid)
    if raw["state"] == "closed":
        if not card or card.get("owner") != sender:
            return None   # 「结束收集」只认这张表卡的发起人
    elif card and sender not in card.get("recipients", []):
        return None   # 不是这张表卡上的人: 不收 (防冒名报状态)
    return {"form_id": fid, "title": str(raw.get("title") or "")[:80], "state": raw["state"]}
