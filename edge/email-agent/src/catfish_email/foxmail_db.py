"""Foxmail Mac 1.5+ 的 SQLite 数据访问层。

# 实测 schema (Foxmail Mac 1.5.8.94608, 2026-04 实机探测)
=============================================================
Profiles/<email>/messages.db 关键表:

  mailinfo           — 邮件元信息主表
    mailid (PK)      — 内部 64-bit ID
    subject          — 主题
    sender           — 发件人显示名 (例如 "张三")
    from_            — From 地址 (trailing underscore 避 SQL 关键字)
    to_              — To 地址
    date             — Unix epoch (秒, 实测)
    readstat         — 0=未读 / 1=已读
    star             — 0=不标星 / 1=标星
    attachment       — 0=无 / 1=有附件
    messageid        — RFC822 Message-ID
    reference        — In-Reply-To / References
    abstract         — Foxmail 自己提取的摘要 (前 N 行)
    size             — 字节数
    mail_receivedate — 收到时间 (Unix epoch)
    mail_imported    — 0=正常 / 1=用户从外部导入

  mail_box_info      — 邮件 ↔ 文件夹关联表 (一邮件可在多文件夹)
    mail_id          — FK → mailinfo.mailid
    mail_folderid    — FK → boxes.id

  boxes              — 文件夹
    id (PK)          — 数字 ID, 也是 .mail 文件路径里的 <folder_id>
    title            — '收件箱' / '已发送' / '草稿箱' / 'Inbox' / ...
    parentid         — 父文件夹 (0 = 顶层)
    type             — 文件夹类型 (待实测; 推测 1=Inbox, 4=Sent, 5=Drafts)

  mail_fts           — FTS3 虚拟表 (subject/from/to/text)
    用 `mail_fts MATCH 'query'` 触发全文搜索

  attachmentinfo     — 附件元信息
    mailid (FK)
    displayname
    filename
    filetype
    filesize

# .mail 文件
==============
路径: Profiles/<email>/Mail/<folder_id>/<bucket>/<mailid>.mail
内容: 纯 RFC822 (file 命令确认)
bucket 编号貌似是 Foxmail 内部 sharding 策略, 不是 mailid 算出来的
   → 用 glob `Mail/<folder_id>/*/<mailid>.mail` 定位, 避免逆向
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

logger = logging.getLogger("catfish_email.foxmail_db")


# ============================================================
# Schema 常量 (best-effort 跨 Foxmail 版本)
# ============================================================

#: boxes.title 里"收件箱"的中英别名
INBOX_TITLES = ("收件箱", "Inbox", "INBOX")
SENT_TITLES = ("已发送", "Sent", "Sent Items", "已发邮件")
DRAFTS_TITLES = ("草稿箱", "Drafts", "草稿")
TRASH_TITLES = ("已删除", "Trash", "Deleted Items", "Deleted")
JUNK_TITLES = ("垃圾邮件", "Junk", "Spam")


# ============================================================
# 数据类 (DB row 直接映射, 避开 adapters.base.Message 一层)
# ============================================================


@dataclass(frozen=True)
class FoxmailFolder:
    id: int
    title: str
    parent_id: int
    type: int


@dataclass(frozen=True)
class FoxmailMailRow:
    """mailinfo + mail_box_info join 后的一行 (列表场景用)。"""

    mailid: int
    folder_id: int
    folder_title: str
    subject: str
    sender: str
    from_addr: str
    to_addr: str
    date: int
    """Unix epoch 秒。"""
    is_read: bool
    star: bool
    has_attachments: bool
    abstract: str
    """Foxmail 提取的摘要 (前几行), 列表场景比解析 .mail 快很多。"""
    size: int
    messageid: str
    reference: str


# ============================================================
# 主入口
# ============================================================


def open_db(db_path: Path) -> sqlite3.Connection:
    """打开 messages.db, 只读模式 (避免污染 Foxmail 自己的写)。"""
    if not db_path.exists():
        raise FileNotFoundError(f"Foxmail messages.db 不存在: {db_path}")
    # 用 file:?mode=ro URI 强制只读 (Foxmail 在跑也能并发读)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    return conn


def list_folders(conn: sqlite3.Connection) -> list[FoxmailFolder]:
    """列所有文件夹 (含子文件夹)。"""
    rows = conn.execute(
        "SELECT id, title, parentid, type FROM boxes ORDER BY parentid, id"
    ).fetchall()
    return [
        FoxmailFolder(id=r["id"], title=r["title"], parent_id=r["parentid"], type=r["type"])
        for r in rows
    ]


def find_folder_by_title(
    conn: sqlite3.Connection, candidates: Iterable[str]
) -> FoxmailFolder | None:
    """按 title 列表 (中/英别名) 找第一个命中的文件夹。"""
    titles = list(candidates)
    placeholders = ",".join(["?"] * len(titles))
    row = conn.execute(
        f"SELECT id, title, parentid, type FROM boxes WHERE title IN ({placeholders}) "
        f"ORDER BY parentid ASC, id ASC LIMIT 1",
        titles,
    ).fetchone()
    if row is None:
        return None
    return FoxmailFolder(id=row["id"], title=row["title"], parent_id=row["parentid"], type=row["type"])


def resolve_folder_id(conn: sqlite3.Connection, folder_alias: str) -> int | None:
    """把员工 / SKILL 用的 folder 名 (Inbox/收件箱/Sent/...) 映射到真实 boxes.id。

    - 先精确匹配 boxes.title
    - 不命中则按 alias 表找
    - 都不命中返回 None
    """
    # 精确
    row = conn.execute(
        "SELECT id FROM boxes WHERE title = ? LIMIT 1", (folder_alias,)
    ).fetchone()
    if row:
        return row["id"]
    # alias
    alias_groups: dict[str, tuple] = {
        "Inbox": INBOX_TITLES,
        "收件箱": INBOX_TITLES,
        "Sent": SENT_TITLES,
        "已发送": SENT_TITLES,
        "Drafts": DRAFTS_TITLES,
        "草稿箱": DRAFTS_TITLES,
        "Trash": TRASH_TITLES,
        "已删除": TRASH_TITLES,
        "Junk": JUNK_TITLES,
        "垃圾邮件": JUNK_TITLES,
    }
    if folder_alias in alias_groups:
        f = find_folder_by_title(conn, alias_groups[folder_alias])
        return f.id if f else None
    return None


# ============================================================
# 查询: 列邮件 (动态 WHERE)
# ============================================================


def query_messages(
    conn: sqlite3.Connection,
    *,
    folder_id: int | None = None,
    since_unix: int | None = None,
    until_unix: int | None = None,
    sender_contains: str | None = None,
    subject_contains: str | None = None,
    body_contains: str | None = None,
    unread_only: bool = False,
    has_attachments: bool | None = None,
    limit: int = 50,
) -> list[FoxmailMailRow]:
    """按筛选条件查 mailinfo + mail_box_info JOIN, 返回 FoxmailMailRow。

    SQL 用参数化避注入。所有过滤都在 DB 端做, 不在 Python 内存过滤。
    """
    sql = """
        SELECT
            m.mailid, m.subject, m.sender, m.from_, m.to_, m.date,
            m.readstat, m.star, m.attachment, m.abstract, m.size,
            m.messageid, m.reference,
            mbi.mail_folderid AS folder_id,
            b.title AS folder_title
        FROM mailinfo m
        JOIN mail_box_info mbi ON mbi.mail_id = m.mailid
        LEFT JOIN boxes b ON b.id = mbi.mail_folderid
        WHERE 1=1
    """
    args: list = []
    if folder_id is not None:
        sql += " AND mbi.mail_folderid = ?"
        args.append(folder_id)
    if since_unix is not None:
        sql += " AND m.date >= ?"
        args.append(since_unix)
    if until_unix is not None:
        sql += " AND m.date < ?"
        args.append(until_unix)
    if sender_contains:
        sql += " AND (m.sender LIKE ? OR m.from_ LIKE ?)"
        like = f"%{sender_contains}%"
        args.extend([like, like])
    if subject_contains:
        sql += " AND m.subject LIKE ?"
        args.append(f"%{subject_contains}%")
    if body_contains:
        # mailinfo.abstract 有摘要; 完整正文要去 .mail 文件
        sql += " AND m.abstract LIKE ?"
        args.append(f"%{body_contains}%")
    if unread_only:
        sql += " AND m.readstat = 0"
    if has_attachments is True:
        sql += " AND m.attachment = 1"
    elif has_attachments is False:
        sql += " AND m.attachment = 0"

    sql += " ORDER BY m.date DESC LIMIT ?"
    args.append(limit)

    rows = conn.execute(sql, args).fetchall()
    return [_row_to_mail(r) for r in rows]


def query_message_by_id(
    conn: sqlite3.Connection, mailid: int
) -> FoxmailMailRow | None:
    """按 mailid 拉单条 mailinfo + folder。"""
    row = conn.execute(
        """
        SELECT
            m.mailid, m.subject, m.sender, m.from_, m.to_, m.date,
            m.readstat, m.star, m.attachment, m.abstract, m.size,
            m.messageid, m.reference,
            mbi.mail_folderid AS folder_id,
            b.title AS folder_title
        FROM mailinfo m
        LEFT JOIN mail_box_info mbi ON mbi.mail_id = m.mailid
        LEFT JOIN boxes b ON b.id = mbi.mail_folderid
        WHERE m.mailid = ?
        LIMIT 1
        """,
        (mailid,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_mail(row)


def query_attachments(conn: sqlite3.Connection, mailid: int) -> list[tuple[str, int, str]]:
    """拉某邮件附件元信息 (filename, size_bytes, content_type)。"""
    rows = conn.execute(
        """
        SELECT displayname, filename, filesize, filecontenttype, filetype
        FROM attachmentinfo
        WHERE mailid = ?
        ORDER BY attachmentid
        """,
        (mailid,),
    ).fetchall()
    out = []
    for r in rows:
        name = r["displayname"] or r["filename"] or "(unnamed)"
        ctype = r["filecontenttype"] or r["filetype"] or "application/octet-stream"
        out.append((name, int(r["filesize"] or 0), ctype))
    return out


# ============================================================
# 全文搜索 (FTS3)
# ============================================================


def fts_search(
    conn: sqlite3.Connection, query: str, limit: int = 30
) -> list[FoxmailMailRow]:
    """全文搜索, 优先用 mail_fts FTS3 索引, 不可用时退化到 LIKE。

    Foxmail mail_fts 用 ICU tokenizer 做中文分词, 但 Python 内置 sqlite3 没
    编译 ICU 扩展 (跟 Foxmail 用的系统 SQLite 不同), 所以 MATCH 会抛
    'unknown tokenizer: icu'。退化到 LIKE 跑 mailinfo.subject / from_ /
    abstract 三列, 虽然慢一点但 work。
    """
    if not query.strip():
        return []
    try:
        rows = conn.execute(
            """
            SELECT
                m.mailid, m.subject, m.sender, m.from_, m.to_, m.date,
                m.readstat, m.star, m.attachment, m.abstract, m.size,
                m.messageid, m.reference,
                mbi.mail_folderid AS folder_id,
                b.title AS folder_title
            FROM mail_fts f
            JOIN mailinfo m ON m.mailid = f.docid
            LEFT JOIN mail_box_info mbi ON mbi.mail_id = m.mailid
            LEFT JOIN boxes b ON b.id = mbi.mail_folderid
            WHERE f.mail_fts MATCH ?
            ORDER BY m.date DESC
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
        return [_row_to_mail(r) for r in rows]
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "tokenizer" in msg or "no such table" in msg:
            logger.warning(
                "FTS 不可用 (%s), 退化到 LIKE 搜索 subject/from/abstract", e,
            )
            return _like_search(conn, query, limit=limit)
        raise


def _like_search(
    conn: sqlite3.Connection, query: str, limit: int = 30
) -> list[FoxmailMailRow]:
    """FTS 不可用时的退化路径: LIKE 三列 (subject / from / abstract)。

    比 FTS 慢, 但 mailinfo.subject 有 index, mailinfo.date 有 index,
    101 封邮件级别瞬间出结果。
    abstract 是 Foxmail 自己提取的摘要 (前几行), LIKE 它能命中正文里的常见词,
    跟全文搜索覆盖度差不多 (除了纯附件 / 中后段正文不在 abstract 的)。
    """
    like = f"%{query}%"
    rows = conn.execute(
        """
        SELECT
            m.mailid, m.subject, m.sender, m.from_, m.to_, m.date,
            m.readstat, m.star, m.attachment, m.abstract, m.size,
            m.messageid, m.reference,
            mbi.mail_folderid AS folder_id,
            b.title AS folder_title
        FROM mailinfo m
        LEFT JOIN mail_box_info mbi ON mbi.mail_id = m.mailid
        LEFT JOIN boxes b ON b.id = mbi.mail_folderid
        WHERE m.subject LIKE ?
           OR m.from_ LIKE ?
           OR m.sender LIKE ?
           OR m.abstract LIKE ?
        ORDER BY m.date DESC
        LIMIT ?
        """,
        (like, like, like, like, limit),
    ).fetchall()
    return [_row_to_mail(r) for r in rows]


# ============================================================
# .mail 文件路径定位
# ============================================================


def locate_mail_file(account_dir: Path, folder_id: int, mailid: int) -> Path | None:
    """定位 .mail 文件. 多层 fallback (BL-FOXMAIL-MAIL-PATH 5/18 实盘修):

    1. Mail/<folder_id>/*/<mailid>.mail   (老假设: folder_id 当目录, 子层 bucket)
    2. Mail/<folder_id>/<mailid>.mail     (老兜底: 旧 Foxmail 不分桶)
    3. Mail/*/* /<mailid>.mail            (Foxmail mac 1.5 真实 sharding: 顶层
       目录不是 folder_id 而是 mailid 前几位 hash, e.g. mailid=2898... → 28/8/)
    4. Mail/** /<mailid>.mail             (任意深度兜底, 防 Foxmail 改 schema)

    踩坑: 实盘 mailid=2898440511617566600 文件在 Mail/28/8/, 但 DB folder_id ≠ 28.
    sharding 实际按 mailid_first_2_digits/_3rd_digit. (1)+(2) 找不到 → 老代码退
    DB 摘要 → 员工看到的是 abstract 不是完整正文. (3)+(4) 兜住.

    返回找到的第一个; None 表示 Foxmail 真没下载 (只拉 header, 用户没点开).
    """
    mailid_str = str(mailid)
    # (1) folder_id 直接当目录 + bucket 子层
    mail_root_folder = account_dir / "Mail" / str(folder_id)
    if mail_root_folder.is_dir():
        candidates = list(mail_root_folder.glob(f"*/{mailid_str}.mail"))
        if candidates:
            return candidates[0]
        # (2) folder_id 下不分桶 (旧 Foxmail)
        direct = mail_root_folder / f"{mailid_str}.mail"
        if direct.exists():
            return direct
    # (3) Foxmail mac 1.5+ 真实 sharding: Mail/<hash>/<bucket>/<mailid>.mail
    mail_root = account_dir / "Mail"
    if mail_root.is_dir():
        candidates = list(mail_root.glob(f"*/*/{mailid_str}.mail"))
        if candidates:
            return candidates[0]
        # (4) 任意深度兜底
        candidates = list(mail_root.glob(f"**/{mailid_str}.mail"))
        if candidates:
            return candidates[0]
    return None


# ============================================================
# 内部 helpers
# ============================================================


def _row_to_mail(row: sqlite3.Row) -> FoxmailMailRow:
    """sqlite3.Row → FoxmailMailRow, 处理 None / 类型转换"""
    return FoxmailMailRow(
        mailid=int(row["mailid"]),
        folder_id=int(row["folder_id"]) if row["folder_id"] is not None else 0,
        folder_title=row["folder_title"] or "",
        subject=row["subject"] or "",
        sender=row["sender"] or "",
        from_addr=row["from_"] or "",
        to_addr=row["to_"] or "",
        date=int(row["date"] or 0),
        is_read=bool(row["readstat"]),
        star=bool(row["star"]),
        has_attachments=bool(row["attachment"]),
        abstract=row["abstract"] or "",
        size=int(row["size"] or 0),
        messageid=row["messageid"] or "",
        reference=row["reference"] or "",
    )
