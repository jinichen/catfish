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
  · 写操作的入口只有三处 `_select(..., writable=True)`, 读路径一律只读打开。
    9/18 下午之前这里写的是「只读: 写操作走基类 NotSupportedError」——
    补齐标已读/删除/草稿/发送之后换成这条更细的。松一条不变量就得
    换一条更细的, 不能直接划掉
  · id 里必须带 UIDVALIDITY —— 服务器重置它时旧 UID 全部失效, 不带的话
    会去拉到完全不相干的邮件
"""
from __future__ import annotations

import email
import email.policy
import imaplib
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid
from pathlib import Path
from typing import Sequence
from urllib.parse import quote, unquote

from .. import rfc822_util as rfc822
# 9/18 拆分: 文件夹名的编解码和角色识别跟 IMAP 协议无关, 单独一个文件
from .imap_folders import (  # noqa: F401  utf7_encode 是给外部用的
    FOLDER_ALIASES,
    folder_matches,
    folder_role,
    utf7_decode,
    utf7_encode,
)
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


def _bare_address(value: str) -> str:
    """``张三 <a@b.cn>`` → ``a@b.cn``。SMTP 信封只认光地址。"""
    parsed = getaddresses([value or ""])
    return parsed[0][1].strip() if parsed else ""


def smtp_send(config: "ImapConfig", raw: bytes, recipients: list[str]) -> None:
    """发信在 smtp_send.py 里 —— 那不是 IMAP, 是另一个协议。

    这里包一层只为把 SmtpError 翻译成 adapter 的异常体系, 让上层不用认识
    两套错误类型。
    """
    from ..smtp_send import SmtpError, send  # noqa: PLC0415

    try:
        send(config, raw, recipients)
    except SmtpError as error:
        raise EmailAdapterError(str(error)) from None

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

_LIST_LINE = re.compile(rb'^\((?P<flags>[^)]*)\) "(?P<delim>[^"]*)" (?P<name>.+)$')
_UID_IN_FETCH = re.compile(rb"UID (\d+)")
_FLAGS_IN_FETCH = re.compile(rb"FLAGS \(([^)]*)\)")


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
    """IMAP 收发。

    9/18 上午写的第一版是纯只读的, 下午补齐了写: 附件 / 标已读 / 删除 /
    存草稿 / 发送。补的理由是达华那些机器上 IMAP 是**唯一**的路 —— 新版
    Outlook 无 COM、Foxmail 加密, 只读意味着员工打开邮件页看得见、什么都
    做不了, 按钮都在, 一点就报错。

    写操作的入口只有三处 `_select(..., writable=True)`, grep 得到完整清单。
    删除格外小心, 见 delete_message 里那段 EXPUNGE 的说明。
    """

    name = "imap"
    supports_drafts = True
    read_only = False

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

    # ── 写操作 ────────────────────────────────────────────
    #
    # 9/18 补齐。在这之前这个 adapter 全部写操作都落到基类的
    # NotSupportedError —— 在 macOS 上无所谓 (Apple Mail 还在), 但达华那些
    # 机器上 IMAP 是唯一的路, 员工打开邮件页会发现: 看得见, 什么都做不了,
    # 按钮都在, 一点就报错。

    def export_attachment(self, message_id: str, filename: str) -> Path:
        """把附件解到本地临时文件。

        这本来就是个**纯读**操作 (FETCH 整封再 walk MIME), 之前落到基类的
        NotSupportedError 纯粹是没写, 算 bug 不算限制。
        """
        msg = self._fetch_raw(message_id)
        for part in msg.walk():
            if part.get_filename() == filename:
                payload = part.get_payload(decode=True) or b""
                out_dir = Path(tempfile.mkdtemp(prefix="catfish-imap-att-"))
                # 用原文件名落盘 —— 系统用默认程序打开时显示的是这个名字
                out = out_dir / filename
                out.write_bytes(payload)
                return out
        raise DataNotFoundError(f"这封邮件里没有名为 {filename!r} 的附件")

    def mark_read(self, message_id: str, *, read: bool = True) -> None:
        folder_raw, uid = self._locate(message_id, writable=True)
        conn = self._connect()
        op = "+FLAGS" if read else "-FLAGS"
        typ, _ = conn.uid("store", uid, op, r"(\Seen)")
        if typ != "OK":
            raise EmailAdapterError(f"标记已读失败: uid={uid} {typ}")
        # 索引里的 fingerprint 就是 flags 串, 下一轮对账自己就对上了, 不用
        # 在这里手工改索引 (手工改就有了第二个真相来源)。

    def delete_message(self, message_id: str) -> None:
        r"""移到「已删除」, 而不是物理清除。

        # 为什么这里格外小心

        真机 CAPABILITY 里**没有 UIDPLUS, 也没有 MOVE** (9/18 实测
        imap.chinatelecom.cn)。于是:

          · 没有 MOVE       → 只能 COPY 到已删除, 再给原件打 \Deleted
          · 没有 UIDPLUS    → 没有 `UID EXPUNGE`, 只有裸 `EXPUNGE`

        **裸 EXPUNGE 会清掉当前文件夹里所有打了 \Deleted 的邮件** —— 包括
        员工在 Foxmail / Outlook 上标了删除、还没执行压缩的那些。员工在鲶鱼
        里删一封, 结果另一个客户端里攒了半年的待删邮件一起没了, 而且不可恢复。
        这个代价换来的只是"邮件从服务器上早几天消失", 完全不值。

        所以: 有 UIDPLUS 就用 `UID EXPUNGE` 只清这一封; 没有就**不 expunge**,
        留着 \Deleted 标记 —— 邮件已经在已删除里了, 而我们自己的列表会把
        \Deleted 的过滤掉, 员工看到的效果就是删掉了。原件最终由服务器或别的
        客户端压缩时清理。
        """
        folder_raw, uid = self._locate(message_id, writable=True)
        conn = self._connect()
        trash = self._folder_by_role("Trash")
        if trash and trash != folder_raw:
            typ, _ = conn.uid("copy", uid, f'"{trash}"')
            if typ != "OK":
                raise EmailAdapterError(f"复制到已删除失败: uid={uid} {typ}")
        else:
            logger.warning("找不到「已删除」文件夹, 只打删除标记不留副本")
        typ, _ = conn.uid("store", uid, "+FLAGS", r"(\Deleted)")
        if typ != "OK":
            raise EmailAdapterError(f"打删除标记失败: uid={uid} {typ}")
        if self._has_capability("UIDPLUS"):
            # 只清这一封 —— 别人标的 \Deleted 一根汗毛都不动
            conn.uid("expunge", uid)
        else:
            logger.info(
                "服务器没有 UIDPLUS, 不执行 EXPUNGE (裸 EXPUNGE 会连带清掉"
                "别处标记的邮件)。邮件已在已删除里, 列表会过滤掉原件。"
            )

    def create_draft(
        self, *, to: Sequence[str], subject: str, body: str,
        cc: Sequence[str] = (), bcc: Sequence[str] = (),
        in_reply_to: str | None = None, account: str | None = None,
    ) -> str:
        """APPEND 一封草稿到草稿箱, 返回它的 id。

        没有 UIDPLUS 就拿不到 APPEND 之后的新 UID (那是 UIDPLUS 的
        APPENDUID 提供的)。所以**我们自己生成 Message-ID**, APPEND 完再用
        `UID SEARCH HEADER Message-ID` 把它找回来。自己生成还有个好处: 发送
        时 Sent 里那份和草稿是同一个 Message-ID, 线程能对上。
        """
        assert self.config is not None
        drafts = self._folder_by_role("Drafts")
        if not drafts:
            raise DataNotFoundError("服务器上找不到草稿箱")
        msg_id = make_msgid(domain=self.config.user.rsplit("@", 1)[-1] or "catfish")
        raw = self._build_rfc822(
            to=to, subject=subject, body=body, cc=cc, bcc=bcc,
            in_reply_to=in_reply_to, message_id=msg_id,
        )
        conn = self._connect()
        typ, _ = conn.append(f'"{drafts}"', r"(\Draft \Seen)", None, raw)
        if typ != "OK":
            raise EmailAdapterError(f"存草稿失败: {typ}")
        uid = self._uid_by_message_id(drafts, msg_id)
        if uid is None:
            raise EmailAdapterError(
                "草稿存进去了, 但找不回它的 UID —— 请去邮箱网页版确认"
            )
        return self._pack_id(drafts, self._select(conn, drafts), uid)

    def send_message(self, message_id: str) -> None:
        """把草稿箱里的一封真发出去。

        ⚠ 发送走的是 **SMTP**, 不是 IMAP —— 另一个协议、另一个端口、另一次
        认证。IMAP 协议本身没有"发信"这回事。

        红线 (抄自基类): **AI 永不自动调这个**, 必须是员工在界面上人工点
        「发送」之后才走到这里。发出去不可撤销, 没有后悔药。

        顺序是: SMTP 发 → APPEND 一份到已发送 → 删掉草稿。
        先发后归档: 归档失败顶多是"已发送里少一封", 反过来则可能重复发送。
        """
        assert self.config is not None
        raw_msg = self._fetch_raw(message_id)
        recipients = [
            addr for name in ("To", "Cc", "Bcc")
            for addr in rfc822.addresses(raw_msg, name)
        ]
        recipients = [_bare_address(a) for a in recipients if _bare_address(a)]
        if not recipients:
            raise EmailAdapterError("这封草稿没有收件人")

        smtp_send(self.config, raw_msg.as_bytes(), recipients)

        conn = self._connect()
        sent = self._folder_by_role("Sent")
        if sent:
            # 服务器不会因为你 SMTP 发了就自动往已发送塞一份, 得自己 APPEND
            typ, _ = conn.append(f'"{sent}"', r"(\Seen)", None, raw_msg.as_bytes())
            if typ != "OK":
                logger.warning("邮件已发出, 但存进已发送失败: %s", typ)
        else:
            logger.warning("找不到「已发送」文件夹, 发出去的邮件没留底")
        try:
            self.delete_message(message_id)
        except EmailAdapterError as error:
            logger.warning("邮件已发出, 但草稿没删掉: %s", error)

    def check_new_mail(self, *, account: str | None = None) -> None:
        """空操作, 而且**不该报错**。

        基类默认是抛 NotSupportedError, 配的文案是"员工需要手动在客户端里
        refresh" —— 那是给 Foxmail 那种"我们读客户端本地库, 客户端不去拉新
        邮件我们就看不到"的情形写的。IMAP 没有这一层: 列表本来就是现问服务器
        的, 没有"催客户端同步"这回事。

        照抄基类的话, 界面上那个「收信」按钮在 IMAP 下会弹一条让员工去客户端
        刷新的提示 —— 可 IMAP 这条路存在的前提就是没有能用的客户端。
        """
        logger.debug("imap: check_new_mail 无需操作 (列表直接来自服务器)")

    # ── 内部 ──────────────────────────────────────────────

    def _select(
        self, conn: imaplib.IMAP4_SSL, folder_raw: str, *, writable: bool = False
    ) -> str:
        """打开文件夹, 返回它的 UIDVALIDITY。

        **默认只读。** 9/18 之前是永远只读, 连参数都没有 —— 那时 adapter
        本来就不支持任何写操作。现在补了标已读 / 删除 / 存草稿, 这条不变量
        不得不松, 但松的方式是**每个调用点自己声明要不要写**, 而不是全局
        改成可写:

            读路径 (list / read / search / 同步) 一律 writable=False
            只有 mark_read / delete / append 这三处显式传 True

        这样"哪几行可能改动员工的邮箱"是能一眼数清的 —— 全仓 grep
        `writable=True` 就是完整清单。
        """
        typ, _ = conn.select(f'"{folder_raw}"', readonly=not writable)
        if typ != "OK":
            raise DataNotFoundError(f"打不开文件夹: {utf7_decode(folder_raw)}")
        typ, data = conn.response("UIDVALIDITY")
        if typ == "OK" and data and data[0]:
            return data[0].decode("ascii", "replace").strip()
        return "0"

    def _locate(self, message_id: str, *, writable: bool = False) -> tuple[str, str]:
        """解开 id, 打开它所在的文件夹, 校验 UIDVALIDITY。返回 (folder_raw, uid)。

        UIDVALIDITY 这一步不能省: 服务器重建邮箱后 UID 会从头发放, 拿着旧 id
        去 STORE 就是**对另一封不相干的邮件动手**。读错了顶多显示错, 写错了
        是删错邮件。
        """
        folder_raw, uidvalidity, uid = self._unpack_id(message_id)
        conn = self._connect()
        current = self._select(conn, folder_raw, writable=writable)
        if current != uidvalidity:
            raise DataNotFoundError(
                f"邮箱已重建 (UIDVALIDITY {uidvalidity} → {current}), 这条 id 失效了, "
                "请重新拉取列表"
            )
        return folder_raw, uid

    def _fetch_raw(self, message_id: str) -> EmailMessage:
        """把整封原始邮件取下来解析好。"""
        folder_raw, uid = self._locate(message_id)
        conn = self._connect()
        typ, data = conn.uid("fetch", uid, "(UID FLAGS BODY.PEEK[])")
        if typ != "OK":
            raise DataNotFoundError(f"取邮件失败: uid={uid}")
        parsed = self._parse_fetch(
            data, folder_raw, folder_role(utf7_decode(folder_raw)), "0"
        )
        if not parsed:
            raise DataNotFoundError(f"邮件不存在或已删除: uid={uid}")
        return parsed[0].message

    def _folder_by_role(self, role: str) -> str | None:
        """按角色找服务器上的文件夹原名 (「已删除」「草稿箱」这些)。

        只能靠**解码后的名字**认 —— 真机上 flags 只有 (\\Marked), 没有
        \\Trash / \\Drafts 这些 special-use 标记 (9/18 实测)。
        """
        for raw, decoded in self.folders():
            if folder_role(decoded) == role:
                return raw
        return None

    def _uid_by_message_id(self, folder_raw: str, msg_id: str) -> str | None:
        """按 Message-ID 在一个文件夹里找 UID。

        APPEND 之后拿新 UID 的唯一办法 (服务器没有 UIDPLUS 的 APPENDUID)。
        """
        conn = self._connect()
        self._select(conn, folder_raw)
        try:
            typ, data = conn.uid("search", None, "HEADER", "Message-ID", msg_id)
        except imaplib.IMAP4.error:
            return None
        if typ != "OK" or not data or not data[0]:
            return None
        uids = data[0].split()
        return uids[-1].decode("ascii") if uids else None

    def _build_rfc822(
        self, *, to: Sequence[str], subject: str, body: str,
        cc: Sequence[str], bcc: Sequence[str],
        in_reply_to: str | None, message_id: str,
    ) -> bytes:
        """拼一封纯文本邮件。

        UTF-8 + base64 传输编码由 EmailMessage 自己处理 —— 中文正文和中文
        主题都不用我们手工编码 (国内企业邮箱那些 =?GB2312?B?= 我们只负责读,
        自己发一律 UTF-8)。
        """
        assert self.config is not None
        msg = EmailMessage()
        msg["From"] = self.config.user
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        if bcc:
            msg["Bcc"] = ", ".join(bcc)
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = message_id
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to
        msg.set_content(body)
        return msg.as_bytes()

    def _has_capability(self, name: str) -> bool:
        conn = self._connect()
        try:
            caps = conn.capabilities
        except Exception:  # noqa: BLE001
            return False
        return name.upper() in {c.upper() for c in caps}

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

    def _fetch_raw_bytes(
        self, conn: imaplib.IMAP4_SSL, uids: list[bytes],
    ) -> dict[str, bytes]:
        """成批取整封邮件的**原始字节**。返回 {uid: raw}。

        # 为什么不能复用 _fetch_uids

        那条路返回的是解析好的 EmailMessage。归档必须存**服务器原样发来的
        字节** —— 把 EmailMessage 再 serialize 一遍得到的不是同一份东西:
        Python 的 email 库会重新折行、规范化头部大小写、按 policy 重编码。

        差别不只是"不好看":

          · archive_sha256 是拿来跟服务器那份核对的。存的是我们重写过的
            版本, 这个哈希就只能证明"我们的序列化是确定的", 证明不了
            档案跟原件一致 —— 而那正是它唯一的用途。
          · 附件的 Content-Transfer-Encoding、边界串、非标准头 (很多企业
            邮件系统会塞自己的 X- 头), 重新序列化之后未必逐字节还原。
          · 档案的承诺是"服务器清了本地还在"。还在的那份如果不是原件,
            承诺就打了折, 而且是悄悄打折。

        # BODY.PEEK[] 不是 BODY[]

        PEEK 不会给邮件打 \Seen。归档是后台行为, **绝不能把员工没读过的
        邮件标成已读** —— 那是直接改员工邮箱的状态, 而且他看不出是谁干的。
        """
        if not uids:
            return {}
        typ, data = conn.uid("fetch", b",".join(uids), "(UID BODY.PEEK[])")
        if typ != "OK":
            logger.warning("归档取原文失败, 这批跳过 (下一轮会重试)")
            return {}
        out: dict[str, bytes] = {}
        for item in data or []:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            prefix, raw = item[0], item[1]
            if not isinstance(prefix, bytes) or not isinstance(raw, (bytes, bytearray)):
                continue
            uid_match = _UID_IN_FETCH.search(prefix)
            if uid_match is None:
                continue
            out[uid_match.group(1).decode("ascii")] = bytes(raw)
        return out

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
        # 打了 \Deleted 的不进列表。
        #
        # 我们删邮件时故意不执行 EXPUNGE (见 delete_message 里那段: 裸
        # EXPUNGE 会连带清掉员工在别的客户端标记待删的邮件), 所以原件会
        # 带着 \Deleted 留在原文件夹里。不过滤的话员工删完一刷新它又回来了。
        if r"\Deleted" in remote.flags:
            return False
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
            # 内嵌图的 cid: 换成 data: —— 只在读整封时做, 列清单不该为了
            # 缩略图去解几百 KB 的 base64 (full=False 时 body_html 本来就是空的)。
            body_html=(
                rfc822.embed_inline_images(rfc822.body_html(msg), msg) if full else ""
            ),
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

