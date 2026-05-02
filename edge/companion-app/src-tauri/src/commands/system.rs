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
