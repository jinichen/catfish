"""群聊记录在本机保留多久 (11/6, docs/GROUP-CHAT-PLAN.md §4.2)。

四层, **取最短**, 只能往短里调 (不能突破公司上限):

    默认   ROOM_RETENTION_DEFAULT = 180 天
    公司   中央管理门户「协作设置」→ GET /api/org/policy {room_retention_days} (每小时取一次, 取不到用上次的 / 默认)
    群     群主在「改群名 / 公告」里设, 随 room_member 通知所有成员 (room["retention_days"])
    个人   员工在群聊页「记录保留」里设, 只管这台电脑 (库里 meta: personal_retention_days)

到期删 (同步时顺手, 最多 10 分钟一次): 消息按时间算, 超过期限的从本机库删掉; 群结束 (退群 / 被移出 / 解散 / 没加入)
之后, 结束时间超过期限的整群删掉。群里派出的任务不受影响 (任务单和交回的结果在「协同事项」那边另有记录)。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Any

from . import colleague_rooms_store as store

ROOM_RETENTION_DEFAULT = 180
RETENTION_MIN, RETENTION_MAX = 7, 3650
POLICY_TTL = 3600          # 公司设置每小时取一次
PRUNE_EVERY = 600          # 最多 10 分钟清一次
POLICY_PATH = "/api/org/policy"

SOURCE_LABEL = {"default": "默认", "company": "公司规定", "room": "群主设置", "personal": "你自己设的"}


def _valid(v: Any) -> int | None:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if RETENTION_MIN <= n <= RETENTION_MAX else None


def company_days(*, refresh: bool = False) -> int | None:
    """公司规定的上限 (没设 / 老中央 = None)。每小时去中央取一次, 取不到就用上次取到的。"""
    raw = store.get_meta("org_policy")
    cached = json.loads(raw) if raw else {}
    if not refresh and cached and time.time() - float(cached.get("fetched_at") or 0) < POLICY_TTL:
        return _valid(cached.get("room_retention_days"))
    from . import forms_net  # noqa: PLC0415
    try:
        code, data = forms_net._client_call("GET", POLICY_PATH)
    except ValueError:
        return _valid(cached.get("room_retention_days"))
    if code == 200 and isinstance(data, dict):
        days = _valid(data.get("room_retention_days"))
    elif code in (404, 405):
        days = None   # 老中央还没有公司设置: 按默认
    else:
        return _valid(cached.get("room_retention_days"))
    store.set_meta("org_policy", json.dumps({"room_retention_days": days, "fetched_at": time.time()}))
    return days


def personal_days() -> int | None:
    return _valid(store.get_meta("personal_retention_days"))


def ceiling() -> tuple[int, str]:
    """群主 / 个人能设的上限 (= 公司规定, 没有就默认)。"""
    c = company_days()
    return (c, "company") if c else (ROOM_RETENTION_DEFAULT, "default")


def effective(room: dict[str, Any] | None) -> dict[str, Any]:
    """这个群在本机保留几天、谁定的: {days, source, label}。"""
    days, source = ceiling()
    for d, src in ((_valid((room or {}).get("retention_days")), "room"), (personal_days(), "personal")):
        if d and d < days:
            days, source = d, src
    return {"days": days, "source": source, "label": SOURCE_LABEL[source]}


def check_room_days(value: Any) -> int | None:
    """群主改「记录保留」: 空 = 跟公司 / 默认; 有值要在范围内且不能比上限长。"""
    if value in (None, "", 0):
        return None
    n = _valid(value)
    cap, _ = ceiling()
    if n is None:
        raise ValueError(f"记录保留要在 {RETENTION_MIN} 到 {RETENTION_MAX} 天之间")
    if n > cap:
        raise ValueError(f"不能比公司规定的 {cap} 天还长")
    return n


def set_personal(value: Any) -> dict[str, Any]:
    if value in (None, "", 0):
        store.set_meta("personal_retention_days", "")
    else:
        n = _valid(value)
        cap, _ = ceiling()
        if n is None:
            raise ValueError(f"记录保留要在 {RETENTION_MIN} 到 {RETENTION_MAX} 天之间")
        if n > cap:
            raise ValueError(f"不能比公司规定的 {cap} 天还长")
        store.set_meta("personal_retention_days", str(n))
    removed = maintain(force=True)
    return {**settings(), "removed": removed}


def settings() -> dict[str, Any]:
    cap, cap_src = ceiling()
    return {"ok": True, "default": ROOM_RETENTION_DEFAULT, "company": company_days(), "ceiling": cap,
            "ceiling_source": cap_src, "personal": personal_days(), "local": store.encryption()}


def _cutoff(days: int) -> str:
    return (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds")


def maintain(*, force: bool = False) -> int:
    """到期删 (最多 10 分钟一次; force = 刚改了设置, 马上清)。返回删了几条消息。"""
    last = float(store.get_meta("pruned_at") or 0)
    if not force and time.time() - last < PRUNE_EVERY:
        return 0
    removed = 0
    for rid in store.room_ids():
        room = store.get_room(rid)
        if not room:
            continue
        cut = _cutoff(effective(room)["days"])
        ended = room.get("ended_at") if room.get("my_state") not in ("invited", "joined") else None
        if ended and str(ended) < cut:
            removed += len(store.all_messages(rid))
            store.delete_room(rid)   # 群结束满期限: 整群删掉
            continue
        removed += store.prune_before(cut, rid)
    store.set_meta("pruned_at", str(time.time()))
    return removed
