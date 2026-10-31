#!/usr/bin/env python3
"""换机迁移清单不能漏 (docs/DEVICE-MIGRATION-DESIGN.md §4, 10/31)。

代码里引用到的每个 `~/.catfish/<名字>` (第一层), 都必须在
src-tauri/src/commands/device_migration/catalog.rs 里有归类 (必带 / 可选 / 合并 / 重建 / 凭证)。

为什么要这道检查: 迁移包只带登记过的东西。谁新加了一个数据文件却没登记, 员工换机时它就悄悄
丢了 —— 以前导出页会列「未归类」提醒员工, 但员工看到了也做不了什么 (10/31 鸿波: 列太细反而乱),
所以把关挪到这里, 在合进 main 之前拦住。

用法: python3 edge/companion-app/scripts/check_migration_catalog.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

EDGE = Path(__file__).resolve().parents[2]
CATALOG = EDGE / "companion-app/src-tauri/src/commands/device_migration/catalog.rs"
SCAN = [
    EDGE / "companion-app/src-tauri/src",
    EDGE / "companion-app/src",
    EDGE / "hermes-plugins",
    EDGE / "tool-bridge/src",
    EDGE / "catfish-cli",
    EDGE / "local-search",
    EDGE / "email-agent/src",
]
SKIP_PARTS = {"node_modules", "venv", ".venv", "target", "__pycache__", "device_migration", "tests", "dist"}
EXTS = {".rs", ".ts", ".tsx", ".py"}

# 各种拼 ~/.catfish/<名字> 的写法 (只取第一层名字)
PATTERNS = [
    re.compile(r"\.catfish[/\\]+([A-Za-z0-9_][A-Za-z0-9_.\-]*)"),
    re.compile(r'join\("\.catfish"\)\s*\.join\("([^"/\\]+)"\)'),
    re.compile(r'catfish_(?:home|dir)\(\)\??\s*\.join\("([^"/\\]+)"\)'),
    re.compile(r'(?:_?catfish_home|CATFISH_HOME|catfish_dir|CATFISH_DIR)(?:\(\))?\s*/\s*"([^"/\\]+)"'),
    re.compile(r'"\.catfish"\s*/\s*"([^"/\\]+)"'),
]


def rules():
    text = CATALOG.read_text(encoding="utf-8")
    sec = text.split("const CATFISH")[1].split("];")[0]
    return re.findall(r'\((Exact|Prefix|Suffix|Contains)\("([^"]*)"\), (\w+)', sec)


def classify(name: str, rs) -> str:
    if name.endswith(("-wal", "-shm", "-journal")):
        return "Rebuild"
    for kind, pat, cls in rs:
        if (
            (kind == "Exact" and name == pat)
            or (kind == "Prefix" and name.startswith(pat))
            or (kind == "Suffix" and name.endswith(pat))
            or (kind == "Contains" and pat in name)
        ):
            return cls
    return "Unclassified"


def main() -> int:
    rs = rules()
    missing: dict[str, str] = {}
    for root in SCAN:
        for f in root.rglob("*"):
            if f.suffix not in EXTS or not f.is_file() or SKIP_PARTS & set(f.parts):
                continue
            if f.name.endswith((".test.ts", ".test.tsx")) or f.name.startswith("test_") or f.name.endswith("_tests.rs"):
                continue
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                for p in PATTERNS:
                    for name in p.findall(line):
                        name = name.rstrip(".")
                        if not name or "{" in name or "<" in name or name in {".", ".."}:
                            continue
                        if classify(name, rs) == "Unclassified" and name not in missing:
                            missing[name] = f"{f.relative_to(EDGE.parent)}:{i}"
    if missing:
        print("❌ 换机迁移清单漏登记 —— 这些 ~/.catfish/ 下的东西代码在用, 但 catalog.rs 里没归类:")
        for name, where in sorted(missing.items()):
            print(f"   {name:32s} ({where})")
        print("\n   在 edge/companion-app/src-tauri/src/commands/device_migration/catalog.rs 的 CATFISH 表里加一行:")
        print("   员工的积累 → Required; 大文件 → Optional; 缓存 / 可重建 → Rebuild; 凭证 → Secret。")
        return 1
    print("✓ 换机迁移清单覆盖了代码里引用的所有 ~/.catfish/ 条目")
    return 0


if __name__ == "__main__":
    sys.exit(main())
