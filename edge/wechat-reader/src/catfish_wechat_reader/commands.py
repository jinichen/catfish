"""Protocol command implementations without persistence or model calls."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .readers import parse_timestamp

MAX_ITEMS = 200
_MESSAGE_FIELDS = (
    "message_id", "session_id", "sender_id", "sender_name", "timestamp", "type", "text", "is_self",
)


def bounded_limit(value: object, default: int) -> int:
    try:
        return max(1, min(MAX_ITEMS, int(value)))
    except (TypeError, ValueError):
        return default


def _public_message(item: dict[str, object]) -> dict[str, object]:
    return {key: item[key] for key in _MESSAGE_FIELDS if key in item}


def _in_range(item: dict[str, object], start: datetime, end: datetime) -> bool:
    timestamp = item["_timestamp"]
    return isinstance(timestamp, datetime) and start <= timestamp <= end


def sessions(records: Iterable[dict[str, object]], limit: int) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    for item in records:
        session_id = str(item["session_id"])
        current = grouped.get(session_id)
        if current is None:
            grouped[session_id] = {
                "session_id": session_id,
                "name": item["session_name"],
                "type": "chat",
                "last_message_at": item["timestamp"],
                "_last": item["_timestamp"],
                "message_count": 1,
            }
            continue
        current["message_count"] = int(current["message_count"]) + 1
        if item["_timestamp"] > current["_last"]:
            current["last_message_at"] = item["timestamp"]
            current["_last"] = item["_timestamp"]
            current["name"] = item["session_name"]
    ordered = sorted(grouped.values(), key=lambda item: item["_last"], reverse=True)[:limit]
    return [{key: value for key, value in item.items() if not key.startswith("_")} for item in ordered]


def history(
    records: Iterable[dict[str, object]],
    session_id: str,
    start_text: str,
    end_text: str,
    limit: int,
) -> list[dict[str, object]]:
    start, end = parse_timestamp(start_text), parse_timestamp(end_text)
    matched = [
        item for item in records
        if item["session_id"] == session_id and _in_range(item, start, end)
    ]
    matched.sort(key=lambda item: item["_timestamp"])
    return [_public_message(item) for item in matched[:limit]]


def search(
    records: Iterable[dict[str, object]],
    query: str,
    start_text: str,
    end_text: str,
    limit: int,
    session_id: str | None = None,
) -> list[dict[str, object]]:
    start, end = parse_timestamp(start_text), parse_timestamp(end_text)
    needle = query.casefold()
    matched = [
        item for item in records
        if _in_range(item, start, end)
        and (not session_id or item["session_id"] == session_id)
        and needle in str(item.get("text", "")).casefold()
    ]
    matched.sort(key=lambda item: item["_timestamp"], reverse=True)
    return [_public_message(item) for item in matched[:limit]]
