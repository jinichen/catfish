//! 系统能力 —— 打开外部终端 / 系统通知。

use std::process::Command;

/// 打开系统终端窗口并自动运行 `catfish`（鲶鱼包装命令,起 hermes）。
///
/// macOS: 用 osascript 调 Terminal.app
/// Windows: 用 cmd start
/// Linux: 暂不支持（terminal emulator 太多，没办法通用探测）
#[tauri::command]
pub async fn open_terminal(cwd: Option<String>) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        // 拼 cd 前缀，cwd 用单引号包让带空格的路径也能用
        let inner_cmd = match cwd {
            Some(p) if !p.is_empty() => format!("cd '{}' && catfish", p.replace('\'', "'\\''")),
            _ => "catfish".to_string(),
        };
        // AppleScript：先 do script 起新窗口，再 activate 把它前置
        let script = format!(
            r#"tell application "Terminal"
                do script "{}"
                activate
            end tell"#,
            inner_cmd.replace('"', "\\\"")
        );
        Command::new("osascript")
            .args(["-e", &script])
            .output()
            .map_err(|e| format!("osascript 调用失败: {e}"))?;
        return Ok(());
    }

    #[cfg(target_os = "windows")]
    {
        // Windows: 起新 cmd 窗口跑 catfish (/K 让窗口跑完命令后保留)
        let mut cmd = Command::new("cmd");
        cmd.args(["/C", "start", "cmd", "/K"]);
        if let Some(p) = cwd {
            cmd.arg(format!("cd /d {} && catfish", p));
        } else {
            cmd.arg("catfish");
        }
        cmd.spawn().map_err(|e| format!("启动失败: {e}"))?;
        return Ok(());
    }

    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = cwd;
        Err("Linux 暂不支持自动开终端 — 请在你常用的终端里运行 'catfish'".into())
    }
}

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
        return Ok(());
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
        return Ok(stdout);
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
        return Ok(lists);
    }

    #[cfg(not(target_os = "macos"))]
    {
        Err("list_reminder_lists: 当前平台未实现 (只 macOS)".into())
    }
}
