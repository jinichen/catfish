"""同事群聊 · 没送到的信 (11/07)。

原来投递失败只在点按钮那一下弹一次红字 (插在群名和聊天之间, 把聊天往下推), 还是原始报错; 关掉 / 换个群就没了,
那条消息看着跟送到的一样, 也没有补发的地方 (鸿波: 体验不好)。参照 hermes 群聊的做法 (准备连续失败就在群里说一句、给「重试」),
改成: **每封没送到的信记一行, 挂在出事的那条消息 (或那条系统提示) 下面, 按原因自动补发, 实在不行界面给「重发」**。

原因 (reason) 和怎么补:
  queued    还没发 (群文件的分块先排队, 后台慢慢发)                 马上发
  network   连不上中央 / 中央 5xx                                    1 / 5 / 15 分钟后各再试一次, 还不行 → 放弃, 等人点「重发」
  nokey     对方还没升级到能加密的版本 (或升级后没打开过)             每 10 分钟看一次他登记了没, 登记了就补发; 7 天还没有 → 放弃
  too_old   中央端版本太旧                                           每小时试一次 (管理员升级后自己通); 7 天 → 放弃
  failed    别的 (内容太长 / 中央拒收)                               不自动补, 等人点「重发」

信封原样存着 (本机库整库加密), 补发时重新加密。收件人已经不在群里的, 那行直接删。
同时有两处在发 (取邮筒时顺手补 + 发文件的后台线程): 取出来先「占住」(next_at 往后推 2 分钟), 不会重发同一封。
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import colleague_rooms_store as store
from . import forms_net

BACKOFF = (60, 300, 900)        # network: 第 1 / 2 / 3 次失败后隔多久再试
NOKEY_EVERY = 600
TOO_OLD_EVERY = 3600
GIVE_UP_AFTER = 7 * 24 * 3600   # 跟邮筒期限一样: 7 天还发不出去就不自动补了
LEASE = 120                     # 取出来先占住这么久
BATCH = 60                      # 一次最多补发这么多封 (取邮筒时顺手做, 别拖慢)

_drainer_lock = threading.Lock()
_draining = False


def classify(err: Exception) -> str:
    from . import mailbox_crypto  # noqa: PLC0415
    if isinstance(err, mailbox_crypto.NoKeyError):
        return "nokey"
    s = str(err)
    if "版本太旧" in s:
        return "too_old"
    if "连不上中央" in s or "失败 (5" in s:
        return "network"
    return "failed"


def _plan(reason: str, tries: int, created: float, now: float) -> tuple[float, int]:
    """(下次什么时候试, 放不放弃)。"""
    if reason == "queued":
        return now, 0
    if reason == "network":
        return (now + BACKOFF[tries - 1], 0) if tries <= len(BACKOFF) else (now, 1)
    if reason in ("nokey", "too_old"):
        if now - created > GIVE_UP_AFTER:
            return now, 1
        return now + (NOKEY_EVERY if reason == "nokey" else TOO_OLD_EVERY), 0
    return now, 1


def _short(err: Exception | str) -> str:
    return str(err)[:300]


def record(room_id: str, ref: str | None, failed: list[dict[str, Any]], kind: str, env: dict[str, Any],
           source: str | None = None) -> None:
    """一次投递里没送到的那几封, 各记一行。failed: [{email, error, reason}]。"""
    now = time.time()
    with store.tx() as con:
        for f in failed:
            nxt, gave_up = _plan(f["reason"], 1, now, now)
            con.execute(
                "INSERT INTO outbox (room_id, ref, to_email, kind, env, source, reason, error, tries, next_at, created_at, gave_up)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (room_id, ref, f["email"], kind, json.dumps(env, ensure_ascii=False), source, f["reason"], f["error"],
                 nxt, now, gave_up))


def queue(room_id: str, ref: str | None, to: list[str], kind: str, envs: list[dict[str, Any]], source: str | None = None) -> int:
    """排队待发 (群文件分块: 一封一行, 后台发)。返回排了几封。"""
    now = time.time()
    with store.tx() as con:
        for e in to:
            for env in envs:
                con.execute(
                    "INSERT INTO outbox (room_id, ref, to_email, kind, env, source, reason, error, tries, next_at, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, 'queued', NULL, 0, ?, ?)",
                    (room_id, ref, e, kind, json.dumps(env, ensure_ascii=False), source, now, now))
    return len(to) * len(envs)


def _claim(limit: int, room_id: str | None = None, ref: str | None = None, email: str | None = None,
           force: bool = False) -> list[dict[str, Any]]:
    """取出到点的 (force: 人点了「重发」, 放弃了的也取), 先占住。"""
    now = time.time()
    sql = "SELECT * FROM outbox WHERE "
    args: list[Any] = []
    if force:
        sql += "1 = 1"
    else:
        sql += "gave_up = 0 AND next_at <= ?"
        args.append(now)
    for col, v in (("room_id", room_id), ("ref", ref), ("to_email", email)):
        if v:
            sql += f" AND {col} = ?"
            args.append(v)
    sql += " ORDER BY id LIMIT ?"
    args.append(limit)
    with store.tx() as con:
        rows = [dict(r) for r in con.execute(sql, args).fetchall()]
        for r in rows:
            con.execute("UPDATE outbox SET next_at = ?, gave_up = 0 WHERE id = ?", (now + LEASE, r["id"]))
    return rows


def _still_member(room: dict[str, Any] | None, row: dict[str, Any]) -> bool:
    """群消息 / 文件: 收件人还在群里才补; 邀请 / 名单 / 退群这类通知照补 (发出时他在)。"""
    if not room:
        return False
    if row["kind"] != "room_msg":
        return True
    from .colleague_rooms import ACTIVE  # noqa: PLC0415
    return (room.get("members", {}).get(row["to_email"]) or {}).get("state") in ACTIVE and room.get("my_state") == "joined"


def _send(rows: list[dict[str, Any]]) -> int:
    """发出去; 送到的删掉, 没送到的按原因排下一次。返回送到几封。"""
    if not rows:
        return 0
    rooms: dict[str, Any] = {}

    def one(r: dict[str, Any]) -> tuple[dict[str, Any], Exception | None, bool]:
        rid = r["room_id"]
        if rid not in rooms:
            rooms[rid] = store.get_room(rid)
        if not _still_member(rooms[rid], r):
            return r, None, False
        try:
            env = json.loads(r["env"])
            if r["source"]:
                forms_net.post(r["to_email"], r["kind"], env, _ttl(), source=r["source"])
            else:
                forms_net.post(r["to_email"], r["kind"], env, _ttl())
            return r, None, True
        except ValueError as e:
            return r, e, True

    with ThreadPoolExecutor(max_workers=min(8, len(rows))) as ex:
        results = list(ex.map(one, rows))
    now, sent = time.time(), 0
    with store.tx() as con:
        for r, err, tried in results:
            if err is None:
                con.execute("DELETE FROM outbox WHERE id = ?", (r["id"],))
                sent += int(tried)
                continue
            reason = classify(err)
            tries = r["tries"] + 1
            nxt, gave_up = _plan(reason, tries, r["created_at"], now)
            con.execute("UPDATE outbox SET reason = ?, error = ?, tries = ?, next_at = ?, gave_up = ? WHERE id = ?",
                        (reason, _short(err), tries, nxt, gave_up, r["id"]))
    return sent


def _ttl() -> int:
    from .colleague_rooms import TTL_SECONDS  # noqa: PLC0415
    return TTL_SECONDS


def retry_due(limit: int = BATCH) -> int:
    """取邮筒时顺手补发到点的。返回送到几封。"""
    return _send(_claim(limit))


def resend(room_id: str, ref: str, email: str | None = None) -> dict[str, Any]:
    """人点了「重发」: 这条消息下没送到的 (或只发给某人的) 重新算到点、重新开始自动重试。
    不多 (一条普通消息) 就当场发, 界面马上看到结果; 多 (群文件几十块) 交给后台线程, 界面显示「正在发」。"""
    now = time.time()
    sql = "UPDATE outbox SET gave_up = 0, next_at = ?, tries = 0 WHERE room_id = ? AND ref = ?"
    args: list[Any] = [now, room_id, ref]
    if email:
        sql += " AND to_email = ?"
        args.append(email)
    with store.tx() as con:
        n = con.execute(sql, args).rowcount
    sent = 0
    if n <= BATCH:
        sent = _send(_claim(BATCH, room_id=room_id, ref=ref, email=email))
    else:
        drain_in_background()
    return {"ok": True, "sent": sent, "left": _left(room_id, ref, email)}


def _left(room_id: str, ref: str, email: str | None) -> int:
    sql = "SELECT COUNT(*) FROM outbox WHERE room_id = ? AND ref = ?"
    args: list[Any] = [room_id, ref]
    if email:
        sql += " AND to_email = ?"
        args.append(email)
    with store.tx() as con:
        return con.execute(sql, args).fetchone()[0]


def drain_in_background() -> None:
    """群文件分块排好队后在后台发完 (发送那一下不等)。同时只跑一个。"""
    global _draining
    with _drainer_lock:
        if _draining:
            return
        _draining = True

    def run() -> None:
        global _draining
        try:
            for _ in range(500):
                rows = _claim(BATCH)
                if not rows:
                    break
                _send(rows)
        finally:
            with _drainer_lock:
                _draining = False

    threading.Thread(target=run, name="room-outbox", daemon=True).start()


# ── 给界面看 ─────────────────────────────────────────────────────

def state_of(row: dict[str, Any]) -> str:
    """界面上的说法: sending 发送中 / nokey 等对方升级 / too_old 中央太旧 / failed 没送到 (可重发)。"""
    if row["gave_up"]:
        return "failed"
    if row["reason"] in ("queued", "network"):
        return "sending"
    return row["reason"] if row["reason"] in ("nokey", "too_old") else "failed"


_RANK = {"failed": 3, "too_old": 2, "nokey": 1, "sending": 0}


def by_ref(room_id: str, refs: list[str]) -> dict[str, list[dict[str, Any]]]:
    """这几条消息下没送到的: {msg_id: [{email, state, error, left}]} —— 一个人一行 (文件分块合并, 取最糟的那个状态)。"""
    if not refs:
        return {}
    out: dict[str, dict[str, dict[str, Any]]] = {}
    q = ",".join("?" * len(refs))
    with store.tx() as con:
        rows = con.execute(f"SELECT ref, to_email, reason, error, gave_up FROM outbox WHERE room_id = ? AND ref IN ({q})",
                           (room_id, *refs)).fetchall()
    for r in rows:
        r = dict(r)
        st = state_of(r)
        cur = out.setdefault(r["ref"], {}).get(r["to_email"])
        if cur is None:
            out[r["ref"]][r["to_email"]] = {"email": r["to_email"], "state": st, "error": r["error"] or "", "left": 1}
        else:
            cur["left"] += 1
            if _RANK[st] > _RANK[cur["state"]]:
                cur.update(state=st, error=r["error"] or cur["error"])
    return {ref: list(v.values()) for ref, v in out.items()}


def room_issue(room_id: str) -> str | None:
    """整个群的问题 (界面顶上一条细横条): 中央太旧。"""
    with store.tx() as con:
        r = con.execute("SELECT 1 FROM outbox WHERE room_id = ? AND reason = 'too_old' AND gave_up = 0 LIMIT 1",
                        (room_id,)).fetchone()
    return "too_old" if r else None
