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
from typing import Callable, Iterable, Iterator, Sequence

from .adapters.base import Message

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    -- 9/18: 从 (source_path, mtime, size) 泛化成 (source_key, fingerprint)。
    --
    -- 原来写死了"来源是文件"这个假设: 主键是 .emlx 绝对路径, 变更判据是
    -- stat 的 mtime+size。IMAP 两样都没有 —— 它的身份是 UID, 变更判据是 FLAGS。
    --
    -- source_key  文件型: 绝对路径
    --             IMAP:  imap:<folder>:<uidvalidity>:<uid>
    --                    (必须带 UIDVALIDITY —— 服务器重建邮箱后旧 UID 指向
    --                     完全不相干的邮件)
    -- fingerprint 文件型: "mtime:size"
    --             IMAP:  flags 串 (UID 不变, 只有已读/标记会变)
    source_key    TEXT PRIMARY KEY,
    fingerprint   TEXT NOT NULL,
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


def _known_fingerprints(
    conn: sqlite3.Connection, *, account: str, folder: str
) -> dict[str, str]:
    return dict(
        conn.execute(
            "SELECT source_key, fingerprint FROM messages WHERE account=? AND folder=?",
            (account, folder),
        )
    )


def changed_keys(
    conn: sqlite3.Connection,
    *,
    account: str,
    folder: str,
    items: Iterable[tuple[str, str]],
) -> list[str]:
    """哪些 key 需要重新解析。**纯 SQLite, 不碰网络、不碰磁盘。**

    reconcile 自己也会算一遍同样的东西 (两者共用 _known_fingerprints), 这里
    单独暴露出来是给**远程来源批量取**用的:

        文件型来源 parse 一次 = 读一个本地文件, 一封一次无所谓。
        IMAP parse 一次 = 一个网络往返。首次同步两千封就是两千个往返,
        跨广域网按 100ms 算要三分多钟, 而这两千封本可以十次 FETCH 取完。

    所以远程 adapter 的用法是: 先 changed_keys 问"要取哪些" → 批量取进内存
    → 再 reconcile, parse 从内存里拿。多一次 SELECT, 省掉 N-1 个往返。
    """
    known = _known_fingerprints(conn, account=account, folder=folder)
    return [key for key, fingerprint in items if known.get(key) != fingerprint]


def reconcile(
    conn: sqlite3.Connection,
    *,
    account: str,
    folder: str,
    items: Iterable[tuple[str, str]],
    parse: Callable[[str], Message],
) -> ReconcileStats:
    """把一组 (source_key, fingerprint) 对账进索引。**只解析新增/变更的。**

    Args:
        items: 这个 account+folder 下当前存在的全部条目。
               fingerprint 变了就重新解析, 没变就跳过 —— 增量的全部秘密。
               **没出现在这里的 key 会被当成"没了"删掉**, 所以调用方必须给全,
               不能只给一页。
        parse: 按 source_key 取一封邮件。生产传偏函数, 测试传假的 ——
               解析次数是增量正确性的**直接判据**。

    9/18: 从"文件路径 + mtime/size"泛化过来。原来的形状把"来源是文件"焊死在
    表结构里, IMAP 接不上 —— 它的身份是 UID, 变更判据是 FLAGS。
    """
    t0 = time.monotonic()
    stats = ReconcileStats()

    known = _known_fingerprints(conn, account=account, folder=folder)
    seen: set[str] = set()

    for key, fingerprint in items:
        stats.scanned += 1
        seen.add(key)
        if known.get(key) == fingerprint:
            stats.unchanged += 1
            continue
        try:
            m = parse(key)
        except Exception as e:  # noqa: BLE001 — 单条坏不拖垮整次对账
            stats.errors += 1
            logger.debug("email_index: 解析失败跳过 %s: %s", key, e)
            continue
        conn.execute(
            """INSERT INTO messages (source_key, fingerprint, account, folder,
                   msg_id, subject, sender, recipients, date, is_read,
                   has_attachments, snippet, message_id, in_reply_to, refs, indexed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source_key) DO UPDATE SET
                   fingerprint=excluded.fingerprint,
                   subject=excluded.subject, sender=excluded.sender,
                   recipients=excluded.recipients, date=excluded.date,
                   is_read=excluded.is_read,
                   has_attachments=excluded.has_attachments,
                   snippet=excluded.snippet, message_id=excluded.message_id,
                   in_reply_to=excluded.in_reply_to, refs=excluded.refs,
                   indexed_at=excluded.indexed_at""",
            (
                key, fingerprint, account, folder,
                m.id, m.subject, m.sender, json.dumps(list(m.recipients)),
                m.date, int(m.is_read), int(m.has_attachments),
                (m.body_text or "")[:300], m.message_id, m.in_reply_to,
                m.references, time.time(),
            ),
        )
        stats.parsed += 1

    # 来源里没了的 → 删索引行 (文件被删/挪走, 或服务器上那封没了)
    gone = [k for k in known if k not in seen]
    if gone:
        conn.executemany(
            "DELETE FROM messages WHERE source_key=?", [(g,) for g in gone]
        )
        stats.removed = len(gone)

    conn.commit()
    stats.elapsed_ms = int((time.monotonic() - t0) * 1000)
    return stats


def file_items(paths: Iterable[Path]) -> Iterator[tuple[str, str]]:
    """文件型来源的 (source_key, fingerprint) 生成器。

    stat 不到的**直接不产出** —— 那样它既不会被解析, 也不会算进 seen,
    于是会被当成"没了"删掉。这正是想要的: 枚举到但 stat 不到, 说明刚被删。
    """
    for path in paths:
        try:
            st = path.stat()
        except OSError:
            continue
        yield str(path), f"{st.st_mtime}:{st.st_size}"


def reconcile_files(
    conn: sqlite3.Connection,
    *,
    account: str,
    folder: str,
    files: Iterable[Path],
    parse: Callable[[Path], Message],
) -> ReconcileStats:
    """文件型来源的便利封装 —— 调用方不用自己拼 fingerprint。"""
    return reconcile(
        conn, account=account, folder=folder,
        items=file_items(files), parse=lambda key: parse(Path(key)),
    )


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
