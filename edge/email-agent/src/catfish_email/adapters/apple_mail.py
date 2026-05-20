"""Apple Mail (macOS Mail.app) · AppleScript-first adapter (BL-EMAIL-APPLEMAIL 5/17).

# 为啥选 Apple Mail 而不是 Outlook for Mac

Mail.app 是 macOS 系统自带, 跟 Exchange / IMAP / iCloud / Gmail 全兼容. 国内员工
不用买 Microsoft 365 订阅就能用. Outlook for Mac 在国内 enterprise 渗透 <20%,
Mail.app 默认装机 100%.

# 数据访问

走 **AppleScript** 主路径 (`osascript` subprocess), Mail.app AS dictionary 完整
+ Apple 维护. 比 Outlook for Mac AS 历史失修可靠.

EMLX 文件解析 fallback 留 P1 (员工不给 Automation 权限时降级). 当前 MVP 仅 AS.

# 协议: AS stdout 用 ASCII control chars 分隔

- FS (\\x1f) field separator — 邮件正文里不可能出现
- RS (\\x1e) record separator — 同上

Python `split(RS).split(FS)` 解析. Body content (含换行 / tab / 特殊字符) 经
temp 文件传 (AS 写, Python 读), 避免 escape 噩梦.

# 错误映射

- osascript exit !=0 + stderr 含 "MESSAGE_NOT_FOUND" → DataNotFoundError
- 含 "not allowed" / "not authorized" → ClientNotRunningError (Automation 权限缺)
- "Application isn't running" / "-600" → ClientNotRunningError (Mail 没开)
- timeout → EmailAdapterError
- 其他 → EmailAdapterError + stderr 前 200 字

# 红线

create_draft 用 AS `make new outgoing message`, **不直接 send**. 草稿放 Drafts
mailbox, 员工开 Mail.app 点 Send (0.5s 认知 checkpoint, 跟 DESIGN.md 1.3 一致).
"""
from __future__ import annotations

import email
import email.parser
import email.policy
import logging
import os
import plistlib
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Sequence

from .base import (
    Account,
    ClientNotRunningError,
    DataNotFoundError,
    EmailAdapter,
    EmailAdapterError,
    ListFilter,
    Message,
    NotSupportedError,
)

logger = logging.getLogger("catfish_email.adapters.apple_mail")

# ASCII 控制字符做分隔符 — 邮件正文不可能出现
FS = "\x1f"  # field separator (单元分隔符)
RS = "\x1e"  # record separator (记录分隔符)

_OSASCRIPT_TIMEOUT_SECS = 30.0


# ── AppleScript templates (字符串模板, 用 .replace 注入) ──────────



# 5/20 BL-AM-SPLIT: 8 个 _AS_* osascript templates 抽到 apple_mail_scripts.py
from .apple_mail_scripts import (
    _AS_CREATE_DRAFT,
    _AS_DELETE_MESSAGE,
    _AS_GET_MESSAGE,
    _AS_LIST_ACCOUNTS,
    _AS_LIST_MESSAGES,
    _AS_MARK_READ,
    _AS_PING,
    _AS_SEARCH,
    _AS_SEND_MESSAGE,
)


def _run_osascript(
    script: str, *, timeout: float = _OSASCRIPT_TIMEOUT_SECS,
) -> str:
    """跑 AppleScript 返 stdout (去末尾换行). 错误映射到 EmailAdapterError 子类."""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise EmailAdapterError(
            f"Apple Mail AppleScript 超时 ({timeout}s) — "
            f"Mail 卡死或同步太慢? 重启 Mail.app 再试.",
        ) from e
    except FileNotFoundError as e:
        raise EmailAdapterError(
            "找不到 osascript — 不在 macOS 上跑? "
            "Apple Mail adapter 仅支持 macOS.",
        ) from e

    if result.returncode != 0:
        err = (result.stderr or "").strip()
        err_lower = err.lower()
        if "MESSAGE_NOT_FOUND" in err or "8001" in err:
            raise DataNotFoundError(
                "Mail 里找不到这条消息 (id 错 / 邮件已删 / 不在该账号下).",
            )
        if "not allowed" in err_lower or "not authorized" in err_lower or "1743" in err:
            raise ClientNotRunningError(
                "macOS 没给 catfish '控制 Mail' 的权限. 去 "
                "System Settings → Privacy & Security → Automation, "
                "找运行 catfish 的 terminal / catfish-companion, 勾上 Mail. "
                "(macOS 第一次调 osascript 应该已弹过这个窗.)",
            )
        if "Application isn't running" in err or "(-600)" in err or "isn't running" in err_lower:
            raise ClientNotRunningError(
                "Mail.app 没在跑. 先打开 Mail 再调 catfish-email.",
            )
        # 5/18 BL-EMAIL-APPLEMAIL-INVALID-INDEX (-1719): "不能获得 account 1
        # whose name = X 无效的索引". 真因是 id 里塞的 account name 在 Mail.app
        # 找不到 (jini.chen@icloud.com 这种邮箱地址 ≠ Mail 内部账号名 "iCloud").
        # 翻译成友好提示 + 引导走 list_accounts 拿真实名.
        if "-1719" in err or "无效的索引" in err or "Invalid index" in err:
            raise DataNotFoundError(
                "Apple Mail 找不到这个账号 (id 里的 account name 不对). "
                "Mail.app 内部账号名跟邮箱地址可能不同 "
                "(比如 jini.chen@icloud.com 对应的内部名是 'iCloud'). "
                "用 `catfish-email accounts --json` 看真实账号名, "
                "或直接从 `catfish-email list --json` 拷完整 id."
            )
        raise EmailAdapterError(
            f"AppleScript 失败 (exit={result.returncode}): {err[:300]}",
        )
    return result.stdout.rstrip("\n")


def _is_mail_running() -> bool:
    """检查 Mail.app 进程在跑. 不抛 (探测用)."""
    try:
        out = _run_osascript(_AS_PING, timeout=5)
        return out.strip().lower() == "true"
    except EmailAdapterError:
        return False


def _parse_records(text: str, n_fields: int) -> list[list[str]]:
    """osascript stdout split 成 records of fields. 末尾空记录 / 短记录跳过."""
    records: list[list[str]] = []
    for rec in text.split(RS):
        rec = rec.strip("\n").strip()
        if not rec:
            continue
        fields = rec.split(FS)
        if len(fields) < n_fields:
            logger.debug(
                "跳过格式错的记录 (字段 %d < %d): %r",
                len(fields), n_fields, rec[:100],
            )
            continue
        records.append(fields[:n_fields])
    return records


# 5/20 BL-AM-SPLIT: _parse_applescript_date 移到 apple_mail_emlx.py
# (EMLX 跟 AS 路径都用, 放 emlx 避免循环 import)


def _escape_as_string(s: str) -> str:
    """把 Python 字符串 escape 成可安全 inline 到 AS 双引号字面量的形式.

    AS 字符串只需 escape `"` 和 `\\`. 不允许 raw newline (会破 AS 语法),
    替成空格.
    """
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", " ")
    )



# 5/20 BL-AM-SPLIT: EMLX file 处理抽到 apple_mail_emlx.py (240 行)
from .apple_mail_emlx import (  # noqa: F401
    _detect_mail_data_dir,
    _emlx_is_read,
    _extract_html_from_source_file,
    _find_emlx_files,
    _parse_applescript_date,
    _parse_email_from_dir_name,
    _parse_emlx_full,
    _parse_emlx_summary,
    _read_emlx_raw,
    _safe_header,
)

class AppleMailAdapter(EmailAdapter):
    """Apple Mail.app AppleScript-first adapter + EMLX fallback (只读).

    BL-EMAIL-APPLEMAIL-FULL (5/18):
      没拿到 Automation 权限 / Mail 没开 → 自动降级 EMLX 文件解析模式 (只读).
      `supports_drafts` 切 False, create_draft 抛 NotSupportedError.
    """

    name = "apple_mail"
    supports_drafts = True  # AS 支持 make new outgoing message (需 Automation 权限)

    def __init__(self) -> None:
        # __init__ 不主动 ping — 让 list_accounts 第一次调时再触发, 避免 import 时
        # 就弹 Automation 权限窗.
        self._use_emlx_fallback = False
        self._emlx_mail_dir: Path | None = None  # lazy: 第一次 fallback 时探测

    # ── 公共接口 ──

    def list_accounts(self) -> list[Account]:
        if self._use_emlx_fallback:
            return self._list_accounts_emlx()
        if not _is_mail_running():
            # AS 不行 → 试 EMLX
            if self._enable_emlx_fallback_if_available():
                return self._list_accounts_emlx()
            raise ClientNotRunningError(
                "Mail.app 没在跑且没本地 EMLX 缓存. 先打开 Mail 再调.",
            )
        try:
            out = _run_osascript(_AS_LIST_ACCOUNTS)
        except ClientNotRunningError:
            if self._enable_emlx_fallback_if_available():
                logger.info("apple_mail: AS 不可用, 切 EMLX 只读 fallback")
                return self._list_accounts_emlx()
            raise
        records = _parse_records(out, n_fields=3)
        if not records:
            raise DataNotFoundError(
                "Mail.app 里没配过任何邮箱账号. 员工先在 Mail 里加邮箱.",
            )
        return [
            Account(name=r[0], address=r[1], is_default=(r[2] == "1"))
            for r in records
        ]

    def list_messages(self, filt: ListFilter) -> list[Message]:
        if self._use_emlx_fallback:
            return self._list_messages_emlx(filt)
        try:
            return self._list_messages_as(filt)
        except ClientNotRunningError:
            if self._enable_emlx_fallback_if_available():
                return self._list_messages_emlx(filt)
            raise

    def _list_messages_as(self, filt: ListFilter) -> list[Message]:
        account_name = self._resolve_account_name(filt.account)
        script = (
            _AS_LIST_MESSAGES
            .replace("{ACCOUNT}", _escape_as_string(account_name))
            .replace("{FOLDER}", _escape_as_string(filt.folder))
            .replace("{LIMIT}", str(max(1, min(500, filt.limit))))
            .replace("{UNREAD_ONLY}", "true" if filt.unread_only else "false")
        )
        out = _run_osascript(script)
        records = _parse_records(out, n_fields=6)
        # since/until/sender_contains/subject_contains 用 Python 后过滤
        # (AS 里塞复杂 where 太脆 — `messages whose ... and ... and ...` 性能差 + locale 坑多)
        result: list[Message] = []
        for r in records:
            msg_id, subj, sndr, dt_str, read_st, folder = r
            date_iso = _parse_applescript_date(dt_str)
            if filt.since and date_iso and date_iso < filt.since:
                continue
            if filt.until and date_iso and date_iso >= filt.until:
                continue
            if (
                filt.sender_contains
                and filt.sender_contains.lower() not in sndr.lower()
            ):
                continue
            if (
                filt.subject_contains
                and filt.subject_contains.lower() not in subj.lower()
            ):
                continue
            result.append(
                Message(
                    id=self._pack_id(account_name, msg_id),
                    account=account_name,
                    folder=folder,
                    subject=subj,
                    sender=sndr,
                    date=date_iso,
                    is_read=(read_st == "1"),
                    body_text="",  # list 场景不带 body
                ),
            )
        return result

    def read_message(self, message_id: str) -> Message:
        # BL-EMAIL-APPLEMAIL-FULL (5/18): EMLX id 优先走文件解析路径
        if message_id.startswith("emlx:") or "|emlx:" in message_id:
            return self._read_message_emlx(message_id)
        if self._use_emlx_fallback:
            return self._read_message_emlx(message_id)

        try:
            return self._read_message_as(message_id)
        except ClientNotRunningError:
            # AS 路径不可用 → 切 EMLX
            if self._enable_emlx_fallback_if_available():
                return self._read_message_emlx(message_id)
            raise

    def _read_message_as(self, message_id: str) -> Message:
        """AS 主路径: AS 写 body + source RFC822 到 2 个 tmp 文件, Python 解析."""
        account_name, msg_id = self._unpack_id(message_id)
        # 2 个 tmp 文件: body (纯文本) + source (完整 RFC822, 含 HTML part)
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
            body_path = tf.name
        with tempfile.NamedTemporaryFile(suffix=".eml", delete=False) as tf:
            source_path = tf.name
        try:
            script = (
                _AS_GET_MESSAGE
                .replace("{ACCOUNT}", _escape_as_string(account_name))
                # 5/18 BL-EMAIL-APPLEMAIL-READ-ID-STR: msg_id 总作字符串塞 AS,
                # 不假设纯数字 (新版 Mail.app id 可能含 - / UUID 字母). escape 防 `"`.
                .replace("{MSG_ID}", _escape_as_string(msg_id))
                .replace("{BODY_PATH}", body_path)
                .replace("{SOURCE_PATH}", source_path)
            )
            out = _run_osascript(script)
            fields = out.split(FS)
            if len(fields) < 6:
                raise EmailAdapterError(
                    f"read_message: AS 返字段不全 ({len(fields)}/6): {out[:100]}",
                )
            subj, sndr, dt_str, to_str, cc_str, folder = fields[:6]
            body_text = ""
            try:
                with open(body_path, encoding="utf-8") as f:
                    body_text = f.read()
            except OSError as e:
                logger.warning("body tmp 文件读失败: %s", e)
            # BL-EMAIL-APPLEMAIL-FULL (5/18): 从 source RFC822 抽 body_html
            body_html = _extract_html_from_source_file(source_path)
            return Message(
                id=message_id,
                account=account_name,
                folder=folder.strip(),
                subject=subj,
                sender=sndr,
                recipients=tuple(
                    a.strip() for a in to_str.split(",") if a.strip()
                ),
                cc=tuple(a.strip() for a in cc_str.split(",") if a.strip()),
                date=_parse_applescript_date(dt_str),
                body_text=body_text,
                body_html=body_html,
            )
        finally:
            for p in (body_path, source_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    def send_message(self, message_id: str) -> None:
        """5/18 BL-EMAIL-COMPOSE-SEND: AS `send <msg>` 真发草稿.

        红线: caller (Companion compose panel) **必须人工 confirm 才调**,
        adapter 不做"是不是人发的" 校验. EMLX fallback 模式拒.
        """
        if self._use_emlx_fallback:
            from .base import NotSupportedError
            raise NotSupportedError(
                "Apple Mail EMLX fallback 模式不支持 send_message — "
                "Mail.app 必须开着才能发邮件"
            )

        account_name, msg_id = self._unpack_id(message_id)
        script = (
            _AS_SEND_MESSAGE
            .replace("{ACCOUNT}", _escape_as_string(account_name))
            .replace("{MSG_ID}", _escape_as_string(msg_id))
        )
        out = _run_osascript(script)
        if out.strip() != "OK":
            raise EmailAdapterError(
                f"send_message: AS 返非 OK ({out[:120]!r})"
            )

    def delete_message(self, message_id: str) -> None:
        """5/18 BL-EMAIL-DELETE: AS `delete <msg>` = 移到 Trash (软删).

        EMLX fallback 模式不支持 (直接删 emlx 文件 Mail.app 重启会重新生成,
        IMAP server 那边没改, 体验诡异). 显式拒.
        """
        if self._use_emlx_fallback:
            from .base import NotSupportedError
            raise NotSupportedError(
                "Apple Mail EMLX fallback 模式不支持 delete_message — "
                "Mail.app 必须开着才能持久化"
            )

        account_name, msg_id = self._unpack_id(message_id)
        script = (
            _AS_DELETE_MESSAGE
            .replace("{ACCOUNT}", _escape_as_string(account_name))
            .replace("{MSG_ID}", _escape_as_string(msg_id))
        )
        out = _run_osascript(script)
        if out.strip() != "OK":
            raise EmailAdapterError(
                f"delete_message: AS 返非 OK ({out[:120]!r})"
            )

    def mark_read(self, message_id: str, *, read: bool = True) -> None:
        """5/18 BL-EMAIL-MARK-READ: AS `set read status of m to true/false`.

        EMLX fallback 路径不实现 — emlx 文件状态由 Mail.app 维护, 直接改文件
        Mail.app 不刷新会出"看着改了重启又回去"假象. EMLX 模式下报 NotSupportedError.
        """
        if self._use_emlx_fallback:
            # 不啃 emlx 文件状态 (Mail.app 重启会覆盖)
            from .base import NotSupportedError
            raise NotSupportedError(
                "Apple Mail EMLX fallback 模式不支持 mark_read — "
                "Mail.app 必须开着才能持久化 read status"
            )

        account_name, msg_id = self._unpack_id(message_id)
        script = (
            _AS_MARK_READ
            .replace("{ACCOUNT}", _escape_as_string(account_name))
            .replace("{MSG_ID}", _escape_as_string(msg_id))
            # AS boolean 字面量: true / false (小写)
            .replace("{READ_FLAG}", "true" if read else "false")
        )
        out = _run_osascript(script)
        if out.strip() != "OK":
            raise EmailAdapterError(
                f"mark_read: AS 返非 OK ({out[:120]!r})"
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
        if self._use_emlx_fallback:
            return self._search_emlx(query, account=account, folder=folder, limit=limit)
        try:
            return self._search_as(query, account=account, folder=folder, limit=limit)
        except ClientNotRunningError:
            if self._enable_emlx_fallback_if_available():
                return self._search_emlx(
                    query, account=account, folder=folder, limit=limit,
                )
            raise

    def _search_as(
        self,
        query: str,
        *,
        account: str | None,
        folder: str,
        limit: int,
    ) -> list[Message]:
        account_name = self._resolve_account_name(account)
        script = (
            _AS_SEARCH
            .replace("{ACCOUNT}", _escape_as_string(account_name))
            .replace("{FOLDER}", _escape_as_string(folder))
            .replace("{QUERY}", _escape_as_string(query))
            .replace("{LIMIT}", str(max(1, min(500, limit))))
        )
        out = _run_osascript(script)
        records = _parse_records(out, n_fields=6)
        return [
            Message(
                id=self._pack_id(account_name, r[0]),
                account=account_name,
                folder=r[5],
                subject=r[1],
                sender=r[2],
                date=_parse_applescript_date(r[3]),
                is_read=(r[4] == "1"),
                body_text="",
            )
            for r in records
        ]

    def create_draft(
        self,
        *,
        to: Sequence[str],
        subject: str,
        body: str,
        cc: Sequence[str] = (),
        bcc: Sequence[str] = (),
        in_reply_to: str | None = None,
        account: str | None = None,
    ) -> str:
        if not to:
            raise ValueError("create_draft: to 不能空")
        # EMLX fallback 模式只读 → 不支持起草
        if self._use_emlx_fallback:
            raise NotSupportedError(
                "Apple Mail EMLX fallback 模式只读, 不能写草稿. "
                "去 System Settings 给 catfish 'Mail' Automation 权限后重试.",
            )
        account_name = self._resolve_account_name(account)
        # body 写 tmp 文件传给 AS
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8",
        ) as tf:
            tf.write(body)
            body_path = tf.name
        try:
            to_str = ",".join(to)
            cc_str = ",".join(cc)
            bcc_str = ",".join(bcc)  # BL-EMAIL-APPLEMAIL-FULL (5/18): bcc 支持
            script = (
                _AS_CREATE_DRAFT
                .replace("{ACCOUNT}", _escape_as_string(account_name))
                .replace("{SUBJECT}", _escape_as_string(subject))
                .replace("{BODY_PATH}", body_path)
                .replace("{TO}", _escape_as_string(to_str))
                .replace("{CC}", _escape_as_string(cc_str))
                .replace("{BCC}", _escape_as_string(bcc_str))
            )
            out = _run_osascript(script)
            draft_id = out.strip()
            if not draft_id:
                raise EmailAdapterError("create_draft: AS 没返新草稿 id")
            return self._pack_id(account_name, draft_id)
        finally:
            try:
                os.unlink(body_path)
            except OSError:
                pass

    # ── EMLX fallback (只读) ────────────────────────────
    #
    # 触发条件: AS 不可用 (没 Automation 权限 / Mail.app 没开) 且本机有
    # ~/Library/Mail/V*/ 目录 (Mail 曾同步过本地).
    #
    # 数据布局 (macOS 14+):
    #   ~/Library/Mail/V10/
    #     ├ MailData/          ← 元数据 (账号 plist 等)
    #     ├ <UUID>-IMAP@imap.host/   ← 账号目录, name 含 email
    #     │   ├ INBOX.mbox/
    #     │   │   └ <UUID>-Data/Messages/<id>.emlx
    #     │   ├ Drafts.mbox/
    #     │   └ Sent.mbox/
    #     └ ...
    #
    # ID 格式 (EMLX 模式): "<account_name>|emlx:<emlx_file_path>"

    def _enable_emlx_fallback_if_available(self) -> bool:
        """探测本机 EMLX 数据目录. 有 → 切 fallback 返 True; 没 → False.

        副作用: 设 _use_emlx_fallback=True + supports_drafts=False.
        """
        if self._use_emlx_fallback:
            return True
        mail_dir = _detect_mail_data_dir()
        if mail_dir is None:
            return False
        self._emlx_mail_dir = mail_dir
        self._use_emlx_fallback = True
        self.supports_drafts = False  # EMLX 只读, 不能写草稿
        logger.info(
            "apple_mail EMLX fallback 激活: %s (只读, create_draft 会抛 "
            "NotSupportedError)", mail_dir,
        )
        return True

    def _list_accounts_emlx(self) -> list[Account]:
        """扫 V* 目录里子目录, 名字 parse 出 email."""
        mail_dir = self._emlx_mail_dir
        if mail_dir is None or not mail_dir.is_dir():
            raise DataNotFoundError(
                f"EMLX fallback: 找不到 Mail 数据目录 {mail_dir}",
            )
        accounts: list[Account] = []
        for child in sorted(mail_dir.iterdir()):
            if not child.is_dir():
                continue
            # 跳过 MailData / Mailboxes 等系统目录
            if child.name in ("MailData", "Mailboxes"):
                continue
            email_addr = _parse_email_from_dir_name(child.name)
            display_name = email_addr or child.name
            accounts.append(
                Account(
                    name=display_name,
                    address=email_addr or display_name,
                    is_default=(len(accounts) == 0),  # 第一个标 default
                ),
            )
        if not accounts:
            raise DataNotFoundError(
                f"EMLX fallback: {mail_dir} 下没找到账号目录",
            )
        return accounts

    def _account_dir_emlx(self, account_name: str) -> Path:
        """resolve 账号目录. account_name 可能是显示名 / email 地址."""
        mail_dir = self._emlx_mail_dir
        if mail_dir is None:
            raise DataNotFoundError("EMLX mail_dir 未初始化")
        for child in mail_dir.iterdir():
            if not child.is_dir():
                continue
            if child.name in ("MailData", "Mailboxes"):
                continue
            if (
                account_name in child.name
                or _parse_email_from_dir_name(child.name) == account_name
            ):
                return child
        raise DataNotFoundError(
            f"EMLX fallback: 找不到账号 {account_name!r} 的目录",
        )

    def _list_messages_emlx(self, filt: ListFilter) -> list[Message]:
        """扫账号目录下指定 folder (默认 INBOX), 解 .emlx 文件头."""
        accounts = self._list_accounts_emlx()
        if filt.account:
            account = next(
                (a for a in accounts if a.address == filt.account or a.name == filt.account),
                None,
            )
            if account is None:
                raise DataNotFoundError(
                    f"EMLX fallback: 账号 {filt.account!r} 不在",
                )
        else:
            account = next((a for a in accounts if a.is_default), accounts[0])
        account_dir = self._account_dir_emlx(account.name)
        emlx_files = _find_emlx_files(account_dir, folder=filt.folder)
        # 按修改时间倒序 (最近的在前), 然后 limit
        emlx_files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        result: list[Message] = []
        for emlx_path in emlx_files:
            if len(result) >= filt.limit:
                break
            try:
                msg = _parse_emlx_summary(emlx_path, account.name, filt.folder)
            except Exception as e:  # noqa: BLE001
                logger.debug("跳过损坏的 emlx %s: %s", emlx_path, e)
                continue
            # Python 端 filter (跟 AS 路径同套规则)
            if filt.since and msg.date and msg.date < filt.since:
                continue
            if filt.until and msg.date and msg.date >= filt.until:
                continue
            if (
                filt.sender_contains
                and filt.sender_contains.lower() not in msg.sender.lower()
            ):
                continue
            if (
                filt.subject_contains
                and filt.subject_contains.lower() not in msg.subject.lower()
            ):
                continue
            if filt.unread_only and msg.is_read:
                continue
            result.append(msg)
        return result

    def _read_message_emlx(self, message_id: str) -> Message:
        """ID 格式 'account|emlx:/path/to/file.emlx', 全文解析."""
        if "|emlx:" not in message_id:
            raise ValueError(
                f"_read_message_emlx: id 格式错 (应 'account|emlx:path'): {message_id!r}",
            )
        account_name, rest = message_id.split("|", 1)
        emlx_path = Path(rest[len("emlx:"):])
        if not emlx_path.exists():
            raise DataNotFoundError(f"EMLX 文件不在: {emlx_path}")
        return _parse_emlx_full(emlx_path, account_name)

    def _search_emlx(
        self,
        query: str,
        *,
        account: str | None,
        folder: str,
        limit: int,
    ) -> list[Message]:
        """全扫所有 emlx, subject/sender contains. 慢但够 fallback 用."""
        accounts = self._list_accounts_emlx()
        if account:
            target = next(
                (a for a in accounts if a.address == account or a.name == account),
                None,
            )
            if target is None:
                return []
            account_dirs = [self._account_dir_emlx(target.name)]
            account_names = [target.name]
        else:
            account_dirs = [self._account_dir_emlx(a.name) for a in accounts]
            account_names = [a.name for a in accounts]
        q_lower = query.lower()
        result: list[Message] = []
        for acc_name, acc_dir in zip(account_names, account_dirs):
            for emlx_path in _find_emlx_files(acc_dir, folder=folder):
                if len(result) >= limit:
                    return result
                try:
                    msg = _parse_emlx_summary(emlx_path, acc_name, folder)
                except Exception:
                    continue
                if (
                    q_lower in msg.subject.lower()
                    or q_lower in msg.sender.lower()
                ):
                    result.append(msg)
        return result

    # ── 内部 helpers ──

    def _resolve_account_name(self, account: str | None) -> str:
        """把 account 参数 (None / email 地址 / 显示名) 解析成 Mail 里的 'name'.

        Mail.app AS 用 `account whose name of it is X` — 必须用显示名. 我们
        DESIGN.md 约定外部用 email 地址, 这里映射回显示名.
        """
        accounts = self.list_accounts()
        if account is None:
            default = next((a for a in accounts if a.is_default), accounts[0])
            return default.name
        # 先按地址匹配
        for a in accounts:
            if a.address == account:
                return a.name
        # 再按显示名匹配
        for a in accounts:
            if a.name == account:
                return a.name
        raise DataNotFoundError(
            f"Mail.app 里找不到账号 {account!r}. 现有: "
            f"{', '.join(f'{a.address} ({a.name})' for a in accounts)}",
        )

    @staticmethod
    def _pack_id(account_name: str, msg_id: str) -> str:
        """打包 Mail 里的 numeric msg id + account name 成稳定字符串 id.

        分隔用 `|`. account_name 不允许含 `|` (Mail 限制 + 显示名常理).
        5/18 BL-EMAIL-READ-ROUTING-BY-PREFIX: 加 'apple_mail|' 前缀, 跟 Foxmail
        ('foxmail-mac|...') 同 3 段格式, _cmd_read 看前缀路由不走错 adapter.
        """
        return f"apple_mail|{account_name}|{msg_id}"

    @staticmethod
    def _unpack_id(packed: str) -> tuple[str, str]:
        """5/18 BL-EMAIL-READ-ROUTING-BY-PREFIX: 兼容两种格式
            - 新 (3 段): 'apple_mail|account|msg_id' (5/18 起 _pack_id 用这个)
            - 老 (2 段): 'account|msg_id' (历史 list 输出的 id, 仍能 unpack)
        """
        if "|" not in packed:
            raise ValueError(
                f"Apple Mail message_id 格式错 (应 'apple_mail|account|id' 或 'account|id'): {packed!r}",
            )
        parts = packed.split("|", 2)
        if len(parts) == 3 and parts[0] == "apple_mail":
            return parts[1], parts[2]
        # 老 2 段格式: account|msg_id
        return parts[0], "|".join(parts[1:])
