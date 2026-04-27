"""Foxmail .box 文件解析器 —— Win/Mac 共用。

# 背景: Foxmail 自己的存储格式
==================================
Foxmail 7+ (Windows + Mac) 把邮件存成两种主要文件:

  <folder>.box   — 数据文件, 一个文件夹的所有邮件 RFC822 内容连续拼接
                   (每封邮件之间用 14-byte 标记分隔)
  <folder>.ind   — 索引文件, 每条记录是一封邮件的元信息 + 在 .box 里的 byte 偏移

更新版本 (7.2+) 的部分文件夹会改成 per-message 文件:
  <folder>/<msgid>.eml   — 单封 RFC822
  <folder>/index.lst     — 简单的 id 列表

我们的 parser 同时处理两种:
  - 如果目录里有 .box + .ind → 走 box 模式
  - 如果目录里有大量 .eml → 走 per-message 模式
  - 都没有 → DataNotFoundError

# .box 文件格式 (经多个公开逆向项目对齐)
=========================================
[14-byte header] [RFC822 message body] [14-byte header] [body] ...

14-byte header:
  bytes 0-3: magic 0x46 0x4f 0x58 0x4d  ("FOXM")
  bytes 4-7: message length (little-endian uint32) — 这条 RFC822 body 的字节长度
  bytes 8-11: flags (little-endian uint32) — 已读位 / 优先级 / 等
  bytes 12-13: 保留 / 未知 (旧版本 0x00 0x00)

flags 已知位 (best-effort, 不是所有版本都一致):
  bit 0: 已读
  bit 1: 已回复
  bit 2: 已转发
  bit 3: 标星
  bit 4: 有附件
  ...

# 注意事项
==========
- Foxmail Mac 7+ 实测路径: ~/Library/Application Support/Foxmail7/Storage/<email>/
  (沙盒版可能在 ~/Library/Containers/com.tencent.foxmail/Data/...)
  跨版本路径不一致, 需要 path 解析层另做 (config.py)
- magic 在某些 7.0 版本是 ' \\xfa\\xfa\\xff\\x60' 而不是 'FOXM' —— 我们检 2 个
- RFC822 body 用 Python 标准库 email.parser.BytesParser 解析, 不自己写
- 如果 magic 不对 / 长度异常, 跳过并 warn, 不直接 raise (一封坏的邮件不该让整个
  收件箱不可读)
"""
from __future__ import annotations

import email
import email.policy
import logging
import struct
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable

logger = logging.getLogger("catfish_email.box_parser")

#: Foxmail 标准 magic 头 ('FOXM' ASCII)
MAGIC_FOXM = b"FOXM"
#: 旧版本另一种 magic (实测 7.0.x 中文版偶见)
MAGIC_LEGACY = b"\xfa\xfa\xff\x60"
HEADER_SIZE = 14
"""14 bytes: 4 magic + 4 length + 4 flags + 2 reserved。"""

#: 单封邮件大小硬上限 (50 MB) —— 超过认为格式错乱, 跳过
MAX_MESSAGE_SIZE = 50 * 1024 * 1024


# ============================================================
# 数据类
# ============================================================


@dataclass(frozen=True)
class ParsedMessage:
    """从 .box / .eml 解析出的单封邮件。

    跟 adapters.base.Message 不同: 这个是底层格式, adapter 再做转换。
    保留 EmailMessage 对象方便 adapter 自己抽信息 (附件 / HTML / 等)。
    """

    raw_offset: int
    """这封邮件在 .box 文件里的起始 byte offset (per-message 模式 = 0)。"""

    raw_length: int
    """RFC822 body 的字节长度 (不含 14-byte header)。"""

    flags: int
    """Foxmail 内部 flags 位图。bit 0 = 已读。"""

    message: EmailMessage
    """Python 标准库解析过的 EmailMessage 对象。"""


# ============================================================
# 主入口
# ============================================================


def parse_box_file(path: Path) -> Iterable[ParsedMessage]:
    """解析单个 .box 文件, 逐封 yield ParsedMessage。

    遇到坏的 header → 跳过 + log warning, 不 raise (容错)。

    Args:
        path: .box 文件绝对路径

    Yields:
        ParsedMessage (按 .box 里的存储顺序, 通常是时间 ASC)

    Raises:
        FileNotFoundError: 文件不存在
        OSError: 读不动文件
    """
    if not path.exists():
        raise FileNotFoundError(f".box 文件不存在: {path}")

    data = path.read_bytes()
    pos = 0
    n = 0
    while pos + HEADER_SIZE <= len(data):
        header = data[pos : pos + HEADER_SIZE]
        magic = header[:4]
        length = struct.unpack("<I", header[4:8])[0]
        flags = struct.unpack("<I", header[8:12])[0]

        if magic not in (MAGIC_FOXM, MAGIC_LEGACY):
            logger.warning(
                "box parser: 跳过位置 %d 的坏 header (magic=%r), 试探下个字节",
                pos, magic,
            )
            pos += 1  # 试探: 错位 1 字节再来 (保守, 大概率到下一个 magic 处会重新对齐)
            continue

        if length == 0 or length > MAX_MESSAGE_SIZE:
            logger.warning(
                "box parser: 跳过位置 %d 异常长度 length=%d", pos, length,
            )
            pos += HEADER_SIZE
            continue

        body_start = pos + HEADER_SIZE
        body_end = body_start + length
        if body_end > len(data):
            logger.warning(
                "box parser: 位置 %d 声称 length=%d 但文件只剩 %d, 截止",
                pos, length, len(data) - body_start,
            )
            break

        body_bytes = data[body_start:body_end]
        try:
            msg = email.message_from_bytes(body_bytes, policy=email.policy.default)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "box parser: 位置 %d 邮件解析失败 (%s), 跳过", pos, e,
            )
            pos = body_end
            continue

        if not isinstance(msg, EmailMessage):
            # email.policy.default 应该返回 EmailMessage, 但兜底
            logger.warning(
                "box parser: 位置 %d 不是 EmailMessage 类型, 跳过", pos,
            )
            pos = body_end
            continue

        yield ParsedMessage(
            raw_offset=pos,
            raw_length=length,
            flags=flags,
            message=msg,
        )
        n += 1
        pos = body_end

    logger.info("box parser: %s 解析出 %d 封邮件", path.name, n)


def parse_eml_directory(dir_path: Path) -> Iterable[ParsedMessage]:
    """per-message 模式: 目录里每个 .eml 一个封邮件。

    Foxmail 7.2+ 部分文件夹用这个格式 (尤其是 Drafts / Sent)。
    没 14-byte header, raw_offset=0, flags=0 (信息丢了, adapter 自己另存)。
    """
    if not dir_path.is_dir():
        raise FileNotFoundError(f".eml 目录不存在: {dir_path}")

    files = sorted(dir_path.glob("*.eml"))
    for f in files:
        try:
            data = f.read_bytes()
            msg = email.message_from_bytes(data, policy=email.policy.default)
        except Exception as e:  # noqa: BLE001
            logger.warning("eml parser: %s 解析失败 (%s), 跳过", f.name, e)
            continue

        if not isinstance(msg, EmailMessage):
            continue

        yield ParsedMessage(
            raw_offset=0,
            raw_length=len(data),
            flags=0,
            message=msg,
        )


def detect_storage_mode(folder_dir: Path) -> str:
    """探测某个 Foxmail 文件夹用的是 box 模式还是 eml 模式。

    Returns:
        'box'  — 找到 <name>.box + <name>.ind
        'eml'  — 找到一堆 .eml 文件
        'unknown' — 都没找到
    """
    if not folder_dir.is_dir():
        return "unknown"

    has_box = any(folder_dir.glob("*.box"))
    has_eml = any(folder_dir.glob("*.eml"))

    if has_box:
        return "box"
    if has_eml:
        return "eml"
    return "unknown"


# ============================================================
# 元信息提取 helpers
# ============================================================


def extract_header(msg: EmailMessage, name: str, default: str = "") -> str:
    """安全拿 header (处理 None / 编码问题)。"""
    val = msg.get(name)
    if val is None:
        return default
    return str(val).strip()


def parse_date_to_iso(raw_date: str) -> str:
    """把 RFC 5322 Date 头转 ISO-8601 UTC。失败返回空字符串。"""
    if not raw_date:
        return ""
    try:
        dt = parsedate_to_datetime(raw_date)
        if dt is None:
            return ""
        return dt.isoformat()
    except (TypeError, ValueError):
        return ""


def is_read(flags: int) -> bool:
    """Foxmail flags bit 0 = 已读。"""
    return bool(flags & 0x01)


def has_attachments(msg: EmailMessage) -> bool:
    """看 multipart 里有没有 Content-Disposition: attachment 部分。"""
    if not msg.is_multipart():
        return False
    for part in msg.walk():
        cd = part.get("Content-Disposition", "")
        if cd and "attachment" in cd.lower():
            return True
    return False


def extract_body_text(msg: EmailMessage, max_chars: int | None = None) -> str:
    """从 EmailMessage 抽纯文本正文。

    - 优先找 text/plain part
    - 没有就把 text/html 转纯文本 (简陋: 去 HTML tag)
    - max_chars 可选, 给 list 场景的 snippet 用 (~200 字)
    """
    body = ""
    if msg.is_multipart():
        # 找第一个 text/plain
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body = _safe_get_payload(part)
                break
        if not body:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    body = _strip_html(_safe_get_payload(part))
                    break
    else:
        ctype = msg.get_content_type()
        body = _safe_get_payload(msg)
        if ctype == "text/html":
            body = _strip_html(body)

    if max_chars is not None and len(body) > max_chars:
        body = body[:max_chars] + "…"
    return body


def extract_body_html(msg: EmailMessage) -> str:
    """从 EmailMessage 抽 HTML 正文 (没有就空字符串)。"""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                return _safe_get_payload(part)
    elif msg.get_content_type() == "text/html":
        return _safe_get_payload(msg)
    return ""


def _safe_get_payload(msg) -> str:
    """从 part 拿 decoded text payload, 容错处理编码。"""
    try:
        payload = msg.get_payload(decode=True)
    except Exception:
        return ""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    # bytes → 尝试常见编码 (charset header > utf-8 > gbk/gb18030 > latin1 兜底)
    charset = msg.get_content_charset() or "utf-8"
    for enc in (charset, "utf-8", "gb18030", "gbk", "latin-1"):
        try:
            return payload.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    # 兜底: utf-8 + replace
    return payload.decode("utf-8", errors="replace")


def _strip_html(html: str) -> str:
    """简陋的 HTML → 纯文本 (列表 snippet 用, 不追求保真)。"""
    if not html:
        return ""
    import re
    # 删 script/style
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
    # 把 <br> / </p> 转换行
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"</p>", "\n", html, flags=re.I)
    # 删所有 tag
    html = re.sub(r"<[^>]+>", "", html)
    # 多空格压一下
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


def extract_attachment_metadata(msg: EmailMessage) -> list[tuple[str, int, str]]:
    """列附件元信息 (filename, size_bytes, content_type)。

    不下载附件正文 (那是另一个 API)。
    """
    out = []
    if not msg.is_multipart():
        return out
    for part in msg.walk():
        cd = part.get("Content-Disposition", "")
        if not cd or "attachment" not in cd.lower():
            continue
        fname = part.get_filename() or "(unnamed)"
        ctype = part.get_content_type()
        try:
            payload = part.get_payload(decode=True)
            size = len(payload) if payload else 0
        except Exception:
            size = 0
        out.append((fname, size, ctype))
    return out
