"""同事群聊 (11/5, docs/GROUP-CHAT-PLAN.md 第一步: 能用的群聊)。

真人同事 + 各自的小鲶的群, 不是一群 AI 互相聊。中央只当邮筒 (跟派任务 / 填表同一个, 取走即清空):
**一条群消息 = 给每个成员各投一封**, 群的聊天记录只在每个成员自己电脑上, 中央和 web 都看不到。
每一封都是员工在界面上点了才发 (发消息 / 建群 / 加入 / 群主改名单 / 退群)。

本机账本 (`~/.catfish/collab_rooms/`):
  <room_id>/room.json        群: 名字 / 用途 / 群主 / 名单版本 / 成员 (我看到的状态) / 我的状态 / 读到第几条 (read_upto)
  <room_id>/messages.jsonl   消息 (一行一条; 系统提示 kind=system 也在这里)
  _sync.json                 上次取邮筒的时间 (超过 7 天没取 = 中间可能缺消息)

邮筒里的信封 (JSON, 中央看不懂也不看), 期限 7 天:
  room_invite  {v, room_id, ver, name, purpose, owner, members: {邮箱: {state, name}}, at}   群主拉人
  room_join    {v, room_id, status: joined|declined, at}        加入发给群里所有人 / 不加入只回群主
  room_msg     {v, room_id, msg_id, at, text, mentions[], reply_to?}
  room_member  {v, room_id, ver, op: update|disband, name, purpose, members: {...}, at}   只认群主
  room_leave   {v, room_id, at}                                 退群, 发给群里所有人

规则 (11/5 鸿波定): 一个群最多 50 人; 一条消息 ≤ 4000 字; 离线最多补 7 天; 后加入的人看不到加入前的记录;
不是成员发来的不收, 名单 / 群名 / 解散只认群主发的 (防冒名)。
"""
from __future__ import annotations

import json
import re
import secrets
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import forms, forms_net, wiki_files

ENVELOPE_VERSION = 1
MAX_MEMBERS = 50
TEXT_MAX = 4000
NAME_MAX = 30
PURPOSE_MAX = 500
TTL_SECONDS = 7 * 24 * 3600
GAP_DAYS = 7
HISTORY_LIMIT = 300
_ID = re.compile(r"r_[0-9]{8}_[0-9a-f]{6,16}")
_MSG_ID = re.compile(r"m_[0-9a-f]{8,32}")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
ACTIVE = ("invited", "joined")          # 还在群里 (已邀请 / 已加入)
GONE = ("declined", "left", "removed")


# ── 本机账本 ─────────────────────────────────────────────────────

def rooms_dir() -> Path:
    d = wiki_files._catfish_home() / "collab_rooms"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_id(room_id: Any) -> str:
    rid = str(room_id or "").strip()
    if not _ID.fullmatch(rid):
        raise ValueError(f"群编号不对: {room_id!r}")
    return rid


def _room_path(room_id: str) -> Path:
    return rooms_dir() / _safe_id(room_id) / "room.json"


def _msgs_path(room_id: str) -> Path:
    return rooms_dir() / _safe_id(room_id) / "messages.jsonl"


def load(room_id: str) -> dict[str, Any]:
    room = forms._read(_room_path(room_id))
    if not room:
        raise LookupError(f"没有这个群: {room_id}")
    return room


def _save(room: dict[str, Any]) -> None:
    forms._write(_room_path(room["room_id"]), room)


def messages(room_id: str) -> list[dict[str, Any]]:
    p = _msgs_path(room_id)
    out = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if isinstance(m, dict):
                out.append(m)
    return out


def _append(room_id: str, msg: dict[str, Any]) -> None:
    p = _msgs_path(room_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(msg, ensure_ascii=False) + "\n")


def _system(room: dict[str, Any], text: str) -> None:
    _append(room["room_id"], {"msg_id": f"m_{secrets.token_hex(8)}", "kind": "system", "at": _now(), "text": text})


def _norm_email(e: Any) -> str:
    s = str(e or "").strip().lower()
    if not _EMAIL.fullmatch(s):
        raise ValueError(f"邮箱不对: {e!r}")
    return s


def _parse_people(raw: Any) -> dict[str, str]:
    """成员: ["a@x.com", ...] 或 [{"email": "a@x.com", "name": "张三"}, ...] → {邮箱: 名字}。"""
    out: dict[str, str] = {}
    for item in raw or []:
        if isinstance(item, dict):
            email, name = _norm_email(item.get("email")), str(item.get("name") or "").strip()[:30]
        else:
            email, name = _norm_email(item), ""
        out[email] = name or out.get(email, "")
    return out


def _label(room: dict[str, Any], email: str) -> str:
    return (room.get("members", {}).get(email) or {}).get("name") or email.split("@")[0]


def _active(room: dict[str, Any]) -> list[str]:
    return [e for e, v in room.get("members", {}).items() if v.get("state") in ACTIVE]


def _joined_others(room: dict[str, Any], me: str) -> list[str]:
    return [e for e, v in room.get("members", {}).items() if v.get("state") == "joined" and e != me]


def _roster(room: dict[str, Any]) -> dict[str, dict[str, str]]:
    """信封里带的名单: 只带还在群里的人 (状态 + 名字)。"""
    return {e: {"state": v["state"], "name": v.get("name") or ""}
            for e, v in room.get("members", {}).items() if v.get("state") in ACTIVE}


def _post_all(emails: list[str], kind: str, env: dict[str, Any]) -> list[dict[str, str]]:
    """给每个人各投一封 (并发 8 路)。返回没送到的 [{email, error}]。"""
    def one(e: str) -> dict[str, str] | None:
        try:
            forms_net.post(e, kind, env, TTL_SECONDS)
            return None
        except ValueError as err:
            return {"email": e, "error": str(err)}
    if not emails:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(emails))) as ex:
        return [r for r in ex.map(one, emails) if r]


def _require_owner(room: dict[str, Any], me: str) -> None:
    if room.get("owner") != me:
        raise ValueError("只有群主能改群名单 / 群名 / 解散")
    if room.get("my_state") != "joined":
        raise ValueError("这个群已经结束了")


# ── 群主: 建群 / 拉人 / 移出 / 改名 / 解散 ──────────────────────

def create(args: dict[str, Any]) -> dict[str, Any]:
    me = forms_net.me()
    name = str(args.get("name") or "").strip()[:NAME_MAX]
    if not name:
        raise ValueError("给群起个名字")
    people = _parse_people(args.get("members"))
    people.pop(me, None)
    if not people:
        raise ValueError("至少拉一位同事进群")
    if len(people) + 1 > MAX_MEMBERS:
        raise ValueError(f"一个群最多 {MAX_MEMBERS} 人 (含你自己)")
    rid = f"r_{datetime.now():%Y%m%d}_{secrets.token_hex(6)}"
    room = {"room_id": rid, "name": name, "purpose": str(args.get("purpose") or "").strip()[:PURPOSE_MAX],
            "owner": me, "ver": 1, "my_state": "joined", "created_at": _now(),
            "members": {me: {"state": "joined", "name": str(args.get("my_name") or "").strip()[:30]},
                        **{e: {"state": "invited", "name": n} for e, n in people.items()}}}
    _save(room)
    _system(room, f"你建了群「{name}」, 邀请了 {len(people)} 位同事")
    failed = _post_all(list(people), "room_invite", _invite_env(room))
    return {"ok": True, "room": summary(room, me=me), "failed": failed}


def _invite_env(room: dict[str, Any]) -> dict[str, Any]:
    return {"v": ENVELOPE_VERSION, "room_id": room["room_id"], "ver": room["ver"], "name": room["name"],
            "purpose": room.get("purpose") or "", "owner": room["owner"], "members": _roster(room), "at": _now()}


def _member_env(room: dict[str, Any], op: str = "update") -> dict[str, Any]:
    return {"v": ENVELOPE_VERSION, "room_id": room["room_id"], "ver": room["ver"], "op": op, "name": room["name"],
            "purpose": room.get("purpose") or "", "members": _roster(room), "at": _now()}


def add_members(room_id: str, raw: Any) -> dict[str, Any]:
    me = forms_net.me()
    room = load(room_id)
    _require_owner(room, me)
    new = {e: n for e, n in _parse_people(raw).items()
           if e != me and (room["members"].get(e) or {}).get("state") not in ACTIVE}
    if not new:
        raise ValueError("没有新同事要拉 (已经在群里的不用再拉)")
    if len(_active(room)) + len(new) > MAX_MEMBERS:
        raise ValueError(f"一个群最多 {MAX_MEMBERS} 人")
    old_joined = _joined_others(room, me)
    for e, n in new.items():
        room["members"][e] = {"state": "invited", "name": n or (room["members"].get(e) or {}).get("name") or ""}
    room["ver"] += 1
    _save(room)
    _system(room, f"你邀请了 {'、'.join(_label(room, e) for e in new)}")
    failed = _post_all(list(new), "room_invite", _invite_env(room))
    failed += _post_all(old_joined, "room_member", _member_env(room))
    return {"ok": True, "room": summary(room, me=me), "failed": failed}


def remove_member(room_id: str, email: str) -> dict[str, Any]:
    me = forms_net.me()
    room = load(room_id)
    _require_owner(room, me)
    email = _norm_email(email)
    if email == me:
        raise ValueError("群主不能把自己移出; 不想要这个群就解散")
    if (room["members"].get(email) or {}).get("state") not in ACTIVE:
        raise ValueError("这个人已经不在群里")
    notify = _joined_others(room, me)
    room["members"][email]["state"] = "removed"
    room["ver"] += 1
    _save(room)
    _system(room, f"你把 {_label(room, email)} 移出了群")
    env = _member_env(room)
    env["removed"] = [email]
    failed = _post_all(sorted(set(notify) | {email}), "room_member", env)
    return {"ok": True, "room": summary(room, me=me), "failed": failed}


def update_info(room_id: str, args: dict[str, Any]) -> dict[str, Any]:
    me = forms_net.me()
    room = load(room_id)
    _require_owner(room, me)
    name = str(args.get("name") if args.get("name") is not None else room["name"]).strip()[:NAME_MAX]
    if not name:
        raise ValueError("群名不能空")
    purpose = str(args.get("purpose") if args.get("purpose") is not None else room.get("purpose") or "").strip()[:PURPOSE_MAX]
    if name == room["name"] and purpose == (room.get("purpose") or ""):
        return {"ok": True, "room": summary(room, me=me), "failed": []}
    changed = []
    if name != room["name"]:
        changed.append(f"群名改成「{name}」")
    if purpose != (room.get("purpose") or ""):
        changed.append("更新了群公告")
    room.update(name=name, purpose=purpose, ver=room["ver"] + 1)
    _save(room)
    _system(room, f"你{'、'.join(changed)}")
    failed = _post_all(_active_others(room, me), "room_member", _member_env(room))
    return {"ok": True, "room": summary(room, me=me), "failed": failed}


def _active_others(room: dict[str, Any], me: str) -> list[str]:
    return [e for e in _active(room) if e != me]


def disband(room_id: str) -> dict[str, Any]:
    me = forms_net.me()
    room = load(room_id)
    _require_owner(room, me)
    notify = _active_others(room, me)
    room["ver"] += 1
    room["my_state"], room["ended_at"] = "disbanded", _now()
    _save(room)
    _system(room, "你解散了这个群")
    failed = _post_all(notify, "room_member", _member_env(room, "disband"))
    return {"ok": True, "room": summary(room, me=me), "failed": failed}


# ── 成员: 加入 / 不加入 / 退群 ───────────────────────────────────

def join(room_id: str) -> dict[str, Any]:
    me = forms_net.me()
    room = load(room_id)
    if room.get("my_state") != "invited":
        raise ValueError("这个群现在不能加入 (已经加入、已结束或没被邀请)")
    room["my_state"] = "joined"
    room.setdefault("members", {}).setdefault(me, {"name": ""})["state"] = "joined"
    room["joined_at"] = _now()
    _system(room, "你加入了群 (加入前的消息看不到)")
    room["read_upto"] = len(messages(room["room_id"]))
    _save(room)
    env = {"v": ENVELOPE_VERSION, "room_id": room["room_id"], "status": "joined", "at": _now()}
    failed = _post_all(_active_others(room, me), "room_join", env)
    return {"ok": True, "room": summary(room, me=me), "failed": failed}


def decline(room_id: str) -> dict[str, Any]:
    me = forms_net.me()
    room = load(room_id)
    if room.get("my_state") != "invited":
        raise ValueError("这个群现在不能拒绝")
    room["my_state"] = "declined"
    room.setdefault("members", {}).setdefault(me, {"name": ""})["state"] = "declined"
    room["ended_at"] = _now()
    _save(room)
    env = {"v": ENVELOPE_VERSION, "room_id": room["room_id"], "status": "declined", "at": _now()}
    failed = _post_all([room["owner"]], "room_join", env)
    return {"ok": True, "room": summary(room, me=me), "failed": failed}


def leave(room_id: str) -> dict[str, Any]:
    me = forms_net.me()
    room = load(room_id)
    if room.get("owner") == me:
        raise ValueError("群主不能退群; 不想要这个群就解散")
    if room.get("my_state") != "joined":
        raise ValueError("你已经不在这个群里")
    notify = _active_others(room, me)
    room["my_state"], room["ended_at"] = "left", _now()
    room["members"].setdefault(me, {"name": ""})["state"] = "left"
    _save(room)
    _system(room, "你退出了群")
    failed = _post_all(notify, "room_leave", {"v": ENVELOPE_VERSION, "room_id": room["room_id"], "at": _now()})
    return {"ok": True, "room": summary(room, me=me), "failed": failed}


def delete_local(room_id: str) -> dict[str, Any]:
    """已经结束的群 (退了 / 被移出 / 解散 / 没加入), 把本机的记录删掉。还在的群不能删 (要先退)。"""
    room = load(room_id)
    if room.get("my_state") in ("joined", "invited"):
        raise ValueError("还在群里的不能删记录; 先退群 (群主先解散)")
    shutil.rmtree(rooms_dir() / _safe_id(room_id), ignore_errors=True)
    return {"ok": True}


# ── 发消息 / 已读 ────────────────────────────────────────────────

def send(room_id: str, text: Any, mentions: Any = None, reply_to: Any = None) -> dict[str, Any]:
    me = forms_net.me()
    room = load(room_id)
    if room.get("my_state") != "joined":
        raise ValueError("你不在这个群里, 发不了消息")
    body = str(text or "").strip()
    if not body:
        raise ValueError("消息是空的")
    if len(body) > TEXT_MAX:
        raise ValueError(f"一条消息最多 {TEXT_MAX} 字, 长材料请用邮件")
    members = set(_active(room))
    ments = [e for e in dict.fromkeys(_norm_email(x) for x in (mentions or [])) if e in members and e != me]
    rt = str(reply_to or "").strip() or None
    if rt and not _MSG_ID.fullmatch(rt):
        rt = None
    return _broadcast(room, me, body, ments, {**({"reply_to": rt} if rt else {})})


def _broadcast(room: dict[str, Any], me: str, body: str, ments: list[str], extra: dict[str, Any],
               kind: str = "text") -> dict[str, Any]:
    """发给群里已加入的其他人 + 记进本机。extra = 信封和本机记录里都带的附加字段 (reply_to / card / status)。"""
    msg = {"msg_id": f"m_{secrets.token_hex(10)}", "kind": kind, "from": me, "at": _now(), "text": body,
           "mentions": ments, **extra}
    others = _joined_others(room, me)
    env = {"v": ENVELOPE_VERSION, "room_id": room["room_id"], "msg_id": msg["msg_id"], "at": msg["at"],
           "text": body, "mentions": ments, **extra}
    failed = _post_all(others, "room_msg", env)
    if failed:
        msg["undelivered"] = [f["email"] for f in failed]
    _append(room["room_id"], msg)
    room["last_msg_at"] = msg["at"]
    room["read_upto"] = len(messages(room["room_id"]))
    _save(room)
    return {"ok": True, "message": msg, "sent_to": len(others) - len(failed), "failed": failed}


# ── 第二步: 群里派任务 (docs/GROUP-CHAT-PLAN.md §2) ─────────────────
#
# 任务本身照旧走派任务 (colleague_tasks, 结果只回发起人); 群里只多两样:
#   任务卡 card   {task_id, title, deadline, schedule, assignees[]}  —— 发起人派出后发到群里, 收件人算被 @
#   状态 status   {task_id, title, state}                             —— 收件人点「接下 / 交回 / 不接」时同一次点击发到群里,
#                                                                       只有状态, 结果正文不进群
MSG_KINDS = ("text", "card", "status")
STATUS_WORDS = {"accepted": "接下了", "done": "交回了", "declined": "不接", "quit": "退出了"}


def joined_room(room_id: str) -> dict[str, Any]:
    """派任务前核对: 这个群在本机、我已加入。"""
    room = load(_safe_id(room_id))
    if room.get("my_state") != "joined":
        raise ValueError("你不在这个群里, 不能往群里派任务")
    return room


def joined_members(room: dict[str, Any]) -> set[str]:
    return {e for e, v in room.get("members", {}).items() if v.get("state") == "joined"}


def post_card(room_id: str, task: dict[str, Any], schedule_text: str | None = None) -> dict[str, Any]:
    """发起人派出任务后, 把任务卡发到群里 (界面「派任务」那一次点击的一部分)。"""
    me = forms_net.me()
    room = joined_room(room_id)
    assignees = [e for e in task.get("recipients") or {} if e != me]
    card = {"task_id": task["task_id"], "title": task["title"], "deadline": task.get("deadline"),
            "schedule": schedule_text, "assignees": assignees, "owner": me}
    # 群里显示的就是发起人在聊天里说的那句话 (room_text); 多阶段后面几段开始时是「进入第 k 段」
    body = (task.get("room_text") or task["text"])[:TEXT_MAX]
    return _broadcast(room, me, body, [e for e in assignees if e in _active(room)], {"card": card}, kind="card")


def echo_status(room_id: str, task_id: str, title: str, state: str, period: int | None = None) -> dict[str, Any] | None:
    """收件人点了「接下 / 交回 / 不接」: 同一次点击往群里发一条只有状态的消息。不在群里了就不发, 发不出去也不影响任务本身。"""
    if state not in STATUS_WORDS:
        return None
    try:
        me = forms_net.me()
        room = joined_room(room_id)
    except (ValueError, LookupError):
        return None
    what = f"第 {period} 期" if period else ""
    body = f"{_label(room, me)} {STATUS_WORDS[state]}「{title}」{what}"
    status = {"task_id": task_id, "title": title, "state": state, **({"period": period} if period else {})}
    try:
        return _broadcast(room, me, body, [], {"status": status}, kind="status")
    except ValueError:
        return None


def _clean_card(raw: Any, sender: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or str(raw.get("owner") or "").lower() != sender:
        return None   # 任务卡只认派任务的人自己发的
    tid = str(raw.get("task_id") or "")
    if not re.fullmatch(r"t_\d{8}_[0-9a-f]{8}", tid):
        return None
    people = [str(x).lower() for x in raw.get("assignees") or [] if isinstance(x, str) and "@" in x][:MAX_MEMBERS]
    return {"task_id": tid, "title": str(raw.get("title") or "")[:60], "deadline": str(raw.get("deadline") or "")[:20] or None,
            "schedule": str(raw.get("schedule") or "")[:80] or None, "assignees": people, "owner": sender}


def _clean_status(raw: Any, rid: str, sender: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or raw.get("state") not in STATUS_WORDS:
        return None
    tid = str(raw.get("task_id") or "")
    card = next((m.get("card") for m in reversed(messages(rid)) if m.get("kind") == "card" and (m.get("card") or {}).get("task_id") == tid), None)
    if card and sender not in card.get("assignees", []):
        return None   # 不是这张任务卡上的人: 不收 (防冒名报状态)
    period = raw.get("period")
    return {"task_id": tid, "title": str(raw.get("title") or "")[:60], "state": raw["state"],
            **({"period": int(period)} if isinstance(period, int) and period > 0 else {})}


def mark_read(room_id: str) -> dict[str, Any]:
    room = load(room_id)
    room["read_upto"] = len(messages(room_id))
    _save(room)
    return {"ok": True}


# ── 收邮筒 ───────────────────────────────────────────────────────

def _apply(msg: dict[str, Any], me: str) -> dict[str, Any] | None:
    env, kind, sender = msg["env"], msg["kind"], msg["from"]
    try:
        rid = _safe_id(env.get("room_id"))
    except ValueError:
        return None
    room = forms._read(_room_path(rid))
    if kind == "room_invite":
        owner = str(env.get("owner") or "").lower()
        if owner != sender:
            return None   # 邀请只认群主本人发的
        if room and room.get("owner") != sender:
            return None   # 同编号别人建的: 不覆盖 (防冒名)
        if room and room.get("my_state") == "joined":
            return _apply_roster(room, env, sender, me)   # 已经在群里: 当成名单更新
        roster = _clean_roster(env.get("members"))
        if me not in roster:
            return None
        room = {**(room or {}), "room_id": rid, "owner": owner, "name": str(env.get("name") or "")[:NAME_MAX] or "未命名群",
                "purpose": str(env.get("purpose") or "")[:PURPOSE_MAX], "ver": int(env.get("ver") or 1),
                "members": roster, "my_state": "invited", "invited_at": _now()}
        room.setdefault("created_at", _now())
        room.pop("ended_at", None)
        _save(room)
        return {"kind": "invite", "room_id": rid, "from": sender, "title": room["name"]}
    if not room:
        return None
    members = room.setdefault("members", {})
    if kind == "room_join":
        status = env.get("status")
        if sender not in members or members[sender].get("state") not in ACTIVE or status not in ("joined", "declined"):
            return None
        if members[sender]["state"] == status:
            return None
        members[sender]["state"] = status
        _save(room)
        if status == "joined" and room.get("my_state") == "joined":
            _system(room, f"{_label(room, sender)} 加入了群")
            return {"kind": "join", "room_id": rid, "from": sender, "title": room["name"]}
        return {"kind": "declined", "room_id": rid, "from": sender, "title": room["name"]} if status == "declined" else None
    if kind == "room_leave":
        if (members.get(sender) or {}).get("state") not in ACTIVE:
            return None
        members[sender]["state"] = "left"
        _save(room)
        _system(room, f"{_label(room, sender)} 退出了群")
        return {"kind": "leave", "room_id": rid, "from": sender, "title": room["name"]}
    if kind == "room_member":
        if sender != room.get("owner"):
            return None   # 名单 / 群名 / 解散只认群主
        return _apply_roster(room, env, sender, me)
    if kind == "room_msg":
        if room.get("my_state") not in ACTIVE:
            return None
        st = (members.get(sender) or {}).get("state")
        if st not in ACTIVE:
            return None   # 不是成员发来的不收
        mid = str(env.get("msg_id") or "")
        text = str(env.get("text") or "").strip()[:TEXT_MAX]
        if not _MSG_ID.fullmatch(mid) or not text:
            return None
        if any(m.get("msg_id") == mid for m in messages(rid)):
            return None   # 重复
        if st == "invited":   # 没收到他的「加入」就先收到消息: 他已经在群里了
            members[sender]["state"] = "joined"
        ments = [str(x).lower() for x in env.get("mentions") or [] if isinstance(x, str)][:MAX_MEMBERS]
        rt = str(env.get("reply_to") or "")
        extra: dict[str, Any] = {"reply_to": rt} if _MSG_ID.fullmatch(rt) else {}
        mkind = "text"
        if "card" in env:
            card = _clean_card(env.get("card"), sender)
            if not card:
                return None
            mkind, extra = "card", {"card": card}
        elif "status" in env:
            status = _clean_status(env.get("status"), rid, sender)
            if not status:
                return None
            mkind, extra, ments = "status", {"status": status}, []
        rec = {"msg_id": mid, "kind": mkind, "from": sender, "at": str(env.get("at") or _now()), "text": text,
               "mentions": ments, **extra, "received_at": _now()}
        _append(rid, rec)
        room["last_msg_at"] = rec["received_at"]
        _save(room)
        return {"kind": "mention" if me in ments else "msg", "room_id": rid, "from": sender, "title": room["name"],
                "text": text[:80], "joined": room.get("my_state") == "joined"}
    return None


def _clean_roster(raw: Any) -> dict[str, dict[str, str]]:
    out = {}
    for e, v in (raw or {}).items() if isinstance(raw, dict) else []:
        try:
            email = _norm_email(e)
        except ValueError:
            continue
        v = v if isinstance(v, dict) else {}
        st = v.get("state") if v.get("state") in ACTIVE else "invited"
        out[email] = {"state": st, "name": str(v.get("name") or "")[:30]}
        if len(out) >= MAX_MEMBERS:
            break
    return out


def _apply_roster(room: dict[str, Any], env: dict[str, Any], sender: str, me: str) -> dict[str, Any] | None:
    ver = int(env.get("ver") or 0)
    if ver <= int(room.get("ver") or 0):
        return None   # 重复 / 更旧的名单
    rid = room["room_id"]
    if env.get("op") == "disband":
        room["ver"], room["ended_at"] = ver, _now()
        if room.get("my_state") in ACTIVE:
            room["my_state"] = "disbanded"
        _save(room)
        _system(room, "群主解散了这个群")
        return {"kind": "disband", "room_id": rid, "from": sender, "title": room["name"]}
    roster = _clean_roster(env.get("members"))
    old = room.get("members", {})
    notes = []
    added = [e for e in roster if (old.get(e) or {}).get("state") not in ACTIVE and e != me]
    gone = [e for e, v in old.items() if v.get("state") in ACTIVE and e not in roster]
    merged = {**old}
    for e in gone:
        merged[e] = {**merged[e], "state": "removed"}
    for e, v in roster.items():
        # 我这边已经知道他加入了, 名单里还写着「已邀请」(群主那边晚一步) —— 留着「已加入」
        keep_joined = (old.get(e) or {}).get("state") == "joined" and v["state"] == "invited"
        merged[e] = {"state": "joined" if keep_joined else v["state"], "name": v["name"] or (old.get(e) or {}).get("name") or ""}
    if added:
        notes.append(f"群主邀请了 {'、'.join(merged[e]['name'] or e.split('@')[0] for e in added)}")
    if gone and me not in gone:
        notes.append(f"{'、'.join(merged[e].get('name') or e.split('@')[0] for e in gone)} 被移出了群")
    name = str(env.get("name") or room["name"])[:NAME_MAX]
    purpose = str(env.get("purpose") or "")[:PURPOSE_MAX]
    if name != room["name"]:
        notes.append(f"群名改成「{name}」")
    if purpose != (room.get("purpose") or ""):
        notes.append("群主更新了群公告")
    room.update(ver=ver, name=name, purpose=purpose, members=merged)
    removed_me = me in gone or (env.get("removed") and me in env.get("removed"))
    if removed_me and room.get("my_state") in ACTIVE:
        room["my_state"], room["ended_at"] = "removed", _now()
        notes = ["你被群主移出了群"]
    _save(room)
    for n in notes:
        _system(room, n)
    if removed_me:
        return {"kind": "removed", "room_id": rid, "from": sender, "title": room["name"]}
    return {"kind": "member", "room_id": rid, "from": sender, "title": room["name"]} if notes else None


def sync() -> dict[str, Any]:
    me = forms_net.me()
    state_path = rooms_dir() / "_sync.json"
    last = (forms._read(state_path) or {}).get("at")
    gap = False
    if last:
        try:
            gap = datetime.now().astimezone() - datetime.fromisoformat(last) > timedelta(days=GAP_DAYS)
        except ValueError:
            gap = False
    events = []
    for msg in forms_net.take(forms_net.ROOM_KINDS):
        ev = _apply(msg, me)
        if ev:
            events.append(ev)
    forms._write(state_path, {"at": _now()})
    if gap:
        for rid in _room_ids():
            room = forms._read(_room_path(rid))
            if room and room.get("my_state") == "joined":
                _system(room, f"超过 {GAP_DAYS} 天没打开 Companion, 这期间的群消息可能没收到")
    return {"ok": True, "events": events, "gap": gap}


# ── 列表 / 详情 ──────────────────────────────────────────────────

def _room_ids() -> list[str]:
    return [p.parent.name for p in rooms_dir().glob("r_*/room.json") if _ID.fullmatch(p.parent.name)]


def summary(room: dict[str, Any], msgs: list[dict[str, Any]] | None = None, me: str = "") -> dict[str, Any]:
    msgs = messages(room["room_id"]) if msgs is None else msgs
    # 读到第几条 (按本机记录的条数, 不按时间: 同一秒里收到的不会被当成已读)
    unread = [m for m in msgs[int(room.get("read_upto") or 0):] if m.get("kind") in MSG_KINDS and m.get("from") != me]
    last = next((m for m in reversed(msgs) if m.get("kind") in MSG_KINDS), None)
    return {"room_id": room["room_id"], "name": room["name"], "purpose": room.get("purpose") or "",
            "owner": room["owner"], "my_state": room.get("my_state"),
            "members": len(_active(room)), "joined": sum(1 for v in room.get("members", {}).values() if v.get("state") == "joined"),
            "unread": len(unread) if room.get("my_state") == "joined" else 0,
            "mentioned": sum(1 for m in unread if me and me in (m.get("mentions") or [])) if room.get("my_state") == "joined" else 0,
            "last": ({"from": last.get("from"), "name": _label(room, last.get("from") or ""), "text": last["text"][:60], "at": last.get("at")}
                     if last else None),
            "activity": room.get("last_msg_at") or room.get("invited_at") or room.get("created_at") or ""}


def list_rooms() -> dict[str, Any]:
    try:
        me = forms_net.me()
    except ValueError:
        me = ""
    rooms = []
    for rid in _room_ids():
        room = forms._read(_room_path(rid))
        if room and room.get("room_id") == rid:
            rooms.append(summary(room, me=me))
    rooms.sort(key=lambda r: (r["my_state"] == "invited", r["activity"]), reverse=True)
    return {"ok": True, "me": me, "rooms": rooms}


def get(room_id: str) -> dict[str, Any]:
    me = forms_net.me()
    room = load(room_id)
    msgs = messages(room_id)
    people = [{"email": e, "name": v.get("name") or "", "state": v.get("state"), "owner": e == room["owner"], "me": e == me}
              for e, v in room.get("members", {}).items()]
    order = {"joined": 0, "invited": 1}
    people.sort(key=lambda p: (not p["owner"], order.get(p["state"], 2), p["name"] or p["email"]))
    return {"ok": True, "me": me, "room": summary(room, msgs, me), "members": people,
            "messages": msgs[-HISTORY_LIMIT:], "older": max(0, len(msgs) - HISTORY_LIMIT)}


_LOCK = threading.RLock()


def pim_handlers(params: dict[str, Any]) -> dict[str, Any]:
    """Companion 界面 (Rust commands/colleague_rooms.rs → pim/croom_*)。按钮本身就是员工的确认。"""
    rid = str(params.get("room_id") or "")
    return forms.serialized({
        "pim/croom_list": list_rooms,
        "pim/croom_get": lambda: get(rid),
        "pim/croom_sync": sync,
        "pim/croom_create": lambda: create(params),
        "pim/croom_join": lambda: join(rid),
        "pim/croom_decline": lambda: decline(rid),
        "pim/croom_send": lambda: send(rid, params.get("text"), params.get("mentions"), params.get("reply_to")),
        "pim/croom_read": lambda: mark_read(rid),
        "pim/croom_add": lambda: add_members(rid, params.get("members")),
        "pim/croom_remove": lambda: remove_member(rid, str(params.get("email") or "")),
        "pim/croom_update": lambda: update_info(rid, params),
        "pim/croom_disband": lambda: disband(rid),
        "pim/croom_leave": lambda: leave(rid),
        "pim/croom_delete": lambda: delete_local(rid),
    }, _LOCK)
