"""同事群聊 · 群里发文件 (11/07)。

原来群里不传附件 (长材料用邮件)。参照 hermes 群聊的附件做法 (消息里只带附件编号, 文件内容另存; 发送那一刻定好给谁),
但守我们的边界 —— **中央只转交、取走即清空, 不存内容**:

- 不新开中央接口: 文件切成 80KB 一块, 每块装进一封普通的 `room_msg` 信 (信封里 `chunk`), 跟消息一样端到端加密,
  走现有邮筒, 期限 7 天。中央看到的只是一封封密文 (加密后每封 ~110KB, 在 128KB 上限内)。
- 消息本身带文件清单 `files: [{id, name, size, sha256, n}]` (n = 几块); 收件人按清单收齐、核对 sha256 才算收到。
- 给谁: 发送那一刻已加入的人 (跟消息一样)。后加入的人看不到之前的文件。
- 分块排进 outbox (colleague_rooms_outbox.py) 在后台发, 发送按钮不等; 没送到的照 outbox 的规矩补发 / 重发。
- 收件人那边: 块先放 file_chunks, 收齐拼好放 files (本机库整库加密, 不落明文文件)。
  邮筒是「取走即清空」, 万一取的时候断了, 那几块就没了 —— 收件人发现 10 分钟没新块、还缺, 就给发件人发一封
  `need` (缺哪几块), 发件人电脑自动补那几块 (最多要 3 次)。
- 打开: 点「打开」才把文件写到 ~/Downloads/鲶鱼群文件/<群名>/ 再用默认程序打开 (员工自己点的, 不算替他打开)。
- 上限: 一条消息最多 5 个文件, 合计 5MB (再大用邮件 / 网盘)。
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from pathlib import Path
from typing import Any

from . import colleague_rooms_outbox as outbox
from . import colleague_rooms_store as store

CHUNK = 80 * 1024
MAX_FILES = 5
MAX_TOTAL = 5 * 1024 * 1024
MAX_CHUNKS = MAX_TOTAL // CHUNK + MAX_FILES
NEED_AFTER = 600          # 10 分钟没收到新块、还缺 → 找发件人要
NEED_MAX = 3
ORPHAN_AFTER = 8 * 24 * 3600
_FILE_ID = re.compile(r"f_[0-9a-f]{16}")


def _clean_name(name: Any) -> str:
    s = Path(str(name or "").replace("\\", "/")).name.strip()
    s = re.sub(r"[\x00-\x1f]", "", s)[:120]
    if not s or s in (".", ".."):
        raise ValueError("文件名不对")
    return s


def prepare(raw: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """界面传来的文件 → (小文件, 大文件)。
    界面传法: [{name, stage_id}] (先一段段暂存好, 见 colleague_rooms_bigfiles.stage) 或老的 [{name, data(base64)}]。
    一条消息合计 ≤ 5MB: 全按小文件走邮筒分块 → [{id, name, size, sha256, n, data(bytes)}];
    超过: 全按大文件走中央暂存 (11/07) → [{name, path(明文暂存)}], 单个 / 合计 ≤ 200MB。"""
    from . import colleague_rooms_bigfiles as big  # noqa: PLC0415
    items = list(raw or []) if isinstance(raw, (list, tuple)) else []
    if not items:
        return [], []
    if len(items) > MAX_FILES:
        raise ValueError(f"一条消息最多带 {MAX_FILES} 个文件")
    got, total = [], 0
    for it in items:
        if not isinstance(it, dict):
            raise ValueError("文件格式不对")
        name = _clean_name(it.get("name"))
        if it.get("stage_id"):
            path = big.staged(str(it["stage_id"]))
            size, data = path.stat().st_size, None
        else:
            try:
                data = base64.b64decode(str(it.get("data") or ""), validate=True)
            except ValueError:
                raise ValueError(f"文件读不出来: {name}") from None
            path, size = None, len(data)
        if not size:
            raise ValueError(f"文件是空的: {name}")
        if size > big.MAX_FILE:
            raise ValueError(f"文件太大 (单个最多 {big.MAX_FILE // 1024 // 1024}MB): {name} —— 更大的用网盘")
        total += size
        if total > big.MAX_TOTAL:
            raise ValueError(f"文件太大 (一条消息合计最多 {big.MAX_TOTAL // 1024 // 1024}MB) —— 分几条发, 或用网盘")
        got.append({"name": name, "path": path, "data": data})
    if total > MAX_TOTAL:   # 大文件: 都要有暂存的明文 (老的 base64 传法也落一份)
        for g in got:
            if g["path"] is None:
                p = big.staged(big.stage({"offset": 0, "data": base64.b64encode(g["data"]).decode()})["stage_id"])
                g["path"] = p
        return [], [{"name": g["name"], "path": g["path"]} for g in got]
    out = []
    for g in got:
        data = g["data"] if g["data"] is not None else g["path"].read_bytes()
        if g["path"] is not None:
            g["path"].unlink(missing_ok=True)
        out.append({"id": f"f_{secrets.token_hex(8)}", "name": g["name"], "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(), "n": max(1, -(-len(data) // CHUNK)), "data": data})
    return out, []


def meta(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """消息 / 信封里带的清单 (不带内容)。"""
    return [{k: f[k] for k in ("id", "name", "size", "sha256", "n")} for f in files]


def chunk_envs(room_id: str, msg_id: str, f: dict[str, Any], only: list[int] | None = None) -> list[dict[str, Any]]:
    from .colleague_rooms import ENVELOPE_VERSION  # noqa: PLC0415
    data = f["data"]
    idx = range(f["n"]) if only is None else [i for i in only if 0 <= i < f["n"]]
    return [{"v": ENVELOPE_VERSION, "room_id": room_id, "msg_id": msg_id,
             "chunk": {"file_id": f["id"], "i": i, "n": f["n"],
                       "data": base64.b64encode(data[i * CHUNK:(i + 1) * CHUNK]).decode("ascii")}}
            for i in idx]


def keep_sent(room_id: str, msg: dict[str, Any], files: list[dict[str, Any]], me: str) -> None:
    """自己发的: 本机直接存一份 (自己能打开, 别人来要缺的块时也从这里取)。"""
    with store.tx() as con:
        for f in files:
            con.execute("INSERT OR IGNORE INTO files (room_id, file_id, msg_id, sender, name, size, sha256, data, at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (room_id, f["id"], msg["msg_id"], me, f["name"], f["size"], f["sha256"], f["data"], msg["at"]))


def send_chunks(room_id: str, msg_id: str, files: list[dict[str, Any]], to: list[str]) -> int:
    """分块排进 outbox, 后台发。"""
    n = 0
    for f in files:
        n += outbox.queue(room_id, msg_id, to, "room_msg", chunk_envs(room_id, msg_id, f), source="room-file")
    if n:
        outbox.drain_in_background()
    return n


def clean_meta(raw: Any) -> list[dict[str, Any]]:
    """收到的消息里的文件清单, 核对格式 (不合规的丢掉)。"""
    from . import colleague_rooms_bigfiles as big  # noqa: PLC0415
    out = []
    for f in (raw if isinstance(raw, list) else [])[:MAX_FILES]:
        if not isinstance(f, dict) or not _FILE_ID.fullmatch(str(f.get("id") or "")):
            continue
        try:
            name = _clean_name(f.get("name"))
            size, n = int(f.get("size") or 0), int(f.get("n") or 0)
        except (ValueError, TypeError):
            continue
        sha = str(f.get("sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            continue
        if f.get("blob") is not None:   # 11/07 大文件: 中央暂存的编号 + 解密钥匙
            extra = big.clean_meta(f)
            if extra:
                out.append({"id": f["id"], "name": name, "size": size, "sha256": sha, "n": n, **extra})
            continue
        if not 0 < size <= MAX_TOTAL or n != max(1, -(-size // CHUNK)):
            continue
        out.append({"id": f["id"], "name": name, "size": size, "sha256": sha, "n": n})
    return out


# ── 收 ───────────────────────────────────────────────────────────

def take_chunk(room_id: str, sender: str, env: dict[str, Any]) -> None:
    """收到一块: 先存着, 收齐了就拼。发件人在不在群里由调用方核对过。"""
    c = env.get("chunk") if isinstance(env.get("chunk"), dict) else {}
    fid = str(c.get("file_id") or "")
    try:
        i, n = int(c.get("i")), int(c.get("n"))
        data = base64.b64decode(str(c.get("data") or ""), validate=True)
    except (ValueError, TypeError):
        return
    if not _FILE_ID.fullmatch(fid) or not 0 <= i < n <= MAX_CHUNKS or not 0 < len(data) <= CHUNK:
        return
    with store.tx() as con:
        if con.execute("SELECT 1 FROM files WHERE room_id = ? AND file_id = ?", (room_id, fid)).fetchone():
            return   # 早就收齐了 (补发的重复块)
        con.execute("INSERT OR IGNORE INTO file_chunks (room_id, file_id, sender, i, data, at) VALUES (?, ?, ?, ?, ?, ?)",
                    (room_id, fid, sender, i, data, time.time()))
    msg_id = str(env.get("msg_id") or "")
    msg = _msg(room_id, msg_id)
    if msg:
        assemble(room_id, msg)


def _msg(room_id: str, msg_id: str) -> dict[str, Any] | None:
    with store.tx() as con:
        r = con.execute("SELECT seq, data FROM messages WHERE room_id = ? AND msg_id = ?", (room_id, msg_id)).fetchone()
    return json.loads(r["data"]) if r else None


def assemble(room_id: str, msg: dict[str, Any]) -> None:
    """这条消息的文件, 块收齐了就拼起来、核对 sha256, 存进 files。核对不上的丢掉那些块 (等补发)。"""
    sender = msg.get("from") or ""
    for f in msg.get("files") or []:
        if f.get("blob"):
            continue   # 大文件不走邮筒分块
        with store.tx() as con:
            if con.execute("SELECT 1 FROM files WHERE room_id = ? AND file_id = ?", (room_id, f["id"])).fetchone():
                continue
            rows = con.execute("SELECT i, data FROM file_chunks WHERE room_id = ? AND file_id = ? AND sender = ? ORDER BY i",
                               (room_id, f["id"], sender)).fetchall()
            if len(rows) < f["n"] or [r["i"] for r in rows[:f["n"]]] != list(range(f["n"])):
                continue
            data = b"".join(bytes(r["data"]) for r in rows[:f["n"]])
            if len(data) != f["size"] or hashlib.sha256(data).hexdigest() != f["sha256"]:
                con.execute("DELETE FROM file_chunks WHERE room_id = ? AND file_id = ?", (room_id, f["id"]))
                continue
            con.execute("INSERT OR IGNORE INTO files (room_id, file_id, msg_id, sender, name, size, sha256, data, at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (room_id, f["id"], msg["msg_id"], sender, f["name"], f["size"], f["sha256"], data, msg.get("at") or ""))
            con.execute("DELETE FROM file_chunks WHERE room_id = ? AND file_id = ?", (room_id, f["id"]))


def status(room_id: str, files: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """{file_id: {have: bool, got: 收到几块}} —— 界面显示「接收中 3/64」。"""
    if not files:
        return {}
    out = {}
    with store.tx() as con:
        for f in files:
            have = con.execute("SELECT 1 FROM files WHERE room_id = ? AND file_id = ?", (room_id, f["id"])).fetchone() is not None
            got = f["n"] if have else con.execute("SELECT COUNT(*) FROM file_chunks WHERE room_id = ? AND file_id = ?",
                                                  (room_id, f["id"])).fetchone()[0]
            out[f["id"]] = {"have": have, "got": got}
    return out


# ── 缺块: 找发件人要 / 替别人补 ─────────────────────────────────────

def missing_requests(now: float | None = None) -> list[tuple[str, str, dict[str, Any]]]:
    """该去要缺块的: [(room_id, 发件人, need 信封)]。只看 10 分钟没新块的; 每个文件最多要 3 次, 每次隔 30 分钟。"""
    from .colleague_rooms import ENVELOPE_VERSION  # noqa: PLC0415
    now = now or time.time()
    out = []
    state = json.loads(store.get_meta("file_need") or "{}")
    with store.tx() as con:
        rows = con.execute("SELECT room_id, msg_id, data FROM messages WHERE kind = 'text' AND data LIKE '%\"files\"%'"
                           " AND at >= ?", (time.strftime("%Y-%m-%dT%H:%M", time.localtime(now - ORPHAN_AFTER)),)).fetchall()
        for r in rows:
            m = json.loads(r["data"])
            if not m.get("received_at"):
                continue   # 我自己发的
            for f in m.get("files") or []:
                if f.get("blob"):
                    continue   # 大文件走中央暂存, 不找人要块
                if con.execute("SELECT 1 FROM files WHERE room_id = ? AND file_id = ?", (r["room_id"], f["id"])).fetchone():
                    continue
                got = con.execute("SELECT i, at FROM file_chunks WHERE room_id = ? AND file_id = ?",
                                  (r["room_id"], f["id"])).fetchall()
                last = max([g["at"] for g in got], default=0.0)
                try:
                    received = time.mktime(time.strptime(m["received_at"][:19], "%Y-%m-%dT%H:%M:%S"))
                except (ValueError, KeyError):
                    received = 0.0
                if now - max(last, received) < NEED_AFTER:
                    continue
                k = f"{r['room_id']}|{f['id']}"
                times, at = state.get(k, [0, 0.0])
                if times >= NEED_MAX or now - at < 3 * NEED_AFTER:
                    continue
                have = {g["i"] for g in got}
                missing = [i for i in range(f["n"]) if i not in have]
                state[k] = [times + 1, now]
                out.append((r["room_id"], m["from"], {"v": ENVELOPE_VERSION, "room_id": r["room_id"], "msg_id": r["msg_id"],
                                                     "need": {"file_id": f["id"], "missing": missing}}))
    state = {k: v for k, v in state.items() if now - v[1] < ORPHAN_AFTER}   # 8 天前的记录不留
    store.set_meta("file_need", json.dumps(state))
    return out


def serve_need(room_id: str, requester: str, me: str, env: dict[str, Any]) -> int:
    """同事来要缺的块: 那条消息是我发的、文件我这有, 就把缺的排进 outbox 补给他。"""
    need = env.get("need") if isinstance(env.get("need"), dict) else {}
    fid = str(need.get("file_id") or "")
    msg = _msg(room_id, str(env.get("msg_id") or ""))
    if not msg or msg.get("from") != me or not _FILE_ID.fullmatch(fid):
        return 0
    f = next((x for x in msg.get("files") or [] if x["id"] == fid), None)
    if not f:
        return 0
    with store.tx() as con:
        r = con.execute("SELECT data FROM files WHERE room_id = ? AND file_id = ?", (room_id, fid)).fetchone()
    if not r:
        return 0
    try:
        missing = sorted({int(i) for i in need.get("missing") or []})[:MAX_CHUNKS]
    except (ValueError, TypeError):
        return 0
    # 同一个人要同一个文件: 上一轮还没补完就不再排; 最多补 NEED_MAX 轮 (别人要多了, 我这边不至于一直往外发)
    served = json.loads(store.get_meta("file_served") or "{}")
    key = f"{room_id}|{fid}|{requester}"
    if served.get(key, 0) >= NEED_MAX:
        return 0
    with store.tx() as con:
        if con.execute("SELECT 1 FROM outbox WHERE room_id = ? AND ref = ? AND to_email = ? LIMIT 1",
                       (room_id, msg["msg_id"], requester)).fetchone():
            return 0
    served[key] = served.get(key, 0) + 1
    store.set_meta("file_served", json.dumps(served))
    envs = chunk_envs(room_id, msg["msg_id"], {**f, "data": bytes(r["data"])}, missing)
    n = outbox.queue(room_id, msg["msg_id"], [requester], "room_msg", envs, source="room-file")
    if n:
        outbox.drain_in_background()
    return n


def drop_orphans(now: float | None = None) -> None:
    """8 天还没收齐的块 (消息早删了 / 发件人再也没补) 清掉。"""
    with store.tx() as con:
        con.execute("DELETE FROM file_chunks WHERE at < ?", ((now or time.time()) - ORPHAN_AFTER,))


# ── 打开 ─────────────────────────────────────────────────────────

def save(room: dict[str, Any], file_id: str) -> dict[str, Any]:
    """写到 ~/Downloads/鲶鱼群文件/<群名>/<文件名> (重名加 (1)(2)), 返回路径给界面打开。同一个文件已经写过、内容没变就不再写。"""
    from . import colleague_rooms_bigfiles as big  # noqa: PLC0415
    if not _FILE_ID.fullmatch(str(file_id or "")):
        raise ValueError("文件编号不对")
    b = big.row(room["room_id"], file_id)
    with store.tx() as con:
        r = None if b else con.execute("SELECT name, sha256, data FROM files WHERE room_id = ? AND file_id = ?",
                                       (room["room_id"], file_id)).fetchone()
    if b and b["state"] not in ("ready", "sent", "local", "uploading"):
        raise ValueError("这个文件还没收下来, 稍等再开")
    if not b and not r:
        raise ValueError("这个文件还没收齐, 稍等再开")
    folder = Path.home() / "Downloads" / "鲶鱼群文件" / _safe_dir(room.get("name") or room["room_id"])
    folder.mkdir(parents=True, exist_ok=True)
    if b:
        r = {"name": b["name"], "sha256": b["sha256"]}
    data = bytes(r["data"]) if not b else None
    target = folder / r["name"]
    stem, suffix, k = target.stem, target.suffix, 1
    while target.exists():
        try:
            if hashlib.sha256(target.read_bytes()).hexdigest() == r["sha256"]:
                return {"ok": True, "path": str(target)}
        except OSError:
            pass
        target = folder / f"{stem} ({k}){suffix}"
        k += 1
    if b:
        big.write_plain(b, target)   # 大文件: 本机密文边解边写
    else:
        target.write_bytes(data)
    return {"ok": True, "path": str(target)}


def _safe_dir(name: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")[:60]
    return s or "群"
