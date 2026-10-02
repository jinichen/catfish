//! Windows 上读经典版 Outlook 的日历 (9/23; 10/2 起只剩"读")。
//!
//! 9/23 这里还写 Outlook 的任务 / 日历。10/2 鸿波拍板 Windows 上小鲶记的提醒 / 日历
//! 存在 Catfish 自己 (system_local_pim.rs) —— 新版 Outlook 没有 COM, 写 Outlook 在新机器上
//! 等于不能用。剩下的只有早安页读员工已有的会议: 装了经典版 Outlook 就并进来, 没装
//! (或新版 Outlook) 就跳过, calendar.rs 不因为它报错。
//!
//! 参数一律走环境变量传进脚本, 不拼字符串。输出强制 UTF-8 (PowerShell 5.1 默认按
//! 系统代码页输出, 中文日历名会乱码)。

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
