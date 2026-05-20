"""catfish-journal CLI — hermes 通过 terminal 工具调用.

子命令:
  list                列未完成 TODO (JSON / markdown / human 三种格式)
  done   --line N --hint TEXT          mark TODO 完成
  delete --line N --hint TEXT          删 TODO 整行
  add    TEXT [--section SEC] [--done] 追加新 TODO (--done 写 [x] 历史 v0.1.9)
  sync   --stdin                       批量 sync (jsonl stdin) — catfish-todo-sync 用 v0.1.10

红线 (BL-CENTRAL-EDGE-BOUNDARY 5/17):
  - 只读写 ~/.catfish/employee_journal.md, 不碰中央端
  - 失败原子化: read → modify in memory → write back, 不留中间状态
  - 双重定位 line + hint 防误伤
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from . import core


JOURNAL_PATH = Path.home() / ".catfish" / "employee_journal.md"


def _read_journal() -> str:
    """读 journal 全文, 没文件返空字符串."""
    if not JOURNAL_PATH.exists():
        return ""
    return JOURNAL_PATH.read_text(encoding="utf-8")


def _write_journal(content: str) -> None:
    """写 journal. 自动建 ~/.catfish 目录."""
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    JOURNAL_PATH.write_text(content, encoding="utf-8")


# ── 子命令 implementations ────────────────────────────────────────────


def cmd_list(args: argparse.Namespace) -> int:
    content = _read_journal()
    todos = core.extract_todos(content)
    if args.limit:
        # 截取最近 N 条 (倒数, 因为 journal 是按时间累加)
        todos = todos[-args.limit :]

    if args.format == "json":
        print(json.dumps([asdict(t) for t in todos], ensure_ascii=False, indent=2))
    elif args.format == "markdown":
        if not todos:
            print("_journal 没未完成事项_")
        else:
            print(f"**未完成 TODO · {len(todos)} 件**\n")
            print("| line | text | section | source |")
            print("|------|------|---------|--------|")
            for t in todos:
                print(
                    f"| {t.line} | {t.text} | {t.section or '-'} | {t.source} |"
                )
    else:  # human
        if not todos:
            print("journal 没未完成事项")
        else:
            for t in todos:
                marker = "☐" if t.source == "checkbox" else "·"
                section = f"  [{t.section}]" if t.section else ""
                print(f"  {marker} L{t.line} {t.text}{section}")
    return 0


def cmd_done(args: argparse.Namespace) -> int:
    content = _read_journal()
    if not content:
        print(f"✗ journal 不存在: {JOURNAL_PATH}", file=sys.stderr)
        return 3

    try:
        new_content = core.mark_todo_done(content, args.line, args.hint)
    except core.JournalEditError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2

    _write_journal(new_content)
    print(f"✅ 标已完成: line {args.line} {args.hint!r}")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    content = _read_journal()
    if not content:
        print(f"✗ journal 不存在: {JOURNAL_PATH}", file=sys.stderr)
        return 3

    try:
        new_content, deleted = core.delete_todo(content, args.line, args.hint)
    except core.JournalEditError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2

    _write_journal(new_content)
    print(f"🗑 已删: {deleted.strip()[:60]!r}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    """Batch sync TODO statuses (BL-CATFISH-TODO-SYNC v0.1.10, 5/20).

    stdin 接 JSON 一行一 op (jsonl):
      {"status": "pending|in_progress|completed|cancelled", "content": "..."}

    一次读 journal → 处理所有 ops in-memory → 一次写回. 比 N 次 subprocess
    省 N-1 次 Python 启动开销 (50-200ms 每次).

    适用 caller: catfish-todo-sync plugin monkey-patched TodoStore.write
    (hermes 0.13 内置 todo tool 每轮 sync_turn 拿全量 todos list).

    Returns:
        exit 0: 所有 op 处理完 (含 skip / error), 写 journal
        exit 2: 非 JSON 输入 / 致命读 journal 失败
    """
    raw = sys.stdin.read()
    ops = []
    for line_no, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            ops.append(json.loads(line))
        except json.JSONDecodeError as e:
            print(f"✗ line {line_no} 非 JSON: {e}", file=sys.stderr)
            return 2

    content = _read_journal()
    new_content = content
    stats = {
        "add": 0,
        "done": 0,
        "delete": 0,
        "add_done": 0,
        "skip": 0,
        "error": 0,
    }

    for op in ops:
        status = str(op.get("status", "pending")).strip().lower()
        text = str(op.get("content", "")).strip()
        if not text:
            stats["skip"] += 1
            continue
        try:
            if status in ("pending", "in_progress"):
                new_content = core.add_todo(new_content, text)
                stats["add"] += 1
            elif status == "completed":
                # 从最新 new_content (含已 add 的) 找 line
                located = None
                for t in core.extract_todos(new_content):
                    if t.text.strip() == text:
                        located = t
                        break
                if located:
                    new_content = core.mark_todo_done(
                        new_content, located.line, located.text[:30]
                    )
                    stats["done"] += 1
                else:
                    # journal 找不到 → 补 [x] 历史 (v0.1.9 同语义)
                    new_content = core.add_todo(new_content, text, done=True)
                    stats["add_done"] += 1
            elif status == "cancelled":
                # 找 TODO 行 (checkbox 或 inline) 删整行
                target_line = None
                for t in core.extract_todos(new_content):
                    if t.text.strip() == text:
                        target_line = t.line
                        break
                if target_line:
                    new_content, _ = core.delete_todo(
                        new_content, target_line, text[:30]
                    )
                    stats["delete"] += 1
                else:
                    stats["skip"] += 1
            else:
                # 未知 status
                stats["skip"] += 1
        except core.JournalEditError:
            stats["error"] += 1
            continue

    if new_content != content:
        _write_journal(new_content)
    print(json.dumps(stats, ensure_ascii=False))
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    content = _read_journal()
    try:
        new_content = core.add_todo(
            content, args.text, args.section, done=args.done
        )
    except core.JournalEditError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2

    _write_journal(new_content)
    section_info = f" [section: {args.section}]" if args.section else ""
    state_emoji = "✅" if args.done else "📝"
    state_label = "(已完成)" if args.done else ""
    print(f"{state_emoji} 已加{state_label}: {args.text.strip()[:60]!r}{section_info}")
    return 0


# ── argparse 入口 ────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="catfish-journal",
        description="改员工 ~/.catfish/employee_journal.md TODO. hermes skill 通过 terminal 工具调用.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # list
    p_list = sub.add_parser("list", help="列未完成 TODO")
    p_list.add_argument("--limit", type=int, default=None, help="最近 N 条 (默认全部)")
    p_list.add_argument(
        "--format",
        choices=["json", "markdown", "human"],
        default="json",
        help="输出格式 (默认 json, 给 hermes parse 用; human 给员工命令行 debug)",
    )
    p_list.set_defaults(func=cmd_list)

    # done
    p_done = sub.add_parser("done", help="标 TODO 已完成 (- [ ] → - [x])")
    p_done.add_argument("--line", type=int, required=True, help="1-based 行号")
    p_done.add_argument(
        "--hint", required=True, help="该行 text substring (双重定位防误伤)"
    )
    p_done.set_defaults(func=cmd_done)

    # delete
    p_del = sub.add_parser("delete", help="删 TODO 整行")
    p_del.add_argument("--line", type=int, required=True, help="1-based 行号")
    p_del.add_argument("--hint", required=True, help="该行 text substring")
    p_del.set_defaults(func=cmd_delete)

    # sync (v0.1.10 batch)
    p_sync = sub.add_parser(
        "sync",
        help="批量 sync TODO statuses (jsonl stdin), catfish-todo-sync plugin 用",
    )
    p_sync.add_argument(
        "--stdin",
        action="store_true",
        help="从 stdin 读 jsonl ops (一行一 {status, content})",
    )
    p_sync.set_defaults(func=cmd_sync)

    # add
    p_add = sub.add_parser("add", help="追加新 TODO")
    p_add.add_argument("text", help="TODO 文本")
    p_add.add_argument(
        "--section",
        default=None,
        help="所属段标题 (默认追加到文末; 给定则插到该段尾)",
    )
    p_add.add_argument(
        "--done",
        action="store_true",
        help="加 `- [x]` 已完成行 (历史记录场景, BL-CATFISH-TODO-SYNC v0.1.9 用), 默认 `- [ ]`",
    )
    p_add.set_defaults(func=cmd_add)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
