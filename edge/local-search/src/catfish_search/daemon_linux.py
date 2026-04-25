"""Linux 用 systemd --user 做守护进程。

service 文件装在 ~/.config/systemd/user/，完全用户级别，不需要 sudo。
Restart=always + RestartSec=10 做崩溃自重启。
前提是当前发行版启用了 user systemd（主流发行版默认都有）。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

SERVICE_NAME = "catfish-search.service"
SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
SERVICE_PATH = SYSTEMD_USER_DIR / SERVICE_NAME
LOG_PATH = Path.home() / ".catfish" / "watcher.log"


SERVICE_TEMPLATE = """[Unit]
Description=Catfish local file search watcher
After=default.target

[Service]
Type=simple
ExecStart={python} -m catfish_search.cli watch
Restart=always
RestartSec=10
StandardOutput=append:{log}
StandardError=append:{log}
Environment=LOG_LEVEL=INFO

[Install]
WantedBy=default.target
"""


def _render_service() -> str:
    return SERVICE_TEMPLATE.format(python=sys.executable, log=str(LOG_PATH))


def _systemctl(*args) -> subprocess.CompletedProcess:
    if shutil.which("systemctl") is None:
        raise RuntimeError(
            "找不到 systemctl。当前发行版可能没有用户级 systemd。"
            "你可以直接在 ~/.profile 里加一行 `catfish-search watch &` 自启。"
        )
    return subprocess.run(  # noqa: S603
        ["systemctl", "--user", *args],
        check=False,
        capture_output=True,
        text=True,
    )


def install() -> int:
    SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    SERVICE_PATH.write_text(_render_service(), encoding="utf-8")
    print(f"已写入 {SERVICE_PATH}")

    _systemctl("daemon-reload")
    r = _systemctl("enable", "--now", SERVICE_NAME)
    if r.returncode != 0:
        print(f"systemctl enable 失败：{r.stderr.strip() or r.stdout.strip()}")
        return 1
    print(f"已启用 {SERVICE_NAME}。开机自启，后台常驻。")
    print(f"日志：{LOG_PATH}（或 `journalctl --user -u {SERVICE_NAME}`）")
    return 0


def uninstall() -> int:
    _systemctl("disable", "--now", SERVICE_NAME)
    if SERVICE_PATH.exists():
        SERVICE_PATH.unlink()
        print(f"已删除 {SERVICE_PATH}")
    _systemctl("daemon-reload")
    return 0


def status() -> int:
    if not SERVICE_PATH.exists():
        print("未安装。运行 `catfish-search daemon install` 启用。")
        return 0

    r = _systemctl("status", SERVICE_NAME, "--no-pager")
    # systemctl status 对已停止的服务返回非 0，这里不判 returncode，直接打。
    print(r.stdout or r.stderr)
    return 0
