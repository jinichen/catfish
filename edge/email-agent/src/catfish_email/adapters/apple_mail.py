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

import logging
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Sequence

from .base import (
    Account,
    ClientNotRunningError,
    DataNotFoundError,
    EmailAdapter,
    EmailAdapterError,
    ListFilter,
    Message,
)

logger = logging.getLogger("catfish_email.adapters.apple_mail")

# ASCII 控制字符做分隔符 — 邮件正文不可能出现
FS = "\x1f"  # field separator (单元分隔符)
RS = "\x1e"  # record separator (记录分隔符)

_OSASCRIPT_TIMEOUT_SECS = 30.0


# ── AppleScript templates (字符串模板, 用 .replace 注入) ──────────


_AS_PING = """
tell application "System Events"
    return (exists process "Mail")
end tell
"""

_AS_LIST_ACCOUNTS = f"""
tell application "Mail"
    set accs to accounts
    set out to ""
    set defaultName to ""
    try
        set defaultName to name of default account
    end try
    repeat with acc in accs
        set accName to name of acc as string
        set addrs to email addresses of acc
        set addr to ""
        if (count of addrs) > 0 then
            set addr to item 1 of addrs as string
        end if
        set isDefault to "0"
        if accName = defaultName then set isDefault to "1"
        set out to out & accName & "{FS}" & addr & "{FS}" & isDefault & "{RS}"
    end repeat
    return out
end tell
"""

# list_messages: messageId | subject | sender | date | isRead | folder
_AS_LIST_MESSAGES = f"""
tell application "Mail"
    set accName to "{{ACCOUNT}}"
    set folderName to "{{FOLDER}}"
    set limitN to {{LIMIT}}
    set unreadOnly to {{UNREAD_ONLY}}
    set acc to first account whose name of it is accName
    set mb to mailbox folderName of acc
    set msgs to messages of mb
    set out to ""
    set i to 0
    repeat with m in msgs
        if i ≥ limitN then exit repeat
        set skipIt to false
        if unreadOnly and (read status of m) is true then set skipIt to true
        if not skipIt then
            set msgId to id of m as string
            set subj to subject of m as string
            set sndr to sender of m as string
            set dt to (date received of m) as string
            set readSt to "1"
            if (read status of m) is false then set readSt to "0"
            set out to out & msgId & "{FS}" & subj & "{FS}" & sndr & "{FS}" & dt & "{FS}" & readSt & "{FS}" & folderName & "{RS}"
            set i to i + 1
        end if
    end repeat
    return out
end tell
"""

# read_message: 写 body 到 temp 文件 (避 AS string escape 噩梦), 返其余 metadata
_AS_GET_MESSAGE = f"""
tell application "Mail"
    set accName to "{{ACCOUNT}}"
    set targetId to {{MSG_ID}}
    set bodyPath to "{{BODY_PATH}}"

    set acc to first account whose name of it is accName
    set foundMsg to missing value
    repeat with mb in mailboxes of acc
        try
            set m to (first message of mb whose id is targetId)
            set foundMsg to m
            exit repeat
        end try
    end repeat
    if foundMsg is missing value then
        error "MESSAGE_NOT_FOUND" number 8001
    end if

    set bodyText to content of foundMsg
    set fileRef to open for access POSIX file bodyPath with write permission
    set eof fileRef to 0
    write bodyText to fileRef as «class utf8»
    close access fileRef

    set subj to subject of foundMsg
    set sndr to sender of foundMsg
    set dt to (date received of foundMsg) as string
    set toStr to ""
    try
        repeat with r in to recipients of foundMsg
            if toStr = "" then
                set toStr to address of r
            else
                set toStr to toStr & ", " & address of r
            end if
        end repeat
    end try
    set ccStr to ""
    try
        repeat with r in cc recipients of foundMsg
            if ccStr = "" then
                set ccStr to address of r
            else
                set ccStr to ccStr & ", " & address of r
            end if
        end repeat
    end try
    set folderName to name of mailbox of foundMsg

    return subj & "{FS}" & sndr & "{FS}" & dt & "{FS}" & toStr & "{FS}" & ccStr & "{FS}" & folderName
end tell
"""

# search: AS messages whose subject contains q OR sender contains q
_AS_SEARCH = f"""
tell application "Mail"
    set accName to "{{ACCOUNT}}"
    set folderName to "{{FOLDER}}"
    set q to "{{QUERY}}"
    set limitN to {{LIMIT}}
    set acc to first account whose name of it is accName
    set mb to mailbox folderName of acc
    set msgs to (messages of mb whose subject contains q or sender contains q)
    set out to ""
    set i to 0
    repeat with m in msgs
        if i ≥ limitN then exit repeat
        set msgId to id of m as string
        set subj to subject of m as string
        set sndr to sender of m as string
        set dt to (date received of m) as string
        set readSt to "1"
        if (read status of m) is false then set readSt to "0"
        set out to out & msgId & "{FS}" & subj & "{FS}" & sndr & "{FS}" & dt & "{FS}" & readSt & "{FS}" & folderName & "{RS}"
        set i to i + 1
    end repeat
    return out
end tell
"""

# create_draft: body 从 temp 文件读
_AS_CREATE_DRAFT = """
tell application "Mail"
    set accName to "{ACCOUNT}"
    set subj to "{SUBJECT}"
    set bodyPath to "{BODY_PATH}"
    set toList to "{TO}"
    set ccList to "{CC}"

    set fileRef to open for access POSIX file bodyPath
    set bodyText to (read fileRef as «class utf8»)
    close access fileRef

    set newMsg to make new outgoing message with properties {visible:true, subject:subj, content:bodyText}
    tell newMsg
        -- to recipients
        set toItems to my splitText(toList, ",")
        repeat with addr in toItems
            set cleanAddr to my trimText(addr as string)
            if cleanAddr is not "" then
                make new to recipient at end of to recipients with properties {address:cleanAddr}
            end if
        end repeat
        -- cc recipients
        set ccItems to my splitText(ccList, ",")
        repeat with addr in ccItems
            set cleanAddr to my trimText(addr as string)
            if cleanAddr is not "" then
                make new cc recipient at end of cc recipients with properties {address:cleanAddr}
            end if
        end repeat
        -- 不调 send! 留在 Drafts
    end tell

    return id of newMsg as string
end tell

on splitText(s, delim)
    set AppleScript's text item delimiters to delim
    set out to text items of s
    set AppleScript's text item delimiters to ""
    return out
end splitText

on trimText(s)
    set t to s
    repeat while t starts with " "
        set t to text 2 thru -1 of t
    end repeat
    repeat while t ends with " "
        set t to text 1 thru -2 of t
    end repeat
    return t
end trimText
"""


# ── 工具函数 ────────────────────────────────────────────


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


def _parse_applescript_date(s: str) -> str:
    """AS date → ISO-8601 UTC.

    AS date 格式跟 system locale 走. 常见:
      - 英文: 'Friday, May 17, 2026 at 1:30:00 PM'
      - 中文: '2026年5月17日 星期五 下午1:30:00'

    解析不了返原字符串 (老 date 解析失败不阻 list).
    """
    if not s:
        return ""
    # 先 RFC 2822 (Mail 内部很多 header 是这格式)
    try:
        dt = parsedate_to_datetime(s)
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    # 再几个常见 locale format
    for fmt in (
        "%A, %B %d, %Y at %I:%M:%S %p",
        "%A, %d %B %Y at %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return s  # 解析不了原样返


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


# ── Adapter 主类 ────────────────────────────────────────


class AppleMailAdapter(EmailAdapter):
    """Apple Mail.app AppleScript-first adapter."""

    name = "apple_mail"
    supports_drafts = True  # AS 支持 make new outgoing message (需 Automation 权限)

    def __init__(self) -> None:
        # __init__ 不主动 ping — 让 list_accounts 第一次调时再触发, 避免 import 时
        # 就弹 Automation 权限窗.
        pass

    # ── 公共接口 ──

    def list_accounts(self) -> list[Account]:
        if not _is_mail_running():
            raise ClientNotRunningError(
                "Mail.app 没在跑. 先打开 Mail 再调.",
            )
        out = _run_osascript(_AS_LIST_ACCOUNTS)
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
        account_name, msg_id = self._unpack_id(message_id)
        # body 写 tmp 文件: AS 写, Python 读 (避 escape)
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
            body_path = tf.name
        try:
            script = (
                _AS_GET_MESSAGE
                .replace("{ACCOUNT}", _escape_as_string(account_name))
                .replace("{MSG_ID}", msg_id)  # msg_id 是数字, 不引号
                .replace("{BODY_PATH}", body_path)
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
                body_html="",  # Mail.app AS 不直接给 HTML body, P1 再加
            )
        finally:
            try:
                os.unlink(body_path)
            except OSError:
                pass

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
        if bcc:
            # AS bcc 支持复杂, MVP 暂不接 (员工要 bcc 自己开 Mail 加)
            logger.warning(
                "create_draft: bcc 参数 MVP 暂忽略 (%d 个). 员工自己开 Mail 加.",
                len(bcc),
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
            script = (
                _AS_CREATE_DRAFT
                .replace("{ACCOUNT}", _escape_as_string(account_name))
                .replace("{SUBJECT}", _escape_as_string(subject))
                .replace("{BODY_PATH}", body_path)
                .replace("{TO}", _escape_as_string(to_str))
                .replace("{CC}", _escape_as_string(cc_str))
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
        """
        return f"{account_name}|{msg_id}"

    @staticmethod
    def _unpack_id(packed: str) -> tuple[str, str]:
        if "|" not in packed:
            raise ValueError(
                f"Apple Mail message_id 格式错 (应 'account|id'): {packed!r}",
            )
        account, msg_id = packed.split("|", 1)
        return account, msg_id
