"""Strict readers for employee-selected chat export files."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterator

MAX_SOURCE_BYTES = 512 * 1024 * 1024
SUPPORTED_SUFFIXES = {".json", ".jsonl", ".csv"}

_ALIASES: dict[str, tuple[str, ...]] = {
    "message_id": ("message_id", "msg_id", "id", "消息id", "消息ID"),
    "session_id": ("session_id", "chat_id", "talker", "会话id", "会话ID"),
    "session_name": ("session_name", "chat_name", "会话名称", "群名称"),
    "sender_id": ("sender_id", "from_user", "发送人id", "发送人ID"),
    "sender_name": ("sender_name", "sender", "nickname", "发送人", "发送者"),
    "timestamp": ("timestamp", "time", "create_time", "datetime", "时间", "发送时间"),
    "type": ("type", "msg_type", "消息类型", "类型"),
    "text": ("text", "content", "message", "内容", "消息内容"),
    "is_self": ("is_self", "outgoing", "是否本人", "是否自己"),
}


class ReaderFailure(ValueError):
    def __init__(self, message: str, reason_code: str = "invalid_record") -> None:
        super().__init__(message)
        self.reason_code = reason_code


def parse_timestamp(value: object) -> datetime:
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        return datetime.fromtimestamp(number, tz=timezone.utc)
    text = str(value or "").strip()
    if not text:
        raise ReaderFailure("记录缺少 timestamp/时间")
    if text.replace(".", "", 1).isdigit():
        return parse_timestamp(float(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReaderFailure(f"无法识别时间: {text[:80]}") from exc
    if parsed.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo or timezone.utc
        parsed = parsed.replace(tzinfo=local_tz)
    return parsed


def _first(raw: dict[str, Any], field: str) -> object:
    for key in _ALIASES[field]:
        if key in raw and raw[key] not in (None, ""):
            return raw[key]
    return None


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {
        "1", "true", "yes", "y", "是", "本人", "自己", "outgoing",
    }


def normalize_record(raw: object, ordinal: int) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ReaderFailure(f"第 {ordinal} 条记录不是对象")
    session_id = str(_first(raw, "session_id") or "").strip()
    session_name = str(_first(raw, "session_name") or "").strip()
    if not session_id:
        session_id = session_name
    if not session_id:
        raise ReaderFailure(f"第 {ordinal} 条记录缺少 session_id/会话ID")
    timestamp = parse_timestamp(_first(raw, "timestamp"))
    text = str(_first(raw, "text") or "")
    sender_id = str(_first(raw, "sender_id") or "").strip()
    sender_name = str(_first(raw, "sender_name") or sender_id).strip()
    message_id = str(_first(raw, "message_id") or "").strip()
    if not message_id:
        digest = hashlib.sha256(
            f"{session_id}\0{timestamp.isoformat()}\0{sender_id}\0{text}".encode("utf-8")
        ).hexdigest()[:24]
        message_id = f"derived-{digest}"
    return {
        "message_id": message_id[:300],
        "session_id": session_id[:300],
        "session_name": (session_name or session_id)[:500],
        "sender_id": sender_id[:300],
        "sender_name": sender_name[:500],
        "timestamp": timestamp.isoformat(),
        "_timestamp": timestamp,
        "type": str(_first(raw, "type") or "text")[:80],
        "text": text[:12000],
        "is_self": _as_bool(_first(raw, "is_self")),
    }


def _json_records(path: Path) -> Iterator[object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReaderFailure("JSON 文件无法解析", "invalid_source") from exc
    if isinstance(payload, dict):
        payload = payload.get("messages")
    if not isinstance(payload, list):
        raise ReaderFailure("JSON 必须是消息数组或包含 messages 数组", "invalid_source")
    yield from payload


def _jsonl_records(path: Path) -> Iterator[object]:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ReaderFailure(
                        f"JSONL 第 {line_number} 行无法解析", "invalid_source"
                    ) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ReaderFailure("JSONL 文件无法读取", "invalid_source") from exc


def _csv_records(path: Path) -> Iterator[object]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise ReaderFailure("CSV 缺少表头", "invalid_source")
            yield from reader
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise ReaderFailure("CSV 文件无法解析", "invalid_source") from exc


def iter_records(source: str | Path) -> Iterator[dict[str, object]]:
    path = Path(source).expanduser()
    try:
        resolved = path.resolve(strict=True)
        size = resolved.stat().st_size
    except OSError as exc:
        raise ReaderFailure("导出文件不存在或不可读取", "source_missing") from exc
    if not resolved.is_file():
        raise ReaderFailure("数据源必须是文件", "invalid_source")
    suffix = resolved.suffix.casefold()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ReaderFailure("只支持 JSON、JSONL、CSV", "unsupported_format")
    if size > MAX_SOURCE_BYTES:
        raise ReaderFailure("导出文件超过 512 MB 安全上限", "source_too_large")
    factory = {".json": _json_records, ".jsonl": _jsonl_records, ".csv": _csv_records}[suffix]
    for ordinal, raw in enumerate(factory(resolved), 1):
        yield normalize_record(raw, ordinal)
