"""通用 ``.eml`` 目录适配器 —— 读邮件客户端导出的标准邮件文件。

# 为什么是它, 而不是继续读 Foxmail 自己的存储 (9/18)

原先有一整套 Foxmail Windows 本地存储解析 (``foxmail_win`` / ``box_parser`` /
``foxmail7_store`` / ``foxmail_discovery``)。一天之内在上面踩了六个 bug, 前五个
成因完全相同: 把某一版 Foxmail 的私有布局当判据写死, 换个版本全不匹配。

第六个把整条路堵死了: **Foxmail 7.2 Windows 版的邮件文件是加密的。**
实测五个样本 16 KB–262 MB, 熵 7.96–7.97, 可打印字符 38%, 最长可读片段 10–14
字节, 彼此没有任何共同前缀 (每封带独立随机头)。不是压缩也不是混淆。
``Mime/Decode.rec0`` 里倒是有明文邮件头, 但只有头没有正文 —— 而没有正文,
邮件进知识库这件事本身就没价值了。

所以换路: **让用户用邮件客户端自己导出 ``.eml``, 我们读导出目录。**
正文、附件、编码全是 RFC822 标准, 不碰任何厂商私有格式, 也不需要邮箱凭据。

这个模块因此**刻意不认识任何邮件客户端**。它不知道 Foxmail 是什么, 也不该
知道。谁导出的都一样读 —— 这正是上面那六个 bug 的反面。

# 目录约定

两种都认, 不用配置:

    <root>/*.eml                      → 单账号, 账号名 = 目录名
    <root>/<账号>/*.eml               → 每个子目录一个账号
    <root>/<账号>/<文件夹>/*.eml      → 再分一层就是文件夹

文件夹名按 ``_FOLDER_ALIASES`` 归一 (收件箱/Inbox/inbox → Inbox), 认不出的
按原名保留, 平铺的一律算 Inbox。

# 起始偏移靠嗅探

标准 ``.eml`` 从第 0 字节就是 RFC822, 但有的客户端会在前面挂一小段自己的东西。
与其假设, 不如嗅探邮件头在哪 —— 判据是"连续若干行 ``Name: value`` 且含至少一个
真实邮件头字段", 这个特征来自 RFC822 而不是来自某个客户端, 跨来源稳定。
这是上面那套代码里唯一值得留下来的东西。
"""
from __future__ import annotations

import email
import email.policy
import logging
import os
import re
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote, unquote

from .. import rfc822_util as rfc822
from .base import Account, Attachment, DataNotFoundError, EmailAdapter, ListFilter, Message

logger = logging.getLogger("catfish_email.adapters.eml_dir")

#: 显式指定导出目录
ROOT_ENV = "CATFISH_EML_DIR"
#: 老键名 —— 以前是 Foxmail Storage 目录, 现在语义变成"邮件目录"。
#: 留着是为了已经配过的机器升级后不至于突然找不到邮件。
LEGACY_ROOT_ENV = "CATFISH_FOXMAIL_ROOT"

_MAX_SCAN_DEPTH = 6
_SUFFIXES = {".eml", ".emlx", ".msg.eml"}

_FOLDER_ALIASES = {
    "inbox": "Inbox", "收件箱": "Inbox",
    "sent": "Sent", "sent items": "Sent", "已发送": "Sent", "已发送邮件": "Sent",
    "drafts": "Drafts", "draft": "Drafts", "草稿": "Drafts", "草稿箱": "Drafts",
    "trash": "Trash", "deleted": "Trash", "垃圾箱": "Trash", "已删除": "Trash",
    "junk": "Junk", "spam": "Junk", "垃圾邮件": "Junk",
}

#: 嗅探窗口 —— 邮件头不可能比这还靠后
_SNIFF_WINDOW = 64 * 1024
_HEADER_START_RE = re.compile(rb"[A-Za-z][A-Za-z0-9-]{0,40}:")
_MIN_HEADER_LINES = 3
_ESSENTIAL_HEADERS = frozenset(
    {
        b"received", b"from", b"to", b"subject", b"date",
        b"message-id", b"mime-version", b"content-type", b"return-path",
    }
)
#: 列清单时每封只读这么多 —— 够拿主题/发件人/日期/Content-Type
_LIST_HEAD_BYTES = 64 * 1024


# ============================================================
# RFC822 起点嗅探
# ============================================================


def _header_name(line: bytes) -> bytes | None:
    colon = line.find(b":")
    if colon <= 0 or colon > 60:
        return None
    name = line[:colon]
    if not all(
        c == 0x2D or (0x30 <= c <= 0x39) or (0x41 <= c <= 0x5A) or (0x61 <= c <= 0x7A)
        for c in name
    ):
        return None
    if not (0x41 <= name[0] <= 0x5A or 0x61 <= name[0] <= 0x7A):
        return None
    return name.lower()  # bytes 没有 casefold


def _looks_like_header_block(window: bytes, start: int) -> bool:
    """从 ``start`` 起是不是一整块邮件头 (而不是正文里一行碰巧带冒号)。"""
    names: set[bytes] = set()
    lines = 0
    pos = start
    while pos < len(window):
        end = window.find(b"\n", pos)
        raw = window[pos : (len(window) if end == -1 else end + 1)]
        line = raw.rstrip(b"\r\n")
        if not line:
            break
        if line[:1] in (b" ", b"\t"):  # 折行续上一个字段
            pos += len(raw)
            continue
        name = _header_name(line)
        if name is None:
            return False
        names.add(name)
        lines += 1
        pos += len(raw)
        if end == -1:
            break
    return lines >= _MIN_HEADER_LINES and bool(names & _ESSENTIAL_HEADERS)


def sniff_rfc822_offset(data: bytes) -> int | None:
    """找 RFC822 邮件头的起始偏移; 找不到返回 None。

    候选起点不取行首而取每个"字段名+冒号"的位置: 前缀如果不以换行结尾, 会跟
    第一行 header 粘成一行, 只从行首找就会丢掉 From。
    """
    window = data[:_SNIFF_WINDOW]
    for match in _HEADER_START_RE.finditer(window):
        if _looks_like_header_block(window, match.start()):
            return match.start()
    return None


def parse_eml(path: Path, *, head_bytes: int | None = None) -> EmailMessage:
    """读一个 ``.eml``。``head_bytes`` 只读开头 (列清单够用, 别整份读)。"""
    if head_bytes is None:
        data = path.read_bytes()
    else:
        with path.open("rb") as handle:
            data = handle.read(head_bytes)
    offset = sniff_rfc822_offset(data)
    if offset is None:
        raise ValueError(f"{path.name} 里找不到 RFC822 邮件头")
    msg = email.message_from_bytes(data[offset:], policy=email.policy.default)
    if not isinstance(msg, EmailMessage):
        raise ValueError(f"{path.name} 不是 EmailMessage")
    return msg


# ============================================================
# 适配器
# ============================================================


@dataclass(frozen=True)
class _Entry:
    account: str
    folder: str
    path: Path
    message: EmailMessage


class EmlDirAdapter(EmailAdapter):
    """读导出的 ``.eml`` 目录。只读 —— 写操作走基类的 NotSupportedError。"""

    name = "eml_dir"
    supports_drafts = False

    def __init__(self, profiles_dir: Path | None = None) -> None:
        self.profiles_dir = profiles_dir or _detect_root()
        if self.profiles_dir is None or not self.profiles_dir.is_dir():
            raise DataNotFoundError(
                "没有配置邮件目录。请在邮件客户端里把邮件导出为 .eml, "
                f"然后在界面上选择导出目录, 或设置 {ROOT_ENV}=<导出目录>。"
            )
        # 根目录**自己**下面就有 .eml → 平铺形态, 整个目录算一个账号。
        # 不能用 _eml_files(depth=1): 那个会连子目录一起收, 于是分账号的目录
        # 也被判成平铺, 账号名成了导出目录名。判据要问的是"根目录直属文件",
        # 直接 iterdir 说得清楚, 不绕 os.walk 的剪枝语义。
        self._flat = _has_direct_eml(self.profiles_dir)
        if not self._account_dirs():
            raise DataNotFoundError(
                f"{self.profiles_dir} 下没找到 .eml 文件。"
                "请确认导出已完成, 且选的是导出目录本身。"
            )
        logger.info("EmlDirAdapter root=%s flat=%s", self.profiles_dir, self._flat)

    # ── 读 ────────────────────────────────────────────────

    def list_accounts(self) -> list[Account]:
        dirs = self._account_dirs()
        if not dirs:
            raise DataNotFoundError(f"{self.profiles_dir} 下没有账号")
        return [
            Account(name=path.name, address=path.name, is_default=index == 0)
            for index, path in enumerate(dirs)
        ]

    def list_messages(self, filt: ListFilter) -> list[Message]:
        head = None if filt.body_contains else _LIST_HEAD_BYTES
        entries = [
            entry for entry in self._iter_entries(filt.account, filt.folder, head_bytes=head)
            if self._matches(entry, filt)
        ]
        entries.sort(key=lambda entry: _date_key(entry.message), reverse=True)
        return [self._to_message(e, full=False) for e in entries[: max(filt.limit, 0)]]

    def read_message(self, message_id: str) -> Message:
        account, relative_path = self._unpack_id(message_id)
        account_dir = self._account_dir(account)
        path = account_dir / Path(relative_path)
        try:
            if not path.resolve().is_relative_to(account_dir.resolve()):
                raise DataNotFoundError("邮件 id 指向了账号目录之外")
        except FileNotFoundError as error:
            raise DataNotFoundError(f"账号目录不存在: {account}") from error
        if not path.is_file():
            raise DataNotFoundError(f"邮件文件不存在: {relative_path}")
        try:
            msg = parse_eml(path)
        except (OSError, ValueError) as error:
            raise DataNotFoundError(f"邮件读不出来: {relative_path} ({error})") from error
        return self._to_message(
            _Entry(account, _folder_for(path, account_dir), path, msg), full=True
        )

    def search(
        self, query: str, *, account: str | None = None,
        folder: str = "Inbox", limit: int = 30,
    ) -> list[Message]:
        needle = query.strip().casefold()
        if not needle:
            return []
        matches = [
            entry for entry in self._iter_entries(account, folder)
            if needle in "\n".join(
                (_header(entry.message, "Subject"), _header(entry.message, "From"),
                 _body_text(entry.message))
            ).casefold()
        ]
        matches.sort(key=lambda entry: _date_key(entry.message), reverse=True)
        return [self._to_message(e, full=False) for e in matches[: max(limit, 0)]]

    # ── 内部 ──────────────────────────────────────────────

    def _account_dirs(self) -> list[Path]:
        if self._flat:
            return [self.profiles_dir]
        try:
            children = sorted(self.profiles_dir.iterdir())
        except OSError as error:
            logger.warning("读不了 %s: %s", self.profiles_dir, error)
            return []
        return [c for c in children if c.is_dir() and _eml_files(c)]

    def _account_dir(self, account: str) -> Path:
        if self._flat and self.profiles_dir.name == account:
            return self.profiles_dir
        return self.profiles_dir / account

    def _iter_entries(
        self, account: str | None, folder: str, *, head_bytes: int | None = None
    ) -> list[_Entry]:
        dirs = self._account_dirs()
        if account is not None:
            dirs = [d for d in dirs if d.name == account]
            if not dirs:
                raise DataNotFoundError(f"没有账号 {account!r}")
        entries: list[_Entry] = []
        for account_dir in dirs:
            for path in _eml_files(account_dir):
                current = _folder_for(path, account_dir)
                if folder != "*" and not _folder_matches(current, folder):
                    continue
                try:
                    msg = parse_eml(path, head_bytes=head_bytes)
                except (OSError, ValueError) as error:
                    logger.warning("跳过读不了的邮件 %s: %s", path, error)
                    continue
                entries.append(_Entry(account_dir.name, current, path, msg))
        return entries

    def _matches(self, entry: _Entry, filt: ListFilter) -> bool:
        msg = entry.message
        if filt.sender_contains and filt.sender_contains.casefold() not in _header(
            msg, "From"
        ).casefold():
            return False
        if filt.subject_contains and filt.subject_contains.casefold() not in _subject(
            msg
        ).casefold():
            return False
        if filt.body_contains and filt.body_contains.casefold() not in _body_text(
            msg
        ).casefold():
            return False
        if filt.unread_only:
            return False  # 导出的文件没有已读状态, 一律当已读
        if filt.has_attachments is not None and _has_attachments(msg) != filt.has_attachments:
            return False
        date = _date_key(msg)
        if filt.since and date and date[:10] < filt.since[:10]:
            return False
        if filt.until and date and date[:10] >= filt.until[:10]:
            return False
        return True

    def _to_message(self, entry: _Entry, *, full: bool) -> Message:
        msg = entry.message
        body = _body_text(msg)
        attachments = tuple(
            Attachment(filename=name, size_bytes=size, content_type=ctype)
            for name, size, ctype in _attachment_meta(msg)
        )
        relative = entry.path.relative_to(self._account_dir(entry.account)).as_posix()
        return Message(
            id=self._pack_id(entry.account, relative),
            account=entry.account,
            folder=entry.folder,
            subject=_subject(msg) or "(无主题)",
            sender=_format_address(_header(msg, "From")),
            recipients=tuple(_addresses(msg, "To")),
            cc=tuple(_addresses(msg, "Cc")),
            bcc=tuple(_addresses(msg, "Bcc")),
            date=_date_key(msg),
            is_read=True,  # 导出的文件不带已读位
            has_attachments=bool(attachments),
            attachments=attachments if full else (),
            body_text=body if full else body[:200] + ("…" if len(body) > 200 else ""),
            # 内嵌图的 cid: 换成 data: —— 跟 imap_mail 同一条理由, 只在整封时做
            body_html=(
                rfc822.embed_inline_images(_body_html(msg), msg) if full else ""
            ),
            in_reply_to=_header(msg, "In-Reply-To") or None,
            references=_header(msg, "References") or None,
            message_id=_header(msg, "Message-ID") or None,
            thread_id=None,
        )

    @staticmethod
    def _pack_id(account: str, relative_path: str) -> str:
        return f"eml-dir|{quote(account, safe='')}|{quote(relative_path, safe='')}"

    @staticmethod
    def _unpack_id(message_id: str) -> tuple[str, str]:
        parts = message_id.split("|")
        if len(parts) != 3 or parts[0] != "eml-dir":
            raise DataNotFoundError(f"非法的 eml-dir message id: {message_id}")
        return unquote(parts[1]), unquote(parts[2])


# ============================================================
# 模块级 helper
# ============================================================


def _detect_root() -> Path | None:
    for var in (ROOT_ENV, LEGACY_ROOT_ENV):
        raw = os.environ.get(var, "").strip()
        if raw:
            path = Path(raw).expanduser()
            if path.is_dir():
                if var == LEGACY_ROOT_ENV:
                    logger.info("用的是老环境变量 %s; 新名字是 %s", LEGACY_ROOT_ENV, ROOT_ENV)
                return path
            logger.warning("%s=%s 不是目录", var, raw)
    return None


def _has_direct_eml(root: Path) -> bool:
    """``root`` 下面**直接**有没有 .eml (不看子目录)。"""
    try:
        return any(
            child.is_file() and child.suffix.casefold() in _SUFFIXES
            for child in root.iterdir()
        )
    except OSError:
        return False


def _eml_files(root: Path, depth: int = _MAX_SCAN_DEPTH) -> list[Path]:
    files: list[Path] = []
    for directory, dirs, names in os.walk(root):
        try:
            level = len(Path(directory).relative_to(root).parts)
        except ValueError:
            continue
        if level >= depth:
            dirs[:] = []
        files.extend(
            Path(directory) / name
            for name in names
            if Path(name).suffix.casefold() in _SUFFIXES
        )
    return sorted(files)


def _folder_for(path: Path, account_dir: Path) -> str:
    parts = path.relative_to(account_dir).parts
    if len(parts) <= 1:
        return "Inbox"  # 平铺 = 收件箱
    return _FOLDER_ALIASES.get(parts[0].casefold(), parts[0])


def _folder_matches(actual: str, requested: str) -> bool:
    return _FOLDER_ALIASES.get(actual.casefold(), actual) == _FOLDER_ALIASES.get(
        requested.casefold(), requested
    )


# ─── RFC822 取值: 逻辑在 rfc822_util, 这里只留别名 ───────────
#
# 9/18: imap_mail 要用同一套 (主题都是 =?GB2312?B?=, 地址、日期、附件元信息
# 的处理一模一样)。抄第二份的那一刻就该抽出去 —— 今天刚在 wiki 那边吃过
# 三条产线各写各的、然后各自漂移的亏。
#
# 用别名而不是改调用点: 调用点一个字没动, 这次重构不可能改变行为。
_header = rfc822.header
_subject = rfc822.subject
_addresses = rfc822.addresses
_format_address = rfc822.format_address
_date_key = rfc822.date_iso
_body_text = rfc822.body_text
_body_html = rfc822.body_html
_has_attachments = rfc822.has_attachments
_attachment_meta = rfc822.attachment_meta
