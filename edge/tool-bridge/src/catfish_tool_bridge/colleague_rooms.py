"""同事群聊 (11/5, docs/GROUP-CHAT-PLAN.md 第一步: 能用的群聊)。

真人同事 + 各自的小鲶的群, 不是一群 AI 互相聊。中央只当邮筒 (跟派任务 / 填表同一个, 取走即清空):
**一条群消息 = 给每个成员各投一封**, 群的聊天记录只在每个成员自己电脑上, 中央和 web 都看不到。
每一封都是员工在界面上点了才发 (发消息 / 建群 / 加入 / 群主改名单 / 退群)。

本机账本: `~/.catfish/collab_rooms/rooms.db` (SQLite, 11/6 起; 见 colleague_rooms_store.py)
  rooms      群: 名字 / 用途 / 群主 / 名单版本 / 成员 (我看到的状态) / 我的状态 / 已读到哪条 (read_upto = seq)
  messages   消息一条一行 (系统提示 kind=system 也在这里), seq = 本机收到的先后
  meta       上次取邮筒的时间 (超过 7 天没取 = 中间可能缺消息)
  (1.2.75 之前是每群一个目录 room.json + messages.jsonl, 第一次打开时导进库, 老目录改名 .migrated 留作备份)

邮筒里的信封 (JSON, 中央看不懂也不看), 期限 7 天:
  room_invite  {v, room_id, ver, name, purpose, owner, members: {邮箱: {state, name}}, at}   群主拉人
  room_join    {v, room_id, status: joined|declined, at}        加入发给群里所有人 / 不加入只回群主
  room_msg     {v, room_id, msg_id, at, text, mentions[], reply_to?, files?}
               11/07 同一种信还装两样不进聊天记录的: chunk (群文件的一块) / need (找发件人要缺的块), 见 colleague_rooms_files.py
  room_member  {v, room_id, ver, op: update|disband, name, purpose, members: {...}, owner?, at}   只认群主
               11/07 owner: 群主转让 (新群主必须是已加入的成员); 之后只认新群主发的, 版本号 ver 照常 +1
  room_leave   {v, room_id, at}                                 退群, 发给群里所有人

规则 (11/5 鸿波定): 一个群最多 50 人; 一条消息 ≤ 4000 字; 离线最多补 7 天; 后加入的人看不到加入前的记录;
不是成员发来的不收, 名单 / 群名 / 解散只认群主发的 (防冒名)。

11/07 没送到的不再只弹一次红字: 每封记进 outbox, 挂在那条消息 / 系统提示下面, 按原因自动补发, 界面可「重发」
(colleague_rooms_outbox.py)。群主可以把群主转给已加入的成员, 转了才能退群。
"""
from __future__ import annotations

import re
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import colleague_rooms_files as rfiles
from . import colleague_rooms_mend as mend
from . import colleague_rooms_outbox as outbox
from . import colleague_rooms_retention as keep
from . import colleague_rooms_store as store
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
    return store.rooms_dir()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_id(room_id: Any) -> str:
    rid = str(room_id or "").strip()
    if not _ID.fullmatch(rid):
        raise ValueError(f"群编号不对: {room_id!r}")
    return rid


def _room(room_id: Any) -> dict[str, Any] | None:
    """本机库里的群 (没有 = None)。"""
    return store.get_room(_safe_id(room_id))


def load(room_id: str) -> dict[str, Any]:
    room = _room(room_id)
    if not room:
        raise LookupError(f"没有这个群: {room_id}")
    return room


def _save(room: dict[str, Any]) -> None:
    store.put_room(room)


def messages(room_id: str) -> list[dict[str, Any]]:
    """这个群本机的全部消息 (按收到的先后, 每条带 seq)。界面只取最近一页, 用 store.recent。"""
    return store.all_messages(_safe_id(room_id))


def _append(room_id: str, msg: dict[str, Any]) -> None:
    store.add_message(room_id, msg)


def _mark_read(room: dict[str, Any]) -> None:
    room["read_upto"] = store.max_seq(room["room_id"])   # 已读到的最大 seq (删旧消息不错位)


def _system(room: dict[str, Any], text: str) -> str:
    """记一条系统提示, 返回它的 msg_id (这次投递没送到的信挂在它下面)。"""
    mid = f"m_{secrets.token_hex(8)}"
    _append(room["room_id"], {"msg_id": mid, "kind": "system", "at": _now(), "text": text})
    return mid


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


def _post_all(emails: list[str], kind: str, env: dict[str, Any], source: str | None = None, *,
              room: dict[str, Any] | None = None, ref: str | None = None) -> list[dict[str, str]]:
    """给每个人各投一封 (并发 8 路)。返回没送到的 [{email, error, reason}]。source: 数据外发记录里的用途标签。
    room 给了: 没送到的记进 outbox (挂在 ref 那条消息 / 系统提示下面), 自动补发、界面可重发 —— 不再靠弹一次红字。"""
    def one(e: str) -> dict[str, str] | None:
        try:
            if source:
                forms_net.post(e, kind, env, TTL_SECONDS, source=source)
            else:
                forms_net.post(e, kind, env, TTL_SECONDS)
            return None
        except ValueError as err:
            return {"email": e, "error": str(err)[:300], "reason": outbox.classify(err)}
    if not emails:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(emails))) as ex:
        failed = [r for r in ex.map(one, emails) if r]
    if failed and room:
        outbox.record(room["room_id"], ref, failed, kind, env, source)
    return failed


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
    ref = _system(room, f"你建了群「{name}」, 邀请了 {len(people)} 位同事")
    failed = _post_all(list(people), "room_invite", _invite_env(room), room=room, ref=ref)
    return {"ok": True, "room": summary(room, me=me), "failed": failed}


def _invite_env(room: dict[str, Any]) -> dict[str, Any]:
    return {"v": ENVELOPE_VERSION, "room_id": room["room_id"], "ver": room["ver"], "name": room["name"],
            "purpose": room.get("purpose") or "", "owner": room["owner"], "members": _roster(room), "at": _now()}


def _member_env(room: dict[str, Any], op: str = "update") -> dict[str, Any]:
    return {"v": ENVELOPE_VERSION, "room_id": room["room_id"], "ver": room["ver"], "op": op, "name": room["name"],
            "purpose": room.get("purpose") or "", "members": _roster(room), "at": _now(),
            "retention_days": room.get("retention_days")}   # 11/6 群主设的记录保留天数 (空 = 跟公司 / 默认)


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
    ref = _system(room, f"你邀请了 {'、'.join(_label(room, e) for e in new)}")
    failed = _post_all(list(new), "room_invite", _invite_env(room), room=room, ref=ref)
    failed += _post_all(old_joined, "room_member", _member_env(room), room=room, ref=ref)
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
    ref = _system(room, f"你把 {_label(room, email)} 移出了群")
    env = _member_env(room)
    env["removed"] = [email]
    failed = _post_all(sorted(set(notify) | {email}), "room_member", env, room=room, ref=ref)
    return {"ok": True, "room": summary(room, me=me), "failed": failed}


def update_info(room_id: str, args: dict[str, Any]) -> dict[str, Any]:
    me = forms_net.me()
    room = load(room_id)
    _require_owner(room, me)
    name = str(args.get("name") if args.get("name") is not None else room["name"]).strip()[:NAME_MAX]
    if not name:
        raise ValueError("群名不能空")
    purpose = str(args.get("purpose") if args.get("purpose") is not None else room.get("purpose") or "").strip()[:PURPOSE_MAX]
    keep_days = keep.check_room_days(args["retention_days"]) if "retention_days" in args else room.get("retention_days")
    if name == room["name"] and purpose == (room.get("purpose") or "") and keep_days == room.get("retention_days"):
        return {"ok": True, "room": summary(room, me=me), "failed": []}
    changed = []
    if name != room["name"]:
        changed.append(f"群名改成「{name}」")
    if purpose != (room.get("purpose") or ""):
        changed.append("更新了群公告")
    if keep_days != room.get("retention_days"):
        changed.append(f"把记录保留改成 {keep_days} 天" if keep_days else "把记录保留改回跟公司规定")
    room.update(name=name, purpose=purpose, ver=room["ver"] + 1, retention_days=keep_days)
    _save(room)
    ref = _system(room, f"你{'、'.join(changed)}")
    failed = _post_all(_active_others(room, me), "room_member", _member_env(room), room=room, ref=ref)
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
    ref = _system(room, "你解散了这个群")
    failed = _post_all(notify, "room_member", _member_env(room, "disband"), room=room, ref=ref)
    return {"ok": True, "room": summary(room, me=me), "failed": failed}


def transfer(room_id: str, email: str) -> dict[str, Any]:
    """群主转让 (11/07, 参照 hermes 群聊的「换主持 + 版本号」): 新群主必须已加入; 版本号 +1 通知所有人,
    之后大家只认新群主发的名单 / 群名 / 解散 —— 原群主再发的一律不认。转了之后原群主就能退群了。
    (原群主电脑坏了 / 人走了、没来得及转: 这一步帮不上, 群里其他人照样能聊, 只是没人能改名单。)"""
    me = forms_net.me()
    room = load(room_id)
    _require_owner(room, me)
    email = _norm_email(email)
    if email == me:
        raise ValueError("你已经是群主了")
    if (room["members"].get(email) or {}).get("state") != "joined":
        raise ValueError("只能转给已经加入的成员")
    notify = _active_others(room, me)
    room["owner"] = email
    room["ver"] += 1
    _save(room)
    ref = _system(room, f"你把群主转给了 {_label(room, email)}")
    env = _member_env(room)
    env["owner"] = email
    failed = _post_all(notify, "room_member", env, room=room, ref=ref)
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
    ref = _system(room, "你加入了群 (加入前的消息看不到)")
    _mark_read(room)
    _save(room)
    env = {"v": ENVELOPE_VERSION, "room_id": room["room_id"], "status": "joined", "at": _now()}
    failed = _post_all(_active_others(room, me), "room_join", env, room=room, ref=ref)
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
    failed = _post_all([room["owner"]], "room_join", env, room=room)
    return {"ok": True, "room": summary(room, me=me), "failed": failed}


def leave(room_id: str) -> dict[str, Any]:
    me = forms_net.me()
    room = load(room_id)
    if room.get("owner") == me:
        raise ValueError("群主不能直接退群: 先在成员栏把群主转给别人, 或者解散这个群")
    if room.get("my_state") != "joined":
        raise ValueError("你已经不在这个群里")
    notify = _active_others(room, me)
    room["my_state"], room["ended_at"] = "left", _now()
    room["members"].setdefault(me, {"name": ""})["state"] = "left"
    _save(room)
    ref = _system(room, "你退出了群")
    failed = _post_all(notify, "room_leave", {"v": ENVELOPE_VERSION, "room_id": room["room_id"], "at": _now()}, room=room, ref=ref)
    return {"ok": True, "room": summary(room, me=me), "failed": failed}


def delete_local(room_id: str) -> dict[str, Any]:
    """已经结束的群 (退了 / 被移出 / 解散 / 没加入), 把本机的记录删掉。还在的群不能删 (要先退)。"""
    room = load(room_id)
    if room.get("my_state") in ("joined", "invited"):
        raise ValueError("还在群里的不能删记录; 先退群 (群主先解散)")
    store.delete_room(_safe_id(room_id))
    return {"ok": True}


# ── 发消息 / 已读 ────────────────────────────────────────────────

def send(room_id: str, text: Any, mentions: Any = None, reply_to: Any = None, files: Any = None) -> dict[str, Any]:
    me = forms_net.me()
    room = load(room_id)
    if room.get("my_state") != "joined":
        raise ValueError("你不在这个群里, 发不了消息")
    body = str(text or "").strip()
    attached = rfiles.prepare(files)   # 11/07 群文件 (先核对, 不合规直接报, 一封都不发)
    if not body and not attached:
        raise ValueError("消息是空的")
    if len(body) > TEXT_MAX:
        raise ValueError(f"一条消息最多 {TEXT_MAX} 字, 长材料请用邮件")
    members = set(_active(room))
    ments = [e for e in dict.fromkeys(_norm_email(x) for x in (mentions or [])) if e in members and e != me]
    rt = str(reply_to or "").strip() or None
    if rt and not _MSG_ID.fullmatch(rt):
        rt = None
    extra: dict[str, Any] = {**({"reply_to": rt} if rt else {}), **({"files": rfiles.meta(attached)} if attached else {})}
    return _broadcast(room, me, body, ments, extra, attached=attached)


def _broadcast(room: dict[str, Any], me: str, body: str, ments: list[str], extra: dict[str, Any],
               kind: str = "text", source: str | None = None, attached: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """发给群里已加入的其他人 + 记进本机。extra = 信封和本机记录里都带的附加字段 (reply_to / card / status / files)。
    没送到的记进 outbox 挂在这条下面 (界面显示「张三没收到 · 重发」), 不再写 undelivered。
    attached: 群文件 —— 消息先发, 文件分块排进 outbox 后台发 (发送按钮不等)。"""
    msg = {"msg_id": f"m_{secrets.token_hex(10)}", "kind": kind, "from": me, "at": _now(), "text": body,
           "mentions": ments, **extra}
    others = _joined_others(room, me)
    env = {"v": ENVELOPE_VERSION, "room_id": room["room_id"], "msg_id": msg["msg_id"], "at": msg["at"],
           "text": body, "mentions": ments, **extra}
    _append(room["room_id"], msg)   # 先记本机: 没送到的信要挂在它下面
    if attached:
        rfiles.keep_sent(room["room_id"], msg, attached, me)
    failed = _post_all(others, "room_msg", env, source, room=room, ref=msg["msg_id"])
    if attached:
        rfiles.send_chunks(room["room_id"], msg["msg_id"], attached, others)
    room["last_msg_at"] = msg["at"]
    _mark_read(room)
    _save(room)
    return {"ok": True, "message": msg, "sent_to": len(others) - len(failed), "failed": failed}


MSG_KINDS = ("text", "card", "status", "auto")   # 列表预览的消息种类
# 11/06 鸿波「协同的都看过、处理了, 为什么还有数字」: 群里派的任务卡 / 任务状态行原来也算未读, 而任务本身在
# 「协同事项」里已经处理 (接下 / 不接 / 交回) —— 那里不会把群里那张卡标成已读, 红点一直挂着 (实测「测试」群 9 条未读里
# 7 张是已处理的任务卡)。任务卡要处理的那一下已经算在协同事项的待办数里 (待处理的任务), 群里再算一遍是重复。
# 所以未读 / @我 只数聊天消息和自动转发的结果; 卡和状态照样显示在群里、照样做最后一条预览。
UNREAD_KINDS = ("text", "auto")


def mark_read(room_id: str) -> dict[str, Any]:
    room = load(room_id)
    _mark_read(room)
    _save(room)
    return {"ok": True}


# ── 收邮筒 ───────────────────────────────────────────────────────

def _apply(msg: dict[str, Any], me: str) -> dict[str, Any] | None:
    env, kind, sender = msg["env"], msg["kind"], msg["from"]
    try:
        rid = _safe_id(env.get("room_id"))
    except ValueError:
        return None
    room = _room(rid)
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
        if not _MSG_ID.fullmatch(mid):
            return None
        if "chunk" in env:   # 11/07 群文件的一块: 不进聊天记录, 收齐了挂到那条消息上
            rfiles.take_chunk(rid, sender, env)
            return None
        if "need" in env:    # 同事来要缺的块: 我发的我补
            rfiles.serve_need(rid, sender, me, env)
            return None
        text = str(env.get("text") or "").strip()[:TEXT_MAX]
        got_files = rfiles.clean_meta(env.get("files"))
        if not text and not got_files:
            return None
        if store.has_message(rid, mid):
            return None   # 重复
        if st == "invited":   # 没收到他的「加入」就先收到消息: 他已经在群里了
            members[sender]["state"] = "joined"
        ments = [str(x).lower() for x in env.get("mentions") or [] if isinstance(x, str)][:MAX_MEMBERS]
        rt = str(env.get("reply_to") or "")
        extra: dict[str, Any] = {"reply_to": rt} if _MSG_ID.fullmatch(rt) else {}
        if got_files:
            extra["files"] = got_files
        mkind = "text"
        from . import colleague_rooms_tasks as rt  # noqa: PLC0415  (第二 / 三步: 任务卡 / 状态 / 自动转发)
        if "card" in env:
            card = rt._clean_card(env.get("card"), sender)
            if not card:
                return None
            mkind, extra = "card", {"card": card}
        elif "status" in env:
            status = rt._clean_status(env.get("status"), rid, sender)
            if not status:
                return None
            mkind, extra, ments = "status", {"status": status}, []
        elif "auto" in env:
            auto = rt._clean_auto(env.get("auto"), rid, sender)
            if not auto:
                return None
            mkind, extra, ments = "auto", {"auto": auto}, []
        rec = {"msg_id": mid, "kind": mkind, "from": sender, "at": str(env.get("at") or _now()), "text": text,
               "mentions": ments, **extra, "received_at": _now(),
               **({"plain": True} if env.get("_plain") else {})}   # 老版本发来的明文: 界面标「未加密」
        _append(rid, rec)
        room["last_msg_at"] = rec["received_at"]
        _save(room)
        if got_files:
            rfiles.assemble(rid, rec)   # 块可能比消息先到
        return {"kind": "mention" if me in ments else "msg", "room_id": rid, "from": sender, "title": room["name"],
                "text": (text or "📎 " + "、".join(f["name"] for f in got_files))[:80], "joined": room.get("my_state") == "joined"}
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
    keep_days = keep._valid(env.get("retention_days"))
    if keep_days != room.get("retention_days"):
        notes.append(f"群主把记录保留改成 {keep_days} 天" if keep_days else "群主把记录保留改回跟公司规定")
    new_owner = str(env.get("owner") or "").lower()
    if new_owner and new_owner != room.get("owner") and (merged.get(new_owner) or {}).get("state") == "joined":
        room["owner"] = new_owner   # 11/07 群主转让: 之后只认新群主
        notes.append("群主把群主转给了你" if new_owner == me else f"群主把群主转给了 {merged[new_owner].get('name') or new_owner.split('@')[0]}")
    room.update(ver=ver, name=name, purpose=purpose, members=merged, retention_days=keep_days)
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
    last = store.get_meta("sync_at")
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
    store.set_meta("sync_at", _now())
    _note_key_changes()
    keep.maintain()   # 到期删 (最多 10 分钟一次)
    mend.retry_and_mend()
    if gap:
        for rid in _room_ids():
            room = _room(rid)
            if room and room.get("my_state") == "joined":
                _system(room, f"超过 {GAP_DAYS} 天没打开 Companion, 这期间的群消息可能没收到")
    return {"ok": True, "events": events, "gap": gap}


def _note_key_changes() -> None:
    """同事的加密公钥变了 (换了电脑, 或者中央换了钥): 在他所在的群里提示一句, 指纹可在成员栏核对。"""
    from . import mailbox_crypto  # noqa: PLC0415
    changes = mailbox_crypto.pop_key_changes()
    if not changes:
        return
    for rid in _room_ids():
        room = _room(rid)
        if not room or room.get("my_state") != "joined":
            continue
        for c in changes:
            if (room.get("members", {}).get(c["email"]) or {}).get("state") in ACTIVE:
                _system(room, f"⚠ {_label(room, c['email'])} 的加密密钥变了 (多半是换了电脑), 新指纹 {c['fingerprint']}")


# ── 列表 / 详情 ──────────────────────────────────────────────────

def _room_ids() -> list[str]:
    return [r for r in store.room_ids() if _ID.fullmatch(r)]


def summary(room: dict[str, Any], me: str = "") -> dict[str, Any]:
    rid = room["room_id"]
    # 已读到哪条按 seq 记 (本机收到的先后, 不按时间: 同一秒里收到的不会被当成已读; 删旧消息也不错位)
    unread = [m for m in store.after(rid, int(room.get("read_upto") or 0), UNREAD_KINDS) if m.get("from") != me]
    last = store.last_of(rid, MSG_KINDS)
    return {"room_id": room["room_id"], "name": room["name"], "purpose": room.get("purpose") or "",
            "owner": room["owner"], "my_state": room.get("my_state"),
            "members": len(_active(room)), "joined": sum(1 for v in room.get("members", {}).values() if v.get("state") == "joined"),
            "unread": len(unread) if room.get("my_state") == "joined" else 0,
            "mentioned": sum(1 for m in unread if me and me in (m.get("mentions") or [])) if room.get("my_state") == "joined" else 0,
            "last": ({"from": last.get("from"), "name": _label(room, last.get("from") or ""), "text": _preview(last)[:60], "at": last.get("at"),
                      "kind": last.get("kind")}
                     if last else None),
            "activity": room.get("last_msg_at") or room.get("invited_at") or room.get("created_at") or "",
            "retention": keep.effective(room), "retention_days": room.get("retention_days")}


def _preview(m: dict[str, Any]) -> str:
    """只发了文件的消息, 预览写文件名。"""
    if m.get("text"):
        return m["text"]
    return "📎 " + "、".join(f.get("name", "") for f in m.get("files") or [])


def list_rooms() -> dict[str, Any]:
    try:
        me = forms_net.me()
    except ValueError:
        me = ""
    rooms = []
    for rid in _room_ids():
        room = _room(rid)
        if room and room.get("room_id") == rid:
            rooms.append(summary(room, me))
    rooms.sort(key=lambda r: (r["my_state"] == "invited", r["activity"]), reverse=True)
    return {"ok": True, "me": me, "rooms": rooms}


def get(room_id: str, before: Any = None, around: Any = None) -> dict[str, Any]:
    """群详情 + 一页消息。before = 往上翻 (只要比这条 seq 早的); around = 搜索结果点进去 (这条前后各一些)。"""
    me = forms_net.me()
    room = load(room_id)
    rid = room["room_id"]
    if around:
        msgs = store.around(rid, int(around))
    else:
        msgs = store.recent(rid, HISTORY_LIMIT, int(before) if before else None)
    people = [{"email": e, "name": v.get("name") or "", "state": v.get("state"), "owner": e == room["owner"], "me": e == me}
              for e, v in room.get("members", {}).items()]
    order = {"joined": 0, "invited": 1}
    people.sort(key=lambda p: (not p["owner"], order.get(p["state"], 2), p["name"] or p["email"]))
    older = store.count_before(rid, msgs[0]["seq"]) if msgs else 0
    mend.decorate(rid, msgs, me)
    return {"ok": True, "me": me, "room": {**summary(room, me), "issue": outbox.room_issue(rid)},
            "members": people, "messages": msgs, "older": older}


def search(room_id: str, q: Any, from_email: Any = None) -> dict[str, Any]:
    """群内搜索本机记录 (§4.4): 正文包含 q, 可按人筛; 最多 50 条, 新的在前。"""
    room = load(room_id)
    text = str(q or "").strip()
    if not text:
        raise ValueError("要搜什么? 写个关键词")
    who = _norm_email(from_email) if from_email else None
    hits = store.search(room["room_id"], text[:100], who)
    return {"ok": True, "q": text, "results": [{**m, "name": _label(room, m.get("from") or "")} for m in hits]}


_LOCK = threading.RLock()


def _snapshot(dest: Any) -> dict[str, Any]:
    """换电脑导出: Companion 要一份本机群聊库的快照 (加密的库出来也是加密的)。只许写到 ~/.catfish 下面的 .db。"""
    home = wiki_files._catfish_home().resolve()
    p = Path(str(dest or "")).expanduser()
    if not p.is_absolute() or p.suffix != ".db" or home not in p.resolve().parents:
        raise ValueError("快照位置不对 (只能在 ~/.catfish 下面)")
    p.parent.mkdir(parents=True, exist_ok=True)
    store.snapshot(p)
    return {"ok": True, "path": str(p), **store.crypt.status(store.db_path())}


def pim_handlers(params: dict[str, Any]) -> dict[str, Any]:
    """Companion 界面 (Rust commands/colleague_rooms.rs → pim/croom_*)。按钮本身就是员工的确认。"""
    rid = str(params.get("room_id") or "")
    return forms.serialized({
        "pim/croom_list": list_rooms,
        "pim/croom_get": lambda: get(rid, params.get("before"), params.get("around")),
        "pim/croom_search": lambda: search(rid, params.get("q"), params.get("from")),
        "pim/croom_settings_get": keep.settings,
        "pim/croom_settings_set": lambda: keep.set_personal(params.get("days")),
        "pim/croom_snapshot": lambda: _snapshot(params.get("dest")),
        "pim/croom_sync": sync,
        "pim/croom_create": lambda: create(params),
        "pim/croom_join": lambda: join(rid),
        "pim/croom_decline": lambda: decline(rid),
        "pim/croom_send": lambda: send(rid, params.get("text"), params.get("mentions"), params.get("reply_to"), params.get("files")),
        "pim/croom_read": lambda: mark_read(rid),
        "pim/croom_add": lambda: add_members(rid, params.get("members")),
        "pim/croom_remove": lambda: remove_member(rid, str(params.get("email") or "")),
        "pim/croom_update": lambda: update_info(rid, params),
        "pim/croom_disband": lambda: disband(rid),
        "pim/croom_transfer": lambda: transfer(rid, str(params.get("email") or "")),
        "pim/croom_resend": lambda: mend.resend(rid, str(params.get("msg_id") or ""), params.get("email")),
        "pim/croom_file_save": lambda: rfiles.save(load(rid), str(params.get("file_id") or "")),
        "pim/croom_leave": lambda: leave(rid),
        "pim/croom_delete": lambda: delete_local(rid),
    }, _LOCK)
