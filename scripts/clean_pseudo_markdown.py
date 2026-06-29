#!/usr/bin/env python3
"""P3.5.134 (6/30 鸿波"注释污染清理"): 清掉 'xxx' 伪强调 markdown.

军规起源: 鸿波 P3.5.137 sprint 立 "不许再输出: 这些乱飞的东西". 历史代码
累积 464 处违规, hygiene 清理.

策略:
  pattern: 真\*\*([^*]+?)\*\* → \1 (只删包装符号, 保留 xxx 内容)
  迭代替换: 嵌套 'AB' 多次 sub 直到稳定
  扫: .rs / .ts / .tsx / .py 注释 + 字符串字面值 (log / error 都清)
  排除: venv / node_modules / target / .git / dist / __pycache__ / venv

合法中文真字 ('真的' / '真正' / '真值' / '真有') 不带 ** 不动.

用法:
  python3 scripts/clean_pseudo_markdown.py --dry-run     # 看 sample 不写
  python3 scripts/clean_pseudo_markdown.py --apply       # 真改文件
  python3 scripts/clean_pseudo_markdown.py --verify      # 改后 grep 残留
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# P3.5.147 (6/30 鸿波): 扩展到 .toml/.yaml/.yml/.md/.sh — P3.5.142 漏了这些类型,
# Cargo.toml / roles.yaml / docs/*.md / scripts/*.sh 都有伪强调残留.
EXTENSIONS = {".rs", ".ts", ".tsx", ".py", ".toml", ".yaml", ".yml", ".md", ".sh"}
EXCLUDE_DIRS = {
    "venv",
    "node_modules",
    "target",
    ".git",
    "dist",
    "__pycache__",
    "build",
    "_archive",  # P3.5.147: hermes-fork/patches/_archive 历史归档不动
}
# P3.5.147: 单文件名排除 — CHANGELOG.md 是历史 log, 不清.
EXCLUDE_FILENAMES = {"CHANGELOG.md"}

# 非贪婪 + 非星号字符. 单次 sub 不处理嵌套, 外层 loop 迭代.
PATTERN = re.compile(r"真\*\*([^*]+?)\*\*")


def clean_text(content: str) -> tuple[str, int]:
    """迭代 sub 直到稳定. 返 (新内容, 替换次数)."""
    total = 0
    for _ in range(20):  # safety bound, 实际 1-3 次就稳
        new_content, n = PATTERN.subn(r"\1", content)
        if n == 0:
            break
        total += n
        content = new_content
    return content, total


def iter_files(root: Path):
    """递归遍历, 跳排除目录 + 排除文件名."""
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in EXTENSIONS:
            continue
        if any(d in path.parts for d in EXCLUDE_DIRS):
            continue
        if path.name in EXCLUDE_FILENAMES:
            continue
        yield path


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="sample 不写")
    g.add_argument("--apply", action="store_true", help="真改文件")
    g.add_argument("--verify", action="store_true", help="grep 残留 check")
    ap.add_argument(
        "--root",
        type=Path,
        default=REPO,
        help=f"扫描根 (默认 {REPO})",
    )
    ap.add_argument(
        "--sample",
        type=int,
        default=5,
        help="dry-run 时每个文件显几条 sample diff (默认 5)",
    )
    args = ap.parse_args()

    total_files_dirty = 0
    total_subs = 0

    for path in iter_files(args.root):
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as e:
            print(f"skip {path}: {e}", file=sys.stderr)
            continue

        new_content, n = clean_text(content)
        if n == 0:
            continue

        total_files_dirty += 1
        total_subs += n
        rel = path.relative_to(args.root)

        if args.verify:
            # 改后还有残留就报
            residue = PATTERN.findall(new_content)
            if residue:
                print(f"⚠ {rel}: 残留 {len(residue)} 处 {residue[:3]}")
            continue

        if args.dry_run:
            # sample diff: 显前 N 行改动前后对比
            old_lines = content.splitlines()
            new_lines = new_content.splitlines()
            shown = 0
            for i, (a, b) in enumerate(zip(old_lines, new_lines)):
                if a == b:
                    continue
                print(f"\n--- {rel}:{i + 1}")
                print(f"  - {a}")
                print(f"  + {b}")
                shown += 1
                if shown >= args.sample:
                    break
            print(f"  ({n} 处 sub in {rel})")
        elif args.apply:
            path.write_text(new_content, encoding="utf-8")
            print(f"clean {rel}: {n} 处")

    print(f"\n total: {total_files_dirty} files, {total_subs} subs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
