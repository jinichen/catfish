"""同事群聊的本机库 (11/6, docs/GROUP-CHAT-PLAN.md §4.3): `~/.catfish/collab_rooms/rooms.db` (SQLite)。

原来每个群一个 `room.json` + `messages.jsonl`: 每次打开整份读、删旧消息要整份重写、并发写靠加锁兜着
(11/5「.tmp 找不到」那次)。改成一个库:

    rooms     (room_id, data)                        群的 JSON (名字 / 公告 / 群主 / 成员 / 我的状态 / 已读到哪)
    messages  (seq, room_id, msg_id, kind, from_email, at, text, data)   一条一行, seq 自增 = 本机收到的先后
    meta      (key, value)                           上次取邮筒时间等

- 写都在事务里, WAL 模式; 每次调用开一个连接 (tool-bridge 每个请求一个线程, 连接不跨线程)。
- 已读位置按 seq 记 (`room["read_upto"]` = 读到的最大 seq), 删旧消息不错位。
- 群内搜索用 `LIKE` (中文不用分词也能搜任意长度; 一个群保留期内几万条, 扫一遍毫秒级)。
- 第一次打开时把老的 `<room_id>/room.json` + `messages.jsonl` 导进来 (按 msg_id 去重), 老目录改名 `<room_id>.migrated` 留作备份;
  老的 `_sync.json` 同样导进 meta。
- 整库加密 (11/6 §4.3): SQLCipher, 密钥在系统钥匙串 —— 打开 / 转加密 / 密钥都在 colleague_rooms_crypt.py。
  组件没装上或还没拿到密钥时, 明文库照常用 (见那个文件头)。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import colleague_rooms_crypt as crypt
from . import wiki_files

_lock = threading.Lock()
_ready: set[tuple[str, int]] = set()   # 已建表 / 已迁移 / 已判过加密的库 (路径, 换钥代数)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (room_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id    TEXT NOT NULL,
    msg_id     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    from_email TEXT,
    at         TEXT NOT NULL,
    text       TEXT NOT NULL DEFAULT '',
    data       TEXT NOT NULL,
    UNIQUE (room_id, msg_id)
);
CREATE INDEX IF NOT EXISTS messages_room_seq ON messages (room_id, seq);
CREATE INDEX IF NOT EXISTS messages_at ON messages (at);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
-- 11/07 没送到的信 (colleague_rooms_outbox.py): 每个收件人一行, 自动补发 / 界面「重发」; 送到就删
CREATE TABLE IF NOT EXISTS outbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id    TEXT NOT NULL,
    ref        TEXT,
    to_email   TEXT NOT NULL,
    kind       TEXT NOT NULL,
    env        TEXT NOT NULL,
    source     TEXT,
    reason     TEXT NOT NULL,
    error      TEXT,
    tries      INTEGER NOT NULL DEFAULT 0,
    next_at    REAL NOT NULL,
    created_at REAL NOT NULL,
    gave_up    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS outbox_due ON outbox (gave_up, next_at);
CREATE INDEX IF NOT EXISTS outbox_ref ON outbox (room_id, ref);
-- 11/07 群文件 (colleague_rooms_files.py): 收齐的文件整个存库里 (库是加密的, 不落明文文件); 没收齐的块先放 file_chunks
CREATE TABLE IF NOT EXISTS files (
    room_id TEXT NOT NULL, file_id TEXT NOT NULL, msg_id TEXT NOT NULL, sender TEXT NOT NULL,
    name TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL, data BLOB NOT NULL, at TEXT NOT NULL,
    PRIMARY KEY (room_id, file_id)
);
-- 11/07 群里发大文件 (colleague_rooms_bigfiles.py): 中央暂存一份密文; 本机 .bin 也是密文, 钥匙 key 在这里
CREATE TABLE IF NOT EXISTS bigfiles (
    room_id TEXT NOT NULL, file_id TEXT NOT NULL, msg_id TEXT NOT NULL, sender TEXT NOT NULL, mine INTEGER NOT NULL,
    name TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL, n INTEGER NOT NULL, key TEXT NOT NULL,
    blob_id TEXT, state TEXT NOT NULL, got INTEGER NOT NULL DEFAULT 0, tries INTEGER NOT NULL DEFAULT 0,
    error TEXT, next_at REAL NOT NULL DEFAULT 0, at TEXT NOT NULL,
    PRIMARY KEY (room_id, file_id)
);
CREATE TABLE IF NOT EXISTS file_chunks (
    room_id TEXT NOT NULL, file_id TEXT NOT NULL, sender TEXT NOT NULL, i INTEGER NOT NULL, data BLOB NOT NULL,
    at REAL NOT NULL, PRIMARY KEY (room_id, file_id, i)
);
"""


def rooms_dir() -> Path:
    d = wiki_files._catfish_home() / "collab_rooms"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return rooms_dir() / "rooms.db"


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    path = db_path()
    key = (str(path), crypt.generation())
    if key not in _ready:
        with _lock:
            if key not in _ready:
                mode = crypt.prepare(path)
                con = crypt.connect(path)
                try:
                    con.execute("PRAGMA journal_mode=WAL")
                    con.executescript(_SCHEMA)
                    con.commit()
                    _migrate_legacy(con)
                finally:
                    con.close()
                    crypt.release()
                if mode == "encrypted":
                    crypt.drop_plain_leftovers(rooms_dir())
                _ready.add(key)
    con = crypt.connect(path)
    try:
        with con:   # 一次调用 = 一个事务
            yield con
    finally:
        con.close()
        crypt.release()


def _row_msg(r: sqlite3.Row) -> dict[str, Any]:
    m = json.loads(r["data"])
    m["seq"] = r["seq"]
    return m


# ── 群 ───────────────────────────────────────────────────────────

def get_room(room_id: str) -> dict[str, Any] | None:
    with _conn() as con:
        r = con.execute("SELECT data FROM rooms WHERE room_id = ?", (room_id,)).fetchone()
    return json.loads(r["data"]) if r else None


def put_room(room: dict[str, Any]) -> None:
    with _conn() as con:
        con.execute("INSERT INTO rooms (room_id, data) VALUES (?, ?) ON CONFLICT(room_id) DO UPDATE SET data = excluded.data",
                    (room["room_id"], json.dumps(room, ensure_ascii=False)))


def room_ids() -> list[str]:
    with _conn() as con:
        return [r["room_id"] for r in con.execute("SELECT room_id FROM rooms ORDER BY room_id")]


def delete_room(room_id: str) -> None:
    with _conn() as con:
        for table in ("messages", "outbox", "files", "file_chunks", "bigfiles", "rooms"):
            con.execute(f"DELETE FROM {table} WHERE room_id = ?", (room_id,))


# ── 消息 ─────────────────────────────────────────────────────────

def add_message(room_id: str, msg: dict[str, Any]) -> int | None:
    """记一条; 同一个 msg_id 已经有了就不记 (返回 None)。返回 seq。"""
    data = {k: v for k, v in msg.items() if k != "seq"}
    with _conn() as con:
        cur = con.execute(
            "INSERT OR IGNORE INTO messages (room_id, msg_id, kind, from_email, at, text, data) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (room_id, msg["msg_id"], msg.get("kind") or "text", msg.get("from"), str(msg.get("at") or ""),
             str(msg.get("text") or ""), json.dumps(data, ensure_ascii=False)))
        return cur.lastrowid if cur.rowcount else None


def update_message(room_id: str, msg: dict[str, Any]) -> None:
    data = {k: v for k, v in msg.items() if k != "seq"}
    with _conn() as con:
        con.execute("UPDATE messages SET data = ? WHERE room_id = ? AND msg_id = ?",
                    (json.dumps(data, ensure_ascii=False), room_id, msg["msg_id"]))


def get_message(room_id: str, msg_id: str) -> dict[str, Any] | None:
    with _conn() as con:
        r = con.execute("SELECT seq, data FROM messages WHERE room_id = ? AND msg_id = ?", (room_id, msg_id)).fetchone()
    return _row_msg(r) if r else None


def has_message(room_id: str, msg_id: str) -> bool:
    with _conn() as con:
        return con.execute("SELECT 1 FROM messages WHERE room_id = ? AND msg_id = ?", (room_id, msg_id)).fetchone() is not None


def all_messages(room_id: str) -> list[dict[str, Any]]:
    with _conn() as con:
        return [_row_msg(r) for r in con.execute("SELECT seq, data FROM messages WHERE room_id = ? ORDER BY seq", (room_id,))]


def recent(room_id: str, limit: int, before_seq: int | None = None) -> list[dict[str, Any]]:
    """最近 limit 条 (before_seq: 只要比它早的, 往上翻一页用), 按先后排好。"""
    with _conn() as con:
        if before_seq:
            rows = con.execute("SELECT seq, data FROM messages WHERE room_id = ? AND seq < ? ORDER BY seq DESC LIMIT ?",
                               (room_id, before_seq, limit)).fetchall()
        else:
            rows = con.execute("SELECT seq, data FROM messages WHERE room_id = ? ORDER BY seq DESC LIMIT ?",
                               (room_id, limit)).fetchall()
    return [_row_msg(r) for r in reversed(rows)]


def around(room_id: str, seq: int, half: int = 30) -> list[dict[str, Any]]:
    """某一条前后各 half 条 (搜索结果点进去用)。"""
    with _conn() as con:
        a = con.execute("SELECT seq, data FROM messages WHERE room_id = ? AND seq < ? ORDER BY seq DESC LIMIT ?",
                        (room_id, seq, half)).fetchall()
        b = con.execute("SELECT seq, data FROM messages WHERE room_id = ? AND seq >= ? ORDER BY seq LIMIT ?",
                        (room_id, seq, half + 1)).fetchall()
    return [_row_msg(r) for r in reversed(a)] + [_row_msg(r) for r in b]


def count_before(room_id: str, seq: int) -> int:
    with _conn() as con:
        return con.execute("SELECT COUNT(*) FROM messages WHERE room_id = ? AND seq < ?", (room_id, seq)).fetchone()[0]


def max_seq(room_id: str) -> int:
    with _conn() as con:
        return con.execute("SELECT COALESCE(MAX(seq), 0) FROM messages WHERE room_id = ?", (room_id,)).fetchone()[0]


def after(room_id: str, seq: int, kinds: tuple[str, ...]) -> list[dict[str, Any]]:
    """seq 之后的这几类消息 (算未读用)。"""
    q = ",".join("?" * len(kinds))
    with _conn() as con:
        return [_row_msg(r) for r in con.execute(
            f"SELECT seq, data FROM messages WHERE room_id = ? AND seq > ? AND kind IN ({q}) ORDER BY seq", (room_id, seq, *kinds))]


def last_of(room_id: str, kinds: tuple[str, ...]) -> dict[str, Any] | None:
    q = ",".join("?" * len(kinds))
    with _conn() as con:
        r = con.execute(f"SELECT seq, data FROM messages WHERE room_id = ? AND kind IN ({q}) ORDER BY seq DESC LIMIT 1",
                        (room_id, *kinds)).fetchone()
    return _row_msg(r) if r else None


def find_card(room_id: str, task_id: str) -> dict[str, Any] | None:
    """这个群里这个任务的任务卡 (最近那张)。"""
    with _conn() as con:
        for r in con.execute("SELECT data FROM messages WHERE room_id = ? AND kind = 'card' ORDER BY seq DESC", (room_id,)):
            card = (json.loads(r["data"]).get("card") or {})
            if card.get("task_id") == task_id:
                return card
    return None


def search(room_id: str, q: str, from_email: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """群内搜索 (正文包含 q, 可按发件人筛), 新的在前。"""
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    sql = "SELECT seq, data FROM messages WHERE room_id = ? AND kind IN ('text','card','auto') AND text LIKE ? ESCAPE '\\'"
    args: list[Any] = [room_id, like]
    if from_email:
        sql += " AND from_email = ?"
        args.append(from_email)
    sql += " ORDER BY seq DESC LIMIT ?"
    args.append(limit)
    with _conn() as con:
        return [_row_msg(r) for r in con.execute(sql, args)]


def prune_before(cutoff_iso: str, room_id: str | None = None) -> int:
    """删掉 at 早于 cutoff 的消息 (保留期限 §4.2)。返回删了几条。"""
    with _conn() as con:
        if room_id:
            cur = con.execute("DELETE FROM messages WHERE room_id = ? AND at < ?", (room_id, cutoff_iso))
        else:
            cur = con.execute("DELETE FROM messages WHERE at < ?", (cutoff_iso,))
        # 11/07 消息删了, 挂在它上面的文件 / 没送到的信一起删 (文件跟着消息的保留期走)
        con.execute("DELETE FROM files WHERE NOT EXISTS "
                    "(SELECT 1 FROM messages m WHERE m.room_id = files.room_id AND m.msg_id = files.msg_id)")
        con.execute("DELETE FROM outbox WHERE ref IS NOT NULL AND NOT EXISTS "
                    "(SELECT 1 FROM messages m WHERE m.room_id = outbox.room_id AND m.msg_id = outbox.ref)")
        return cur.rowcount


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """一个事务 (outbox / 文件那两个文件直接写 SQL 用)。"""
    with _conn() as con:
        yield con


# ── meta ─────────────────────────────────────────────────────────

def snapshot(dest: Path) -> None:
    """换电脑导出用的一致性快照 (`VACUUM INTO`; 库是加密的, 快照也是加密的、同一把密钥)。"""
    dest.unlink(missing_ok=True)
    with _conn() as con:
        con.isolation_level = None
        con.execute("VACUUM INTO ?", (str(dest),))


def encryption() -> dict:
    """本机库加没加密 (给「记录保留」窗口显示)。"""
    with _conn():
        pass
    return crypt.status(db_path())


def get_meta(key: str) -> str | None:
    with _conn() as con:
        r = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return r["value"] if r else None


def set_meta(key: str, value: str) -> None:
    with _conn() as con:
        con.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


# ── 老格式导入 (1.2.75 之前: 每群一个目录) ─────────────────────────

def _migrate_legacy(con: sqlite3.Connection) -> None:
    d = rooms_dir()
    for room_json in sorted(d.glob("r_*/room.json")):
        folder = room_json.parent
        try:
            room = json.loads(room_json.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rid = str(room.get("room_id") or folder.name)
        old_read = int(room.get("read_upto") or 0)   # 老格式: 读到第几条 (条数); 换成 seq
        read_seq = 0
        msgs_file = folder / "messages.jsonl"
        n = 0
        if msgs_file.exists():
            for line in msgs_file.read_text(encoding="utf-8").splitlines():
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(m, dict) or not m.get("msg_id"):
                    continue
                cur = con.execute(
                    "INSERT OR IGNORE INTO messages (room_id, msg_id, kind, from_email, at, text, data) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (rid, m["msg_id"], m.get("kind") or "text", m.get("from"), str(m.get("at") or ""), str(m.get("text") or ""),
                     json.dumps(m, ensure_ascii=False)))
                n += 1
                if n <= old_read and cur.lastrowid:
                    read_seq = cur.lastrowid
        room["read_upto"] = read_seq
        con.execute("INSERT INTO rooms (room_id, data) VALUES (?, ?) ON CONFLICT(room_id) DO UPDATE SET data = excluded.data",
                    (rid, json.dumps(room, ensure_ascii=False)))
        con.commit()
        target = folder.with_name(folder.name + ".migrated")
        if not target.exists():
            folder.rename(target)
    sync = d / "_sync.json"
    if sync.exists():
        try:
            at = json.loads(sync.read_text(encoding="utf-8")).get("at")
            if at:
                con.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('sync_at', ?)", (at,))
                con.commit()
            sync.rename(sync.with_name("_sync.json.migrated"))
        except (OSError, ValueError):
            pass
