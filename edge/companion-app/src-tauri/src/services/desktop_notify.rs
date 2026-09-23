//! 系统通知 —— 三个平台一个入口 (9/23)。
//!
//! 原来 `commands::system::notify` 和 `services::email_notify` 各自写了一份
//! osascript, 非 macOS 分支分别是 `Err("当前平台未实现")` 和
//! `log::info!("非 macOS 不通知 (TODO Linux/Win)")`。于是 Windows 上主动闲聊、
//! cron 完成、紧急邮件都不弹 —— 功能在, 员工永远看不到。
//!
//! Windows 走 PowerShell + WinRT ToastNotificationManager, 不加 crate、不装模块
//! (Win10+ 自带)。AppId 用 Tauri 的 bundle identifier: MSI 装的开始菜单快捷方式
//! 带这个 AUMID, 用别的字符串 Windows 会静默丢弃 toast。
//! 标题和正文走环境变量传进去, 不拼进脚本 —— 省掉转义, 也不怕内容里有引号。

#[cfg(windows)]
const WINDOWS_APP_ID: &str = "com.catfish.companion";

pub fn show(title: &str, body: &str) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        let script = format!(
            "display notification \"{}\" with title \"{}\" sound name \"Glass\"",
            body.replace('\\', "\\\\").replace('"', "\\\""),
            title.replace('\\', "\\\\").replace('"', "\\\""),
        );
        let status = std::process::Command::new("osascript")
            .args(["-e", &script])
            .status()
            .map_err(|e| format!("osascript 启动失败: {e}"))?;
        return if status.success() { Ok(()) } else { Err(format!("osascript 退出非 0: {status}")) };
    }

    #[cfg(windows)]
    {
        const PS: &str = r#"
[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$t = $x.GetElementsByTagName('text')
[void]$t.Item(0).AppendChild($x.CreateTextNode($env:CATFISH_TOAST_TITLE))
[void]$t.Item(1).AppendChild($x.CreateTextNode($env:CATFISH_TOAST_BODY))
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:CATFISH_TOAST_APPID).Show([Windows.UI.Notifications.ToastNotification]::new($x))
"#;
        let out = crate::services::process::background_command("powershell")
            .args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", PS])
            .env("CATFISH_TOAST_TITLE", title)
            .env("CATFISH_TOAST_BODY", body)
            .env("CATFISH_TOAST_APPID", WINDOWS_APP_ID)
            .output()
            .map_err(|e| format!("powershell 启动失败: {e}"))?;
        return if out.status.success() {
            Ok(())
        } else {
            Err(format!("Windows 通知失败: {}", String::from_utf8_lossy(&out.stderr).trim()))
        };
    }

    #[cfg(not(any(target_os = "macos", windows)))]
    {
        let out = std::process::Command::new("notify-send")
            .args([title, body])
            .status()
            .map_err(|e| format!("notify-send 启动失败: {e}"))?;
        if out.success() { Ok(()) } else { Err(format!("notify-send 退出非 0: {out}")) }
    }
}
