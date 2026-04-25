"""macOS 用 launchd 做用户态守护进程。

plist 装在 ~/Library/LaunchAgents，不用 sudo。
RunAtLoad + KeepAlive + ThrottleInterval=10，开机自启 + 崩溃拉起。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

LABEL = "ai.catfish.search.watcher"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_PATH = LAUNCH_AGENTS_DIR / f"{LABEL}.plist"
LOG_PATH = Path.home() / ".catfish" / "watcher.log"


PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>-m</string>
        <string>catfish_search.cli</string>
        <string>watch</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>{log}</string>
    <key>StandardErrorPath</key>
    <string>{log}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{path_env}</string>
        <key>LOG_LEVEL</key>
        <string>INFO</string>
    </dict>
</dict>
</plist>
"""


def _render_plist() -> str:
    return PLIST_TEMPLATE.format(
        label=LABEL,
        python=sys.executable,
        log=str(LOG_PATH),
        path_env=os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
    )


def install() -> int:
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    PLIST_PATH.write_text(_render_plist(), encoding="utf-8")
    print(f"已写入 {PLIST_PATH}")

    # 之前加载过的话先 unload，允许重复执行 install。
    subprocess.run(  # noqa: S603
        ["/bin/launchctl", "unload", str(PLIST_PATH)],
        check=False,
        capture_output=True,
    )
    r = subprocess.run(  # noqa: S603
        ["/bin/launchctl", "load", str(PLIST_PATH)],
        check=False,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print(f"launchctl load 失败：{r.stderr.strip()}")
        return 1
    print("已加载。开机自启，后台常驻。")
    print(f"日志：{LOG_PATH}")
    return 0


def uninstall() -> int:
    if PLIST_PATH.exists():
        subprocess.run(  # noqa: S603
            ["/bin/launchctl", "unload", str(PLIST_PATH)],
            check=False,
            capture_output=True,
        )
        PLIST_PATH.unlink()
        print(f"已卸载 {PLIST_PATH}")
    else:
        print("未安装。")
    return 0


def status() -> int:
    if not PLIST_PATH.exists():
        print("未安装。运行 `catfish-search daemon install` 启用。")
        return 0

    r = subprocess.run(  # noqa: S603
        ["/bin/launchctl", "list"],
        check=False,
        capture_output=True,
        text=True,
    )
    lines = [ln for ln in r.stdout.splitlines() if LABEL in ln]
    if not lines:
        print(f"plist 已安装但未加载。运行 `launchctl load {PLIST_PATH}`。")
        return 0

    parts = lines[0].split()
    pid, exit_code = parts[0], parts[1]
    print(f"Label:    {LABEL}")
    print(f"PID:      {pid if pid != '-' else '(未运行)'}")
    print(f"ExitCode: {exit_code}")
    print(f"Plist:    {PLIST_PATH}")
    print(f"日志:     {LOG_PATH}")
    if LOG_PATH.exists():
        print(f"日志大小: {LOG_PATH.stat().st_size} bytes")
    return 0
