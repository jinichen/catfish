//! menubar / system tray 集成。
//!
//! macOS 显示在右上角菜单栏，Windows 在右下角通知区。
//! 点托盘图标弹出菜单：显示主窗口 / 服务一键启停 / 退出。

pub mod menu;

#[cfg(desktop)]
pub fn install(_app: &tauri::AppHandle) -> tauri::Result<()> {
    // TODO: 用 tauri::tray::TrayIconBuilder 装托盘
    Ok(())
}
