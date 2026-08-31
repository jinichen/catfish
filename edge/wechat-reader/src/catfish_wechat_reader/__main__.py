"""CLI entry point for the Catfish local chat export reader."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from .commands import bounded_limit, history, search, sessions
from .readers import ReaderFailure, iter_records


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="catfish-wechat-reader")
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--json", action="store_true")
    for name in ("sessions", "history", "search"):
        command = commands.add_parser(name)
        command.add_argument("--json", action="store_true")
        command.add_argument("--source", required=True)
        command.add_argument("--limit", type=int, default=50 if name == "sessions" else 100)
        if name in {"history", "search"}:
            command.add_argument("--start", required=True)
            command.add_argument("--end", required=True)
        if name == "history":
            command.add_argument("--session-id", required=True)
        if name == "search":
            command.add_argument("--query", required=True)
            command.add_argument("--session-id")
    return parser


def _emit(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _doctor() -> dict[str, object]:
    return {
        "protocol_version": 1,
        "read_only": True,
        "secure_key_store": True,
        "ephemeral_plaintext_cache": True,
        "modifies_wechat_app": False,
        "source_types": ["export_file"],
        "formats": ["json", "jsonl", "csv"],
        "persists_plaintext": False,
        "network_access": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "doctor":
        _emit(_doctor())
        return 0
    try:
        records = iter_records(args.source)
        limit = bounded_limit(args.limit, 50 if args.command == "sessions" else 100)
        if args.command == "sessions":
            items = sessions(records, limit)
        elif args.command == "history":
            items = history(records, args.session_id, args.start, args.end, limit)
        else:
            items = search(
                records, args.query, args.start, args.end, limit, args.session_id,
            )
        _emit({"ok": True, "items": items, "count": len(items)})
        return 0
    except ReaderFailure as exc:
        _emit({"ok": False, "error": str(exc), "reason_code": exc.reason_code})
        return 2
    except (OSError, ValueError) as exc:
        _emit({"ok": False, "error": str(exc), "reason_code": "invalid_scope"})
        return 2


if __name__ == "__main__":
    sys.exit(main())
