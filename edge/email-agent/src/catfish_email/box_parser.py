"""Foxmail .box 文件解析器 —— Win/Mac 共用。

# 背景: Foxmail 自己的存储格式
==================================
Foxmail 7+ (Windows + Mac) 把邮件存成两种主要文件:

  <folder>.box   — 数据文件, 一个文件夹的所有邮件 RFC822 内容连续拼接
                   (每封邮件之间用 14-byte 标记分隔)
  <folder>.ind   — 索引文件, 每条记录是一封邮件的元信息 + 在 .box 里的 byte 偏移

7.2 实测不是这样 (9/18 在真机上量出来的, 之前这段是猜的):
  Mails/<id%32>/<id//32%32>/<id>   — 单封邮件, **文件名是纯数字 id, 没有扩展名**
  Boxes/<folder>.box              — 只是该文件夹的 id 列表, magic 'LSTG', 不含正文
  Boxes/mId_bId.map               — mail id → box id 映射
  Mime/Decode.*                   — 解码缓存, 不是邮件

也就是说 7.2 换了存储模型: .box 从"数据文件"变成了"索引文件"。
拿 6.x 的判据去读 7.2 的结果是: .box 里找不到 FOXM magic, 逐字节试探几千次,
而真正的 5671 封邮件因为没有 .box/.eml 后缀, 根本没被扫到。

我们的 parser 处理三种:
  - .box 开头是 'LSTG' → 索引文件, 不是邮件容器, 交给 foxmail7_store
  - .box 开头是 FOXM / legacy magic → 6.x 数据文件, 走 parse_box_file
  - 单个文件 (.eml 或无后缀) → parse_mail_file, 起始偏移靠嗅探而不是假设

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
import re
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
#: Foxmail 7.2 的 .box —— 'LSTG' (list group), 是 id 列表索引, **不含邮件正文**
MAGIC_LSTG = b"LSTG"
HEADER_SIZE = 14
"""14 bytes: 4 magic + 4 length + 4 flags + 2 reserved。"""

#: 单封邮件大小硬上限 (50 MB) —— 超过认为格式错乱, 跳过
MAX_MESSAGE_SIZE = 50 * 1024 * 1024

#: 一个文件最多报几条坏 header 警告。
#: 9/18: 格式判据不匹配时旧代码逐字节试探, 每字节一条 WARNING, 真机上刷了
#: 四千多行才结束。解析器认错格式是**一个**事实, 不该产生 O(文件大小) 条日志。
MAX_BAD_HEADER_WARNINGS = 5


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
    if is_lstg_index(data):
        # Foxmail 7.2: 这个 .box 是 id 列表, 邮件正文在 Mails/ 下。
        # 说一次就够, 让调用方去走 foxmail7_store。
        logger.debug("box parser: %s 是 LSTG 索引文件, 不含正文, 跳过", path.name)
        return

    pos = 0
    n = 0
    bad_headers = 0
    while pos + HEADER_SIZE <= len(data):
        header = data[pos : pos + HEADER_SIZE]
        magic = header[:4]
        length = struct.unpack("<I", header[4:8])[0]
        flags = struct.unpack("<I", header[8:12])[0]

        if magic not in (MAGIC_FOXM, MAGIC_LEGACY):
            bad_headers += 1
            if bad_headers <= MAX_BAD_HEADER_WARNINGS:
                logger.warning(
                    "box parser: 跳过位置 %d 的坏 header (magic=%r), 试探下个字节",
                    pos, magic,
                )
            elif bad_headers == MAX_BAD_HEADER_WARNINGS + 1:
                logger.warning(
                    "box parser: %s 连续对不上 header, 后续同类警告不再逐条打印 "
                    "—— 这通常意味着文件不是 6.x 的 .box 数据文件",
                    path.name,
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

    if n == 0 and bad_headers:
        logger.warning(
            "box parser: %s 一封也没解析出来, 共 %d 处 header 对不上 —— 格式判据可能不适用",
            path.name, bad_headers,
        )
    else:
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
            parsed = parse_eml_file(f)
        except Exception as e:  # noqa: BLE001
            logger.warning("eml parser: %s 解析失败 (%s), 跳过", f.name, e)
            continue
        yield parsed


def parse_eml_file(path: Path) -> ParsedMessage:
    """解析单个 per-message `.eml` 文件。"""
    data = path.read_bytes()
    msg = email.message_from_bytes(data, policy=email.policy.default)
    if not isinstance(msg, EmailMessage):
        raise ValueError(f"{path.name} 不是 EmailMessage")
    return ParsedMessage(
        raw_offset=0,
        raw_length=len(data),
        flags=0,
        message=msg,
    )


def is_lstg_index(data: bytes) -> bool:
    """这份字节是不是 Foxmail 7.2 的 'LSTG' id 列表索引 (而非邮件数据)。"""
    return data[:4] == MAGIC_LSTG


# ============================================================
# 7.2 per-message 文件: 起始偏移靠嗅探, 不靠假设
# ============================================================

#: 嗅探只看文件开头这么多字节 —— 邮件头不可能比这还靠后
_SNIFF_WINDOW = 64 * 1024
#: 判定"这确实是邮件头"至少要凑够几行 header
_MIN_HEADER_LINES = 3
#: 并且至少出现一个真正的邮件头字段 (只有 X-Foo: 这类不算)
_ESSENTIAL_HEADERS = frozenset(
    {
        b"received", b"from", b"to", b"subject", b"date",
        b"message-id", b"mime-version", b"content-type", b"return-path",
    }
)


def _header_name(line: bytes) -> bytes | None:
    """``b'Subject: x'`` → ``b'subject'``; 不是 header 行则 None。"""
    colon = line.find(b":")
    if colon <= 0 or colon > 60:
        return None
    name = line[:colon]
    if not all(c == 0x2D or (0x30 <= c <= 0x39) or (0x41 <= c <= 0x5A) or (0x61 <= c <= 0x7A)
               for c in name):
        return None
    if not (0x41 <= name[0] <= 0x5A or 0x61 <= name[0] <= 0x7A):
        return None
    return name.lower()  # bytes 没有 casefold


#: 候选起点的形状: 一个 header 字段名加冒号
_HEADER_START_RE = re.compile(rb"[A-Za-z][A-Za-z0-9-]{0,40}:")
#: 最多验证几个候选起点 —— 纯文本正文里冒号很多, 不封顶会白跑
_MAX_SNIFF_CANDIDATES = 2000


def sniff_rfc822_offset(data: bytes) -> int | None:
    """在 ``data`` 里找 RFC822 邮件头的起始偏移; 找不到返回 None。

    为什么要嗅探而不是写死偏移量:
        9/18 一天之内在 Foxmail 这条线上踩了五个同族 bug, 全都是"按某个版本的
        布局写死判据, 换个版本全不匹配"。单封邮件文件可能是裸 RFC822 (offset 0),
        也可能前面挂了一段专有头。与其再猜一次, 不如让代码自己找到邮件头在哪 ——
        判据是"连续若干行长得像 header, 且含至少一个真实邮件头字段", 这个特征
        跨版本稳定, 因为它来自 RFC822 而不是来自 Foxmail。

    Args:
        data: 邮件文件的字节 (整份或开头一段都行)

    Returns:
        邮件头第一个字节的偏移量, 或 None。
    """
    window = data[:_SNIFF_WINDOW]
    # 候选起点不能只取行首: 专有前缀如果不以换行结尾, 会跟第一行 header 粘成
    # 一行 (实测就是这样), 那样只能从第二个字段开始, 会丢掉 From。
    # 所以对"字段名+冒号"的每个出现位置都试一次, 取最靠前的那个能站住的。
    for index, match in enumerate(_HEADER_START_RE.finditer(window)):
        if index >= _MAX_SNIFF_CANDIDATES:
            break
        if _looks_like_header_block(window, match.start()):
            return match.start()
    return None


def _looks_like_header_block(window: bytes, start: int) -> bool:
    """从 ``start`` 起是不是一整块邮件头 (而不是正文里一行碰巧带冒号)。"""
    names: set[bytes] = set()
    lines = 0
    pos = start
    while pos < len(window):
        end = window.find(b"\n", pos)
        raw = window[pos : (len(window) if end == -1 else end + 1)]
        line = raw.rstrip(b"\r\n")
        if not line:  # 空行 = 头部结束
            break
        if line[:1] in (b" ", b"\t"):  # 折行续上一个字段
            pos += len(raw)
            continue
        name = _header_name(line)
        if name is None:
            return False  # 头部中间冒出非 header 行 → 这不是头部
        names.add(name)
        lines += 1
        pos += len(raw)
        if end == -1:
            break
    return lines >= _MIN_HEADER_LINES and bool(names & _ESSENTIAL_HEADERS)


def parse_mail_file(path: Path, *, head_bytes: int | None = None) -> ParsedMessage:
    """解析 Foxmail 7.2 的单封邮件文件 (``Mails/<桶>/<桶>/<id>``, 无扩展名)。

    跟 :func:`parse_eml_file` 的区别: 不假设文件从第 0 字节就是 RFC822,
    而是嗅探邮件头起点, 前面的专有字节丢掉。嗅不到就 ValueError, 由 adapter
    跳过这一封 —— 一封读不了不该让整个收件箱空。

    Args:
        path: 邮件文件路径
        head_bytes: 只读开头这么多字节。列清单只需要头部 (主题/发件人/日期),
            而真机上一个账号 5671 封共 7.8 GB, 整份读进来是不可接受的。
            ``None`` = 整份读 (打开单封邮件时用)。
    """
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
    return ParsedMessage(
        raw_offset=offset,
        raw_length=len(data) - offset,
        flags=0,  # 7.2 的已读位在索引里, 不在邮件文件里
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
