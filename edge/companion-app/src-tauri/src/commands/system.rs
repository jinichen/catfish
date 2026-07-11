//! 系统能力 —— 系统通知 / Reminders.app / Calendar.app / hermes 版本.
//!
//! P3.5.141 (6/29 鸿波"砍在终端开鲶鱼"): open_terminal fn 砍 — 整 dead 树
//! (SessionsTab/SessionLauncher/ChatSidebar 按钮) 一并清理. Sessions tab 已在
//! P0-3.1 并入工作台 sidebar, 终端入口不再露给员工.

use std::process::Command;

#[tauri::command]
pub async fn notify(title: String, body: String) -> Result<(), String> {
    // 五一 sprint 5/2 收尾 BL-E13 主动闲聊: macOS 通知用 osascript 发, 不引 tauri-plugin-notification 新依赖.
    // Linux / Windows 后续按需扩展 (notify-send / Win toast).
    #[cfg(target_os = "macos")]
    {
        use std::process::Command;
        // osascript 字符串里 " 要 escape, 防 starter 含双引号炸
        let safe_title = title.replace('"', "\\\"");
        let safe_body = body.replace('"', "\\\"");
        let script = format!(
            "display notification \"{}\" with title \"{}\" sound name \"Glass\"",
            safe_body, safe_title,
        );
        let status = Command::new("osascript")
            .arg("-e")
            .arg(&script)
            .status()
            .map_err(|e| format!("osascript 启动失败: {e}"))?;
        if !status.success() {
            return Err(format!("osascript 退出非 0: {status}"));
        }
        Ok(())
    }

    #[cfg(not(target_os = "macos"))]
    {
        let _ = (title, body);
        Err("notify: 当前平台未实现 (只有 macOS)".into())
    }
}

/// BL-REMINDER (5/13 鸿波拍板): 在 macOS Reminders.app 创建提醒.
///
/// 跟 notify (右上角横幅消息, 几秒消失) 互补 —
/// - notify: catfish 内部消息推送 (BL-E13 主动闲聊 / cron 完成通知 / 等)
/// - create_reminder: 用户管理的真 to-do, 跨设备同步 (iCloud → iPhone / iPad), 用户能勾完成
///
/// 用 osascript `tell app "Reminders"`. 首次调用 macOS 弹 TCC 权限申请, 用户允许后才能写.
///
/// 参数:
/// - title: 提醒标题 (必填)
/// - body: 备注内容 (可选)
/// - due_date_iso: ISO 8601 到期时间 (e.g. "2026-05-15T09:00:00") — 可选, 不传就是无截止
/// - list_name: 要写到哪个 list (默认 "提醒事项" — 中文系统; 英文系统是 "Reminders")
/// - priority: 0-9 (0=无, 1-3=高, 4-6=中, 7-9=低), 可选
///
/// 返回: 创建的 reminder name (用作引用 — Reminders.app 没有稳定 ID API)
#[tauri::command]
pub async fn create_reminder(
    title: String,
    body: Option<String>,
    due_date_iso: Option<String>,
    list_name: Option<String>,
    priority: Option<u8>,
) -> Result<String, String> {
    #[cfg(target_os = "macos")]
    {
        if title.trim().is_empty() {
            return Err("title 不能空".into());
        }

        let safe_title = title.replace('"', "\\\"");
        let safe_body = body.unwrap_or_default().replace('"', "\\\"");
        let list = list_name.unwrap_or_else(|| "提醒事项".to_string());
        let safe_list = list.replace('"', "\\\"");

        // 拼 properties record: name 必, body / due date / priority 可选
        let mut props = vec![format!("name:\"{}\"", safe_title)];
        if !safe_body.is_empty() {
            props.push(format!("body:\"{}\"", safe_body));
        }
        if let Some(iso) = due_date_iso.as_ref() {
            // ISO 8601 -> AppleScript date 字符串 "YYYY-MM-DD HH:MM:SS"
            // 简单转换: 替换 T 为空格, 砍掉时区后缀
            let s = iso.replace('T', " ");
            let s = s.split('+').next().unwrap_or(&s);
            let s = s.split('Z').next().unwrap_or(s);
            let s = s.trim();
            // AppleScript 接受 'date "YYYY-MM-DD HH:MM:SS"' 形式
            props.push(format!("due date:date \"{}\"", s.replace('"', "\\\"")));
        }
        if let Some(p) = priority {
            // Reminders.app priority: 0 (无) / 1-3 (高) / 4-6 (中) / 7-9 (低)
            // 钳到 0-9
            let p = p.min(9);
            props.push(format!("priority:{}", p));
        }

        let script = format!(
            r#"tell application "Reminders"
    set targetList to first list whose name is "{}"
    set newReminder to make new reminder at end of targetList with properties {{{}}}
    return name of newReminder
end tell"#,
            safe_list,
            props.join(", "),
        );

        let output = Command::new("osascript")
            .arg("-e")
            .arg(&script)
            .output()
            .map_err(|e| format!("osascript 启动失败: {e}"))?;

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            // 常见错误: list 不存在 / 权限未给 (需用户首次允许 Reminders 访问)
            if stderr.contains("Not authorized") || stderr.contains("权限") {
                return Err(format!(
                    "Reminders.app 权限未给 — 系统设置 → 隐私与安全性 → 提醒事项 → 勾上 Catfish Companion. 然后重试. (osascript stderr: {})",
                    stderr.trim(),
                ));
            }
            if stderr.contains("Can\u{2019}t get list") || stderr.contains("can't find") {
                return Err(format!(
                    "list \"{}\" 不存在 — 检查 list 名 (中文系统默认 \"提醒事项\", 英文 \"Reminders\"). osascript: {}",
                    list, stderr.trim(),
                ));
            }
            return Err(format!("osascript 失败: {}", stderr.trim()));
        }

        let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
        Ok(stdout)
    }

    #[cfg(not(target_os = "macos"))]
    {
        let _ = (title, body, due_date_iso, list_name, priority);
        Err("create_reminder: 当前平台未实现 (只 macOS, 走 Reminders.app)".into())
    }
}

/// BL-REMINDER: 列 macOS Reminders.app 所有 list 名 (给 LLM 选 list 时用).
#[tauri::command]
pub async fn list_reminder_lists() -> Result<Vec<String>, String> {
    #[cfg(target_os = "macos")]
    {
        let script = r#"tell application "Reminders"
    set lst to {}
    repeat with L in lists
        set end of lst to name of L
    end repeat
    return lst
end tell"#;

        let output = Command::new("osascript")
            .arg("-e")
            .arg(script)
            .output()
            .map_err(|e| format!("osascript 启动失败: {e}"))?;

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            if stderr.contains("Not authorized") {
                return Err("Reminders.app 权限未给 — 系统设置 → 隐私与安全性 → 提醒事项".into());
            }
            return Err(format!("osascript 失败: {}", stderr.trim()));
        }

        // osascript 返 "List1, List2, List3" 用 ", " 分隔
        let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
        let lists: Vec<String> = stdout
            .split(", ")
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect();
        Ok(lists)
    }

    #[cfg(not(target_os = "macos"))]
    {
        Err("list_reminder_lists: 当前平台未实现 (只 macOS)".into())
    }
}

/// BL-CALENDAR (5/14 0:30 鸿波拍板): 在 macOS Calendar.app 创建**时间锚定的事件**.
///
/// 跟 5/13 BL-REMINDER (Reminders.app to-do) 互补:
/// - reminder: 待办 (用户能勾完成, 截止时间)
/// - **calendar event (本命令)**: 时间锚定事件 (会议 / 现场审核 / 行程, 带 location + 时长)
///
/// 用 osascript `tell app "Calendar"`. 首次调用 macOS 弹 TCC 权限申请 (隐私与安全性 → 日历).
///
/// 关键: AppleScript 的 record literal **不允许多行换行**, 必须压一行 (5/14 鸿波 ISO 审核脚本踩的坑).
///
/// 参数:
/// - title: 事件标题 (必填)
/// - start_iso: ISO 8601 开始时间 (必填)
/// - end_iso: ISO 8601 结束时间 (可选, 默认 start + 1h)
/// - location: 地点 (可选)
/// - description: 详情备注 (可选)
/// - calendar_name: 写到哪个日历 (默认 "工作")
/// - alarm_minutes_before: 事件前几分钟弹通知 (默认 [15]). iPhone 上震动+弹通知靠这字段.
///   传 [] 显式不提醒. 单值或多值都接受 (vec).
///
/// 返回: 创建的 event summary (作引用 — Calendar.app 没稳定 ID API)
#[tauri::command]
pub async fn create_calendar_event(
    title: String,
    start_iso: String,
    end_iso: Option<String>,
    location: Option<String>,
    description: Option<String>,
    calendar_name: Option<String>,
    alarm_minutes_before: Option<Vec<u32>>,
) -> Result<String, String> {
    #[cfg(target_os = "macos")]
    {
        if title.trim().is_empty() {
            return Err("title 不能空".into());
        }
        if start_iso.trim().is_empty() {
            return Err("start_iso 不能空 (ISO 8601, e.g. '2026-05-18T08:40:00')".into());
        }

        // ISO -> AppleScript date 字符串 (砍 T / 时区后缀, 同 reminder 模式)
        fn iso_to_applescript(iso: &str) -> String {
            let s = iso.replace('T', " ");
            let s = s.split('+').next().unwrap_or(&s);
            let s = s.split('Z').next().unwrap_or(s);
            s.trim().to_string()
        }

        let start_str = iso_to_applescript(&start_iso);

        // end_iso 没传: 默认 start + 1h. Rust stdlib 没有易用的 datetime parsing
        // (chrono 没在 Cargo.toml), 简单处理: 若 start_str 是 "YYYY-MM-DD HH:MM:SS"
        // 把 HH+1 (跨天 / 跨月不严谨, 但 99% 场景够; 严谨场景 LLM 应该传 end_iso).
        let end_str = match end_iso {
            Some(e) if !e.trim().is_empty() => iso_to_applescript(&e),
            _ => {
                // 简易加 1h (LLM 一般会传 end_iso, 这只是兜底)
                let parts: Vec<&str> = start_str.splitn(2, ' ').collect();
                if parts.len() == 2 {
                    let date_part = parts[0];
                    let time_part = parts[1];
                    let tparts: Vec<&str> = time_part.splitn(3, ':').collect();
                    if tparts.len() >= 2 {
                        if let Ok(h) = tparts[0].parse::<u32>() {
                            let new_h = (h + 1) % 24;
                            let mm = tparts[1];
                            let ss = tparts.get(2).unwrap_or(&"00");
                            format!("{} {:02}:{}:{}", date_part, new_h, mm, ss)
                        } else { start_str.clone() }
                    } else { start_str.clone() }
                } else { start_str.clone() }
            }
        };

        let safe_title = title.replace('"', "\\\"");
        let safe_start = start_str.replace('"', "\\\"");
        let safe_end = end_str.replace('"', "\\\"");
        let cal = calendar_name.unwrap_or_else(|| "工作".to_string());
        let safe_cal = cal.replace('"', "\\\"");

        // 拼 properties record (压一行!)
        let mut props = vec![
            format!("summary:\"{}\"", safe_title),
            format!("start date:date \"{}\"", safe_start),
            format!("end date:date \"{}\"", safe_end),
        ];
        if let Some(loc) = location.as_ref().filter(|s| !s.trim().is_empty()) {
            props.push(format!("location:\"{}\"", loc.replace('"', "\\\"")));
        }
        if let Some(desc) = description.as_ref().filter(|s| !s.trim().is_empty()) {
            props.push(format!("description:\"{}\"", desc.replace('"', "\\\"")));
        }

        // alarm 段 — 默认 [15] (15 min 前 1 次), None 也用默认.
        // 显式禁用要传 vec![] (调用方明确传空).
        let alarms: Vec<u32> = match alarm_minutes_before {
            None => vec![15],
            Some(v) => {
                let mut clean: Vec<u32> = v.into_iter()
                    .map(|m| m.min(40320))  // 钳到 28 天
                    .collect();
                clean.sort();
                clean.dedup();
                clean
            }
        };
        let alarm_segment = if alarms.is_empty() {
            String::new()
        } else {
            let mut s = String::from("\n    tell newEvent\n");
            for m in &alarms {
                // m=0 → trigger interval:0, m>0 → -m (前 m 分钟)
                let trigger: i64 = if *m == 0 { 0 } else { -(*m as i64) };
                s.push_str(&format!(
                    "        make new display alarm at end of display alarms with properties {{trigger interval:{}}}\n",
                    trigger,
                ));
            }
            s.push_str("    end tell");
            s
        };

        let script = format!(
            r#"tell application "Calendar"
    set targetCal to first calendar whose name is "{}"
    set newEvent to make new event at targetCal with properties {{{}}}{}
    return summary of newEvent
end tell"#,
            safe_cal,
            props.join(", "),
            alarm_segment,
        );

        let output = Command::new("osascript")
            .arg("-e")
            .arg(&script)
            .output()
            .map_err(|e| format!("osascript 启动失败: {e}"))?;

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            if stderr.contains("Not authorized") || stderr.contains("权限") {
                return Err(format!(
                    "Calendar.app 权限未给 — 系统设置 → 隐私与安全性 → 日历 → 勾上 Catfish Companion. 然后重试. (osascript stderr: {})",
                    stderr.trim(),
                ));
            }
            if stderr.contains("Can\u{2019}t get calendar") || stderr.contains("can't find") {
                return Err(format!(
                    "calendar \"{}\" 不存在 — 中文系统常见 \"工作\" / \"家庭\" / \"我的日历\", 英文 \"Work\" / \"Home\" / \"Calendar\". osascript: {}",
                    cal, stderr.trim(),
                ));
            }
            return Err(format!("osascript 失败: {}", stderr.trim()));
        }

        let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
        Ok(stdout)
    }

    #[cfg(not(target_os = "macos"))]
    {
        let _ = (title, start_iso, end_iso, location, description, calendar_name);
        Err("create_calendar_event: 当前平台未实现 (只 macOS, 走 Calendar.app)".into())
    }
}

/// BL-CALENDAR: 列 macOS Calendar.app 所有日历名 (给 LLM 选 calendar 时用).
#[tauri::command]
pub async fn list_calendars() -> Result<Vec<String>, String> {
    #[cfg(target_os = "macos")]
    {
        let script = r#"tell application "Calendar"
    set lst to {}
    repeat with C in calendars
        set end of lst to name of C
    end repeat
    return lst
end tell"#;

        let output = Command::new("osascript")
            .arg("-e")
            .arg(script)
            .output()
            .map_err(|e| format!("osascript 启动失败: {e}"))?;

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            if stderr.contains("Not authorized") {
                return Err("Calendar.app 权限未给 — 系统设置 → 隐私与安全性 → 日历".into());
            }
            return Err(format!("osascript 失败: {}", stderr.trim()));
        }

        let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
        let cals: Vec<String> = stdout
            .split(", ")
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect();
        Ok(cals)
    }

    #[cfg(not(target_os = "macos"))]
    {
        Err("list_calendars: 当前平台未实现 (只 macOS)".into())
    }
}

/// 读 ~/.hermes/hermes-agent/pyproject.toml 抽 version (BL-CATFISH-HERMES-VERSION-SYNC-B, 6/1).
///
/// AboutModal 显 "鲶鱼 v0.15.1 · hermes 0.15.1 (一致)" / "· hermes 0.15.1 (⚠ 漂移)".
/// hermes 没装 / 路径不对 → 返 None (前端不显这行).
///
/// 设计:
///   - 不 spawn `hermes --version` 进程 (慢 + venv activate 复杂)
///   - 直读 pyproject.toml 第一个 `version = "..."` 行 (toml crate 不引入新依赖)
///   - 永不抛 — 拿不到返 None, AboutModal 静默隐藏
#[tauri::command]
pub async fn get_hermes_version() -> Result<Option<String>, String> {
    let home = std::env::var_os("HOME").map(std::path::PathBuf::from);
    let Some(home) = home else { return Ok(None); };
    let path = home.join(".hermes/hermes-agent/pyproject.toml");
    let content = match std::fs::read_to_string(&path) {
        Ok(s) => s,
        Err(_) => return Ok(None), // 没装 hermes / 路径不对 — 静默
    };
    // 找第一个 `version = "X.Y.Z"` 行 (pyproject.toml [project] 段第一项一般是 name 然后 version)
    for line in content.lines() {
        let trimmed = line.trim();
        if let Some(rest) = trimmed.strip_prefix("version") {
            // 匹配 `version = "..."` 或 `version="..."` (空格 / 引号)
            let rest = rest.trim_start();
            if !rest.starts_with('=') { continue; }
            let rest = rest[1..].trim_start();
            if let Some(start) = rest.find('"') {
                if let Some(end) = rest[start + 1..].find('"') {
                    let v = &rest[start + 1..start + 1 + end];
                    if !v.is_empty() {
                        return Ok(Some(v.to_string()));
                    }
                }
            }
        }
    }
    Ok(None)
}
