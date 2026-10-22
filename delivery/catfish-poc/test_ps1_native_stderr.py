#!/usr/bin/env python3
"""装机 .ps1 里, 重定向了 stderr 的外部命令必须包在 Invoke-Native 里 (10/22)。

Windows 自带的 powershell 是 5.1, 不认 $PSNativeCommandUseErrorActionPreference (7.3+ 才有)。
在 $ErrorActionPreference='Stop' 下, 外部命令只要往 stderr 写了字、而 stderr 被重定向
(2>$null / 2>&1 / *>), 就当场抛错退出 —— 10/22 Win11 现场: 收件箱版 wsl.exe --version 打了
一句「需要更新」, 装机崩在第一个检测上, 离线装新版 WSL 那一步根本没机会跑。

规则: 设了 'Stop' 的脚本里, 带 2>/*> 重定向的行要么在 Invoke-Native { ... } 里, 要么是给
Linux 跑的字符串 (单引号里的 bash 命令)。静态检查, 不需要 PowerShell。

跑法: python3 test_ps1_native_stderr.py   退出码 0 = 通过
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REDIRECT = re.compile(r"(2>\s*\$null|2>&1|\*>\s*\$null)")


def check(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8-sig")
    if "$ErrorActionPreference = 'Stop'" not in text:
        return []
    bad = []
    for n, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0]
        if not REDIRECT.search(code) or "Invoke-Native" in code:
            continue
        stripped = re.sub(r"'[^']*'", "''", code)  # 单引号字符串里的是给 bash 的
        if REDIRECT.search(stripped):
            bad.append(f"{path.relative_to(HERE)}:{n}: {line.strip()}")
    return bad


def main() -> int:
    files = [HERE / "setup.ps1", *sorted((HERE / "windows").glob("*.ps1"))]
    bad = [b for f in files for b in check(f)]
    if bad:
        print("❌ 这些外部命令重定向了 stderr 但没包 Invoke-Native (Windows PowerShell 5.1 上会直接崩):")
        print("\n".join("   " + b for b in bad))
        return 1
    print(f"✓ {len(files)} 个装机 .ps1: 重定向 stderr 的外部命令都包在 Invoke-Native 里")
    return 0


if __name__ == "__main__":
    sys.exit(main())
