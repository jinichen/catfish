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


_AS_PING = """
tell application "System Events"
    return (exists process "Mail")
end tell
"""

# BL-EMAIL-APPLEMAIL-AS-CTRLCHAR (5/18):
#   - 老 f-string interpolate FS="\x1f"/RS="\x1e" 进 AS string literal → osascript -2741.
#   - AS 里用 `character id 31` (modern, Mac 10.5+ ASCII character 替代品) 重建分隔符.
#   - AS 注释里 *不* 写中文 — osascript 解析中文 comment 时 line/col 计算错位, 错误
#     位置难定位; 中文说明全挪到 Python 这边.
#   - `set accs to every account` 比 `set accs to accounts` 更明确, 部分 macOS 版本
#     `accounts` 单独出现会被解析成 class name (-2741 在 col 145 / 497 都踩这).
_AS_LIST_ACCOUNTS = """
tell application "Mail"
    set FS to (character id 31)
    set RS to (character id 30)
    set out to ""
    repeat with acc in every account
        set accName to (name of acc) as string
        set addrList to (email addresses of acc)
        set addr to ""
        if (count of addrList) > 0 then
            set addr to (item 1 of addrList) as string
        end if
        set out to out & accName & FS & addr & FS & "0" & RS
    end repeat
    return out
end tell
"""

# list_messages: messageId | subject | sender | date | isRead | folder
# BL-EMAIL-APPLEMAIL-INBOX-NAMES (5/18 鸿波实盘):
#   Mail.app 在不同 IMAP provider 下 inbox 物理名不一样:
#     - iCloud:   "INBOX" / "Inbox"
#     - Gmail:    "INBOX" / "[Gmail]/All Mail" / 本地化"收件箱"
#     - Exchange: "Inbox" / 本地化"收件箱"
#   老硬编码 `mailbox "Inbox" of acc` 在 Gmail 5 个账号挂 "不能获得 mailbox Inbox of account id...".
#   改成: 当 folderName="Inbox" 时, AS 端按候选列表逐个 try 找第一个能拿到的;
#   非 "Inbox" 时按字面名 (员工自己指定 subfolder 不该兜底).
_AS_LIST_MESSAGES = """
tell application "Mail"
    set FS to (character id 31)
    set RS to (character id 30)
    set accName to "{ACCOUNT}"
    set folderName to "{FOLDER}"
    set limitN to {LIMIT}
    set unreadOnly to {UNREAD_ONLY}
    set acc to first account whose name of it is accName
    set mb to my resolveInbox(acc, folderName)
    set msgs to (messages of mb)
    set out to ""
    set i to 0
    repeat with m in msgs
        if i >= limitN then exit repeat
        set skipIt to false
        if unreadOnly and (read status of m) is true then set skipIt to true
        if not skipIt then
            set msgId to (id of m) as string
            set subj to (subject of m) as string
            set sndr to (sender of m) as string
            set dt to my isoDate(date received of m)
            set readSt to "1"
            if (read status of m) is false then set readSt to "0"
            set out to out & msgId & FS & subj & FS & sndr & FS & dt & FS & readSt & FS & folderName & RS
            set i to i + 1
        end if
    end repeat
    return out
end tell

-- BL-EMAIL-APPLEMAIL-INBOX-NAMES (5/18): resolve canonical inbox across providers.
-- iCloud/Gmail/Exchange/Outlook all name their inbox differently; try the common
-- candidates one by one, fall back to literal name if not "Inbox".
on resolveInbox(acc, wantName)
    if wantName is "Inbox" then
        set candidates to {"INBOX", "Inbox", "收件箱", "受信箱"}
        repeat with cand in candidates
            tell application "Mail"
                try
                    return mailbox (cand as string) of acc
                end try
            end tell
        end repeat
    end if
    tell application "Mail"
        return mailbox wantName of acc
    end tell
end resolveInbox

-- BL-EMAIL-DATE-ISO (5/18): coerce AS date to ISO-8601 ourselves.
-- `(date received of m) as string` is locale-dependent (zh-CN gives '2026年...' which
-- Python's strptime can't parse without explicit locale). We assemble year-mo-dyTh:mn:sc
-- manually, in local TZ (no offset suffix). Python side just parses as naive ISO.
on isoDate(d)
    set yr to year of d as integer
    set mo to month of d as integer
    set dy to day of d as integer
    set hr to hours of d as integer
    set mn to minutes of d as integer
    set sc to seconds of d as integer
    return _pad4(yr) & "-" & _pad2(mo) & "-" & _pad2(dy) & "T" & _pad2(hr) & ":" & _pad2(mn) & ":" & _pad2(sc)
end isoDate

on _pad2(n)
    set s to n as string
    if (count of s) < 2 then set s to "0" & s
    return s
end _pad2

on _pad4(n)
    set s to n as string
    repeat while (count of s) < 4
        set s to "0" & s
    end repeat
    return s
end _pad4
"""

# read_message: AS 写 body (text) + source (完整 RFC822) 到 2 个 temp 文件,
# Python 解析 source 提 HTML part. BL-EMAIL-APPLEMAIL-FULL (5/18).
_AS_GET_MESSAGE = """
tell application "Mail"
    set FS to (character id 31)
    set accName to "{ACCOUNT}"
    set targetId to {MSG_ID}
    set bodyPath to "{BODY_PATH}"
    set sourcePath to "{SOURCE_PATH}"

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

    -- Write full RFC822 source (with HTML part); Python parses body_html via email.parser
    try
        set rawSource to source of foundMsg
        set srcRef to open for access POSIX file sourcePath with write permission
        set eof srcRef to 0
        write rawSource to srcRef as «class utf8»
        close access srcRef
    on error
        -- source unavailable (old Mail version / network fetch fail) - leave HTML blank
    end try

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

    return subj & FS & sndr & FS & dt & FS & toStr & FS & ccStr & FS & folderName
end tell
"""

# search: AS messages whose subject contains q OR sender contains q
# BL-EMAIL-APPLEMAIL-INBOX-NAMES (5/18): 同样走 resolveInbox 兜底 cross-account inbox 名.
_AS_SEARCH = """
tell application "Mail"
    set FS to (character id 31)
    set RS to (character id 30)
    set accName to "{ACCOUNT}"
    set folderName to "{FOLDER}"
    set q to "{QUERY}"
    set limitN to {LIMIT}
    set acc to first account whose name of it is accName
    set mb to my resolveInbox(acc, folderName)
    set msgs to (messages of mb whose subject contains q or sender contains q)
    set out to ""
    set i to 0
    repeat with m in msgs
        if i >= limitN then exit repeat
        set msgId to (id of m) as string
        set subj to (subject of m) as string
        set sndr to (sender of m) as string
        set dt to my isoDate(date received of m)
        set readSt to "1"
        if (read status of m) is false then set readSt to "0"
        set out to out & msgId & FS & subj & FS & sndr & FS & dt & FS & readSt & FS & folderName & RS
        set i to i + 1
    end repeat
    return out
end tell

-- BL-EMAIL-APPLEMAIL-INBOX-NAMES (5/18): same handler as _AS_LIST_MESSAGES; AS doesn't
-- share handlers across osascript invocations so we repeat it.
on resolveInbox(acc, wantName)
    if wantName is "Inbox" then
        set candidates to {"INBOX", "Inbox", "收件箱", "受信箱"}
        repeat with cand in candidates
            tell application "Mail"
                try
                    return mailbox (cand as string) of acc
                end try
            end tell
        end repeat
    end if
    tell application "Mail"
        return mailbox wantName of acc
    end tell
end resolveInbox

-- BL-EMAIL-DATE-ISO (5/18): same as _AS_LIST_MESSAGES, repeat handlers.
on isoDate(d)
    set yr to year of d as integer
    set mo to month of d as integer
    set dy to day of d as integer
    set hr to hours of d as integer
    set mn to minutes of d as integer
    set sc to seconds of d as integer
    return _pad4(yr) & "-" & _pad2(mo) & "-" & _pad2(dy) & "T" & _pad2(hr) & ":" & _pad2(mn) & ":" & _pad2(sc)
end isoDate

on _pad2(n)
    set s to n as string
    if (count of s) < 2 then set s to "0" & s
    return s
end _pad2

on _pad4(n)
    set s to n as string
    repeat while (count of s) < 4
        set s to "0" & s
    end repeat
    return s
end _pad4
"""

# create_draft: body 从 temp 文件读; 支持 to/cc/bcc
_AS_CREATE_DRAFT = """
tell application "Mail"
    set accName to "{ACCOUNT}"
    set subj to "{SUBJECT}"
    set bodyPath to "{BODY_PATH}"
    set toList to "{TO}"
    set ccList to "{CC}"
    set bccList to "{BCC}"

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
        -- bcc recipients (BL-EMAIL-APPLEMAIL-FULL 5/18)
        set bccItems to my splitText(bccList, ",")
        repeat with addr in bccItems
            set cleanAddr to my trimText(addr as string)
            if cleanAddr is not "" then
                make new bcc recipient at end of bcc recipients with properties {address:cleanAddr}
            end if
        end repeat
        -- DO NOT call send; stays in Drafts (red line)
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

    5/18 BL-EMAIL-DATE-ISO: AS 端用 isoDate() handler 直接 format 成
    'YYYY-MM-DDTHH:MM:SS' (本地时区, 无 offset), Python 这里解 ISO 后假设
    本地时区, 转 UTC. 老 locale 字符串路径 fallback 保留 (防 AS 端某天回退).

    Args:
        s: AS 输出的日期串. 通常 'YYYY-MM-DDTHH:MM:SS' (5/18 起新格式),
           老格式 '2026年5月17日 星期五 下午1:30:00' / 'Friday, May 17, 2026 at 1:30:00 PM' 也尝试.

    Returns:
        ISO-8601 UTC '2026-05-17T13:30:00+00:00', 解析不了原样返.
    """
    if not s:
        return ""
    # 5/18 新路径: AS isoDate() 输出 'YYYY-MM-DDTHH:MM:SS' (naive, 本地时区)
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            # 假设本地时区, 转 UTC. astimezone(None) 拿系统时区.
            dt = dt.astimezone()
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass
    # RFC 2822 fallback (Mail header 偶尔出这格式)
    try:
        dt = parsedate_to_datetime(s)
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    # locale 字符串 fallback (老 adapter 路径或本地化变种)
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


def _extract_html_from_source_file(source_path: str) -> str:
    """从 AS 写的 RFC822 源码文件抽出 text/html 部分.

    BL-EMAIL-APPLEMAIL-FULL (5/18). source 拿不到 (老版本 / 网络 fetch 失败 /
    AS 没写) 时返空字符串.
    """
    try:
        with open(source_path, "rb") as f:
            raw = f.read()
        if not raw:
            return ""
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        # 遍历 multipart, 找 text/html
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    try:
                        return part.get_content()
                    except Exception:  # noqa: BLE001
                        # 编码解析失败, 退到原 bytes decode
                        payload = part.get_payload(decode=True) or b""
                        return payload.decode("utf-8", errors="replace")
            return ""
        # 非 multipart: 看本身是不是 html
        if msg.get_content_type() == "text/html":
            return msg.get_content()
        return ""
    except (OSError, ValueError) as e:
        logger.debug("_extract_html_from_source_file 解析失败 %s: %s", source_path, e)
        return ""


# ── EMLX (Apple Mail 本地缓存文件格式) 解析 helpers ──────


# Mail.app 账号目录名格式. 实测样本:
#   "IMAP-hongbo@example.com@imap.example.com"
#   "iCloud-hongbo@iCloud"
#   "Exchange-hongbo@company.com"
# 提取第一个 @ 后到 @host 前的 email
_EMLX_ACCOUNT_DIR_RE = re.compile(r"^[A-Za-z]+-([^@]+@[^@]+?)(?:@[^@]+)?$")


def _parse_email_from_dir_name(name: str) -> str | None:
    """从 Mail.app 账号目录名提 email. 提不到返 None."""
    m = _EMLX_ACCOUNT_DIR_RE.match(name)
    if m:
        return m.group(1)
    # fallback: 找名字里第一个 @ 子串
    parts = name.split("@")
    if len(parts) >= 2 and "." in parts[1]:
        return f"{parts[0].split('-')[-1]}@{parts[1].split('-')[0]}"
    return None


# Folder 名 → 可能的 .mbox 子目录名候选 (Mail.app 国际化)
_FOLDER_ALIASES: dict[str, list[str]] = {
    "Inbox": ["INBOX.mbox", "Inbox.mbox", "收件箱.mbox"],
    "INBOX": ["INBOX.mbox", "Inbox.mbox", "收件箱.mbox"],
    "Drafts": ["Drafts.mbox", "草稿.mbox", "Drafts (This computer).mbox"],
    "Sent": ["Sent.mbox", "Sent Messages.mbox", "已发送.mbox"],
}


def _find_emlx_files(account_dir: Path, folder: str) -> list[Path]:
    """找账号目录下指定 folder 的所有 .emlx 文件路径."""
    folder_candidates = _FOLDER_ALIASES.get(folder, [f"{folder}.mbox", folder])
    for cand in folder_candidates:
        mbox_dir = account_dir / cand
        if mbox_dir.is_dir():
            # mbox 下面是 <UUID>-Data/Messages/*.emlx
            return list(mbox_dir.rglob("*.emlx"))
    return []


def _parse_emlx_summary(emlx_path: Path, account_name: str, folder: str) -> Message:
    """轻量解析 .emlx (只读 header), 给 list/search 用.

    EMLX 格式: 第一行 = byte count, 然后 RFC822 邮件, 末尾 plist trailer.
    我们只解析 RFC822 头部, plist 用来读 is_read flag.
    """
    raw, plist = _read_emlx_raw(emlx_path)
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    subject = _safe_header(msg, "Subject")
    sender = _safe_header(msg, "From")
    date_header = _safe_header(msg, "Date")
    date_iso = _parse_applescript_date(date_header)  # RFC2822 同样能解
    is_read = _emlx_is_read(plist)
    # ID 用 emlx 路径 (绝对) — Python 端能直接打开
    msg_id = f"emlx:{emlx_path}"
    return Message(
        id=f"{account_name}|{msg_id}",
        account=account_name,
        folder=folder,
        subject=subject,
        sender=sender,
        date=date_iso,
        is_read=is_read,
        body_text="",
    )


def _parse_emlx_full(emlx_path: Path, account_name: str) -> Message:
    """完整解析 .emlx, 给 read_message 用 — body_text + body_html + 附件元数据."""
    raw, plist = _read_emlx_raw(emlx_path)
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    subject = _safe_header(msg, "Subject")
    sender = _safe_header(msg, "From")
    date_iso = _parse_applescript_date(_safe_header(msg, "Date"))
    to_str = _safe_header(msg, "To")
    cc_str = _safe_header(msg, "Cc")
    body_text = ""
    body_html = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain" and not body_text:
                try:
                    body_text = part.get_content()
                except Exception:  # noqa: BLE001
                    payload = part.get_payload(decode=True) or b""
                    body_text = payload.decode("utf-8", errors="replace")
            elif ct == "text/html" and not body_html:
                try:
                    body_html = part.get_content()
                except Exception:  # noqa: BLE001
                    payload = part.get_payload(decode=True) or b""
                    body_html = payload.decode("utf-8", errors="replace")
    else:
        ct = msg.get_content_type()
        try:
            content = msg.get_content()
        except Exception:  # noqa: BLE001
            payload = msg.get_payload(decode=True) or b""
            content = payload.decode("utf-8", errors="replace")
        if ct == "text/html":
            body_html = content
        else:
            body_text = content
    return Message(
        id=f"{account_name}|emlx:{emlx_path}",
        account=account_name,
        folder="Inbox",  # emlx 路径里有 .mbox 名, 但简化
        subject=subject,
        sender=sender,
        recipients=tuple(
            a.strip() for a in to_str.split(",") if a.strip()
        ),
        cc=tuple(a.strip() for a in cc_str.split(",") if a.strip()),
        date=date_iso,
        is_read=_emlx_is_read(plist),
        body_text=body_text,
        body_html=body_html,
    )


def _read_emlx_raw(emlx_path: Path) -> tuple[bytes, dict | None]:
    """读 .emlx 返 (RFC822 bytes, trailer plist dict).

    EMLX 格式:
      <byte_count>\\n           ← ASCII 数字 + newline
      <RFC822 message bytes>    ← 长度 = byte_count
      <plist xml trailer>       ← 可选, Mail 元数据 (flags / labels 等)
    """
    raw = emlx_path.read_bytes()
    # 第一行 byte count
    nl_idx = raw.find(b"\n")
    if nl_idx == -1:
        return raw, None
    try:
        byte_count = int(raw[:nl_idx].decode("ascii").strip())
    except ValueError:
        return raw, None
    rfc822 = raw[nl_idx + 1: nl_idx + 1 + byte_count]
    trailer = raw[nl_idx + 1 + byte_count:].strip()
    plist_dict: dict | None = None
    if trailer:
        try:
            plist_dict = plistlib.loads(trailer)
        except Exception:  # noqa: BLE001
            plist_dict = None
    return rfc822, plist_dict


def _emlx_is_read(plist: dict | None) -> bool:
    """从 emlx trailer plist 读 read flag. 'flags' 是 64-bit int, bit 0 = read.

    详见 Apple Mail 内部文档 (反向工程): flag bit layout:
      bit 0: read
      bit 1: deleted
      bit 2: answered
      bit 3: encrypted
      bit 4: flagged
      ...
    解不出返 False (保守: 当作未读).
    """
    if not plist:
        return False
    flags = plist.get("flags")
    if not isinstance(flags, int):
        return False
    return bool(flags & 1)


def _safe_header(msg: email.message.Message, name: str) -> str:
    """读 RFC822 header, 解码后返字符串. 缺/烂 → 空字符串."""
    val = msg.get(name)
    if val is None:
        return ""
    if isinstance(val, str):
        return val.strip()
    try:
        return str(val).strip()
    except Exception:  # noqa: BLE001
        return ""


def _detect_mail_data_dir() -> Path | None:
    """探测 Apple Mail 本地缓存目录, 优先取最新版本号.

    macOS 14+ (Sonoma+): ~/Library/Mail/V10
    macOS 12-13:        ~/Library/Mail/V9
    更早:               V2-V8 (旧版本 emlx 仍能读)

    返优先级最高现存的, 都没有则 None.
    """
    base = Path.home() / "Library" / "Mail"
    if not base.is_dir():
        return None
    candidates = sorted(
        (p for p in base.iterdir() if p.name.startswith("V") and p.is_dir()),
        key=lambda p: -int(p.name[1:]) if p.name[1:].isdigit() else 0,
    )
    return candidates[0] if candidates else None


# ── Adapter 主类 ────────────────────────────────────────


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
                .replace("{MSG_ID}", msg_id)  # msg_id 是数字, 不引号
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
