"""任务完成通知在 Windows 上也要弹 (10/2)。

原来 _notify_task_done 只认 macOS (osascript), Windows 上长任务做完 / 失败都不吭声。
这里在 mac / Linux 上模拟 Windows: platform.system() 返 "Windows", Popen 换成 mock。
"""
from __future__ import annotations

import subprocess
import time
from unittest import mock

from catfish_tool_bridge import task_manager_notify as tmn
from catfish_tool_bridge.task_manager_types import Task


def _failed_task() -> Task:
    now = time.time()
    t = Task(task_id="t1", kind="parse_file", label="周报解析")
    t.status = "failed"
    t.error = "文件坏了 \"引号\""
    t.started_at = now - 5
    t.finished_at = now
    return t


def test_windows_toast_goes_through_powershell_without_console(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.delenv("CATFISH_TASK_NOTIFY", raising=False)
    tmn._RECENT_NOTIFY_BY_LABEL.clear()
    with mock.patch.object(tmn.subprocess, "Popen") as popen:
        tmn._notify_task_done(_failed_task())

    popen.assert_called_once()
    args, kwargs = popen.call_args
    assert args[0][0] == "powershell"
    assert "ToastNotificationManager" in args[0][-1]
    env = kwargs["env"]
    assert env["CATFISH_TOAST_TITLE"] == "鲶鱼 · 任务失败"
    # 正文原样走环境变量, 引号不用转义
    assert '"引号"' in env["CATFISH_TOAST_BODY"]
    # 跟 Companion desktop_notify.rs 同一个 AUMID, 不然 Windows 静默丢弃
    assert env["CATFISH_TOAST_APPID"] == "com.catfish.companion"
    assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_windows_respects_notify_off_switch(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setenv("CATFISH_TASK_NOTIFY", "0")
    tmn._RECENT_NOTIFY_BY_LABEL.clear()
    with mock.patch.object(tmn.subprocess, "Popen") as popen:
        tmn._notify_task_done(_failed_task())
    popen.assert_not_called()


def test_linux_still_silent(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.delenv("CATFISH_TASK_NOTIFY", raising=False)
    tmn._RECENT_NOTIFY_BY_LABEL.clear()
    with mock.patch.object(tmn.subprocess, "Popen") as popen:
        tmn._notify_task_done(_failed_task())
    popen.assert_not_called()
