"""邮件本地索引 —— list/search 的读侧真源 (8/21 治本)。

# 为什么要有这一层

8/21 之前, EmailTab 每次挂载 (切 tab 就卸载重挂, App.tsx:268 条件渲染) 都要:

    Rust spawn `catfish-email list`      ← Python 解释器冷启动
      └ Mail.app 在跑 → AppleScript      ← 每封 8 个字段 = 8 次 Apple Event IPC
        500 封 × 8 字段 × 4 账号 ≈ 1.6 万次 IPC, Sent 200 封再来一遍

切一次 tab = 把全部邮件从头重抽一遍。这就是「切回邮件页要等很久」的真因。
同一个根源还烧过配额: 8/15 评级 effect 自触发, 83 分钟 2470 万 token ——
一切都是现抓现算, 没有任何东西落地。

# 设计

**一张 SQLite 表, 按 (source_path, mtime, size) 对账, 只解析新增/变更的文件。**

    对账:  rglob 枚举 .emlx + stat           ← 只有 readdir 成本, 无解析
    增量:  (path, mtime, size) 没变 → 跳过    ← 解析成本只在文件首次/变更时发生
    查询:  SELECT ... ORDER BY date DESC      ← 毫秒级

emlx 是 Mail.app 的磁盘真源格式, 纯文件 IO 可读 (adapters/apple_mail_emlx.py
已有全套解析)。AppleScript 保留给**写侧** (发送/草稿/标已读) —— DESIGN.md 里
AS-first 的理由全是写侧的 ("dictionary 完整"), 读列表没有非 AS 不可的理由。

# 已知限制 (写清楚, 别让下一个人重新踩)

1. **已读状态可能滞后**: emlx trailer plist 的 read flag 由 Mail.app 惰性回写。
   员工在 Mail.app 里读了一封, 鲶鱼里可能短时间仍显示未读。真源在 Mail.app 的
   Envelope Index (私有 schema, 故意不碰)。mtime 变化时会重解析拿到新 flag。
2. **单账号单文件夹一张表**: folder 是索引键的一部分, Inbox/Sent 分开对账。
3. **删除**: Mail.app 删邮件 → emlx 文件消失 → 对账时索引行同步删除。

# 索引文件

~/.catfish/email_index.db (CATFISH_HOME 优先, 跟 picker_state 等同一套约定)。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .adapters.base import Message

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    source_path   TEXT PRIMARY KEY,   -- .emlx 绝对路径 (对账主键)
    mtime         REAL NOT NULL,      -- stat().st_mtime  (对账判据 1)
    size          INTEGER NOT NULL,   -- stat().st_size   (对账判据 2)
    account       TEXT NOT NULL,
    folder        TEXT NOT NULL,
    msg_id        TEXT NOT NULL,      -- adapter 的稳定 id ('account|emlx:path')
    subject       TEXT NOT NULL DEFAULT '',
    sender        TEXT NOT NULL DEFAULT '',
    recipients    TEXT NOT NULL DEFAULT '[]',   -- JSON array
    date          TEXT NOT NULL DEFAULT '',     -- ISO-8601 UTC
    is_read       INTEGER NOT NULL DEFAULT 0,
    has_attachments INTEGER NOT NULL DEFAULT 0,
    snippet       TEXT NOT NULL DEFAULT '',
    message_id    TEXT,               -- RFC822 Message-ID (thread 三件套)
    in_reply_to   TEXT,
    refs          TEXT,               -- RFC822 References (refs: references 是保留字)
    indexed_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_list
    ON messages (account, folder, date DESC);
CREATE INDEX IF NOT EXISTS idx_messages_unread
    ON messages (account, folder, is_read, date DESC);
"""


def index_db_path() -> Path:
    """~/.catfish/email_index.db。CATFISH_HOME 优先 (对齐 picker_state 那套)。"""
    env = os.environ.get("CATFISH_HOME", "").strip()
    base = Path(env).expanduser() if env else Path.home() / ".catfish"
    return base / "email_index.db"


def open_index(db_path: Path | None = None) -> sqlite3.Connection:
    p = db_path or index_db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.executescript(_SCHEMA)
    # schema 版本钉住: 变了就重建 (索引是纯缓存, 重建无代价, 别写迁移)
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(_SCHEMA_VERSION),),
        )
        conn.commit()
    elif row[0] != str(_SCHEMA_VERSION):
        logger.info("email_index schema %s → %s, 重建", row[0], _SCHEMA_VERSION)
        conn.executescript("DROP TABLE messages; DROP TABLE meta;")
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(_SCHEMA_VERSION),),
        )
        conn.commit()
    return conn


@dataclass
class ReconcileStats:
    """一次对账的账目 —— 测试和日志都靠它, 别省。"""

    scanned: int = 0      # 磁盘上枚举到的文件数
    unchanged: int = 0    # (path,mtime,size) 没变, 跳过解析
    parsed: int = 0       # 真正解析了的 (新增 + 变更)
    removed: int = 0      # 磁盘上没了, 索引里删掉
    errors: int = 0       # 解析失败 (跳过, 不进索引)
    elapsed_ms: int = 0


def reconcile(
    conn: sqlite3.Connection,
    *,
    account: str,
    folder: str,
    emlx_files: Iterable[Path],
    parse: Callable[[Path], Message],
) -> ReconcileStats:
    """把磁盘上的 emlx 集合对账进索引。**只解析新增/变更的文件。**

    Args:
        emlx_files: 这个 account+folder 下的全部 .emlx (调用方 rglob 来的)
        parse:      单文件解析器 (生产传 _parse_emlx_summary 的偏函数;
                    测试传假的 —— 解析次数是增量正确性的**直接判据**)
    """
    t0 = time.monotonic()
    stats = ReconcileStats()

    known: dict[str, tuple[float, int]] = {
        path: (mtime, size)
        for path, mtime, size in conn.execute(
            "SELECT source_path, mtime, size FROM messages WHERE account=? AND folder=?",
            (account, folder),
        )
    }
    seen: set[str] = set()

    for p in emlx_files:
        stats.scanned += 1
        sp = str(p)
        seen.add(sp)
        try:
            st = p.stat()
        except OSError:
            # 枚举到但 stat 不到 (刚被 Mail.app 删掉) → 当不存在
            seen.discard(sp)
            continue
        prev = known.get(sp)
        if prev is not None and prev[0] == st.st_mtime and prev[1] == st.st_size:
            stats.unchanged += 1
            continue
        try:
            m = parse(p)
        except Exception as e:  # noqa: BLE001 — 单文件坏不拖垮整次对账
            stats.errors += 1
            logger.debug("email_index: 解析失败跳过 %s: %s", p, e)
            continue
        conn.execute(
            """INSERT INTO messages (source_path, mtime, size, account, folder,
                   msg_id, subject, sender, recipients, date, is_read,
                   has_attachments, snippet, message_id, in_reply_to, refs, indexed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source_path) DO UPDATE SET
                   mtime=excluded.mtime, size=excluded.size,
                   subject=excluded.subject, sender=excluded.sender,
                   recipients=excluded.recipients, date=excluded.date,
                   is_read=excluded.is_read,
                   has_attachments=excluded.has_attachments,
                   snippet=excluded.snippet, message_id=excluded.message_id,
                   in_reply_to=excluded.in_reply_to, refs=excluded.refs,
                   indexed_at=excluded.indexed_at""",
            (
                sp, st.st_mtime, st.st_size, account, folder,
                m.id, m.subject, m.sender, json.dumps(list(m.recipients)),
                m.date, int(m.is_read), int(m.has_attachments),
                (m.body_text or "")[:300], m.message_id, m.in_reply_to,
                m.references, time.time(),
            ),
        )
        stats.parsed += 1

    # 磁盘上没了的 → 删索引行 (Mail.app 删信/挪文件夹)
    gone = [sp for sp in known if sp not in seen]
    if gone:
        conn.executemany(
            "DELETE FROM messages WHERE source_path=?", [(g,) for g in gone]
        )
        stats.removed = len(gone)

    conn.commit()
    stats.elapsed_ms = int((time.monotonic() - t0) * 1000)
    return stats


def query_messages(
    conn: sqlite3.Connection,
    *,
    account: str,
    folder: str,
    unread_only: bool = False,
    limit: int = 50,
) -> list[Message]:
    """从索引出 list 结果, 形状与 adapter.list_messages 一致 (Message 序列)。"""
    sql = (
        "SELECT msg_id, account, folder, subject, sender, recipients, date, "
        "is_read, has_attachments, snippet, message_id, in_reply_to, refs "
        "FROM messages WHERE account=? AND folder=?"
    )
    args: list = [account, folder]
    if unread_only:
        sql += " AND is_read=0"
    sql += " ORDER BY date DESC LIMIT ?"
    args.append(max(1, limit))

    out: list[Message] = []
    for r in conn.execute(sql, args):
        out.append(Message(
            id=r[0], account=r[1], folder=r[2], subject=r[3], sender=r[4],
            recipients=tuple(json.loads(r[5] or "[]")), date=r[6],
            is_read=bool(r[7]), has_attachments=bool(r[8]), body_text=r[9],
            message_id=r[10], in_reply_to=r[11], references=r[12],
        ))
    return out
