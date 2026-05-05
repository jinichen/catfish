//! 桌宠副窗控制 (BL-E27 spike, 5/5 凌晨).
//!
//! 三个命令:
//!   - pet_show: 把桌宠副窗显出来 (默认 visible:false 启动)
//!   - pet_hide: 隐藏 (右键菜单"先关掉" 用)
//!   - pet_clicked: 桌宠点了 → 把主窗口拉到前台 + 聚焦
//!
//! 设计:
//!   - 桌宠 window label="pet" 在 tauri.conf.json 预定义
//!   - 主窗口 label="main"
//!   - 不动主窗口, 只 toggle 副窗

use tauri::{Manager, AppHandle};

/// 显示桌宠副窗 (默认启动时 visible:false).
#[tauri::command]
pub async fn pet_show(app: AppHandle) -> Result<(), String> {
    let win = app
        .get_webview_window("pet")
        .ok_or_else(|| "pet 副窗找不到 (tauri.conf.json label='pet'?)".to_string())?;
    win.show().map_err(|e| format!("show 失败: {e}"))?;
    // BL-E27 关键: 桌宠不抢 focus, 不上 dock.
    // (always_on_top 已在 tauri.conf.json 配, 这里不重复设)
    Ok(())
}

/// 隐藏桌宠副窗.
#[tauri::command]
pub async fn pet_hide(app: AppHandle) -> Result<(), String> {
    let win = app
        .get_webview_window("pet")
        .ok_or_else(|| "pet 副窗找不到".to_string())?;
    win.hide().map_err(|e| format!("hide 失败: {e}"))?;
    Ok(())
}

/// 桌宠被点 → 把主窗口拉前台 + 聚焦 (双击唤起 Companion 行为).
#[tauri::command]
pub async fn pet_clicked(app: AppHandle) -> Result<(), String> {
    let main = app
        .get_webview_window("main")
        .ok_or_else(|| "main 主窗口找不到".to_string())?;
    let _ = main.unminimize();
    let _ = main.show();
    let _ = main.set_focus();
    log::info!("pet_clicked: 主窗口已唤醒");
    Ok(())
}

/// 把桌宠移到屏幕 4 个角之一 (5/5 鸿波报"拖拽完全不动" 妥协方案).
/// corner: "tl" | "tr" | "bl" | "br" (top-left/right, bottom-left/right).
/// BL-E27.1 真做 (5/22) 时用 NSPanel objc2 修真拖拽, 那时这命令仍保留作为补充.
#[tauri::command]
pub async fn pet_move_corner(app: AppHandle, corner: String) -> Result<(), String> {
    let pet = app
        .get_webview_window("pet")
        .ok_or_else(|| "pet 副窗找不到".to_string())?;
    let monitor = pet
        .current_monitor()
        .map_err(|e| format!("拿屏幕失败: {e}"))?
        .ok_or_else(|| "找不到当前 monitor".to_string())?;
    let m_size = monitor.size();
    let m_pos = monitor.position();
    // 桌宠窗口尺寸 (跟 tauri.conf.json 一致, 120x120)
    const W: i32 = 120;
    const H: i32 = 120;
    // 边距, 别完全贴边 (留 16px)
    const MARGIN: i32 = 16;

    let (x, y) = match corner.as_str() {
        "tl" => (m_pos.x + MARGIN, m_pos.y + MARGIN),
        "tr" => (m_pos.x + m_size.width as i32 - W - MARGIN, m_pos.y + MARGIN),
        "bl" => (m_pos.x + MARGIN, m_pos.y + m_size.height as i32 - H - MARGIN),
        "br" => (
            m_pos.x + m_size.width as i32 - W - MARGIN,
            m_pos.y + m_size.height as i32 - H - MARGIN,
        ),
        other => return Err(format!("无效 corner: {other}, 用 tl/tr/bl/br")),
    };
    pet.set_position(tauri::PhysicalPosition::new(x, y))
        .map_err(|e| format!("set_position 失败: {e}"))?;
    log::info!("pet_move_corner({corner}): ({x}, {y})");
    Ok(())
}
