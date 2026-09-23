//! Windows 上的提醒事项 / 日历 —— 走 Outlook COM (9/23).
//!
//! mac 上这四个命令用 osascript 驱动 Reminders.app / Calendar.app (commands/system.rs)。
//! Windows 分支原来是 `Err("当前平台未实现 (只 macOS)")` —— 员工让小鲶"明天九点提醒
//! 我开会", 在 Windows 上永远失败。
//!
//! Windows 办公机上对应的东西是 Outlook 的任务和日历, 而邮件那边 (catfish-email
//! 的 Outlook 适配器) 本来就假设装了桌面版 Outlook。所以同一个假设, 不引新依赖:
//! PowerShell `New-Object -ComObject Outlook.Application`。
//!
//! 参数一律走环境变量传进脚本, 不拼字符串 —— 省掉转义, 标题里有引号 / 换行也不会炸。
//! 输出强制 UTF-8 (PowerShell 5.1 默认按系统代码页输出, 中文日历名会乱码)。

#![cfg(windows)]

use crate::services::process::background_command;

const PRELUDE: &str = r#"
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
"#;

fn run(script: &str, env: &[(&str, String)]) -> Result<String, String> {
    let full = format!("{PRELUDE}\n{script}");
    let mut cmd = background_command("powershell");
    cmd.args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", &full]);
    for (k, v) in env {
        cmd.env(k, v);
    }
    let out = cmd.output().map_err(|e| format!("powershell 启动失败: {e}"))?;
    let stderr = String::from_utf8_lossy(&out.stderr);
    if out.status.code() == Some(3) || stderr.contains("NO_OUTLOOK") {
        return Err(
            "Windows 上的提醒 / 日历写进 Outlook, 这台机器没装桌面版 Outlook (或没登录过)。".into(),
        );
    }
    if !out.status.success() {
        return Err(format!("Outlook 操作失败: {}", stderr.trim()));
    }
    Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

fn lines(s: String) -> Vec<String> {
    s.lines().map(|l| l.trim().to_string()).filter(|l| !l.is_empty()).collect()
}

/// Reminders.app 的优先级 (1-3 高 / 4-6 中 / 7-9 低 / 0 无) → Outlook Importance (2 / 1 / 0)。
fn importance(priority: Option<u8>) -> &'static str {
    match priority.unwrap_or(0) {
        1..=3 => "2",
        7..=9 => "0",
        _ => "1",
    }
}

pub fn create_reminder(
    title: &str,
    body: Option<&str>,
    due_iso: Option<&str>,
    list_name: Option<&str>,
    priority: Option<u8>,
) -> Result<String, String> {
    // 13 = olFolderTasks, 3 = olTaskItem。list 找不到就落默认任务文件夹 —— 跟 mac 上
    // "list 不存在就报错"不同, 因为 Outlook 里员工很少建任务子文件夹, 报错只会挡路。
    let script = r#"
$root = $ns.GetDefaultFolder(13)
$folder = Find-Folder $root $env:CF_LIST
if (-not $folder) { $folder = $root }
$t = $folder.Items.Add(3)
$t.Subject = $env:CF_TITLE
if ($env:CF_BODY) { $t.Body = $env:CF_BODY }
if ($env:CF_DUE) { $d = [datetime]::Parse($env:CF_DUE); $t.DueDate = $d; $t.ReminderSet = $true; $t.ReminderTime = $d }
$t.Importance = [int]$env:CF_IMPORTANCE
$t.Save()
$t.Subject
"#;
    run(script, &[
        ("CF_TITLE", title.to_string()),
        ("CF_BODY", body.unwrap_or_default().to_string()),
        ("CF_DUE", due_iso.unwrap_or_default().to_string()),
        ("CF_LIST", list_name.unwrap_or_default().to_string()),
        ("CF_IMPORTANCE", importance(priority).to_string()),
    ])
}

pub fn list_reminder_lists() -> Result<Vec<String>, String> {
    let script = r#"
$root = $ns.GetDefaultFolder(13)
$root.Name
foreach ($f in $root.Folders) { $f.Name }
"#;
    run(script, &[]).map(lines)
}

#[allow(clippy::too_many_arguments)]
pub fn create_calendar_event(
    title: &str,
    start_iso: &str,
    end_iso: Option<&str>,
    location: Option<&str>,
    description: Option<&str>,
    calendar_name: Option<&str>,
    alarm_minutes_before: &[u32],
) -> Result<String, String> {
    // 9 = olFolderCalendar, 1 = olAppointmentItem。Outlook 一个事件只有一个提醒 ——
    // 多个提醒时间取最早那个 (离事件最远), 不静默丢掉全部。
    let reminder = alarm_minutes_before.iter().max().map(|m| m.to_string()).unwrap_or_default();
    let script = r#"
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
$a.Subject
"#;
    run(script, &[
        ("CF_TITLE", title.to_string()),
        ("CF_START", start_iso.to_string()),
        ("CF_END", end_iso.unwrap_or_default().to_string()),
        ("CF_LOCATION", location.unwrap_or_default().to_string()),
        ("CF_DESC", description.unwrap_or_default().to_string()),
        ("CF_CAL", calendar_name.unwrap_or_default().to_string()),
        ("CF_REMIND", reminder),
    ])
}

pub fn list_calendars() -> Result<Vec<String>, String> {
    let script = r#"
$root = $ns.GetDefaultFolder(9)
$root.Name
foreach ($f in $root.Folders) { $f.Name }
"#;
    run(script, &[]).map(lines)
}

/// 读 Outlook 默认日历在一个区间里的事件, 输出跟 mac 的 JXA / EventKit 同一个
/// JSON 形状 (`[{calendar, summary, start, end, all_day, location?, attendees?,
/// description?}]`), 前端不用分平台。
///
/// `range`: `"today"` / `"natural-week"` (本周一 00:00 到下周一 00:00, 跟 mac 一致)。
/// 用 `IncludeRecurrences` + `Restrict` —— 不加 IncludeRecurrences 的话周会这类
/// 重复事件一条都读不出来 (Outlook 只返回母事件, 它的 Start 是第一次开会那天)。
pub fn read_events(range: &str) -> Result<String, String> {
    let script = r#"
$today = (Get-Date).Date
if ($env:CF_RANGE -eq 'natural-week') {
  $from = $today.AddDays(-(([int]$today.DayOfWeek + 6) % 7)); $to = $from.AddDays(7)
} else { $from = $today; $to = $today.AddDays(1) }
$cal = $ns.GetDefaultFolder(9)
$items = $cal.Items
$items.IncludeRecurrences = $true
$items.Sort('[Start]')
$f = "[Start] >= '" + $from.ToString('g') + "' AND [Start] < '" + $to.ToString('g') + "'"
$out = @()
foreach ($e in $items.Restrict($f)) {
  $o = [ordered]@{
    calendar = $cal.Name
    summary  = if ($e.Subject) { $e.Subject } else { '(无标题)' }
    start    = $e.Start.ToUniversalTime().ToString('o')
    end      = $e.End.ToUniversalTime().ToString('o')
    all_day  = [bool]$e.AllDayEvent
  }
  if ($e.Location) { $o.location = $e.Location }
  $att = @($e.RequiredAttendees -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
  if ($att.Count -gt 0) { $o.attendees = $att }
  if ($e.Body) { $b = $e.Body; if ($b.Length -gt 500) { $b = $b.Substring(0, 500) + '…' }; $o.description = $b }
  $out += [pscustomobject]$o
}
ConvertTo-Json -InputObject @($out) -Depth 4 -Compress
"#;
    run(script, &[("CF_RANGE", range.to_string())])
}
