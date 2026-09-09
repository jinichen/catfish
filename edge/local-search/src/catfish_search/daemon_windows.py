"""旧 Windows BAT 守护入口已停用；由 Companion 管理启动和迁移。

保留旧脚本模板用于识别/回归，不再生成。status 仍可诊断旧任务。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TASK_NAME = "CatfishSearchWatcher"

# Windows 用 APPDATA 放配置/脚本比较规范（即使用户禁用了重定向也能用）。
_APP_DATA = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
CATFISH_DIR = _APP_DATA / "catfish"
WRAPPER_BAT = CATFISH_DIR / "catfish-search-watcher.bat"
LOG_PATH = CATFISH_DIR / "watcher.log"


def _render_wrapper() -> str:
    """死循环 + 10 秒节流，中文注释用 rem。"""
    return (
        "@echo off\r\n"
        "rem catfish-search watcher wrapper\r\n"
        "rem 崩了自动重启，每次至少间隔 10 秒，防止 bug 时 CPU 爆炸\r\n"
        ":loop\r\n"
        f'"{sys.executable}" -m catfish_search.cli watch >> "{LOG_PATH}" 2>&1\r\n'
        "timeout /t 10 /nobreak >nul\r\n"
        "goto loop\r\n"
    )


def install() -> int:
    # Companion owns the watchdog. Never recreate the deprecated visible BAT loop.
    print("Windows 搜索守护已改由 Catfish Companion 管理，请启动或升级 Companion。")
    print("不再创建 CatfishSearchWatcher 登录任务；新版 Companion 会迁移旧入口。")
    return 1


def uninstall() -> int:
    # Avoid claiming success after deleting only a file: the running BAT may loop forever.
    # The signed MSI/current Companion has the owner-checked migration implementation.
    print("请通过新版 Catfish Companion 启动迁移，或卸载 Companion 完成清理。")
    print("需要停止旧 BAT 及其子进程，不能只删任务或脚本。此次未删除任何数据。")
    return 1


def status() -> int:
    if not WRAPPER_BAT.exists():
        print("没有旧版 watcher 脚本；Windows 后台搜索由 Catfish Companion 管理。")
        return 0

    r = subprocess.run(  # noqa: S603
        ["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"],
        check=False,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if r.returncode != 0:
        print(f"任务 '{TASK_NAME}' 未注册（wrapper 还在）。")
        return 0

    # 只挑关键字段打，schtasks /V 输出太长。
    wanted = ("TaskName:", "Status:", "Last Run Time:", "Next Run Time:", "Last Result:")
    for line in r.stdout.splitlines():
        if line.strip().startswith(wanted):
            print(line.strip())
    print(f"Wrapper: {WRAPPER_BAT}")
    print(f"日志:    {LOG_PATH}")
    if LOG_PATH.exists():
        print(f"日志大小: {LOG_PATH.stat().st_size} bytes")
    return 0
