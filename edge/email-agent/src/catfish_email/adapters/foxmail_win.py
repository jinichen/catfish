"""Windows Foxmail 7+ 只读 adapter。

Foxmail for Windows 没有稳定的公开 API。这里读取 Foxmail 自己落盘的
``.box``/``.eml`` 文件，所有写操作继续走基类的 NotSupportedError，避免
直接改 Foxmail 数据库导致同步状态损坏。

路径探测支持实际安装常见的 ``%LOCALAPPDATA%`` / ``%APPDATA%`` 位置，也支持
``CATFISH_FOXMAIL_ROOT`` 显式指定 Storage 目录。后者既方便企业定制安装，
也让现场可以在不改代码的情况下验证另一种 Foxmail 目录布局。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from email.utils import getaddresses
from pathlib import Path
from urllib.parse import quote, unquote

from .. import box_parser
from .base import Account, Attachment, DataNotFoundError, EmailAdapter, ListFilter, Message

logger = logging.getLogger("catfish_email.adapters.foxmail_win")

_ROOT_ENV = "CATFISH_FOXMAIL_ROOT"
_MAX_SCAN_DEPTH = 6
_FOLDER_ALIASES = {
    "inbox": "Inbox", "收件箱": "Inbox",
    "sent": "Sent", "sent items": "Sent", "已发送": "Sent", "已发送邮件": "Sent",
    "drafts": "Drafts", "草稿": "Drafts", "草稿箱": "Drafts",
    "trash": "Trash", "deleted": "Trash", "垃圾箱": "Trash", "已删除": "Trash",
}


@dataclass(frozen=True)
class _Entry:
    account: str
    folder: str
    path: Path
    parsed: box_parser.ParsedMessage


class FoxmailWinAdapter(EmailAdapter):
    """Foxmail Windows 本地数据只读适配器。"""

    name = "foxmail_win"
    supports_drafts = False

    def __init__(self, profiles_dir: Path | None = None) -> None:
        self.profiles_dir = profiles_dir or _detect_profiles_dir()
        self._flat_account_dir = (
            self.profiles_dir
            if self.profiles_dir is not None
            and _has_mail_data(self.profiles_dir)
            and _looks_like_account_root(self.profiles_dir)
            else None
        )
        if self.profiles_dir is None or not self.profiles_dir.is_dir():
            raise DataNotFoundError(
                "找不到 Foxmail Windows Storage 目录。请确认 Foxmail 已配置账号，"
                f"或设置 {_ROOT_ENV}=Foxmail\\Storage。"
            )
        if not self._account_dirs():
            raise DataNotFoundError(
                f"Foxmail Storage {self.profiles_dir} 下没有可读账号或 .box 文件。"
            )
        logger.info("FoxmailWinAdapter using profiles_dir=%s", self.profiles_dir)

    def list_accounts(self) -> list[Account]:
        accounts = [
            Account(name=path.name, address=path.name, is_default=index == 0)
            for index, path in enumerate(self._account_dirs())
        ]
        if not accounts:
            raise DataNotFoundError(f"Foxmail Storage {self.profiles_dir} 没有账号")
        return accounts

    def list_messages(self, filt: ListFilter) -> list[Message]:
        entries = [
            entry for entry in self._iter_entries(filt.account, filt.folder)
            if self._matches(entry, filt)
        ]
        entries.sort(key=lambda entry: _date_key(entry.parsed.message), reverse=True)
        return [
            self._to_message(entry, full=False)
            for entry in entries[:max(filt.limit, 0)]
        ]

    def read_message(self, message_id: str) -> Message:
        account, relative_path, offset = self._unpack_id(message_id)
        account_dir = self._account_dir(account)
        path = account_dir / Path(relative_path)
        try:
            if not path.resolve().is_relative_to(account_dir.resolve()):
                raise DataNotFoundError("Foxmail message id 指向了账号目录之外")
        except FileNotFoundError as error:
            raise DataNotFoundError(f"Foxmail 账号目录不存在: {account}") from error
        if not path.is_file():
            raise DataNotFoundError(f"Foxmail 邮件文件不存在: {relative_path}")

        for entry in self._parse_path(account, path):
            if entry.parsed.raw_offset == offset:
                return self._to_message(entry, full=True)
        raise DataNotFoundError(f"Foxmail 邮件不存在或已被 Foxmail 重建: {message_id}")

    def search(
        self,
        query: str,
        *,
        account: str | None = None,
        folder: str = "Inbox",
        limit: int = 30,
    ) -> list[Message]:
        needle = query.strip().casefold()
        if not needle:
            return []
        matches = []
        for entry in self._iter_entries(account, folder):
            msg = entry.parsed.message
            text = "\n".join(
                (
                    box_parser.extract_header(msg, "Subject"),
                    box_parser.extract_header(msg, "From"),
                    box_parser.extract_body_text(msg),
                )
            ).casefold()
            if needle in text:
                matches.append(entry)
        matches.sort(key=lambda entry: _date_key(entry.parsed.message), reverse=True)
        return [self._to_message(entry, full=False) for entry in matches[:max(limit, 0)]]

    def _account_dirs(self) -> list[Path]:
        try:
            children = sorted(self.profiles_dir.iterdir())
        except OSError as error:
            logger.warning("读取 Foxmail Storage 失败 %s: %s", self.profiles_dir, error)
            return []
        if self._flat_account_dir is not None:
            return [self._flat_account_dir]
        return [child for child in children if child.is_dir() and _has_mail_data(child)]

    def _account_dir(self, account: str) -> Path:
        if self._flat_account_dir is not None and self._flat_account_dir.name == account:
            return self._flat_account_dir
        return self.profiles_dir / account

    def _iter_entries(self, account: str | None, folder: str) -> list[_Entry]:
        account_dirs = self._account_dirs()
        if account is not None:
            account_dirs = [path for path in account_dirs if path.name == account]
            if not account_dirs:
                raise DataNotFoundError(f"Foxmail 没账号 {account!r}")
        entries: list[_Entry] = []
        for account_dir in account_dirs:
            for path in _mail_files(account_dir):
                current_folder = _folder_for(path, account_dir)
                if folder != "*" and not _folder_matches(current_folder, folder):
                    continue
                entries.extend(self._parse_path(account_dir.name, path))
        return entries

    def _parse_path(self, account: str, path: Path) -> list[_Entry]:
        account_dir = self._account_dir(account)
        folder = _folder_for(path, account_dir)
        try:
            if path.suffix.casefold() == ".box":
                parsed = box_parser.parse_box_file(path)
                return [_Entry(account, folder, path, item) for item in parsed]
            # Each EML file is its own message. Keep the file in the stable ID
            # so reading one sibling cannot return another sibling.
            item = box_parser.parse_eml_file(path)
            return [_Entry(account, folder, path, item)]
        except (OSError, ValueError) as error:
            logger.warning("跳过无法读取的 Foxmail 文件 %s: %s", path, error)
            return []

    def _matches(self, entry: _Entry, filt: ListFilter) -> bool:
        msg = entry.parsed.message
        subject = box_parser.extract_header(msg, "Subject")
        sender = box_parser.extract_header(msg, "From")
        body = box_parser.extract_body_text(msg)
        if filt.sender_contains and filt.sender_contains.casefold() not in sender.casefold():
            return False
        if filt.subject_contains and filt.subject_contains.casefold() not in subject.casefold():
            return False
        if filt.body_contains and filt.body_contains.casefold() not in body.casefold():
            return False
        if filt.unread_only and box_parser.is_read(entry.parsed.flags):
            return False
        has_attachments = box_parser.has_attachments(msg)
        if filt.has_attachments is not None and has_attachments != filt.has_attachments:
            return False
        date = _date_key(msg)
        if filt.since and date and date[:10] < filt.since[:10]:
            return False
        if filt.until and date and date[:10] >= filt.until[:10]:
            return False
        return True

    def _to_message(self, entry: _Entry, *, full: bool) -> Message:
        msg = entry.parsed.message
        subject = box_parser.extract_header(msg, "Subject", "(无主题)")
        body = box_parser.extract_body_text(msg)
        attachments = tuple(
            Attachment(filename=name, size_bytes=size, content_type=content_type)
            for name, size, content_type in box_parser.extract_attachment_metadata(msg)
        )
        relative_path = entry.path.relative_to(self._account_dir(entry.account)).as_posix()
        return Message(
            id=self._pack_id(entry.account, relative_path, entry.parsed.raw_offset),
            account=entry.account,
            folder=entry.folder,
            subject=subject,
            sender=_format_addresses(box_parser.extract_header(msg, "From")),
            recipients=tuple(_format_addresses(value) for value in _headers(msg, "To")),
            cc=tuple(_format_addresses(value) for value in _headers(msg, "Cc")),
            bcc=tuple(_format_addresses(value) for value in _headers(msg, "Bcc")),
            date=box_parser.parse_date_to_iso(box_parser.extract_header(msg, "Date")),
            is_read=box_parser.is_read(entry.parsed.flags),
            has_attachments=bool(attachments),
            attachments=attachments if full else (),
            body_text=body if full else body[:200] + ("…" if len(body) > 200 else ""),
            body_html=box_parser.extract_body_html(msg) if full else "",
            in_reply_to=box_parser.extract_header(msg, "In-Reply-To") or None,
            references=box_parser.extract_header(msg, "References") or None,
            message_id=box_parser.extract_header(msg, "Message-ID") or None,
            thread_id=None,
        )

    @staticmethod
    def _pack_id(account: str, relative_path: str, offset: int) -> str:
        return "foxmail-win|{}|{}|{}".format(
            quote(account, safe=""), quote(relative_path, safe=""), offset
        )

    @staticmethod
    def _unpack_id(message_id: str) -> tuple[str, str, int]:
        parts = message_id.split("|")
        if len(parts) != 4 or parts[0] != "foxmail-win":
            raise DataNotFoundError(f"非法的 foxmail-win message id: {message_id}")
        try:
            return unquote(parts[1]), unquote(parts[2]), int(parts[3])
        except ValueError as error:
            raise DataNotFoundError(f"Foxmail message id 偏移量非法: {message_id}") from error


def _detect_profiles_dir() -> Path | None:
    """按 Windows 真实目录约定探测 Storage；环境变量优先。"""
    env_root = os.environ.get(_ROOT_ENV, "").strip()
    candidates: list[Path] = [Path(env_root)] if env_root else []
    for base_name in ("LOCALAPPDATA", "APPDATA"):
        base_value = os.environ.get(base_name, "").strip()
        if not base_value:
            continue
        base = Path(base_value)
        candidates.extend(
            base / relative
            for relative in (
                "Tencent/Foxmail7/Storage", "Foxmail7/Storage",
                "Tencent/Foxmail/Storage", "Foxmail/Storage",
            )
        )
    for candidate in candidates:
        try:
            if candidate.is_dir() and (_has_mail_data(candidate) or any(p.is_dir() for p in candidate.iterdir())):
                logger.info("探测到 Foxmail Windows Storage: %s", candidate)
                return candidate
        except OSError:
            continue
    return None


def _mail_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for directory, dirs, names in os.walk(root):
        try:
            depth = len(Path(directory).relative_to(root).parts)
        except ValueError:
            continue
        if depth >= _MAX_SCAN_DEPTH:
            dirs[:] = []
        files.extend(
            Path(directory) / name
            for name in names
            if Path(name).suffix.casefold() in {".box", ".eml"}
        )
    return sorted(files)


def _has_mail_data(root: Path) -> bool:
    return bool(_mail_files(root))


def _looks_like_account_root(root: Path) -> bool:
    """识别直接配置到单个账号目录的情况。

    Foxmail 的显式目录可能是 Storage 根目录，也可能是
    ``Storage/<account>``。后者通常直接包含 ``Mail`` 子目录；旧逻辑把
    ``Mail`` 错当成账号，最终界面显示账号名 ``Mail`` 而不是邮箱地址。
    """
    try:
        return any(
            child.is_dir() and child.name.casefold() == "mail"
            for child in root.iterdir()
        )
    except OSError:
        return False


def _folder_for(path: Path, account_dir: Path) -> str:
    parts = path.relative_to(account_dir).parts
    lowered = [part.casefold() for part in parts]
    after_mail = parts[lowered.index("mail") + 1 :] if "mail" in lowered else parts
    if not after_mail:
        return "Inbox"
    stem = Path(after_mail[-1]).stem
    first = after_mail[0] if len(after_mail) > 1 else stem
    return _FOLDER_ALIASES.get(first.casefold(), first)


def _folder_matches(actual: str, requested: str) -> bool:
    return _FOLDER_ALIASES.get(actual.casefold(), actual) == _FOLDER_ALIASES.get(
        requested.casefold(), requested
    )


def _headers(msg, name: str) -> list[str]:
    return [
        f"{display} <{address}>" if display else address
        for display, address in getaddresses(msg.get_all(name, []))
        if address
    ]


def _format_addresses(raw: str) -> str:
    addresses = getaddresses([raw])
    if not addresses:
        return raw.strip()
    name, address = addresses[0]
    return f"{name} <{address}>" if name else address


def _date_key(msg) -> str:
    return box_parser.parse_date_to_iso(box_parser.extract_header(msg, "Date"))
