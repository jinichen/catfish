"""同事群聊 · 补发 / 重发 / 给界面标送达和文件状态 (11/07; 从 colleague_rooms.py 拆出来, 守 800 行)。

规矩在 colleague_rooms_outbox.py (没送到的信) 和 colleague_rooms_files.py (群文件) 的文件头。
"""
from __future__ import annotations

import logging
from typing import Any

from . import colleague_rooms as cr
from . import colleague_rooms_files as rfiles
from . import colleague_rooms_outbox as outbox
from . import colleague_rooms_store as store
from . import forms_net

logger = logging.getLogger("catfish.tool_bridge.rooms")


def retry_and_mend() -> None:
    """11/07 顺手补发到点的 (outbox), 找发件人要没收齐的文件块。出错不影响取邮筒。"""
    try:
        outbox.retry_due()
        for rid, sender, env in rfiles.missing_requests():
            room = cr._room(rid)
            if room and room.get("my_state") == "joined" and (room.get("members", {}).get(sender) or {}).get("state") in cr.ACTIVE:
                cr._post_all([sender], "room_msg", env, "room-file", room=room)
        rfiles.drop_orphans()
        from . import colleague_rooms_bigfiles as big  # noqa: PLC0415
        big.drop_orphans()
        if big.pending():
            big.kick()
    except Exception as e:  # noqa: BLE001
        logger.warning("补发 / 要缺块失败: %s", e)


def resend(room_id: str, msg_id: str, email: Any = None) -> dict[str, Any]:
    """界面「重发」: 这条消息 (或系统提示) 下没送到的马上再发一次。
    升级前发的、只在消息上记了 undelivered 的: 按那条消息重拼信封再发, 送到的从 undelivered 里划掉。"""
    room = cr.load(room_id)
    rid = room["room_id"]
    if not cr._MSG_ID.fullmatch(str(msg_id or "")):
        raise ValueError("消息编号不对")
    who = cr._norm_email(email) if email else None
    msg = store.get_message(rid, msg_id)
    if msg and msg.get("undelivered") and msg.get("kind") in ("text", "card", "auto"):
        me = forms_net.me()
        joined = set(cr._joined_others(room, me))
        targets = [e for e in msg["undelivered"] if (who is None or e == who) and e in joined]
        if targets:
            env = {"v": cr.ENVELOPE_VERSION, "room_id": rid, "msg_id": msg_id, "at": msg["at"], "text": msg["text"],
                   "mentions": msg.get("mentions") or [],
                   **{k: msg[k] for k in ("reply_to", "card", "auto") if k in msg}}
            cr._post_all(targets, "room_msg", env, "room-auto-post" if msg["kind"] == "auto" else None, room=room, ref=msg_id)
        # 发出去的、发不出去的 (已经进 outbox 了) 都不再算老记号
        msg["undelivered"] = [e for e in msg["undelivered"] if e not in targets and e in joined]
        if not msg["undelivered"]:
            msg.pop("undelivered")
        store.update_message(rid, msg)
    return outbox.resend(rid, msg_id, who)


def decorate(rid: str, msgs: list[dict[str, Any]], me: str) -> None:
    """11/07 给界面: 每条下面没送到的 (delivery) / 文件收齐没有 (files[].have / got)。
    升级前只记了 undelivered 的老消息, 当成「没送到 · 可重发」。"""
    pending = outbox.by_ref(rid, [m["msg_id"] for m in msgs if m.get("from") == me or m.get("kind") == "system"])
    for m in msgs:
        rows = pending.get(m["msg_id"], [])
        known = {r["email"] for r in rows}
        rows += [{"email": e, "state": "failed", "error": "升级前发的, 当时没送到", "left": 1}
                 for e in m.get("undelivered") or [] if e not in known]
        if rows:
            m["delivery"] = rows
        if m.get("files"):
            from . import colleague_rooms_bigfiles as big  # noqa: PLC0415
            st = rfiles.status(rid, [f for f in m["files"] if not f.get("blob")])
            st.update(big.status(rid, [f["id"] for f in m["files"] if f.get("blob")]))
            # 钥匙不给界面 (界面用不着); 大文件标 big 状态 (上传中 / 等对方传完 / 接收中 / 中央已删 …)
            m["files"] = [{**{k: v for k, v in f.items() if k != "blob"}, **({"via": "central"} if f.get("blob") else {}),
                           **st.get(f["id"], {"have": False, "got": 0})} for f in m["files"]]
