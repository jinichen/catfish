"""Windows 上的提醒 / 日历 —— 走 Outlook COM (10/2)。

mac 上 catfish_create_reminder / catfish_create_calendar_event 这几个工具用 osascript
驱动 Reminders.app / Calendar.app (reminders.py / calendar_events.py)。Windows 上原来
直接返"只 macOS 支持", 工具清单里也被 x_catfish_runtime 藏掉了 —— 员工跟小鲶说"明天
九点提醒我开会", 在 Windows 上做不到。

Companion 界面那边 9/23 已经用 Outlook 做了同样的事 (src-tauri/src/commands/
system_outlook.rs)。这里是同一套 PowerShell 脚本的 Python 版, 让模型调工具时
跟员工点界面按钮落到同一个地方 (Outlook 的任务 / 日历):
  - 不引新依赖: PowerShell `New-Object -ComObject Outlook.Application`
    (hermes venv 里没有 pywin32, 邮件那边的 pywin32 在 catfish-email 自己的环境里)
  - 参数一律走环境变量, 不拼进脚本 —— 标题里有引号 / 换行也不会炸
  - 输出强制 UTF-8 (PowerShell 5.1 默认按系统代码页输出, 中文会乱)
  - 没装桌面版 Outlook (新版 Outlook 没有 COM) → 明确报错, 不假装成功
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any

_PRELUDE = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try { $ol = New-Object -ComObject Outlook.Application } catch {
  [Console]::Error.WriteLine('NO_OUTLOOK'); exit 3 }
$ns = $ol.GetNamespace('MAPI')
function Find-Folder($root, $name) {
  if (-not $name -or $root.Name -eq $name) { return $root }
  foreach ($f in $root.Folders) { if ($f.Name -eq $name) { return $f } }
  return $null
}
"""

NO_OUTLOOK_ERROR = (
    "Windows 上的提醒 / 日历写进 Outlook, 这台机器没装桌面版 Outlook (或没登录过)。"
    "新版 Outlook 没有可调用的接口; 需要的话先装经典版 Outlook, 或者把这件事记进任务库 (catfish_create_task)。"
)


class OutlookError(RuntimeError):
    def __init__(self, message: str, *, no_outlook: bool = False):
        super().__init__(message)
        self.no_outlook = no_outlook


def _run(script: str, env: dict[str, str] | None = None, timeout_sec: float = 60.0) -> str:
    """跑一段 PowerShell (前面拼上 PRELUDE), 返回 stdout。失败抛 OutlookError。

    超时给得宽: Outlook 没开着时 New-Object 会先把它拉起来, 冷启动十几秒很常见。
    """
    full = _PRELUDE + "\n" + script
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", full],
            env={**os.environ, **(env or {})},
            capture_output=True,
            timeout=timeout_sec,
            # 从没控制台的 tool-bridge 起 powershell, 不加这个会闪黑框
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as e:
        raise OutlookError(f"Outlook {timeout_sec:.0f} 秒没响应 (可能弹了登录 / 配置窗口, 去 Outlook 看一眼)") from e
    except OSError as e:
        raise OutlookError(f"powershell 启动失败: {e}") from e
    stderr = out.stderr.decode("utf-8", "replace")
    if out.returncode == 3 or "NO_OUTLOOK" in stderr:
        raise OutlookError(NO_OUTLOOK_ERROR, no_outlook=True)
    if out.returncode != 0:
        raise OutlookError(f"Outlook 操作失败: {stderr.strip()[-300:]}")
    return out.stdout.decode("utf-8", "replace").strip()


def _lines(s: str) -> list[str]:
    return [ln.strip() for ln in s.splitlines() if ln.strip()]


def _importance(priority: int | None) -> str:
    """Reminders.app 的优先级 (1-3 高 / 4-6 中 / 7-9 低 / 0 无) → Outlook Importance (2 / 1 / 0)。"""
    p = priority or 0
    if 1 <= p <= 3:
        return "2"
    if 7 <= p <= 9:
        return "0"
    return "1"


def _priority_from_importance(importance: int) -> int:
    """反过来, 读出来的时候对齐 Reminders.app 的数值 (高 1 / 低 9 / 普通 0)。"""
    return {2: 1, 0: 9}.get(importance, 0)


def create_reminder(title: str, body: str, due_iso: str, list_name: str, priority: int | None) -> str:
    # 13 = olFolderTasks, 3 = olTaskItem。list 找不到就落默认任务文件夹 —— 跟 system_outlook.rs
    # 一样: Outlook 里员工很少建任务子文件夹, "提醒事项" 这种 mac 清单名在这里本来就不存在。
    script = r"""
$root = $ns.GetDefaultFolder(13)
$folder = Find-Folder $root $env:CF_LIST
if (-not $folder) { $folder = $root }
$t = $folder.Items.Add(3)
$t.Subject = $env:CF_TITLE
if ($env:CF_BODY) { $t.Body = $env:CF_BODY }
if ($env:CF_DUE) { $d = [datetime]::Parse($env:CF_DUE); $t.DueDate = $d; $t.ReminderSet = $true; $t.ReminderTime = $d }
$t.Importance = [int]$env:CF_IMPORTANCE
$t.Save()
$folder.Name
"""
    return _run(script, {
        "CF_TITLE": title, "CF_BODY": body, "CF_DUE": due_iso, "CF_LIST": list_name,
        "CF_IMPORTANCE": _importance(priority),
    })


def list_reminder_lists() -> list[str]:
    return _lines(_run(r"""
$root = $ns.GetDefaultFolder(13)
$root.Name
foreach ($f in $root.Folders) { $f.Name }
"""))


def list_reminders(*, include_completed: bool, list_name: str, timeout_sec: float) -> list[dict[str, Any]]:
    """默认任务文件夹 + 一层子文件夹里的任务, 形状跟 reminders._parse_reminders_output 一样。

    Outlook 的"没有截止日期"是 4501-01-01, 当成 None。DueDate 是本地日期 (零点),
    跟 mac 一样输出不带时区的本地 ISO。scope 过滤交给调用方的 _filter_reminders。
    """
    script = r"""
$root = $ns.GetDefaultFolder(13)
$folders = @($root) + @($root.Folders | ForEach-Object { $_ })
$out = @()
foreach ($f in $folders) {
  if ($env:CF_LIST -and $f.Name -ne $env:CF_LIST) { continue }
  $items = $f.Items
  if ($env:CF_ALL -ne '1') { $items = $items.Restrict('[Complete] = False') }
  foreach ($t in $items) {
    if ($out.Count -ge 500) { break }
    $due = $null
    if ($t.DueDate -and $t.DueDate.Year -lt 4500) { $due = $t.DueDate.ToString('s') }
    $body = if ($t.Body) { $t.Body } else { '' }
    $out += [pscustomobject][ordered]@{
      id = $t.EntryID; title = $t.Subject; list_name = $f.Name; due_date_iso = $due
      completed = [bool]$t.Complete; importance = [int]$t.Importance; body = $body
    }
  }
}
ConvertTo-Json -InputObject @($out) -Depth 3 -Compress
"""
    raw = _run(script, {"CF_LIST": list_name, "CF_ALL": "1" if include_completed else "0"}, timeout_sec=timeout_sec)
    items = json.loads(raw or "[]")
    if isinstance(items, dict):  # PowerShell 5.1 单元素数组有时会被拆成对象
        items = [items]
    return [
        {
            "id": it.get("id") or "",
            "title": it.get("title") or "",
            "list_name": it.get("list_name") or "",
            "due_date_iso": it.get("due_date_iso") or None,
            "completed": bool(it.get("completed")),
            "priority": _priority_from_importance(int(it.get("importance") or 1)),
            "body": it.get("body") or "",
        }
        for it in items
    ]


def create_calendar_event(
    title: str, start_iso: str, end_iso: str, location: str, description: str,
    calendar_name: str, alarm_minutes_before: list[int],
) -> str:
    # 9 = olFolderCalendar, 1 = olAppointmentItem。Outlook 一个事件只有一个提醒 ——
    # 多个提醒时间取最早那个 (离事件最远), 跟 system_outlook.rs 一致, 不静默丢掉全部。
    reminder = str(max(alarm_minutes_before)) if alarm_minutes_before else ""
    script = r"""
$root = $ns.GetDefaultFolder(9)
$folder = Find-Folder $root $env:CF_CAL
if (-not $folder) { $folder = $root }
$a = $folder.Items.Add(1)
$a.Subject = $env:CF_TITLE
$s = [datetime]::Parse($env:CF_START)
$a.Start = $s
if ($env:CF_END) { $a.End = [datetime]::Parse($env:CF_END) } else { $a.End = $s.AddHours(1) }
if ($env:CF_LOCATION) { $a.Location = $env:CF_LOCATION }
if ($env:CF_DESC) { $a.Body = $env:CF_DESC }
if ($env:CF_REMIND -ne '') { $a.ReminderSet = $true; $a.ReminderMinutesBeforeStart = [int]$env:CF_REMIND } else { $a.ReminderSet = $false }
$a.Save()
$folder.Name
"""
    return _run(script, {
        "CF_TITLE": title, "CF_START": start_iso, "CF_END": end_iso, "CF_LOCATION": location,
        "CF_DESC": description, "CF_CAL": calendar_name, "CF_REMIND": reminder,
    })


def list_calendars() -> list[str]:
    return _lines(_run(r"""
$root = $ns.GetDefaultFolder(9)
$root.Name
foreach ($f in $root.Folders) { $f.Name }
"""))
