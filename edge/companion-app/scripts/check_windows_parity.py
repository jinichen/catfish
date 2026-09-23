#!/usr/bin/env python3
"""Windows 与 macOS 同一体验 —— 把"只在 mac 上成立"的写法挡在合并之前 (9/23).

# 为什么有这个文件

9/23 鸿波第一台干净装的 Windows, 一晚上撞了十几件事: 聊天"未知错误"、切
工作台弹黑框、日志每 3 秒刷一行 404、tool-bridge 永远"没装"、通知不弹、
快捷键抢输入法……逐个追下去, **每一件在代码里都有人写过**:

    "Windows 暂不处理 (Companion 当前只 macOS)"
    "notify: 当前平台未实现 (只有 macOS)"
    "全 Rust 代码里这样的还有 40 多处, 没在这次一起动"

写下来了, 没有任何东西执行它。这个脚本把那几类写法变成红灯。

# 查什么 (每条都对应 9/23 的一次真实故障)

  R1  cfg(macos | linux) 把 Windows 排除在外      → 凭证同步整块不在 Windows 编译
  R2  裸 Command::new 起 console 程序               → 切工作台弹黑框
  R3  Rust 里拼 ".hermes" 路径                        → Windows 上 hermes 在 %LOCALAPPDATA%
  R4  只读 HOME 不读 USERPROFILE                      → Windows 没有 HOME
  R5  Python 里 Path.home() / ".hermes"               → 同 R3, 插件和 tool-bridge
  R6  PowerShell 写文件不指定编码                     → PS 5.1 默认 GBK, 把 config.yaml 写坏
  R7  两个平台的安装包资源清单对不上                  → tool-bridge 两边都没打进包
  R8  非 mac 分支写着"未实现 / TODO / 只有 macOS"     → 通知 / 重启 hermes 在 Windows 静默不做

# 例外

真的只能在某个平台做的 (osascript 调 Reminders.app 之类), 在那一行或上一行写
`windows-parity: <为什么>`。要写理由 —— 没理由的例外就是下一个 9/23。

用法:  python3 edge/companion-app/scripts/check_windows_parity.py
退出码: 0 干净 / 1 有问题 (逐条打印位置)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent                      # edge/companion-app
EDGE = APP.parent                      # edge
RUST = APP / "src-tauri" / "src"
MARK = "windows-parity:"

problems: list[str] = []


def bad(rule: str, where: str, msg: str) -> None:
    problems.append(f"  [{rule}] {where}\n        {msg}")


def code_part(line: str, lang: str) -> str:
    """去掉行内注释。只做够用的近似 (字符串里的 // 或 # 会误切, 偏保守 = 少报)。"""
    if lang == "rs":
        s = line.strip()
        if s.startswith("//"):
            return ""
        return line.split("//", 1)[0] if '"//' not in line and "://" not in line else line
    s = line.strip()
    if s.startswith("#"):
        return ""
    return line


def excepted(lines: list[str], i: int) -> bool:
    return MARK in lines[i] or (i > 0 and MARK in lines[i - 1])


def rust_files():
    for p in sorted(RUST.rglob("*.rs")):
        if p.name.endswith("_tests.rs") or "/tests/" in p.as_posix():
            continue
        text = p.read_text(encoding="utf-8")
        # 测试模块一般在文件末尾, 从 #[cfg(test)] mod 开始往后不查
        m = re.search(r"^#\[cfg\(test\)\]\s*\n(\s*#\[.*\]\s*\n)*\s*mod\s", text, re.M)
        if m:
            text = text[: m.start()]
        yield p, text.split("\n")


# ── R1 / R2 / R3 / R4 / R8 · Rust ─────────────────────────────────

# 这些程序只存在于 mac / Linux (或本身就是 GUI 程序, 不会弹控制台), 出现即说明
# 在平台分支里, 不需要 background_command。
PLATFORM_TOOLS = {
    "osascript", "pgrep", "pkill", "kill", "open", "xdg-open", "launchctl",
    "/bin/launchctl", "/usr/bin/id", "which", "explorer", "rundll32.exe", "notify-send",
}
R2_RE = re.compile(r'(?<![\w:])(?:std::process::|tokio::process::)?Command::new\(\s*("([^"]*)"|[^)]*)')
R8_RE = re.compile(r"未实现|暂不支持|暂不处理|TODO.*(Win|win)|只有 macOS|只 macOS|非 macOS 不")

for path, lines in rust_files():
    rel = path.relative_to(APP).as_posix()
    for i, raw in enumerate(lines):
        line = code_part(raw, "rs")
        if not line.strip():
            continue
        where = f"{rel}:{i + 1}"

        # R1
        if re.search(r'cfg\(\s*any\(\s*target_os\s*=\s*"macos"\s*,\s*target_os\s*=\s*"linux"', line) \
                and "windows" not in line and not excepted(lines, i):
            bad("R1", where, "cfg(macos|linux) 把 Windows 排除了。要么三平台一起, 要么写 windows-parity: 理由")

        # R2
        if rel != "src-tauri/src/services/process.rs":
            for m in R2_RE.finditer(line):
                lit = m.group(2)
                if lit is not None and lit in PLATFORM_TOOLS:
                    continue
                if not excepted(lines, i):
                    bad("R2", where, "裸 Command::new —— Windows 上 console 程序会弹黑框, 用 services::process::background_command")

        # R3
        if rel != "src-tauri/src/services/catfish_paths.rs" and re.search(r'"\.hermes[/"\\]', line) \
                and not excepted(lines, i):
            bad("R3", where, 'Rust 里拼 ".hermes" 路径 —— Windows 上是 %LOCALAPPDATA%\\hermes, 用 catfish_paths::hermes_home()')

        # R4
        if rel != "src-tauri/src/util/paths.rs" and re.search(r'var(?:_os)?\("HOME"\)', line):
            window = "\n".join(lines[i : i + 3])
            if "USERPROFILE" not in window and not excepted(lines, i):
                bad("R4", where, "只读 HOME —— Windows 没有 HOME, 用 util::paths::home_env()")

        # R8
        if re.search(r'cfg\(\s*not\(\s*target_os\s*=\s*"macos"\s*\)\s*\)', line):
            body = "\n".join(lines[i + 1 : i + 8])
            if R8_RE.search(body) and not excepted(lines, i):
                bad("R8", where, "非 mac 分支是个空架子 (未实现/TODO/只有 macOS) —— Windows 员工拿到的是静默不做")

# ── R5 · Python (tool-bridge / local-search / hermes 插件) ──────────

PY_ROOTS = [EDGE / "tool-bridge" / "src", EDGE / "local-search" / "src"] + [
    d for d in (EDGE / "hermes-plugins").iterdir() if d.is_dir()
]
R5_RE = re.compile(r'\bPath\.home\(\)\s*/\s*["\']\.hermes["\']|expanduser\(\s*["\']~/\.hermes')
for root in PY_ROOTS:
    for p in sorted(root.rglob("*.py")):
        parts = set(p.parts)
        if parts & {"tests", "venv", ".venv", "site-packages"} or p.name == "hermes_paths.py":
            continue
        lines = p.read_text(encoding="utf-8").split("\n")
        in_doc = False
        for i, raw in enumerate(lines):
            if raw.count('"""') % 2 == 1:
                in_doc = not in_doc
                continue
            if in_doc:
                continue
            line = code_part(raw, "py")
            if R5_RE.search(line) and not excepted(lines, i):
                bad("R5", f"{p.relative_to(EDGE.parent).as_posix()}:{i + 1}",
                    'Path.home() / ".hermes" —— Windows 上 hermes 在 %LOCALAPPDATA%\\hermes')

# ── R6 · PowerShell 写文件要指定编码 ─────────────────────────────────

REPO = EDGE.parent
for p in sorted(list(APP.rglob("*.ps1")) + list((REPO / "delivery").rglob("*.ps1"))):
    if "node_modules" in p.parts:
        continue
    lines = p.read_text(encoding="utf-8", errors="replace").split("\n")
    for i, raw in enumerate(lines):
        line = raw.split("#", 1)[0] if not raw.lstrip().startswith("<#") else ""
        if re.search(r"\b(Set-Content|Out-File|Add-Content)\b", line) and "-Encoding" not in line \
                and not excepted(lines, i):
            bad("R6", f"{p.relative_to(REPO).as_posix()}:{i + 1}",
                "PowerShell 写文件没带 -Encoding —— Windows PowerShell 5.1 默认按系统代码页 (GBK) 写, "
                "YAML / .env 里的中文会坏 (9/23 hermes config.yaml 就是这么坏的)")

# ── R7 · 两个平台的安装包资源要对得上 ───────────────────────────────

# 只在一个平台有的资源 (安装器本身 / 平台专属运行时), 其余必须两边都有
ONLY_WINDOWS = {"install.ps1", "uv.exe", "cpython-3.11.15-embed.zip",
                "install-catfish-email.ps1", "install-wechat-reader.ps1"}
ONLY_MAC = {"install.sh", "uv", "cpython-3.11.15-embed.tar.gz", "node-embed.tar.gz",
            # EventKit 读日历的 Swift 小程序。Windows 那边日历 / 提醒走 Outlook COM
            # (commands/system_outlook.rs), 不需要对应的二进制。
            "catfish-calendar"}


def resource_names(conf: Path) -> set[str]:
    d = json.loads(conf.read_text(encoding="utf-8"))
    res = d.get("bundle", {}).get("resources", {})
    items = res.keys() if isinstance(res, dict) else res
    return {Path(k).name for k in items}


conf_dir = APP / "src-tauri"
win = resource_names(conf_dir / "tauri.windows.conf.json")
for mac_conf in ("tauri.aarch64.conf.json", "tauri.x64.conf.json"):
    mac = resource_names(conf_dir / mac_conf)
    for name in sorted((win - ONLY_WINDOWS) - mac):
        bad("R7", mac_conf, f"Windows 包里有 {name}, 这个 mac 包里没有")
    for name in sorted((mac - ONLY_MAC) - win):
        bad("R7", "tauri.windows.conf.json", f"{mac_conf} 里有 {name}, Windows 包里没有")

# 打包脚本三处都得真的去生成 edge-runtime (清单里有、脚本没打 = 装机时静默缺)
for script in (APP / "scripts" / "build-msi-local.ps1",
               APP / "scripts" / "build-mac-resources.sh",
               REPO / ".github" / "workflows" / "build-windows-msi.yml"):
    if "build_edge_runtime.py" not in script.read_text(encoding="utf-8"):
        bad("R7", script.relative_to(REPO).as_posix(), "没调 build_edge_runtime.py —— 包里不会有 tool-bridge / local-search")

# ── 结果 ──────────────────────────────────────────────────────────

if problems:
    print(f"❌ Windows / macOS 一致性检查: {len(problems)} 处\n")
    print("\n".join(problems))
    print("\n  规则说明见本文件开头。真是平台专属的, 在那一行写 `windows-parity: <理由>`。")
    sys.exit(1)
print("✓ Windows / macOS 一致性检查通过 (R1–R8)")
