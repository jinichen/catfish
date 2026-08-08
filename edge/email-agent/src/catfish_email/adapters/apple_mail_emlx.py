"""Apple Mail EMLX file 处理 — 抽自 apple_mail.py (5/20 拆分).

EMLX 是 Apple Mail.app 在本机磁盘的存储格式 (per-message .emlx file + 一个
trailing plist 标记). 当 AppleScript 调用被拒 / Mail.app 没装时, EMLX 模式
直接读 ~/Library/Mail/V*/<account>/INBOX.mbox/Messages/*.emlx 兜底.

不依赖 AppleScript / osascript. 纯 Python 文件 IO + email package + plistlib.
"""
from __future__ import annotations

import base64
import email
import email.message
import email.policy
import logging
import plistlib
import quopri
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

from .base import Attachment, Message

logger = logging.getLogger(__name__)


def _parse_applescript_date(s: str) -> str:
    """AS date → ISO-8601 UTC. 5/18 BL-EMAIL-DATE-ISO.

    抽到 apple_mail_emlx.py 是因为 EMLX header 解析需要同一格式 ('YYYY-MM-DDTHH:MM:SS'
    naive 本地时区). apple_mail.py 也用, 走 import 回拿.
    """
    if not s:
        return ""
    # 5/18 新路径: AS isoDate() 输出 'YYYY-MM-DDTHH:MM:SS' (naive, 本地时区)
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.astimezone()  # 本地时区
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(s)
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
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
    return s

def _walk_attachments(msg) -> list[Attachment]:
    """从 parse 完的 RFC822 邮件 MIME 树 walk 出 Content-Disposition: attachment 的 part.

    P3.5.100 (6/24 鸿波 catch '附件看不到') — Apple Mail 全家 adapter 之前
    构造 Message(...) 时没传 attachments, 永远空, 前端 DetailPane.tsx:560
    渲染条件 has_attachments && attachments.length > 0 永远 False, UI 永远
    不显附件 row. 真因不在前端, 在 backend adapter.

    本 helper 收集真附件元数据 (filename / size_bytes / content_type):
      - 只收 Content-Disposition 以 'attachment' 开头的 part
      - inline 图片 (e.g. signature / 邮件正文渲染的图) 不算附件, 跳过
      - filename RFC2231 自动解 (Python email.utils 内置), 中文名能解
      - size_bytes 取 decoded payload 字节数, 不是 raw base64 长度
    """
    out: list[Attachment] = []
    if not msg.is_multipart():
        return out
    for part in msg.walk():
        disposition = (part.get("Content-Disposition") or "").lower().strip()
        if not disposition.startswith("attachment"):
            continue
        filename = part.get_filename() or "untitled"
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:  # noqa: BLE001
            payload = b""
        size_bytes = len(payload)
        content_type = part.get_content_type() or "application/octet-stream"
        out.append(Attachment(
            filename=filename,
            size_bytes=size_bytes,
            content_type=content_type,
        ))
    return out


def _save_attachment_payload_from_source(
    source_path: str, target_filename: str,
) -> Path | None:
    """从 RFC822 source 文件 walk MIME 找匹配 filename 的 attachment part,
    decode payload 写本地 tmp 文件, 返新 Path. 找不到返 None.

    P3.5.103 (6/24 鸿波 catch '附件不能点'): 给 export_attachment 用.

    设计:
    - tmp 用 mkdtemp + 原 filename — 系统 open 时显示原中文名
      (单文件 mkdtemp 用 prefix 防 collision)
    - 不修原 filename (用户期待看到原名)
    - 失败返 None, 上层 raise DataNotFoundError
    """
    import tempfile

    try:
        with open(source_path, "rb") as f:
            raw = f.read()
        msg = email.message_from_bytes(raw, policy=email.policy.default)
    except (OSError, ValueError) as e:
        logger.debug("export source 读失败 %s: %s", source_path, e)
        return None
    if not msg.is_multipart():
        return None
    for part in msg.walk():
        disposition = (part.get("Content-Disposition") or "").lower().strip()
        if not disposition.startswith("attachment"):
            continue
        part_filename = part.get_filename() or ""
        if part_filename != target_filename:
            continue
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:  # noqa: BLE001
            return None
        if not payload:
            return None
        tmpdir = Path(tempfile.mkdtemp(prefix="catfish-email-att-"))
        out_path = tmpdir / target_filename
        try:
            out_path.write_bytes(payload)
            return out_path
        except OSError as e:
            logger.warning("写 attachment payload 失败 %s: %s", out_path, e)
            return None
    return None


def _extract_attachments_from_source_file(source_path: str) -> list[Attachment]:
    """从 AS dump 出的 RFC822 source 文件抽出附件元.

    P3.5.100 (6/24). 跟 _extract_html_from_source_file 同 pattern.
    source 拿不到 (老版 / 网络 fetch 失败 / AS 没写) 时返空 list.
    """
    try:
        with open(source_path, "rb") as f:
            raw = f.read()
        if not raw:
            return []
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        return _walk_attachments(msg)
    except (OSError, ValueError) as e:
        logger.debug("_extract_attachments_from_source_file 解析失败 %s: %s", source_path, e)
        return []


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
    # P3.5.100 (6/24 鸿波 catch): 附件元数据 — 复用 _walk_attachments helper.
    # 跟 AS 路径 (_extract_attachments_from_source_file) 走同一函数, 行为一致.
    attachments = _walk_attachments(msg)
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
        has_attachments=len(attachments) > 0,
        attachments=tuple(attachments),
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

    **读不到一律返 None, 不抛。** 8/8 修:

    `~/Library/Mail` 在 macOS 上受 TCC 保护, 没有完全磁盘访问权限的进程
    `is_dir()` 会过 (目录 stat 得到), 但 `iterdir()` 抛 PermissionError。
    Companion 拉起的子进程正是这种情况 —— 从终端手跑没事 (Terminal 一般有 FDA),
    在 Companion 里就炸。

    以前这个函数只在 `list_messages` 里被调, 那层 `except (..., OSError, ...)`
    能接住 (PermissionError 是 OSError 子类), 表现成一行错误。8/8 把它挪进了
    adapter 构造 (可用性探测), 而 `get_all_adapters()` 的 except 不含 OSError,
    于是异常冒穿整个 CLI, 邮件页从"有噪音"变成"拉取失败 + 一屏 traceback"。

    对调用方来说"没权限读"和"没有这个目录"结论完全一样: EMLX 这条路走不通。
    与其让每个调用点各自记得 catch, 不如在这里给出确定的语义。
    """
    base = Path.home() / "Library" / "Mail"
    try:
        if not base.is_dir():
            return None
        candidates = sorted(
            (p for p in base.iterdir() if p.name.startswith("V") and p.is_dir()),
            key=lambda p: -int(p.name[1:]) if p.name[1:].isdigit() else 0,
        )
    except OSError as e:
        # PermissionError (无 FDA) 最常见; 也可能是 Mail 目录在网络卷上不可达。
        logger.debug("读不到 %s (%s: %s), 当作没有 EMLX 数据", base, type(e).__name__, e)
        return None
    return candidates[0] if candidates else None


# ── Adapter 主类 ────────────────────────────────────────


