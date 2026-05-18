"""Foxmail for Mac 1.5+ · 只读 adapter (基于 SQLite + .mail 文件混合架构)。

# 数据布局 (实测 Foxmail Mac 1.5.8.94608)
==========================================
~/Library/Containers/com.tencent.Foxmail/Data/Library/Foxmail/Profiles/<email>/
├── messages.db                                  ← SQLite 元数据 (mailinfo / mail_box_info / boxes / mail_fts / ...)
├── Mail/<folder_id>/<bucket>/<mailid>.mail      ← 单文件 RFC822 邮件正文
├── Contacts/contacts.db
├── calendar/foxcalendar.db
└── xtag/xtag.db

# 红线
=====
create_draft 抛 NotSupportedError —— Foxmail Mac 写入 SQLite + 触发 rescan
不可靠 (跨版本不一致), 不冒"草稿丢失"的险。SKILL.md 检测到后降级到
"我把正文给你, 复制粘贴到 Foxmail 撰写窗口"模式。
"""
from __future__ import annotations

import email
import email.policy
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .. import foxmail_db
from .base import (
    Account,
    Attachment,
    DataNotFoundError,
    EmailAdapter,
    ListFilter,
    Message,
    NotSupportedError,
)

logger = logging.getLogger("catfish_email.adapters.foxmail_mac")


class FoxmailMacAdapter(EmailAdapter):
    """Foxmail Mac 只读 adapter (新版 SQLite 后端)。"""

    name = "foxmail_mac"
    supports_drafts = False  # 红线: Foxmail Mac 写入不可靠

    def __init__(self, profiles_dir: Path | None = None) -> None:
        """
        Args:
            profiles_dir: Foxmail Profiles 目录绝对路径; None 则自动探测。
                          每个子目录是一个邮箱账号 (例如 706574875@qq.com)。
        Raises:
            DataNotFoundError: 找不到 Foxmail 数据目录
        """
        if profiles_dir is None:
            profiles_dir = _detect_profiles_dir()
        if profiles_dir is None or not profiles_dir.is_dir():
            raise DataNotFoundError(
                "找不到 Foxmail Mac Profiles 目录. 检查:\n"
                "  ~/Library/Containers/com.tencent.Foxmail/Data/Library/Foxmail/Profiles/\n"
                "或者通过 profiles_dir 参数显式指定"
            )
        self.profiles_dir = profiles_dir
        logger.info("FoxmailMacAdapter using profiles_dir=%s", profiles_dir)

    # ============================================================
    # 公共接口
    # ============================================================

    def list_accounts(self) -> list[Account]:
        out: list[Account] = []
        for entry in sorted(self.profiles_dir.iterdir()):
            if not entry.is_dir():
                continue
            if "@" not in entry.name:
                continue
            db = entry / "messages.db"
            if not db.exists():
                logger.warning("账号目录 %s 没 messages.db, 跳过", entry.name)
                continue
            out.append(Account(name=entry.name, address=entry.name, is_default=False))
        if not out:
            raise DataNotFoundError(
                f"Profiles 目录 {self.profiles_dir} 下没找到任何账号. "
                f"员工是不是没在 Foxmail 配过邮箱?"
            )
        # 第一个标 default
        return [
            Account(a.name, a.address, is_default=(i == 0))
            for i, a in enumerate(out)
        ]

    def list_messages(self, filt: ListFilter) -> list[Message]:
        account = self._resolve_account(filt.account)
        with foxmail_db.open_db(self._db_path(account)) as conn:
            folder_id = foxmail_db.resolve_folder_id(conn, filt.folder)
            if folder_id is None:
                logger.warning("找不到 folder %r", filt.folder)
                return []
            rows = foxmail_db.query_messages(
                conn,
                folder_id=folder_id,
                since_unix=_iso_to_unix(filt.since),
                until_unix=_iso_to_unix(filt.until),
                sender_contains=filt.sender_contains,
                subject_contains=filt.subject_contains,
                body_contains=filt.body_contains,
                unread_only=filt.unread_only,
                has_attachments=filt.has_attachments,
                limit=filt.limit,
            )
        return [self._row_to_message_snippet(account, r) for r in rows]

    def read_message(self, message_id: str) -> Message:
        account, mailid = self._unpack_id(message_id)
        db_path = self._db_path(account)
        if not db_path.exists():
            # account 不在 (员工 typo / 已删 / 老 id) 统一报 DataNotFoundError
            raise DataNotFoundError(
                f"账号 {account} 的 messages.db 不存在 ({db_path})"
            )
        with foxmail_db.open_db(db_path) as conn:
            row = foxmail_db.query_message_by_id(conn, mailid)
            if row is None:
                raise DataNotFoundError(f"邮件 mailid={mailid} 不在 messages.db")
            attachments_meta = foxmail_db.query_attachments(conn, mailid)

        # 找 .mail 文件 (含完整 RFC822, 用于抽 body_html / 全文)
        mail_path = foxmail_db.locate_mail_file(
            self._account_dir(account), row.folder_id, mailid
        )
        body_text, body_html, recipients_full, cc_full = "", "", (), ()
        if mail_path is not None and mail_path.exists():
            try:
                raw = mail_path.read_bytes()
                msg = email.message_from_bytes(raw, policy=email.policy.default)
                body_text = _extract_body_text(msg)
                body_html = _extract_body_html(msg)
                recipients_full = tuple(_split_addrs(_safe_header(msg, "To")))
                cc_full = tuple(_split_addrs(_safe_header(msg, "Cc")))
            except Exception as e:  # noqa: BLE001
                logger.warning(".mail 文件解析失败 %s: %s", mail_path, e)
                body_text = row.abstract  # fallback to DB 摘要
        else:
            logger.warning("mailid=%d 的 .mail 文件没找到, 用 DB 摘要", mailid)
            body_text = row.abstract

        return Message(
            id=message_id,
            account=account,
            folder=row.folder_title or str(row.folder_id),
            subject=row.subject,
            sender=row.sender or row.from_addr,
            recipients=recipients_full or tuple(_split_addrs(row.to_addr)),
            cc=cc_full,
            date=_unix_to_iso(row.date),
            is_read=row.is_read,
            has_attachments=row.has_attachments,
            attachments=tuple(
                Attachment(filename=fn, size_bytes=sz, content_type=ct)
                for fn, sz, ct in attachments_meta
            ),
            body_text=body_text,
            body_html=body_html,
            in_reply_to=row.reference or None,
            thread_id=None,
        )

    def search(
        self,
        query: str,
        *,
        account: str | None = None,
        folder: str = "Inbox",
        limit: int = 30,
    ) -> list[Message]:
        if not query.strip():
            return []
        account = self._resolve_account(account)
        with foxmail_db.open_db(self._db_path(account)) as conn:
            rows = foxmail_db.fts_search(conn, query, limit=limit)
        # FTS 不限文件夹 (Foxmail FTS 表跨所有 folder), 这里若有 folder 过滤再筛一遍
        if folder and folder != "*":
            with foxmail_db.open_db(self._db_path(account)) as conn:
                target = foxmail_db.resolve_folder_id(conn, folder)
            if target is not None:
                rows = [r for r in rows if r.folder_id == target]
        return [self._row_to_message_snippet(account, r) for r in rows]

    def create_draft(self, **kwargs) -> str:  # noqa: D401
        raise NotSupportedError(
            "Foxmail Mac 不支持自动创建草稿 (写入 messages.db + 触发 rescan 不可靠)。"
            " SKILL 应把正文 quote 给员工, 让员工自己粘贴到 Foxmail 撰写窗口。"
        )

    def delete_message(self, message_id: str) -> None:
        """5/18 BL-EMAIL-FOXMAIL-DELETE-REVERT: Foxmail 不支持自动删除.

        历史: 5/18 早期尝试走 sqlite UPDATE mail_box_info 软删, 实盘鸿波重启
        Foxmail 后邮件**回到 INBOX** — Foxmail IMAP 同步把本地 folder 改动
        当作"过时本地状态" 用 server-end (server 还在 INBOX) 覆盖. Foxmail Mac
        schema 没暴露"待同步删除" 队列表, 没有可靠路径让 Foxmail 把删除推到
        server. 走 GUI scripting (System Events 模拟 ⌫) 太脆弱不同 Foxmail
        版本会挂. 撤回这条实现, 引导员工去 Foxmail 客户端自己删.

        foxmail_db.move_to_trash() 保留作为 reference / debug 用, 不被这里调.
        """
        raise NotSupportedError(
            "Foxmail Mac 不支持自动删除 — Foxmail 没暴露删除 IPC, 走 sqlite "
            "会被 IMAP 同步从 server 拉回 INBOX 让删除无效. 请去 Foxmail "
            "客户端自己删 (它会通知 server, 然后下次 Companion 同步看不到)."
        )

    def mark_read(self, message_id: str, *, read: bool = True) -> None:
        """5/18 BL-EMAIL-MARK-READ: 直接 UPDATE mailinfo.readstat.

        Foxmail Mac 1.5+ 用 sqlite, 写 readstat=1 就持久化了, 下次启动 Foxmail
        看到的就是已读. WAL 模式 + 2s timeout, Foxmail 并发跑时不会撞死.
        """
        account, mailid_str = self._unpack_id(message_id)
        try:
            mailid = int(mailid_str)
        except ValueError as e:
            raise DataNotFoundError(
                f"Foxmail mailid 必须是整数, 收到: {mailid_str!r}"
            ) from e
        db_path = self._db_path(account)
        if not db_path.exists():
            raise DataNotFoundError(
                f"账号 {account} 的 messages.db 不存在 ({db_path})"
            )
        with foxmail_db.open_db_writable(db_path) as conn:
            ok = foxmail_db.mark_message_read(conn, mailid, read=read)
        if not ok:
            raise DataNotFoundError(
                f"邮件 mailid={mailid} 不在 messages.db (账号 {account})"
            )

    # ============================================================
    # 内部 helpers
    # ============================================================

    def _resolve_account(self, account: str | None) -> str:
        """resolve 账号名. 不存在 → raise DataNotFoundError (EmailAdapterError 子类)
        让上层 _cmd_list 的 try/except 兜得住, 避免单 adapter 缺账号让全命令挂.

        5/18 BL-EMAIL-ACCOUNT-CROSS-ADAPTER-CRASH: 实盘 `catfish-email list
        --account "Google"` 时, Apple Mail 找到了 Google 账号, Foxmail
        没"Google" profile, 但 `_db_path("Google")` 直接拼出不存在路径
        让 sqlite open 抛 FileNotFoundError 不在 EmailAdapterError 体系内 →
        _cmd_list except 不接 → 整命令崩溃.
        """
        if account is None:
            accounts = self.list_accounts()
            return accounts[0].address
        # 显式账号 — 验证存在 (Foxmail profile 目录名匹配)
        if not self._db_path(account).exists():
            raise DataNotFoundError(
                f"Foxmail 没账号 {account!r} (在 {self.profiles_dir} 下没找到 "
                f"对应 profile/messages.db). 跨客户端查询时这是预期 — "
                f"--account 可能只在 Apple Mail 那一侧存在."
            )
        return account

    def _account_dir(self, account: str) -> Path:
        return self.profiles_dir / account

    def _db_path(self, account: str) -> Path:
        return self._account_dir(account) / "messages.db"

    def _row_to_message_snippet(
        self, account: str, row: foxmail_db.FoxmailMailRow
    ) -> Message:
        """list 场景: 用 DB 元信息 + abstract 当 body snippet, 不读 .mail 文件 (快)。"""
        return Message(
            id=self._pack_id(account, row.mailid),
            account=account,
            folder=row.folder_title or str(row.folder_id),
            subject=row.subject,
            sender=row.sender or row.from_addr,
            recipients=tuple(_split_addrs(row.to_addr)),
            cc=(),
            date=_unix_to_iso(row.date),
            is_read=row.is_read,
            has_attachments=row.has_attachments,
            attachments=(),
            body_text=row.abstract or "",
            body_html="",
            in_reply_to=row.reference or None,
            thread_id=None,
        )

    @staticmethod
    def _pack_id(account: str, mailid: int) -> str:
        return f"foxmail-mac|{account}|{mailid}"

    @staticmethod
    def _unpack_id(message_id: str) -> tuple[str, int]:
        parts = message_id.split("|")
        if len(parts) != 3 or parts[0] != "foxmail-mac":
            raise DataNotFoundError(f"非法的 foxmail-mac message id: {message_id}")
        try:
            return parts[1], int(parts[2])
        except ValueError as e:
            raise DataNotFoundError(f"message id 中的 mailid 不是整数: {message_id}") from e


# ============================================================
# Profiles dir 探测
# ============================================================


def _detect_profiles_dir() -> Path | None:
    """按可能性顺序探 Foxmail Mac Profiles dir, 第一个存在就返回。"""
    home = Path.home()
    candidates = [
        # 沙盒版 (App Store / 现代官网下载)
        home / "Library/Containers/com.tencent.Foxmail/Data/Library/Foxmail/Profiles",
        # 旧路径 (实测罕见, 兜底)
        home / "Library/Application Support/Foxmail/Profiles",
        home / "Library/Application Support/Foxmail7/Profiles",
    ]
    for c in candidates:
        if c.is_dir():
            logger.info("探测到 Foxmail Profiles: %s", c)
            return c
    return None


# ============================================================
# 时间 / 地址 / 正文 helpers
# ============================================================


def _iso_to_unix(iso: str | None) -> int | None:
    """'2026-04-26' / '2026-04-26T10:00:00Z' → unix 秒; None / 空 → None。"""
    if not iso:
        return None
    # 容忍 'YYYY-MM-DD' 简写
    try:
        if len(iso) == 10:
            dt = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
        else:
            # fromisoformat 不接受 'Z', 改成 +00:00
            iso2 = iso.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso2)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        logger.warning("无法解析 ISO 时间: %r", iso)
        return None


def _unix_to_iso(unix_secs: int) -> str:
    if not unix_secs:
        return ""
    return datetime.fromtimestamp(unix_secs, tz=timezone.utc).isoformat()


def _safe_header(msg, name: str) -> str:
    val = msg.get(name)
    return str(val).strip() if val is not None else ""


_ADDR_SEP_RE = None  # lazy init


def _split_addrs(raw: str) -> list[str]:
    if not raw:
        return []
    global _ADDR_SEP_RE
    if _ADDR_SEP_RE is None:
        import re
        _ADDR_SEP_RE = re.compile(r"\s*,\s*")
    return [s for s in _ADDR_SEP_RE.split(raw) if s]


def _extract_body_text(msg) -> str:
    """从 EmailMessage 抽纯文本正文 (优先 text/plain, 没有就转 HTML)。"""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body = _decode_payload(part)
                break
        if not body:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    body = _strip_html(_decode_payload(part))
                    break
    else:
        body = _decode_payload(msg)
        if msg.get_content_type() == "text/html":
            body = _strip_html(body)
    return body


def _extract_body_html(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                return _decode_payload(part)
    elif msg.get_content_type() == "text/html":
        return _decode_payload(msg)
    return ""


def _decode_payload(part) -> str:
    try:
        payload = part.get_payload(decode=True)
    except Exception:
        return ""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    charset = part.get_content_charset() or "utf-8"
    for enc in (charset, "utf-8", "gb18030", "gbk", "latin-1"):
        try:
            return payload.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return payload.decode("utf-8", errors="replace")


def _strip_html(html: str) -> str:
    if not html:
        return ""
    import re
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"</p>", "\n", html, flags=re.I)
    html = re.sub(r"<[^>]+>", "", html)
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()
