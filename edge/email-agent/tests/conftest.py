"""共享 fixture: 合成 Foxmail Mac 1.5+ 的 SQLite + .mail 文件结构。

不依赖真 Foxmail Mac 安装 —— 直接用 sqlite3 建 schema 灌数据 + 写 .eml 字节到 .mail。

实际 schema 摘自 Foxmail Mac 1.5.8.94608 实测 (.schema dump):
  meta / boxes / mailinfo / mail_box_info / mail_meeting_info /
  mail_fts (FTS3) / mail_fts_content / mail_fts_segments / mail_fts_segdir /
  attachmentinfo / body_structure
我们的 fixture 只建 adapter 真用到的:
  boxes / mailinfo / mail_box_info / attachmentinfo / mail_fts (+ FTS 内表)
  其它表不建; adapter SQL 不会引用它们。
"""
from __future__ import annotations

import sqlite3
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path

import pytest


# ============================================================
# Schema (跟实测对齐)
# ============================================================

#: 创建 mailinfo 表 (字段精简版, 覆盖 adapter 真用到的)
SQL_CREATE_MAILINFO = """
CREATE TABLE mailinfo (
    mailid INTEGER PRIMARY KEY,
    clsid LONGVARCHAR DEFAULT '' NOT NULL,
    subject LONGVARCHAR DEFAULT '' NOT NULL,
    sender LONGVARCHAR DEFAULT '' NOT NULL,
    from_ LONGVARCHAR DEFAULT '' NOT NULL,
    to_ LONGVARCHAR DEFAULT '' NOT NULL,
    reply_to LONGVARCHAR DEFAULT '' NOT NULL,
    date INTEGER DEFAULT 0 NOT NULL,
    size INTEGER DEFAULT 0 NOT NULL,
    attachment INTEGER DEFAULT 0 NOT NULL,
    readstat INTEGER DEFAULT 0 NOT NULL,
    star INTEGER DEFAULT 0 NOT NULL,
    abstract LONGVARCHAR DEFAULT '' NOT NULL,
    messageid LONGVARCHAR DEFAULT '' NOT NULL,
    reference LONGVARCHAR DEFAULT '' NOT NULL,
    csender LONGVARCHAR DEFAULT '' NOT NULL,
    priority INTEGER DEFAULT 0 NOT NULL
)
"""

SQL_CREATE_BOXES = """
CREATE TABLE boxes (
    id INTEGER PRIMARY KEY,
    title LONGVARCHAR DEFAULT '' NOT NULL,
    parentid INTEGER DEFAULT 0 NOT NULL,
    type INTEGER DEFAULT 0 NOT NULL,
    subtype INTEGER DEFAULT 0 NOT NULL
)
"""

SQL_CREATE_MAIL_BOX_INFO = """
CREATE TABLE mail_box_info (
    rowid INTEGER PRIMARY KEY,
    mail_id INTEGER DEFAULT 0 NOT NULL,
    mail_folderid INTEGER,
    mail_uid LONGVARCHAR DEFAULT '' NOT NULL,
    UNIQUE (mail_id, mail_folderid) ON CONFLICT REPLACE
)
"""

SQL_CREATE_ATTACHMENTINFO = """
CREATE TABLE attachmentinfo (
    attachmentid INTEGER PRIMARY KEY,
    mailid INTEGER NOT NULL,
    displayname LONGVARCHAR DEFAULT '' NOT NULL,
    filename LONGVARCHAR DEFAULT '' NOT NULL,
    filetype LONGVARCHAR DEFAULT '' NOT NULL,
    filesize INTEGER DEFAULT 0 NOT NULL,
    filecontenttype VARCHAR DEFAULT '' NOT NULL
)
"""

#: FTS3 虚拟表 (跟实测对齐)
SQL_CREATE_FTS = """
CREATE VIRTUAL TABLE mail_fts USING fts3(
    TOKENIZE simple,
    `subject` LONGVARCHAR DEFAULT '' NOT NULL,
    `from` LONGVARCHAR DEFAULT '' NOT NULL,
    `to` LONGVARCHAR DEFAULT '' NOT NULL,
    `text` LONGVARCHAR DEFAULT '' NOT NULL
)
"""
# 注: 实测用的 TOKENIZE icu 在 SQLite 默认编译里没有, fixture 里换成 simple


# ============================================================
# fixtures
# ============================================================


def _build_eml_bytes(
    *,
    subject: str = "测试主题",
    sender: str = '"张三" <zhang@example.com>',
    to: str = '"陈鸿波" <hongbo@example.com>',
    body: str = "你好, 这是测试邮件正文。",
    cc: str = "",
    in_reply_to: str | None = None,
    date_rfc: str | None = None,
    is_html: bool = False,
) -> bytes:
    m = EmailMessage()
    m["Subject"] = subject
    m["From"] = sender
    m["To"] = to
    if cc:
        m["Cc"] = cc
    m["Date"] = date_rfc or formatdate(localtime=False)
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
    if is_html:
        # 纯 HTML 邮件 (没 plain text 兜底) — 让 body_text 抽取走 strip_html 路径
        m.set_content(body, subtype="html")
    else:
        m.set_content(body)
    return m.as_bytes()


@pytest.fixture
def make_foxmail_profile(tmp_path: Path):
    """factory: 给一组邮件 spec, 搭一个完整的 Foxmail Profile 目录返回路径。

    用法:
        profiles_dir, account = make_foxmail_profile(
            account="hongbo@example.com",
            mails=[
                {"subject": "...", "folder_id": 1, "is_read": False, "body": "..."},
                ...
            ],
        )
    """
    def _factory(
        *,
        account: str = "hongbo@example.com",
        mails: list[dict] | None = None,
        folders: list[dict] | None = None,
    ) -> tuple[Path, str]:
        mails = mails or []
        folders = folders or [
            {"id": 1, "title": "收件箱", "type": 1},
            {"id": 4, "title": "已发送", "type": 4},
            {"id": 5, "title": "草稿箱", "type": 5},
        ]

        profiles = tmp_path / "Profiles"
        account_dir = profiles / account
        account_dir.mkdir(parents=True)

        # 1. 建 messages.db
        db_path = account_dir / "messages.db"
        conn = sqlite3.connect(db_path)
        conn.executescript(
            f"""
            {SQL_CREATE_MAILINFO};
            {SQL_CREATE_BOXES};
            {SQL_CREATE_MAIL_BOX_INFO};
            {SQL_CREATE_ATTACHMENTINFO};
            {SQL_CREATE_FTS};
            """
        )

        # 2. 灌 boxes
        for f in folders:
            conn.execute(
                "INSERT INTO boxes (id, title, parentid, type) VALUES (?, ?, ?, ?)",
                (f["id"], f["title"], f.get("parentid", 0), f.get("type", 0)),
            )

        # 3. 灌 mailinfo + mail_box_info + .mail 文件
        next_mailid = [1000]
        next_attachid = [1]
        for spec in mails:
            mailid = spec.get("mailid") or next_mailid[0]
            next_mailid[0] = mailid + 1
            folder_id = spec.get("folder_id", 1)
            subject = spec.get("subject", "")
            sender = spec.get("sender", '"张三" <zhang@example.com>')
            from_addr = spec.get("from_", sender)
            to_addr = spec.get("to_", "hongbo@example.com")
            body = spec.get("body", "")
            date = int(spec.get("date_unix", 1700000000))
            is_read = 1 if spec.get("is_read", False) else 0
            star = 1 if spec.get("star", False) else 0
            attachments = spec.get("attachments", [])
            has_att = 1 if attachments else 0
            abstract = spec.get("abstract", body[:200])
            messageid = spec.get("messageid", f"<{mailid}@example.com>")
            reference = spec.get("reference", "")
            is_html = spec.get("is_html", False)

            conn.execute(
                """INSERT INTO mailinfo
                (mailid, subject, sender, from_, to_, date, size, attachment,
                 readstat, star, abstract, messageid, reference)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (mailid, subject, sender, from_addr, to_addr, date,
                 spec.get("size", len(body)), has_att, is_read, star,
                 abstract, messageid, reference),
            )
            conn.execute(
                "INSERT INTO mail_box_info (mail_id, mail_folderid) VALUES (?, ?)",
                (mailid, folder_id),
            )
            # FTS 索引 — 用 docid 等于 mailid (跟实测对齐)
            conn.execute(
                "INSERT INTO mail_fts (docid, subject, `from`, `to`, `text`) VALUES (?, ?, ?, ?, ?)",
                (mailid, subject, from_addr, to_addr, body),
            )

            # 附件
            for att in attachments:
                conn.execute(
                    """INSERT INTO attachmentinfo
                    (attachmentid, mailid, displayname, filename, filetype, filesize, filecontenttype)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (next_attachid[0], mailid,
                     att.get("displayname", att.get("filename", "att")),
                     att.get("filename", "att.bin"),
                     att.get("filetype", ""),
                     att.get("filesize", 0),
                     att.get("filecontenttype", "application/octet-stream")),
                )
                next_attachid[0] += 1

            # .mail 文件: Mail/<folder_id>/<bucket>/<mailid>.mail
            bucket = spec.get("bucket", 0)
            mail_dir = account_dir / "Mail" / str(folder_id) / str(bucket)
            mail_dir.mkdir(parents=True, exist_ok=True)
            mail_path = mail_dir / f"{mailid}.mail"
            mail_path.write_bytes(
                _build_eml_bytes(
                    subject=subject,
                    sender=sender,
                    to=to_addr,
                    body=body,
                    cc=spec.get("cc", ""),
                    in_reply_to=reference if reference else None,
                    is_html=is_html,
                )
            )

        conn.commit()
        conn.close()

        return profiles, account

    return _factory
