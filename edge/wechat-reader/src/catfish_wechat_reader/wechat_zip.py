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

解析只在内存里读。加密条目、条目数 > 1000、解压后总量 > 1 GB、
TXT > 16 MB、路径穿越 / 绝对路径 / 反斜杠 一律拒收。CRC 由 zipfile 在读取时校验。
唯一落盘的是 `extract_documents` (二期): 只写文档类附件、只写到调用方给的空目录。
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
# 二期 (9/23): 包里能交给 Companion 文档解析 (parse_file.py) 的附件。图片 / 音视频不在内 ——
# 图片要走视觉模型、音视频要转写, 成本和时长都不是「导入时顺手读」的量级。
DOCUMENT_EXTS = {
    ".pdf", ".docx", ".xlsx", ".xlsm", ".xls", ".pptx",
    ".csv", ".json", ".txt", ".md", ".markdown", ".log",
}
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024  # 跟聊天框单个附件上限一致

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

    @property
    def documents(self) -> list[str]:
        """包里的文档附件: 先按聊天里被提到的先后, 没被提到的排后面。"""
        docs = {n for n in self.attachments if PurePosixPath(n).suffix.lower() in DOCUMENT_EXTS}
        ordered: list[str] = []
        for message in self.messages:
            name = message.attachment_name
            if name in docs and name not in ordered:
                ordered.append(name)
        return ordered + sorted(docs - set(ordered))

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


def extract_documents(source, dest, limit: int, max_bytes: int = MAX_DOCUMENT_BYTES) -> dict:
    """把包里的文档附件写到调用方给的**空**目录, 交给 Companion 的文档解析。

    只写 `documents` 里的名字 (basename, 已过 _safe_name 校验), O_EXCL 新建, 不覆盖、
    不跟随符号链接; 单个超过 max_bytes 的跳过并说明原因。CRC 由 zipfile 读时校验。
    """
    import os
    from pathlib import Path

    target_dir = Path(dest)
    if not target_dir.is_dir() or any(target_dir.iterdir()):
        raise _fail("文档输出目录必须是已存在的空目录", "invalid_scope")
    parsed = read_export(source, compute_hash=False)
    wanted = parsed.documents
    extracted: list[dict] = []
    skipped: list[dict] = [{"name": n, "reason": "超过本条消息的附件数量上限"} for n in wanted[limit:]]
    try:
        with zipfile.ZipFile(source) as archive:
            by_name: dict[str, zipfile.ZipInfo] = {}
            for info in archive.infolist():
                if info.is_dir() or info.filename == parsed.transcript_path:
                    continue
                by_name.setdefault(PurePosixPath(info.filename).name, info)
            for name in wanted[:max(0, limit)]:
                info = by_name.get(name)
                if info is None:
                    skipped.append({"name": name, "reason": "包里找不到"})
                    continue
                if info.file_size > max_bytes:
                    skipped.append({"name": name, "reason": "超过 20 MB"})
                    continue
                data = archive.read(info)
                path = target_dir / name
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) \
                    | getattr(os, "O_BINARY", 0)
                fd = os.open(path, flags, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                extracted.append({"name": name, "path": str(path), "size": len(data)})
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise _fail("文档附件读取失败") from exc
    return {"extracted": extracted, "skipped": skipped}
