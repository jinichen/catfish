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


def check_swallowed_output(path: Path) -> list[str]:
    """`& 外部命令` 后紧跟 `return $LASTEXITCODE` 的函数, 外部命令输出必须 | Out-Host / Out-Null。

    10/22: setup-wsl.ps1 的 InDistro 没接走 wsl.exe 的输出 —— PowerShell 把它们全算成函数返回值:
    setup.sh 的报错说明 (连同只显示一次的加密主密钥) 屏幕上看不到, 返回值成了一串行,
    `$rc -ne 0` 恒真, setup.sh 成功了也报「setup.sh 失败」。
    """
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    bad = []
    for i, line in enumerate(lines[:-1]):
        code = line.split("#", 1)[0].strip()
        nxt = lines[i + 1].split("#", 1)[0].strip()
        if code.startswith("& ") and nxt == "return $LASTEXITCODE" and "| Out-" not in code:
            bad.append(f"{path.relative_to(HERE)}:{i + 1}: {line.strip()}  ← 输出会变成返回值, 加 | Out-Host")
    return bad


def main() -> int:
    files = [HERE / "setup.ps1", *sorted((HERE / "windows").glob("*.ps1"))]
    bad = [b for f in files for b in check(f) + check_swallowed_output(f)]
    if bad:
        print("❌ 装机 .ps1 里有会在 Windows PowerShell 5.1 上出错的外部命令写法:")
        print("\n".join("   " + b for b in bad))
        return 1
    print(f"✓ {len(files)} 个装机 .ps1: 重定向 stderr 的外部命令都包在 Invoke-Native 里, 函数里的外部命令输出都接走了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
