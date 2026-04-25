"""跨平台守护进程管理（install / uninstall / status）。

按平台分发：
    macOS    -> daemon_macos    (launchd user agent)
    Windows  -> daemon_windows  (Task Scheduler, 带 .bat 包装做崩溃自重启)
    Linux    -> daemon_linux    (systemd --user 服务)

三种实现都不需要管理员 / sudo，只装在"当前用户"级别：
    - 出问题只影响这个员工自己，不会污染整个机器
    - 员工自己能随时 uninstall，不用叫 IT
"""
from __future__ import annotations

import platform


def _backend():
    system = platform.system()
    if system == "Darwin":
        from . import daemon_macos as mod  # noqa: PLC0415
        return mod
    if system == "Windows":
        from . import daemon_windows as mod  # noqa: PLC0415
        return mod
    if system == "Linux":
        from . import daemon_linux as mod  # noqa: PLC0415
        return mod
    raise RuntimeError(
        f"暂不支持的平台 {system}。你可以直接 `catfish-search watch` 前台跑。"
    )


def install() -> int:
    try:
        return _backend().install()
    except RuntimeError as e:
        print(str(e))
        return 2


def uninstall() -> int:
    try:
        return _backend().uninstall()
    except RuntimeError as e:
        print(str(e))
        return 2


def status() -> int:
    try:
        return _backend().status()
    except RuntimeError as e:
        print(str(e))
        return 2
