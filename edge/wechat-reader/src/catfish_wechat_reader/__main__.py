"""CLI entry point for the Catfish local chat export reader."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from . import library
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
    # 9/23: 微信导出 ZIP 导入库。--library 由调用方 (Companion) 显式给, reader 不自己猜路径。
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--json", action="store_true")
    inspect.add_argument("--source", required=True)
    inspect.add_argument("--library")
    imp = commands.add_parser("import")
    imp.add_argument("--json", action="store_true")
    imp.add_argument("--source", required=True)
    imp.add_argument("--library", required=True)
    target = imp.add_mutually_exclusive_group()
    target.add_argument("--group-id")
    target.add_argument("--group-name")
    imp.add_argument("--self-name", help='空字符串表示「我不在这些发送人里」')
    render = commands.add_parser("render")
    render.add_argument("--json", action="store_true")
    render.add_argument("--source", required=True)
    render.add_argument("--self-name")
    render.add_argument("--max-chars", type=int, default=30000)
    groups = commands.add_parser("groups")
    groups.add_argument("--json", action="store_true")
    groups.add_argument("--library", required=True)
    update = commands.add_parser("update-group")
    update.add_argument("--json", action="store_true")
    update.add_argument("--library", required=True)
    update.add_argument("--group-id", required=True)
    update.add_argument("--name")
    update.add_argument("--self-name")
    remove = commands.add_parser("remove-group")
    remove.add_argument("--json", action="store_true")
    remove.add_argument("--library", required=True)
    remove.add_argument("--group-id", required=True)
    return parser


def _emit(payload: object) -> None:
    # 9/23: 输出里有中文 (群名 / 正文)。Windows 上管道 stdout 按系统代码页编码,
    # 直接 print 会 UnicodeEncodeError —— 调用方 (Companion / Tool Bridge) 一律按
    # UTF-8 解, 所以这里固定写 UTF-8 字节, 不依赖调用方有没有设 PYTHONIOENCODING。
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:  # pytest capsys 之类的文本流
        sys.stdout.write(data)
        return
    buffer.write(data.encode("utf-8"))
    buffer.flush()


def _doctor() -> dict[str, object]:
    return {
        "protocol_version": 1,
        "read_only": True,
        "secure_key_store": True,
        "ephemeral_plaintext_cache": True,
        "modifies_wechat_app": False,
        "source_types": ["export_file", "export_library"],
        "formats": ["json", "jsonl", "csv", "wechat_zip"],
        # 查询从不落明文; `import` 只在员工显式导入时把原包复制进库, 不建派生索引。
        "persists_plaintext": False,
        "copies_imported_exports": True,
        "network_access": False,
    }


def _groups(args: argparse.Namespace) -> dict[str, object]:
    items = library.list_groups(args.library)
    return {"items": items, "count": len(items)}


_LIBRARY_COMMANDS = {
    "inspect": lambda a: library.inspect(a.source, a.library),
    "import": lambda a: library.import_export(
        a.source, a.library, a.group_id, a.group_name, a.self_name,
    ),
    "render": lambda a: library.render(a.source, a.self_name, max(1000, min(a.max_chars, 200000))),
    "groups": _groups,
    "update-group": lambda a: library.update_group(a.library, a.group_id, a.name, a.self_name),
    "remove-group": lambda a: library.remove_group(a.library, a.group_id),
}


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "doctor":
        _emit(_doctor())
        return 0
    try:
        if args.command in _LIBRARY_COMMANDS:
            _emit({"ok": True, **_LIBRARY_COMMANDS[args.command](args)})
            return 0
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
