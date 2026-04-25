"""Windows 用 Task Scheduler + 包装 .bat 做守护进程。

schtasks 本身没有"崩了自动拉起"的直接选项（/SC ONFAILURE 很别扭），
所以我们额外写一个 wrapper .bat，内部死循环跑 watcher，崩了就 sleep 10 秒再起。
schtasks 只负责"登录时把这个 .bat 启起来"。

只影响当前用户，不需要管理员权限。
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
    CATFISH_DIR.mkdir(parents=True, exist_ok=True)
    WRAPPER_BAT.write_text(_render_wrapper(), encoding="utf-8")
    print(f"已写入 {WRAPPER_BAT}")

    # 幂等：装过就先删再装。
    subprocess.run(  # noqa: S603
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        check=False,
        capture_output=True,
    )

    # /SC ONLOGON     登录时触发
    # /RL LIMITED     普通权限运行（不要求管理员）
    # /F              覆盖已存在的同名任务
    # /TR             要跑的命令
    r = subprocess.run(  # noqa: S603
        [
            "schtasks",
            "/Create",
            "/TN", TASK_NAME,
            "/TR", f'cmd /c "{WRAPPER_BAT}"',
            "/SC", "ONLOGON",
            "/RL", "LIMITED",
            "/F",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print(f"schtasks 注册失败：{r.stderr.strip() or r.stdout.strip()}")
        print("可能的原因：Task Scheduler 被组策略禁用。")
        print("备选：把 wrapper.bat 放到 Startup 文件夹（shell:startup）手动启动。")
        return 1

    # 立即触发一次，不用等下次登录。
    subprocess.run(  # noqa: S603
        ["schtasks", "/Run", "/TN", TASK_NAME],
        check=False,
        capture_output=True,
    )
    print(f"已注册为登录任务 '{TASK_NAME}'。开机登录时自启，已立即运行一次。")
    print(f"日志：{LOG_PATH}")
    return 0


def uninstall() -> int:
    r = subprocess.run(  # noqa: S603
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        check=False,
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        print(f"已删除任务 '{TASK_NAME}'。")
    else:
        print("任务不存在或已被删除。")

    if WRAPPER_BAT.exists():
        WRAPPER_BAT.unlink()
        print(f"已删除 {WRAPPER_BAT}")

    # watcher 进程本身：查一下 python 进程里跑 catfish_search.cli watch 的，杀掉。
    # 简化做法：靠 wrapper.bat 循环退出。这里不主动 kill。
    print("注意：当前正在跑的 watcher 进程会在下次循环结束时退出。")
    return 0


def status() -> int:
    if not WRAPPER_BAT.exists():
        print("未安装。运行 `catfish-search daemon install` 启用。")
        return 0

    r = subprocess.run(  # noqa: S603
        ["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"],
        check=False,
        capture_output=True,
        text=True,
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
