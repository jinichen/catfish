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
from . import foxmail7_store
from .base import Account, Attachment, DataNotFoundError, EmailAdapter, ListFilter, Message
from .foxmail_discovery import ROOT_ENV, discover_storage_path

logger = logging.getLogger("catfish_email.adapters.foxmail_win")

_MAX_SCAN_DEPTH = 6

#: 列清单时每封只读这么多字节 —— 够拿主题/发件人/日期/Content-Type。
#: 真机上一个账号 5671 封共 7.8 GB, 为了显示 5 条而全文读入是不可接受的。
_LIST_HEAD_BYTES = 64 * 1024
#: 列清单最多解析几封。按 mtime 倒序取, 所以拿到的是最近的那批。
#: 这是个近似: mtime 不等于邮件 Date, 极旧邮件被重新落盘会排前面。
#: 要精确排序得先建索引, 那是下一步的事 —— 现在的目标是先让邮件出得来。
_LIST_SCAN_CAP = 400
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
    is_read: bool | None = None
    """7.x 的已读位在 ``Boxes/unread.box`` 里, 不在邮件文件里。
    None = 用 ``parsed.flags`` 判 (6.x 路径)。"""


class FoxmailWinAdapter(EmailAdapter):
    """Foxmail Windows 本地数据只读适配器。"""

    name = "foxmail_win"
    supports_drafts = False

    def __init__(self, profiles_dir: Path | None = None) -> None:
        self.profiles_dir = profiles_dir or _detect_profiles_dir()
        # 7.x 账号目录的遍历结果按目录缓存: 一次 list 里会问很多遍文件夹归属,
        # 每次都重扫 5671 个文件是不行的。
        self._acc7: dict[Path, foxmail7_store.Foxmail7Account] = {}
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
                f"或设置 {ROOT_ENV}=Foxmail\\Storage。"
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
        # 按正文过滤时才需要整份读, 否则只读头部就够列清单
        head_bytes = None if filt.body_contains else _LIST_HEAD_BYTES
        entries = [
            entry for entry in self._iter_entries(
                filt.account, filt.folder, head_bytes=head_bytes, cap=_LIST_SCAN_CAP
            )
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
        for entry in self._iter_entries(account, folder, cap=_LIST_SCAN_CAP):
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

    def _account7(self, account_dir: Path) -> foxmail7_store.Foxmail7Account | None:
        """这个账号目录是 7.x 布局就返回它的索引, 否则 None (走 6.x 老路径)。"""
        if not foxmail7_store.is_foxmail7_account(account_dir):
            return None
        cached = self._acc7.get(account_dir)
        if cached is None:
            cached = foxmail7_store.load_account(account_dir)
            self._acc7[account_dir] = cached
        return cached

    def _iter_entries(
        self,
        account: str | None,
        folder: str,
        *,
        head_bytes: int | None = None,
        cap: int | None = None,
    ) -> list[_Entry]:
        account_dirs = self._account_dirs()
        if account is not None:
            account_dirs = [path for path in account_dirs if path.name == account]
            if not account_dirs:
                raise DataNotFoundError(f"Foxmail 没账号 {account!r}")
        entries: list[_Entry] = []
        for account_dir in account_dirs:
            acc7 = self._account7(account_dir)
            paths = _mail_files(account_dir)
            if acc7 is not None:
                # 7.x: 文件夹归属查索引, 不靠路径猜
                paths = [
                    path for path in paths
                    if folder == "*" or _folder_matches(acc7.folder_for(path), folder)
                ]
                # 一个账号几千封共几 GB, 全解不现实。按落盘时间倒序取最近一批。
                paths.sort(key=_mtime, reverse=True)
                if cap is not None:
                    paths = paths[:cap]
            for path in paths:
                if acc7 is None and folder != "*" and not _folder_matches(
                    _folder_for(path, account_dir), folder
                ):
                    continue
                entries.extend(
                    self._parse_path(account_dir.name, path, head_bytes=head_bytes)
                )
        return entries

    def _parse_path(
        self, account: str, path: Path, *, head_bytes: int | None = None
    ) -> list[_Entry]:
        account_dir = self._account_dir(account)
        acc7 = self._account7(account_dir)
        folder = acc7.folder_for(path) if acc7 is not None else _folder_for(path, account_dir)
        read_state = acc7.is_read(path) if acc7 is not None else None
        try:
            if path.suffix.casefold() == ".box":
                parsed = box_parser.parse_box_file(path)
                return [_Entry(account, folder, path, item) for item in parsed]
            # Each EML file is its own message. Keep the file in the stable ID
            # so reading one sibling cannot return another sibling.
            if path.suffix.casefold() == ".eml":
                item = box_parser.parse_eml_file(path)
            else:
                # 7.x 的无后缀邮件文件: 起始偏移嗅探, 不假设从第 0 字节开始
                item = box_parser.parse_mail_file(path, head_bytes=head_bytes)
            return [_Entry(account, folder, path, item, read_state)]
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
        if filt.unread_only and _entry_is_read(entry):
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
            is_read=_entry_is_read(entry),
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
    """按环境变量、参数/注册表、默认目录顺序探测 Storage。"""
    return discover_storage_path()


def _mail_files(root: Path) -> list[Path]:
    """账号目录下所有邮件文件。

    9/18: 这里原本只收 ``.box``/``.eml``。Foxmail 7.2 的邮件文件叫
    ``Mails/0/0/1024`` —— 纯数字文件名, **没有扩展名**, 于是真机上 5671 封
    邮件一封都没进来, 收件箱当然是空的。按扩展名判断邮件是个 6.x 的假设。
    """
    if foxmail7_store.is_foxmail7_account(root):
        return sorted(foxmail7_store.mail_files(root).values())
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
    # 7.x 先按目录特征短路: 只为回答"是不是账号目录"就去遍历 5671 个文件太贵。
    if foxmail7_store.is_foxmail7_account(root):
        return True
    return bool(_mail_files(root))


def _looks_like_account_root(root: Path) -> bool:
    """识别直接配置到单个账号目录的情况。

    Foxmail 的显式目录可能是 Storage 根目录，也可能是 ``Storage/<account>``。

    6.x 的账号目录直接含 ``Mail`` 子目录。9/18 实测 7.2 不是: 账号目录含
    ``Mails``/``Boxes``/``Accounts``, 没有 ``Mail``。旧判据只认 ``Mail``,
    于是把 7.2 账号目录当成 Storage 根, 再把里面的 ``Boxes`` 当成账号 ——
    界面上就显示出一个叫 "Boxes" 的账号。两种布局都要认。
    """
    if foxmail7_store.is_foxmail7_account(root):
        return True
    try:
        return any(
            child.is_dir() and child.name.casefold() == "mail"
            for child in root.iterdir()
        )
    except OSError:
        return False


#: 7.x 里这些是容器目录, 不是文件夹名
_CONTAINER_DIRS = {"mail", foxmail7_store.MAILS_DIR.casefold(), foxmail7_store.BOXES_DIR.casefold()}


def _folder_for(path: Path, account_dir: Path) -> str:
    """按路径猜文件夹名 (6.x 路径 / 7.x 索引读不懂时的兜底)。"""
    parts = path.relative_to(account_dir).parts
    lowered = [part.casefold() for part in parts]
    after_mail = parts[lowered.index("mail") + 1 :] if "mail" in lowered else parts
    # 7.x: Mails/<桶>/<桶>/<id> —— 桶号是分片, 不是文件夹; Boxes 是索引容器。
    # 旧逻辑取 after_mail[0], 于是所有邮件的文件夹都成了 "Boxes"/"Mails",
    # 前端请求 Inbox 时一封也匹配不上, 收件箱就是空的。
    while after_mail and (
        after_mail[0].casefold() in _CONTAINER_DIRS or after_mail[0].isdigit()
    ):
        after_mail = after_mail[1:]
    if not after_mail:
        return foxmail7_store.DEFAULT_FOLDER
    stem = Path(after_mail[-1]).stem
    first = after_mail[0] if len(after_mail) > 1 else stem
    if first.isdigit():
        return foxmail7_store.DEFAULT_FOLDER
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


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _entry_is_read(entry: _Entry) -> bool:
    """7.x 的已读位来自 ``Boxes/unread.box``; 6.x 来自邮件自带的 flags。"""
    if entry.is_read is not None:
        return entry.is_read
    return box_parser.is_read(entry.parsed.flags)
