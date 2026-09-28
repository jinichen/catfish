#!/usr/bin/env python3
""".gitleaksignore 里每一条豁免都必须还指得到东西 (9/29)。

# 为什么有这个检查

9/24 起, 每晚的 Security audit (gitleaks 全历史扫描) 天天失败, 而每次 push
的扫描都是绿的。原因是 9/22 那次「交付包统一为 catfish 输出」的改名, 把
.gitleaksignore 里两条豁免的路径也一起改了:

    732b735…:delivery/dahua-poc/docs/…SOP.md:generic-api-key:129
 →  732b735…:delivery/catfish-poc/docs/…SOP.md:generic-api-key:129

可豁免指的是**历史上那个提交里的那个文件** —— 732b735 里这个文件就叫
delivery/dahua-poc/…, 历史不会跟着改名。改完之后这两条豁免什么都匹配不上,
那两处早已登记在案的历史发现重新冒出来。push 扫描只看新提交, 看不到它们;
只有每晚的全历史扫描看得到, 于是"老是这个错误"。

gitleaks 自己不会告诉你"这条豁免已经失效" —— 失效的豁免是静默的。所以在
gitleaks 之前先查一遍: 每条 `<commit>:<path>:<rule>:<line>` 的提交要存在、
那个提交里要有这个文件。

用法: python3 scripts/check_gitleaksignore.py   (需要完整历史: fetch-depth 0)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
IGNORE = REPO / ".gitleaksignore"


def entries(text: str) -> list[tuple[int, str, str]]:
    """[(行号, 提交, 路径)] —— 格式 <commit>:<path>:<rule>:<line>, 路径里可能有冒号, 从两头切。"""
    out = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        commit, rest = line.split(":", 1)
        path = rest.rsplit(":", 2)[0]
        out.append((number, commit, path))
    return out


def exists_at(commit: str, path: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(REPO), "cat-file", "-e", f"{commit}:{path}"],
        capture_output=True,
    ).returncode == 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    stale = [
        (number, commit, path)
        for number, commit, path in entries(IGNORE.read_text(encoding="utf-8"))
        if not exists_at(commit, path)
    ]
    if stale:
        print("❌ .gitleaksignore 里有豁免已经指不到东西 —— 它们静默失效, 每晚的全历史扫描会失败:\n")
        for number, commit, path in stale:
            print(f"  第 {number} 行: 提交 {commit[:10]} 里没有 {path}")
        print(
            "\n  豁免指的是**当时那个提交里的文件路径**。文件后来改名/移动了, 这里也不能跟着改。"
            "\n  用 `git show --name-only <提交>` 看它当时叫什么。"
        )
        return 1
    print(f"✓ .gitleaksignore {len(entries(IGNORE.read_text(encoding='utf-8')))} 条豁免都指得到当时的文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
