"""IMAP 适配器 —— 唯一不依赖邮件客户端的取数路径。

# 为什么非它不可 (9/18)

这一天在两条"读客户端本地数据"的路上各撞了一次墙, 而且性质不同:

  Foxmail 7.2 (Windows)   邮件文件**加密**。五个样本 16 KB–262 MB, 熵 7.96–7.97,
                          彼此无共同前缀。有数据, 读不了。
  新版 Outlook            **不提供 COM**, 且本地没有邮件。实测 Store 容器 0 MB,
                          %LOCALAPPDATA%\\Microsoft\\Olk 里只有 WebView 的 HTTP
                          缓存和按哈希存的附件 —— 它是个网页套壳。

两次的共同点: 我们依赖的是厂商的私有接口/私有格式, 存废不由我们决定。
腾讯改了格式, 微软换了架构, 一天之内连输两把。

IMAP 是公开协议, 跟客户端版本、厂商策略完全无关。代价是要持有凭据。

# 实测参数 (chinatelecom.cn, 9/18 真机)

    imap.chinatelecom.cn:993   Hermes 邮件服务器 (注意: 跟 hermes-agent 无关,
                               只是重名, 看日志别混)
    CAPABILITY (登录前后一致):
        IMAP4rev1 ID XLIST XAPPLEPUSHSERVICE AUTH=KERBEROS_V4
    没有 IDLE        → 只能轮询, 接现成的 email_scheduler (默认 600 秒)
    没有 UIDPLUS / CONDSTORE / QRESYNC
                     → 增量同步只能靠 UIDVALIDITY + UID 区间对账
    没有 LOGINDISABLED, 也没有 XOAUTH2
                     → 只能用户名+密码 (或授权码), 必须进 keyring, 绝不落盘
    文件夹是 modified UTF-7, flags 只有 \\Marked, **没有 \\Sent/\\Trash**
                     → 角色识别只能靠解码后的名字

⚠ "登录前的 CAPABILITY 不代表登录后的" —— 不少服务器认证后才亮全部能力。
   所以上面那份是**登录后**重新查过的, 不是拿 pre-auth 的当结论。

# 红线

  · 凭据只从环境变量/keyring 取, **绝不写日志、绝不进异常消息**
  · 只读: SELECT 一律 readonly, 写操作走基类 NotSupportedError
  · id 里必须带 UIDVALIDITY —— 服务器重置它时旧 UID 全部失效, 不带的话
    会去拉到完全不相干的邮件
"""
from __future__ import annotations

import binascii
import email
import email.policy
import imaplib
import logging
import os
import re
from dataclasses import dataclass
from email.message import EmailMessage
from urllib.parse import quote, unquote

from .. import rfc822_util as rfc822
from .base import (
    Account,
    Attachment,
    ClientNotRunningError,
    DataNotFoundError,
    EmailAdapter,
    EmailAdapterError,
    ListFilter,
    Message,
)

logger = logging.getLogger("catfish_email.adapters.imap_mail")

HOST_ENV = "CATFISH_IMAP_HOST"
PORT_ENV = "CATFISH_IMAP_PORT"
USER_ENV = "CATFISH_IMAP_USER"
#: 凭据。阶段一从环境变量取 (Companion 从 keyring 读出来再注入子进程),
#: 这样 catfish-email 自己永远不碰 keyring, 也不需要平台后端。
PASSWORD_ENV = "CATFISH_IMAP_PASSWORD"

DEFAULT_PORT = 993
#: 列清单一次最多解析几封 —— 只取最新的那批, 不整箱拉
LIST_FETCH_CAP = 200
#: 网络超时。现场网络差时宁可报错, 不要挂死在 socket 上
TIMEOUT_SECONDS = 30

#: 解码后的文件夹名 → 统一角色。跟 eml_dir 的表同源 (9/18 实测 chinatelecom.cn
#: 的六个文件夹正好全落在里面: 已发送/草稿箱/垃圾箱/已删除/广告文件夹)。
FOLDER_ALIASES = {
    "inbox": "Inbox", "收件箱": "Inbox",
    "sent": "Sent", "sent items": "Sent", "已发送": "Sent", "已发送邮件": "Sent",
    "drafts": "Drafts", "draft": "Drafts", "草稿": "Drafts", "草稿箱": "Drafts",
    "trash": "Trash", "deleted": "Trash", "已删除": "Trash",
    "junk": "Junk", "spam": "Junk", "垃圾箱": "Junk", "垃圾邮件": "Junk",
}

_LIST_LINE = re.compile(rb'^\((?P<flags>[^)]*)\) "(?P<delim>[^"]*)" (?P<name>.+)$')
_UID_IN_FETCH = re.compile(rb"UID (\d+)")
_FLAGS_IN_FETCH = re.compile(rb"FLAGS \(([^)]*)\)")


# ============================================================
# modified UTF-7 (RFC 3501 §5.1.3)
# ============================================================
#
# IMAP 的文件夹名不是 UTF-8, 是一种改过的 UTF-7: `&` 起头、`-` 收尾, 中间是
# base64 但用 `,` 代替 `/`。实测 chinatelecom.cn:
#     &XfJT0ZAB-        → 已发送
#     &Xn9USmWHTvZZOQ-  → 广告文件夹
# Python 标准库没有这个 codec, 只能自己写。


_B64_ALPHABET = re.compile(r"^[A-Za-z0-9+/]+$")


def _decode_chunk(chunk: str) -> str:
    """一段 modified-base64 → 文本; 解不出返回空串 (调用方回退原文)。

    ⚠ 必须自己校验字母表: `binascii.a2b_base64` 默认**忽略**非法字符而不是报错,
    所以 "&@@@@-" 会被静默解成空串 —— 文件夹名直接消失, 比保留乱码还糟。
    (`strict_mode=True` 是 3.11+ 才有的, 不能依赖。)
    """
    b64 = chunk.replace(",", "/")
    if not _B64_ALPHABET.match(b64):
        return ""
    b64 += "=" * (-len(b64) % 4)
    try:
        data = binascii.a2b_base64(b64)
    except binascii.Error:
        return ""
    if not data or len(data) % 2:  # UTF-16-BE 必须是偶数字节
        return ""
    try:
        return data.decode("utf-16-be")
    except UnicodeDecodeError:
        return ""


def utf7_decode(raw: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(raw):
        if raw[index] != "&":
            out.append(raw[index])
            index += 1
            continue
        end = raw.find("-", index)
        if end == -1:  # 没有收尾符 —— 坏名字, 原样保留
            out.append(raw[index:])
            break
        chunk = raw[index + 1 : end]
        if chunk == "":
            out.append("&")  # `&-` 是转义的字面 &
        else:
            out.append(_decode_chunk(chunk) or raw[index : end + 1])
        index = end + 1
    return "".join(out)


def utf7_encode(text: str) -> str:
    out: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        data = "".join(buffer).encode("utf-16-be")
        b64 = binascii.b2a_base64(data, newline=False).decode("ascii").rstrip("=")
        out.append("&" + b64.replace("/", ",") + "-")
        buffer.clear()

    for char in text:
        if char == "&":
            flush()
            out.append("&-")
        elif 0x20 <= ord(char) <= 0x7E:
            flush()
            out.append(char)
        else:
            buffer.append(char)
    flush()
    return "".join(out)


def folder_role(decoded_name: str) -> str:
    """解码后的文件夹名 → 统一角色; 认不出的原样保留。"""
    return FOLDER_ALIASES.get(decoded_name.casefold(), decoded_name)


def folder_matches(actual: str, requested: str) -> bool:
    if requested == "*":
        return True
    return folder_role(actual).casefold() == folder_role(requested).casefold()


# ============================================================
# 连接配置
# ============================================================


@dataclass(frozen=True)
class ImapConfig:
    host: str
    user: str
    password: str
    port: int = DEFAULT_PORT

    def redacted(self) -> str:
        """能进日志的形态 —— 密码永远不出现。"""
        return f"{self.user}@{self.host}:{self.port}"


def config_from_env() -> ImapConfig | None:
    host = os.environ.get(HOST_ENV, "").strip()
    user = os.environ.get(USER_ENV, "").strip()
    password = os.environ.get(PASSWORD_ENV, "")
    if not (host and user and password):
        return None
    try:
        port = int(os.environ.get(PORT_ENV, "").strip() or DEFAULT_PORT)
    except ValueError:
        port = DEFAULT_PORT
    return ImapConfig(host=host, user=user, password=password, port=port)


# ============================================================
# 适配器
# ============================================================


@dataclass(frozen=True)
class _Remote:
    """一封邮件在服务器上的坐标。UIDVALIDITY 必须带 —— 见模块头的红线。"""

    folder_raw: str
    folder_role: str
    uidvalidity: str
    uid: str
    flags: str
    message: EmailMessage


class ImapAdapter(EmailAdapter):
    """只读 IMAP。写操作一律走基类的 NotSupportedError。"""

    name = "imap"
    supports_drafts = False

    def __init__(self, config: ImapConfig | None = None) -> None:
        self.config = config or config_from_env()
        if self.config is None:
            raise DataNotFoundError(
                "IMAP 没配置。需要 "
                f"{HOST_ENV} / {USER_ENV} / {PASSWORD_ENV} 三个环境变量, "
                "Companion 会从系统凭据库读出来注入。"
            )
        self._conn: imaplib.IMAP4_SSL | None = None

    # ── 连接 ──────────────────────────────────────────────

    def _connect(self) -> imaplib.IMAP4_SSL:
        if self._conn is not None:
            return self._conn
        assert self.config is not None
        try:
            conn = imaplib.IMAP4_SSL(
                self.config.host, self.config.port, timeout=TIMEOUT_SECONDS
            )
        except OSError as error:
            # 只带主机名和错误类型 —— 不带凭据
            raise ClientNotRunningError(
                f"连不上 IMAP 服务器 {self.config.host}:{self.config.port}: "
                f"{type(error).__name__}"
            ) from error
        try:
            conn.login(self.config.user, self.config.password)
        except imaplib.IMAP4.error as error:
            # ⚠ 绝不把 error 原文拼进消息: 某些服务器会把用户名回显在错误里。
            logger.warning("IMAP 登录被拒 %s", self.config.redacted())
            raise ClientNotRunningError(
                f"IMAP 登录被拒 ({self.config.redacted()})。"
                "企业邮箱通常要用「授权码」而不是登录密码。"
            ) from None
        logger.info("IMAP 已连接 %s", self.config.redacted())
        self._conn = conn
        return conn

    def close(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.logout()
        except Exception:  # noqa: BLE001
            pass
        self._conn = None

    # ── 公共接口 ──────────────────────────────────────────

    def list_accounts(self) -> list[Account]:
        assert self.config is not None
        self._connect()  # 连不上就抛, 让上层如实报
        return [Account(name=self.config.user, address=self.config.user, is_default=True)]

    def folders(self) -> list[tuple[str, str]]:
        """``[(服务器原名, 解码后的名字), ...]``。"""
        conn = self._connect()
        typ, data = conn.list()
        if typ != "OK":
            raise EmailAdapterError(f"IMAP LIST 失败: {typ}")
        out: list[tuple[str, str]] = []
        for line in data:
            if not isinstance(line, bytes):
                continue
            match = _LIST_LINE.match(line.strip())
            if match is None:
                logger.debug("IMAP LIST 行解析不了, 跳过: %r", line[:80])
                continue
            raw = match.group("name").decode("ascii", "replace").strip().strip('"')
            out.append((raw, utf7_decode(raw)))
        return out

    def list_messages(self, filt: ListFilter) -> list[Message]:
        remotes: list[_Remote] = []
        for raw, decoded in self.folders():
            role = folder_role(decoded)
            if not folder_matches(role, filt.folder):
                continue
            remotes.extend(self._fetch_folder(raw, role, headers_only=True))
        entries = [r for r in remotes if self._matches(r, filt)]
        entries.sort(key=lambda r: rfc822.date_iso(r.message), reverse=True)
        return [self._to_message(r, full=False) for r in entries[: max(filt.limit, 0)]]

    def read_message(self, message_id: str) -> Message:
        folder_raw, uidvalidity, uid = self._unpack_id(message_id)
        conn = self._connect()
        current = self._select(conn, folder_raw)
        if current != uidvalidity:
            raise DataNotFoundError(
                f"邮箱已重建 (UIDVALIDITY {uidvalidity} → {current}), 这条 id 失效了, "
                "请重新拉取列表"
            )
        typ, data = conn.uid("fetch", uid, "(UID FLAGS BODY.PEEK[])")
        if typ != "OK":
            raise DataNotFoundError(f"取邮件失败: uid={uid}")
        parsed = self._parse_fetch(data, folder_raw, folder_role(utf7_decode(folder_raw)), current)
        if not parsed:
            raise DataNotFoundError(f"邮件不存在或已删除: uid={uid}")
        return self._to_message(parsed[0], full=True)

    def search(
        self, query: str, *, account: str | None = None,
        folder: str = "Inbox", limit: int = 30,
    ) -> list[Message]:
        needle = query.strip()
        if not needle:
            return []
        conn = self._connect()
        found: list[_Remote] = []
        for raw, decoded in self.folders():
            role = folder_role(decoded)
            if not folder_matches(role, folder):
                continue
            uidvalidity = self._select(conn, raw)
            uids = self._search_uids(conn, needle)
            if not uids:
                continue
            found.extend(
                self._fetch_uids(conn, raw, role, uidvalidity, uids[-LIST_FETCH_CAP:], True)
            )
        found.sort(key=lambda r: rfc822.date_iso(r.message), reverse=True)
        return [self._to_message(r, full=False) for r in found[: max(limit, 0)]]

    # ── 内部 ──────────────────────────────────────────────

    def _select(self, conn: imaplib.IMAP4_SSL, folder_raw: str) -> str:
        """只读方式打开文件夹, 返回它的 UIDVALIDITY。"""
        typ, _ = conn.select(f'"{folder_raw}"', readonly=True)
        if typ != "OK":
            raise DataNotFoundError(f"打不开文件夹: {utf7_decode(folder_raw)}")
        typ, data = conn.response("UIDVALIDITY")
        if typ == "OK" and data and data[0]:
            return data[0].decode("ascii", "replace").strip()
        return "0"

    @staticmethod
    def _search_uids(conn: imaplib.IMAP4_SSL, needle: str) -> list[bytes]:
        """服务端搜。服务器不支持 UTF-8 搜索时退回取全部, 由本地过滤兜住。"""
        for args in (
            ("CHARSET", "UTF-8", "TEXT", needle),
            ("TEXT", needle),
        ):
            try:
                typ, data = conn.uid("search", *args)
            except imaplib.IMAP4.error:
                continue
            if typ == "OK" and data and data[0] is not None:
                return data[0].split()
        typ, data = conn.uid("search", None, "ALL")
        return data[0].split() if typ == "OK" and data and data[0] else []

    def _fetch_folder(
        self, folder_raw: str, role: str, *, headers_only: bool
    ) -> list[_Remote]:
        conn = self._connect()
        uidvalidity = self._select(conn, folder_raw)
        typ, data = conn.uid("search", None, "ALL")
        if typ != "OK" or not data or data[0] is None:
            return []
        uids = data[0].split()
        if not uids:
            return []
        # 只要最新的一批 —— UID 递增, 所以取尾部
        return self._fetch_uids(
            conn, folder_raw, role, uidvalidity, uids[-LIST_FETCH_CAP:], headers_only
        )

    def _fetch_uids(
        self, conn: imaplib.IMAP4_SSL, folder_raw: str, role: str,
        uidvalidity: str, uids: list[bytes], headers_only: bool,
    ) -> list[_Remote]:
        if not uids:
            return []
        spec = "(UID FLAGS BODY.PEEK[HEADER])" if headers_only else "(UID FLAGS BODY.PEEK[])"
        typ, data = conn.uid("fetch", b",".join(uids), spec)
        if typ != "OK":
            logger.warning("IMAP FETCH 失败 folder=%s", utf7_decode(folder_raw))
            return []
        return self._parse_fetch(data, folder_raw, role, uidvalidity)

    @staticmethod
    def _parse_fetch(data, folder_raw: str, role: str, uidvalidity: str) -> list[_Remote]:
        """把 imaplib 的 FETCH 响应拆成一封封邮件。

        响应形如 ``[(b'1 (UID 8418 FLAGS (\\Seen) BODY[HEADER] {1234}', b'<raw>'), b')']``。
        UID 在**前缀**里, 不在邮件内容里 —— 拿错了整条 id 就是错的。
        """
        out: list[_Remote] = []
        for item in data or []:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            prefix, raw = item[0], item[1]
            if not isinstance(prefix, bytes) or not isinstance(raw, (bytes, bytearray)):
                continue
            uid_match = _UID_IN_FETCH.search(prefix)
            if uid_match is None:
                logger.debug("FETCH 前缀里没有 UID, 跳过: %r", prefix[:80])
                continue
            flags_match = _FLAGS_IN_FETCH.search(prefix)
            flags = flags_match.group(1).decode("ascii", "replace") if flags_match else ""
            try:
                msg = email.message_from_bytes(bytes(raw), policy=email.policy.default)
            except Exception:  # noqa: BLE001
                logger.warning("邮件解析失败 uid=%s, 跳过", uid_match.group(1).decode())
                continue
            if not isinstance(msg, EmailMessage):
                continue
            out.append(
                _Remote(
                    folder_raw=folder_raw, folder_role=role, uidvalidity=uidvalidity,
                    uid=uid_match.group(1).decode("ascii"), flags=flags, message=msg,
                )
            )
        return out

    def _matches(self, remote: _Remote, filt: ListFilter) -> bool:
        msg = remote.message
        if filt.sender_contains and filt.sender_contains.casefold() not in rfc822.header(
            msg, "From"
        ).casefold():
            return False
        if filt.subject_contains and filt.subject_contains.casefold() not in rfc822.subject(
            msg
        ).casefold():
            return False
        if filt.body_contains and filt.body_contains.casefold() not in rfc822.body_text(
            msg
        ).casefold():
            return False
        if filt.unread_only and "\\Seen" in remote.flags:
            return False
        if filt.has_attachments is not None:
            if rfc822.has_attachments(msg) != filt.has_attachments:
                return False
        date = rfc822.date_iso(msg)
        if filt.since and date and date[:10] < filt.since[:10]:
            return False
        if filt.until and date and date[:10] >= filt.until[:10]:
            return False
        return True

    def _to_message(self, remote: _Remote, *, full: bool) -> Message:
        msg = remote.message
        body = rfc822.body_text(msg)
        attachments = tuple(
            Attachment(filename=name, size_bytes=size, content_type=ctype)
            for name, size, ctype in rfc822.attachment_meta(msg)
        )
        assert self.config is not None
        return Message(
            id=self._pack_id(remote.folder_raw, remote.uidvalidity, remote.uid),
            account=self.config.user,
            folder=remote.folder_role,
            subject=rfc822.subject(msg) or "(无主题)",
            sender=rfc822.format_address(rfc822.header(msg, "From")),
            recipients=tuple(rfc822.addresses(msg, "To")),
            cc=tuple(rfc822.addresses(msg, "Cc")),
            bcc=tuple(rfc822.addresses(msg, "Bcc")),
            date=rfc822.date_iso(msg),
            is_read="\\Seen" in remote.flags,
            has_attachments=bool(attachments) or "X-Has-Attach: yes" in str(msg),
            attachments=attachments if full else (),
            body_text=body if full else body[:200] + ("…" if len(body) > 200 else ""),
            body_html=rfc822.body_html(msg) if full else "",
            in_reply_to=rfc822.header(msg, "In-Reply-To") or None,
            references=rfc822.header(msg, "References") or None,
            message_id=rfc822.header(msg, "Message-ID") or None,
            thread_id=None,
        )

    @staticmethod
    def _pack_id(folder_raw: str, uidvalidity: str, uid: str) -> str:
        return f"imap|{quote(folder_raw, safe='')}|{uidvalidity}|{uid}"

    @staticmethod
    def _unpack_id(message_id: str) -> tuple[str, str, str]:
        parts = message_id.split("|")
        if len(parts) != 4 or parts[0] != "imap":
            raise DataNotFoundError(f"非法的 imap message id: {message_id}")
        return unquote(parts[1]), parts[2], parts[3]
