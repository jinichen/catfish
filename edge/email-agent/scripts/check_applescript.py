#!/usr/bin/env python3
"""把「邮件」App 用到的每一段 AppleScript 在本机**编译一遍** (不运行)。

# 为什么要有它 (9/28)

AppleScript 只有 macOS 能编译, 开发和 CI 都在 Linux 上 —— 单元测试只能看文本,
看不出语法错。1.0.44 引入的 `set m to message id N of mb` 在「邮件」的词典里
是个属性名 (RFC Message-ID), 编译器报 -2741, 读信 / 删信 / 标已读 / 存草稿 /
发送的脚本整段编译不过, 一直带到 1.0.48 才在员工机上暴露。

这个脚本在打 Mac 包时跑 (build-mac-resources.sh), 编不过就不许出包;
开发时也可以手动跑: `python3 edge/email-agent/scripts/check_applescript.py`。

只编译不运行: `osacompile` 读「邮件」的词典, 不给「邮件」发任何命令,
不碰任何邮件。不在 macOS 上 (没有 osacompile) 就说一声跳过, 返回 0。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

#: 占位符换成什么都行, 只要类型对 (数字 / 布尔 / 字符串) —— 编译看的是语法
PLACEHOLDERS = {
    "{ACCOUNT}": "Google", "{MSG_ID}": "123", "{OUT_ID}": "", "{FOLDER}": "Inbox",
    "{LIMIT}": "5", "{UNREAD_ONLY}": "false", "{QUERY}": "x", "{READ_FLAG}": "true",
    "{BODY_PATH}": "/tmp/body.txt", "{SOURCE_PATH}": "/tmp/source.eml",
    "{SUBJECT}": "s", "{TO}": "a@example.cn", "{CC}": "", "{BCC}": "",
}


def templates() -> dict[str, str]:
    """两个模块里所有 `_AS_` 开头的字符串常量, 已替换占位符。新加的模板自动纳入。"""
    from catfish_email.adapters import apple_mail_drafts, apple_mail_scripts

    found: dict[str, str] = {}
    for module in (apple_mail_scripts, apple_mail_drafts):
        for name, value in vars(module).items():
            if name.startswith("_AS_") and isinstance(value, str):
                for key, sub in PLACEHOLDERS.items():
                    value = value.replace(key, sub)
                left = re.findall(r"\{[A-Z_]+\}", value)
                if left:
                    raise SystemExit(f"{name}: 占位符没有替换值 {left} —— 在 PLACEHOLDERS 里补上")
                found[f"{module.__name__.rsplit('.', 1)[-1]}.{name}"] = value
    return found


def main() -> int:
    scripts = templates()
    if shutil.which("osacompile") is None:
        print(f"跳过: 这台机器没有 osacompile (不是 macOS), {len(scripts)} 段脚本未编译")
        return 0
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for name, source in sorted(scripts.items()):
            src = Path(tmp) / "script.applescript"
            src.write_text(source, encoding="utf-8")
            result = subprocess.run(
                ["osacompile", "-o", str(Path(tmp) / "script.scpt"), str(src)],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                print(f"  ✓ {name}")
                continue
            failed += 1
            print(f"  ✗ {name}: {result.stderr.strip()}")
            # osacompile 报的是字符位置 (如 4814:4825), 把那一段指出来
            match = re.search(r"(\d+):(\d+)", result.stderr)
            if match:
                a, b = int(match.group(1)), int(match.group(2))
                print(f"      …{source[max(0, a - 60):a]}【{source[a:b]}】{source[b:b + 40]}…")
    if failed:
        print(f"❌ {failed} 段 AppleScript 编译不过 —— 装到员工机上对应的邮件功能会整个用不了")
        return 1
    print(f"✓ {len(scripts)} 段 AppleScript 全部编译通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
