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

use std::sync::Mutex;
use tauri::{Manager, AppHandle};
use serde::{Deserialize, Serialize};

// 5/6 鸿波: Tauri 2 跨 webview 事件路由 (app.emit_to / pet.emit) 实测都通不到 pet
// 副窗 listener (3 种写法都试过 emit_sent:true 但 listener 永远不接). 弃用 event,
// 走 polling — 全局 Mutex<Option<PendingBubble>>:
//   主窗 pet_emit_bubble → 写入这个 Mutex
//   pet.tsx 300ms 一次轮询 pet_pop_bubble() → atomic take + clear
// 100% 可靠. 300ms 延迟员工感知不到.
//
// (event 路径 A 的死代码 5/6 已清理, 万一 Tauri 修了协议再加回, 5 分钟事.)
static PENDING_BUBBLE: Mutex<Option<PendingBubble>> = Mutex::new(None);
static PENDING_STATUS: Mutex<Option<String>> = Mutex::new(None);

#[derive(Clone, Serialize, Deserialize)]
pub struct PendingBubble {
    pub text: String,
    #[serde(rename = "agentName")]
    pub agent_name: Option<String>,
    /// 主窗写时的时间戳 (ms), pet 端用来去重 — 拉到旧 bubble 不重显
    pub ts: u64,
}

/// 显示桌宠副窗 (默认启动时 visible:false).
#[tauri::command]
pub async fn pet_show(app: AppHandle) -> Result<(), String> {
    let win = app
        .get_webview_window("pet")
        .ok_or_else(|| "pet 副窗找不到 (tauri.conf.json label='pet'?)".to_string())?;
    // 5/6 BL-E27.2: 默认 ignore=true (透明区穿透), pet_hover tracker 80ms 后会
    // 在鲶鱼区域切回 false. 这一行让"显示瞬间"也不拦事件.
    let _ = win.set_ignore_cursor_events(true);
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

/// 桌宠是否可见 (主窗 useProactiveScheduler 决定走桌宠气泡 vs macOS 通知 fallback).
#[tauri::command]
pub async fn pet_is_visible(app: AppHandle) -> Result<bool, String> {
    let win = app
        .get_webview_window("pet")
        .ok_or_else(|| "pet 副窗找不到".to_string())?;
    Ok(win.is_visible().unwrap_or(false))
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
    // 5/8 BL-E27.4: 单击桌宠 = 员工已看 → 写 seen_ts.json 重置颜色 indicator
    if let Err(e) = crate::services::pet_status::mark_all_seen() {
        log::warn!("BL-E27.4: pet_status mark_all_seen 失败 (不阻塞): {e}");
    }
    log::info!("pet_clicked: 主窗口已唤醒, status 已 mark seen");
    Ok(())
}

/// 5/6 BL-E27.2: 前端通知后台"气泡显示状态变化".
/// pet_hover tracker 据此扩展可点区域 — 气泡显示时, 气泡区也接事件.
#[tauri::command]
pub fn pet_set_bubble_visible(visible: bool) {
    crate::services::pet_hover::set_bubble_visible(visible);
}

/// 5/6 BL-E27.2: 触发桌宠窗口拖拽. 前端 mousedown 在鲶鱼区域时调一次.
/// 配合 pet_hover tracker 切的 ignoreCursorEvents=false, NSPanel 真能拖了.
#[tauri::command]
pub async fn pet_start_drag(app: AppHandle) -> Result<(), String> {
    let pet = app
        .get_webview_window("pet")
        .ok_or_else(|| "pet 副窗找不到".to_string())?;
    pet.start_dragging()
        .map_err(|e| format!("start_dragging 失败: {e}"))?;
    Ok(())
}

/// 5/6 鸿波报"主动闲聊不冒泡": frontend `emitTo('pet', ...)` 跨窗 broadcast
/// 行为不一致, 桌宠副窗收不到. 用 Rust 端 emit_to(pet_label) 100% 可靠.
///
/// 主窗 (主动闲聊 / Dashboard 测一下) 调这个命令, Rust 直接定向发到 pet webview.
#[derive(Serialize, Clone)]
pub struct PetEmitBubbleDiag {
    pub pet_window_exists: bool,
    pub pet_visible: bool,
    /// 已 push 到 polling buffer (= pet.tsx 下一次 tick 拿走)
    pub queued: bool,
}

#[tauri::command]
pub async fn pet_emit_bubble(
    app: AppHandle,
    text: String,
    agent_name: Option<String>,
) -> Result<PetEmitBubbleDiag, String> {
    // 检查 pet 副窗存在 + 可见 (诊断用, 不可见也照常 queue 防 race)
    let pet_opt = app.get_webview_window("pet");
    let pet_window_exists = pet_opt.is_some();
    let pet_visible = pet_opt
        .as_ref()
        .map(|p| p.is_visible().unwrap_or(false))
        .unwrap_or(false);
    eprintln!(
        "[catfish] pet_emit_bubble: pet_window_exists={pet_window_exists} visible={pet_visible} text='{}'",
        text.chars().take(40).collect::<String>(),
    );
    if !pet_window_exists {
        return Err("pet 副窗找不到".to_string());
    }
    // 写全局 polling buffer, pet.tsx 300ms tick 内拉走
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0);
    {
        let mut guard = PENDING_BUBBLE.lock().unwrap();
        *guard = Some(PendingBubble { text, agent_name, ts });
    }
    eprintln!("[catfish] pet_emit_bubble: ✅ queued (ts={ts})");
    Ok(PetEmitBubbleDiag {
        pet_window_exists,
        pet_visible,
        queued: true,
    })
}

/// 5/6 polling fallback: pet.tsx 每 300ms 调一次, atomic take + clear.
/// 没新 bubble → 返回 None, 性能开销 ~ms 级.
#[tauri::command]
pub fn pet_pop_bubble() -> Option<PendingBubble> {
    let mut guard = PENDING_BUBBLE.lock().unwrap();
    guard.take()
}

/// 同样 polling 的状态 (idle/thinking/running/done) 取走.
#[tauri::command]
pub fn pet_pop_status() -> Option<String> {
    let mut guard = PENDING_STATUS.lock().unwrap();
    guard.take()
}

/// 5/6: 让 pet.tsx 在 listener 注册 + 收到事件时反向 log 到 Rust stderr.
/// 用户在 `npm run tauri:dev` terminal 里能看到 webview 实际状态, 排查 listener 没 mount / event 没收到 等问题.
#[tauri::command]
pub fn pet_log(tag: String, msg: String) {
    eprintln!("[catfish] [pet-webview] [{tag}] {msg}");
}

/// 5/6: 同样 Rust 端定向发 agent_status (idle / thinking / running / done).
/// 前端 usePetStatusBroadcast 监听 chat 状态变化时调.
#[tauri::command]
pub async fn pet_emit_status(app: AppHandle, status: String) -> Result<(), String> {
    if !["idle", "thinking", "running", "done"].contains(&status.as_str()) {
        return Err(format!("无效状态: {status}, 允许 idle/thinking/running/done"));
    }
    let _ = app
        .get_webview_window("pet")
        .ok_or_else(|| "pet 副窗找不到".to_string())?;
    // 写全局 polling buffer
    {
        let mut guard = PENDING_STATUS.lock().unwrap();
        *guard = Some(status.clone());
    }
    log::trace!("pet_emit_status: {status}");
    Ok(())
}

/// 把桌宠移到屏幕 4 个角之一 (5/5 鸿波报"拖拽完全不动" 妥协方案).
/// corner: "tl" | "tr" | "bl" | "br" (top-left/right, bottom-left/right).
///
/// 5/5 鸿波二次报"3/4 出屏幕": Retina 屏 monitor.size() 返**物理像素** (2880x1800),
/// setPosition 物理坐标导致桌宠被定到 logical (1440x900) 屏外. 修: 用 LogicalPosition
/// + 物理 size 除以 scale_factor 得 logical 尺寸. 顶部留 30px (menu bar),
/// 底部留 80px (dock 默认估值, 不准但比贴底好).
///
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
    let m_size = monitor.size();           // PhysicalSize (Retina 2x = 2880x1800)
    let m_pos = monitor.position();        // PhysicalPosition
    let scale = monitor.scale_factor();    // 2.0 on Retina, 1.0 on non-Retina
    // 物理坐标 → logical (员工真实看到的)
    let logical_w = (m_size.width as f64 / scale) as i32;
    let logical_h = (m_size.height as f64 / scale) as i32;
    let logical_pos_x = (m_pos.x as f64 / scale) as i32;
    let logical_pos_y = (m_pos.y as f64 / scale) as i32;

    // 桌宠窗口 logical 尺寸 (5/6 起从 120x120 扩到 200x200, 顶部留气泡空间)
    const W: i32 = 200;
    const H: i32 = 200;
    const MARGIN: i32 = 16;
    // macOS 顶部 menu bar 24-30px, 底部 dock 默认 80-100px (员工设置可变, 估个保守值)
    const TOP_RESERVED: i32 = 32;
    const BOTTOM_RESERVED: i32 = 80;

    let (x, y) = match corner.as_str() {
        "tl" => (logical_pos_x + MARGIN, logical_pos_y + TOP_RESERVED),
        "tr" => (
            logical_pos_x + logical_w - W - MARGIN,
            logical_pos_y + TOP_RESERVED,
        ),
        "bl" => (
            logical_pos_x + MARGIN,
            logical_pos_y + logical_h - H - BOTTOM_RESERVED,
        ),
        "br" => (
            logical_pos_x + logical_w - W - MARGIN,
            logical_pos_y + logical_h - H - BOTTOM_RESERVED,
        ),
        other => return Err(format!("无效 corner: {other}, 用 tl/tr/bl/br")),
    };
    pet.set_position(tauri::LogicalPosition::new(x as f64, y as f64))
        .map_err(|e| format!("set_position 失败: {e}"))?;
    log::info!(
        "pet_move_corner({corner}): logical ({x}, {y}) on {logical_w}x{logical_h} (scale {scale})",
    );
    Ok(())
}


// ============================================================
// BL-E27.4 (5/8) — 桌宠状态颜色 indicator
// ============================================================

/// 拿桌宠当前状态颜色摘要 (供 pet.tsx 5s polling).
/// 4 状态: default / running / completed / failed (优先级 红>绿>蓝>默认).
#[tauri::command]
pub fn pet_status_summary() -> Result<crate::services::pet_status::PetStatusSummary, String> {
    crate::services::pet_status::read_status_summary().map_err(|e| e.to_string())
}

/// 显式清 unseen 标记 (一般 pet_clicked 自动调, 这里给 Dashboard 备用入口).
#[tauri::command]
pub fn pet_status_clear() -> Result<f64, String> {
    crate::services::pet_status::mark_all_seen().map_err(|e| e.to_string())
}
