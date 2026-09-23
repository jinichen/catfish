"""微信「合并转发 → 其他应用」导出的 ZIP 读取器 (9/23)。

# 格式从哪来

两份真实导出 (私聊 88 条 + 群聊 44 条 8 人) 逐项核过, 跟 WeChatBridge
(MIT, github.com/freestylefly/WeChatBridge) `WeChatForward.swift` 的解析规则一致::

    聊天记录.txt
    聊天记录内的图片、视频和文件/微信图片_202603251934_1.jpg
    ...

TXT 每条消息固定四行::

    ·发送人
    2026年3月25日 19:34
    正文 (可以多行)
    (空行)

正文开头的 `[图片] 文件名` / `[文件] 文件名` 指向包里的附件 —— 但 zip / rar
这类附件**不随导出**, 文件名在 TXT 里, 包里没有。`[小程序] 标题` 只有标题。
`[OK]` 这种是微信内置表情的文字写法, 不是消息类型。`@某人` 后面跟的是
U+2005 (四分之一全角空格), 不归一的话按人名搜不到。

# 只认见过的

消息类型只认样本里真出现过的标签; 没见过的一律按正文原样保留, 不猜。
第一条消息必须从文件开头对齐, 对不上就报「格式不认识」—— 微信哪天改了格式,
宁可明确失败, 也不解析出一堆看着像对的错数据。

# 安全

只在内存里读, 不解压到磁盘。加密条目、条目数 > 1000、解压后总量 > 1 GB、
TXT > 16 MB、路径穿越 / 绝对路径 / 反斜杠 一律拒收。CRC 由 zipfile 在读取时校验。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from pathlib import PurePosixPath
import re
import zipfile
from typing import BinaryIO

from .readers import ReaderFailure

MAX_ENTRIES = 1000
MAX_TOTAL_EXPANDED = 1024 * 1024 * 1024
MAX_TRANSCRIPT_BYTES = 16 * 1024 * 1024
MAX_TEXT_CHARS = 12000
NATIVE_TRANSCRIPT = "聊天记录.txt"

_RECORD_HEAD = re.compile(
    r"^·([^\n]+)\n(\d{4})年(\d{1,2})月(\d{1,2})日 (\d{2}):(\d{2})\n", re.MULTILINE
)
# 样本里真出现过的标签。`[OK]` 这种表情不在表里, 所以按正文保留。
_KNOWN_TAGS = {
    "图片": "image",
    "文件": "file",
    "小程序": "miniprogram",
    "语音通话": "call",
}
_TAG = re.compile(r"^\[([^\]\n]{1,12})\](?: (.*))?$", re.DOTALL)
# U+2000..U+200A 各种宽度的空格, U+202F / U+205F 窄空格。全角空格 U+3000 是正常中文排版, 不动。
_ODD_SPACES = re.compile("[\u2000-\u200a\u202f\u205f]")


@dataclass(frozen=True)
class RawMessage:
    sender: str
    minute: datetime  # 带本机时区; TXT 里的时间就是导出那台机器的本地时间
    text: str
    type: str
    attachment_name: str | None
    attachment_present: bool


@dataclass
class ParsedExport:
    sha256: str
    size: int
    transcript_path: str
    messages: list[RawMessage]
    attachments: list[str] = field(default_factory=list)

    @property
    def senders(self) -> list[tuple[str, int]]:
        # 发言多的在前; 一样多按第一次出现的先后 (dict 保序), 不按码点 —— 起默认群名时更自然
        counts: dict[str, int] = {}
        for message in self.messages:
            counts[message.sender] = counts.get(message.sender, 0) + 1
        order = {name: index for index, name in enumerate(counts)}
        return sorted(counts.items(), key=lambda item: (-item[1], order[item[0]]))

    @property
    def start(self) -> datetime:
        return min(message.minute for message in self.messages)

    @property
    def end(self) -> datetime:
        return max(message.minute for message in self.messages)

    def attachment_summary(self) -> dict[str, int]:
        referenced = [m for m in self.messages if m.attachment_name]
        return {
            "in_archive": len(self.attachments),
            "referenced": len(referenced),
            "missing": sum(1 for m in referenced if not m.attachment_present),
        }


def _fail(message: str, reason: str = "invalid_source") -> ReaderFailure:
    return ReaderFailure(message, reason)


def _safe_name(name: str) -> bool:
    if not name or "\\" in name or name.startswith("/") or ":" in name:
        return False
    parts = PurePosixPath(name.rstrip("/")).parts
    return bool(parts) and all(part not in ("", ".", "..") for part in parts)


def _decode_transcript(raw: bytes) -> str:
    # Mac 版是 UTF-8。Windows 版还没见过样本, 先兼容 GB18030, 两个都不对就明确失败。
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise _fail("聊天记录.txt 编码无法识别")


def _local_tz():
    return datetime.now().astimezone().tzinfo or timezone.utc


def parse_transcript(
    body: str, attachment_names: set[str], tz=None
) -> list[RawMessage]:
    text = body.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    heads = list(_RECORD_HEAD.finditer(text))
    if not heads or heads[0].start() != 0:
        raise _fail("不是认识的微信聊天记录格式 (第一条消息没有对齐)", "unsupported_format")
    tz = tz or _local_tz()
    messages: list[RawMessage] = []
    for index, head in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
        content = _ODD_SPACES.sub(" ", text[head.end():end].strip("\n"))
        content = content.strip()
        year, month, day, hour, minute = (int(head.group(i)) for i in range(2, 7))
        try:
            when = datetime(year, month, day, hour, minute, tzinfo=tz)
        except ValueError as exc:
            raise _fail(f"第 {index + 1} 条消息时间无效") from exc
        kind, attachment = "text", None
        tag = _TAG.match(content)
        if tag:
            kind = _KNOWN_TAGS.get(tag.group(1), "text")
            rest = (tag.group(2) or "").strip()
            if kind in ("image", "file") and rest:
                attachment = rest
            elif rest and rest in attachment_names:
                attachment = rest  # 没见过的标签, 但包里确有同名文件 —— 有证据才关联
        messages.append(RawMessage(
            sender=head.group(1).strip(),
            minute=when,
            text=content[:MAX_TEXT_CHARS],
            type=kind,
            attachment_name=attachment,
            attachment_present=bool(attachment and attachment in attachment_names),
        ))
    return messages


def _hash_stream(handle: BinaryIO) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    for chunk in iter(lambda: handle.read(1 << 20), b""):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def read_export(path, tz=None, compute_hash: bool = True) -> ParsedExport:
    """读一个导出 ZIP。任何一项不满足都抛 ReaderFailure, 不返回半截结果。

    `compute_hash=False` 给查询用: 库里的包文件名就是导入时算好的 sha256,
    每次查询都把几十 MB 图片重新哈希一遍没有意义。
    """
    try:
        with open(path, "rb") as handle:
            if compute_hash:
                sha, size = _hash_stream(handle)
                handle.seek(0)
            else:
                sha, size = "", handle.seek(0, 2)
                handle.seek(0)
            archive = zipfile.ZipFile(handle)
            return _read_archive(archive, sha, size, tz)
    except ReaderFailure:
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, EOFError) as exc:
        # RuntimeError: zipfile 遇到加密条目; BadZipFile 也包括 CRC 不对
        raise _fail("导出的 ZIP 损坏或无法读取") from exc


def _read_archive(archive: zipfile.ZipFile, sha: str, size: int, tz) -> ParsedExport:
    infos = archive.infolist()
    if not infos or len(infos) > MAX_ENTRIES:
        raise _fail(f"ZIP 条目数必须在 1 到 {MAX_ENTRIES} 之间")
    if sum(info.file_size for info in infos) > MAX_TOTAL_EXPANDED:
        raise _fail("ZIP 解压后超过 1 GB 安全上限", "source_too_large")
    names: set[str] = set()
    for info in infos:
        if info.flag_bits & 0x1:
            raise _fail("ZIP 里有加密条目", "unsupported_format")
        if not _safe_name(info.filename) or info.filename in names:
            raise _fail("ZIP 里有不安全或重复的路径")
        names.add(info.filename)
    files = [info for info in infos if not info.is_dir()]
    texts = [info for info in files if info.filename.lower().endswith(".txt")]
    native = [info for info in texts if PurePosixPath(info.filename).name == NATIVE_TRANSCRIPT]
    candidates = native or texts
    if not candidates:
        raise _fail("ZIP 里没有聊天记录.txt", "unsupported_format")
    attachment_names = {
        PurePosixPath(info.filename).name for info in files if info not in candidates
    }
    last_error: ReaderFailure | None = None
    for info in candidates:
        if info.file_size > MAX_TRANSCRIPT_BYTES:
            last_error = _fail("聊天记录.txt 超过 16 MB 安全上限", "source_too_large")
            continue
        try:
            body = _decode_transcript(archive.read(info))
            messages = parse_transcript(body, attachment_names, tz)
        except ReaderFailure as exc:
            last_error = exc
            continue
        return ParsedExport(
            sha256=sha,
            size=size,
            transcript_path=info.filename,
            messages=messages,
            attachments=sorted(attachment_names),
        )
    raise last_error or _fail("ZIP 里没有可解析的聊天记录")
