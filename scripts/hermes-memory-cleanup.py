#!/usr/bin/env python3
"""hermes-memory-cleanup — 列 / 删 hermes USER.md + MEMORY.md 的 entries.

# 背景

仪表盘 "我的 hermes memory" 卡显示了 USER.md / MEMORY.md 的总览, 但点删 / 看
内容的体验差 (entry 可能很长, dashboard 截断). 这脚本给员工本地一个清爽的
终端入口, 1 分钟扫一遍 + 删不要的.

跟仪表盘 "清空印象" 按钮的区别:
  - "清空印象" 清的是 ~/.catfish/employee_journal.md (catfish-memory plugin 自动写的, 60KB+)
  - 本脚本管的是 ~/.hermes/memories/USER.md + MEMORY.md (hermes 原生, LLM 显式调 memory_update 写的)
  这两套**完全独立**, 不会互相影响.

# 用法

  python3 hermes-memory-cleanup.py                # 列所有 entry (默认行为)
  python3 hermes-memory-cleanup.py list           # 同上, 显式
  python3 hermes-memory-cleanup.py list USER      # 只列 USER.md
  python3 hermes-memory-cleanup.py list MEMORY    # 只列 MEMORY.md
  python3 hermes-memory-cleanup.py show USER 3    # 看 USER.md 第 3 条完整内容
  python3 hermes-memory-cleanup.py delete USER 3  # 删 USER.md 第 3 条 (含二次确认 + 自动备份)
  python3 hermes-memory-cleanup.py --json         # 机器可读 JSON 输出

# 安全

- 删之前永远把原文件 cp 成 .bak.<timestamp>
- 删之后立刻 print 备份路径让员工知道怎么恢复
- 不动文件 chmod / 权限 / 属主
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

# hermes ENTRY_DELIMITER — 跟 hermes/agent/memory_provider.py 里写死的对齐.
# 改这个就跟 hermes 不兼容, 不要乱改.
ENTRY_DELIMITER = "\n§\n"

# 默认 cap (跟 companion-app src-tauri/src/commands/hermes_memory.rs:75 对齐).
# 真实值由 hermes config.yaml memory.user_char_limit / memory_char_limit 控制,
# 这里只是给 UI 显示百分比用; 不影响 hermes 真实行为.
DEFAULT_USER_CAP = 1375
DEFAULT_MEMORY_CAP = 2200

# 打 preview 时单行最多多少字符 (防一条 1k 字符 entry 把屏幕灌满)
PREVIEW_CHARS = 100


def _memories_dir() -> Path:
    return Path.home() / ".hermes" / "memories"


def _target_path(target: str) -> Path:
    """target 大小写不敏感 → 'USER' / 'MEMORY' → USER.md / MEMORY.md."""
    t = target.strip().upper()
    if t not in {"USER", "MEMORY"}:
        raise ValueError(f"target 只能是 USER / MEMORY, 你给的 {target!r}")
    return _memories_dir() / f"{t}.md"


def _cap_for(target: str) -> int:
    return DEFAULT_USER_CAP if target.upper() == "USER" else DEFAULT_MEMORY_CAP


def _parse_entries(content: str) -> List[str]:
    """跟 companion hermes_memory.rs parse_entries 同算法: § 分, trim, 去空."""
    if not content:
        return []
    return [s.strip() for s in content.split(ENTRY_DELIMITER) if s.strip()]


def _load(target: str) -> List[str]:
    """读 USER.md / MEMORY.md, parse 成 entries 列表. 文件不存在 → []."""
    path = _target_path(target)
    if not path.exists():
        return []
    try:
        return _parse_entries(path.read_text(encoding="utf-8"))
    except OSError as e:
        print(f"读 {path} 失败: {e}", file=sys.stderr)
        return []


def _save(target: str, entries: List[str]) -> None:
    """覆盖写. caller 应该已经备份了."""
    path = _target_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = ENTRY_DELIMITER.join(entries)
    if entries and not body.endswith("\n"):
        body += "\n"
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


def _backup(target: str) -> Optional[Path]:
    """删之前备份. 返备份路径 (caller print). 文件不存在返 None."""
    path = _target_path(target)
    if not path.exists():
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = path.with_suffix(f".md.bak.{ts}")
    shutil.copy2(path, bak)
    return bak


def _preview(text: str, max_chars: int = PREVIEW_CHARS) -> str:
    """单行 preview — 换行替成空格, 截断到 max_chars + '…'."""
    flat = " ".join(text.split())  # 折叠所有换行 / 多空白
    if len(flat) <= max_chars:
        return flat
    return flat[:max_chars] + "…"


def _color(s: str, code: str) -> str:
    """ANSI 着色. NO_COLOR env 关掉."""
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return s
    return f"\033[{code}m{s}\033[0m"


def _render_entry_line(idx: int, target: str, entry: str) -> str:
    """渲染一行 entry: 序号 + 字符数 + cap% + preview."""
    cap = _cap_for(target)
    chars = len(entry)
    pct = (chars / cap) * 100 if cap else 0
    pct_color = "31" if pct > 80 else ("33" if pct > 50 else "32")  # red/yellow/green
    pct_str = _color(f"{pct:5.1f}%", pct_color)
    return (
        f"  [{idx:>2}] {chars:>5} chars ({pct_str} of {cap}) "
        f"│ {_preview(entry)}"
    )


def _cmd_list(args) -> int:
    targets = [args.target] if args.target else ["USER", "MEMORY"]

    if args.json:
        out = {}
        for t in targets:
            entries = _load(t)
            cap = _cap_for(t)
            out[t] = [
                {"index": i + 1, "chars": len(e), "cap": cap, "preview": _preview(e)}
                for i, e in enumerate(entries)
            ]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    for t in targets:
        entries = _load(t)
        path = _target_path(t)
        cap = _cap_for(t)
        total_chars = sum(len(e) for e in entries)
        size = path.stat().st_size if path.exists() else 0
        header = (
            f"\n{_color('▼', '36')} {_color(t + '.md', '1;36')}  "
            f"({len(entries)} 条, 共 {total_chars} chars / {size} bytes 在盘, cap {cap}/条)"
        )
        print(header)
        print(f"  {path}")
        if not entries:
            print(_color("  (空)", "90"))
            continue
        for i, e in enumerate(entries, 1):
            print(_render_entry_line(i, t, e))

    print()
    print(_color("提示:", "1") + "  python3 hermes-memory-cleanup.py show USER 3   # 看 USER 第 3 条完整内容")
    print(_color("    ", "1") + "  python3 hermes-memory-cleanup.py delete USER 3 # 删 USER 第 3 条")
    return 0


def _cmd_show(args) -> int:
    entries = _load(args.target)
    if not (1 <= args.index <= len(entries)):
        print(f"index {args.index} 越界 ({args.target} 只有 {len(entries)} 条)", file=sys.stderr)
        return 1
    entry = entries[args.index - 1]
    cap = _cap_for(args.target)
    print(f"{_color('▼', '36')} {_color(args.target + '.md', '1;36')} 第 {args.index} 条 "
          f"({len(entry)} chars, {len(entry) / cap * 100:.1f}% of cap {cap})")
    print()
    print(entry)
    print()
    return 0


def _cmd_delete(args) -> int:
    entries = _load(args.target)
    if not (1 <= args.index <= len(entries)):
        print(f"index {args.index} 越界 ({args.target} 只有 {len(entries)} 条)", file=sys.stderr)
        return 1
    entry = entries[args.index - 1]
    cap = _cap_for(args.target)

    print(f"{_color('准备删', '31;1')} {args.target}.md 第 {args.index} 条 "
          f"({len(entry)} chars, {len(entry)/cap*100:.1f}% of cap {cap}):")
    print()
    print(_color(entry, "90"))
    print()

    if not args.yes:
        reply = input(_color("确认删? [y/N] ", "33")).strip().lower()
        if reply not in {"y", "yes"}:
            print("取消, 没动文件.")
            return 0

    bak = _backup(args.target)
    new_entries = [e for i, e in enumerate(entries) if i != args.index - 1]
    _save(args.target, new_entries)
    print(_color("✓ 已删", "32") + f", 剩 {len(new_entries)} 条.")
    if bak:
        print(f"  备份: {bak}")
        print(f"  恢复方法: cp '{bak}' '{_target_path(args.target)}'")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hermes-memory-cleanup",
        description="hermes USER.md / MEMORY.md 扫描与清理工具",
    )
    sub = p.add_subparsers(dest="command")

    list_p = sub.add_parser("list", help="列出所有 entry (默认)")
    list_p.add_argument("target", nargs="?", choices=["USER", "MEMORY", "user", "memory"],
                       help="只看 USER 或 MEMORY (省略 = 都看)")
    list_p.add_argument("--json", action="store_true", help="JSON 输出 (机器可读)")
    list_p.set_defaults(func=_cmd_list)

    show_p = sub.add_parser("show", help="看某条完整内容")
    show_p.add_argument("target", choices=["USER", "MEMORY", "user", "memory"])
    show_p.add_argument("index", type=int)
    show_p.set_defaults(func=_cmd_show)

    delete_p = sub.add_parser("delete", help="删某条 (含二次确认 + 自动备份)")
    delete_p.add_argument("target", choices=["USER", "MEMORY", "user", "memory"])
    delete_p.add_argument("index", type=int)
    delete_p.add_argument("-y", "--yes", action="store_true", help="跳过二次确认 (脚本里用)")
    delete_p.set_defaults(func=_cmd_delete)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        # 默认行为 = list (没给子命令时)
        args = parser.parse_args(["list"])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
