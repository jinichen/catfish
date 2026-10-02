"""任务完成的系统通知 (macOS 通知中心 / Windows toast) —— 含去重。

BL-TASKMGR-SPLIT 8/15: 从 task_manager.py 抽出来 (1133 行超限)。纯搬迁, 逻辑一行未改。

⚠ _RECENT_NOTIFY_BY_LABEL 搬过来是安全的: 测试是
`task_manager._RECENT_NOTIFY_BY_LABEL.clear()` / `[...] = ...` —— **改内容不是
赋值**, re-export 指向同一个 dict, 改哪边都一样。
(对比 _manager: 测试写的是 `task_manager._manager = ...`, 那是**赋值**, 搬走就
只改到 re-export, manager() 读的还是自己那份 —— 所以它留在 task_manager.py。)
"""
from __future__ import annotations

import logging
import os
import subprocess
import time

from .task_manager_types import Task

logger = logging.getLogger("catfish.tool_bridge.task_manager")

#: BL-E27.4 (5/8): macOS 通知去重 — 同 label 1 小时内已发过 → 不重复发.
#: 跨 _notify_task_done 调用的 in-memory 状态, 进程死则丢. dict[label, last_notify_ts].
_RECENT_NOTIFY_BY_LABEL: dict[str, float] = {}
_NOTIFY_DEDUP_WINDOW_S = 3600  # 1 小时
_MIN_NOTIFY_ELAPSED_FOR_SUCCESS = 30.0  # 成功任务 ≥ 30s 才发系统通知


def _is_test_task(task: Task) -> bool:
    """识别 demo / 测试任务 — 避免 BL-A2 测试时反复跑导致通知中心刷屏."""
    label = (task.label or "").lower()
    kind = (task.kind or "").lower()
    return (
        "_test" in kind
        or "test_" in kind
        or kind == "test"
        or "测试" in (task.label or "")
        or label.startswith("test ")
    )


def _should_send_macos_notify(task: Task, elapsed: float) -> bool:
    """BL-E27.4 收紧规则 — 啥时候真发 macOS 通知.

    失败 → 始终发 (除非测试任务).
    成功 → 仅 ≥ 30 秒, 不是测试任务, 同 label 1h 内没发过.
    """
    if _is_test_task(task):
        return False
    label_key = task.label or task.kind or ""
    now = time.time()
    last = _RECENT_NOTIFY_BY_LABEL.get(label_key, 0.0)
    if now - last < _NOTIFY_DEDUP_WINDOW_S:
        return False  # 同 label 去重
    if task.status == "failed":
        # 失败始终通知 (除非测试任务, 已上面拦)
        _RECENT_NOTIFY_BY_LABEL[label_key] = now
        return True
    if task.status == "completed":
        # 成功仅长任务通知
        if elapsed >= _MIN_NOTIFY_ELAPSED_FOR_SUCCESS:
            _RECENT_NOTIFY_BY_LABEL[label_key] = now
            return True
    return False


def _notify_task_done(task: Task) -> None:
    """BL-A2.3 + BL-E27.4 (5/8 重写): 任务完成通知.

    macOS osascript 通知 — 收紧规则:
      - 失败 → 始终发
      - 成功 → ≥ 30s 才发
      - 测试任务过滤 ('_test' / '测试')
      - 同 label 1h 内去重 (防 demo 反复跑刷屏 — 鸿波 5/8 凌晨抱怨)
      - env CATFISH_TASK_NOTIFY=0 一键关

    9/12: 原"通道 2"—— 写 ~/.catfish/pet_pending_bubbles.jsonl 给桌宠冒泡/着色
    —— 随桌宠 (BL-E27) 一起删了。本机实测那个文件攒了 423 条、六周没人看过
    (pet_status_seen_ts.json 停在 7/27)。系统通知本来就是这条路径的兜底, 现在是唯一出口。
    """
    if task.finished_at is None or task.started_at is None:
        return
    elapsed = task.finished_at - task.started_at

    if task.status == "completed":
        title = "鲶鱼 · 任务完成"
        msg = f"{task.label or task.kind} 完成 ({elapsed:.0f}s)"
    elif task.status == "failed":
        title = "鲶鱼 · 任务失败"
        msg = f"{task.label or task.kind} 失败: {(task.error or '')[:80]}"
    else:
        return  # other states 不通知

    # 系统通知 (BL-E27.4 收紧)。10/2: 原来只有 macOS, Windows 上长任务做完 / 失败都不吭声。
    if (
        os.environ.get("CATFISH_TASK_NOTIFY", "1") != "0"
        and (_platform_is_macos() or _platform_is_windows())
        and _should_send_macos_notify(task, elapsed)
    ):
        try:
            _send_system_notify(title, msg)
        except Exception:
            logger.warning("系统通知失败", exc_info=True)


#: 跟 Companion services/desktop_notify.rs 一致: MSI 装的开始菜单快捷方式带这个 AUMID,
#: 用别的字符串 Windows 会静默丢弃 toast。
_WINDOWS_APP_ID = "com.catfish.companion"

#: 同 desktop_notify.rs 的脚本: PowerShell + WinRT, Win10+ 自带, 不装模块。
#: 标题正文走环境变量, 不拼进脚本 —— 省转义, 内容里有引号也不怕。
_WINDOWS_TOAST_PS = (
    "[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime];"
    "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
    "$t = $x.GetElementsByTagName('text');"
    "[void]$t.Item(0).AppendChild($x.CreateTextNode($env:CATFISH_TOAST_TITLE));"
    "[void]$t.Item(1).AppendChild($x.CreateTextNode($env:CATFISH_TOAST_BODY));"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:CATFISH_TOAST_APPID)"
    ".Show([Windows.UI.Notifications.ToastNotification]::new($x))"
)


def _send_system_notify(title: str, msg: str) -> None:
    if _platform_is_windows():
        env = {**os.environ, "CATFISH_TOAST_TITLE": title, "CATFISH_TOAST_BODY": msg,
               "CATFISH_TOAST_APPID": _WINDOWS_APP_ID}
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", _WINDOWS_TOAST_PS],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # 不加这个, 从没控制台的 tool-bridge 起 powershell 会闪一个黑框
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return
    safe_title = title.replace('"', "'")
    safe_msg = msg.replace('"', "'")
    script = f'display notification "{safe_msg}" with title "{safe_title}"'
    subprocess.Popen(
        ["osascript", "-e", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _platform_is_windows() -> bool:
    import platform as _p
    return _p.system() == "Windows"


def _platform_is_macos() -> bool:
    """检测是否 macOS (osascript 仅 macOS)."""
    import platform as _p
    return _p.system() == "Darwin"
