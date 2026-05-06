//! 桌宠鼠标穿透 + 拖拽支持 (5/6 BL-E27.2 鸿波"附近点击被拦+要能拖").
//!
//! # 为啥需要
//!
//! 桌宠副窗 200×200, 但实际"鲶鱼图"只有 96×114 (底部居中) + 有时
//! 显示气泡 (顶部一块). 透明区域在 macOS 系统层 (NSPanel) 仍然吃鼠标
//! 事件, 即使 web 层 `pointer-events:none` 也只让 web 内部 hit-test
//! 失败 — 事件已被这个窗口"消费", 不会穿透到下面 app.
//!
//! # 设计
//!
//! 后台 tokio task 每 `TICK_MS` 毫秒:
//!   1. `app.cursor_position()` 拿全局鼠标 logical 位置
//!   2. 拿桌宠窗口 logical 位置
//!   3. 算"可点区域": 鲶鱼图 (96×114, 底部居中) + 可选 气泡区域
//!      (顶部 200×96, 仅气泡显示时算)
//!   4. 在区域内 → set_ignore_cursor_events(false), 收事件 (拖+点)
//!      不在 → set_ignore_cursor_events(true), 透传到下面 app
//!
//! # 气泡状态
//!
//! 用 `BUBBLE_VISIBLE: AtomicBool` 全局态. 前端 pet.tsx 在 setBubble(...)
//! 时调命令 `pet_set_bubble_visible(true)`, 8s 自动收时调 `false`.
//!
//! # 拖拽
//!
//! 鼠标在鲶鱼区域时窗口 ignoreCursorEvents=false → webview 正常收
//! pointerdown → 前端调 `getCurrentWindow().startDragging()` 即可拖.
//! 之前不工作是因为整个 NSPanel 被卡在奇怪状态, 现在区域切换后正常.

use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use tauri::{AppHandle, Manager};

/// 轮询周期. 50ms 太勤, 100ms 偶尔感觉迟钝, 80ms 经验值.
const TICK_MS: u64 = 80;

/// 桌宠图大小 (跟 pet.tsx 里 `<img width=96 height=114>` 对齐).
const PET_W: f64 = 96.0;
const PET_H: f64 = 114.0;
/// 桌宠窗 logical 大小 (跟 tauri.conf.json + pet_move_corner 对齐).
const WIN_W: f64 = 200.0;
const WIN_H: f64 = 200.0;
/// 气泡区域 (顶部 200×96, 仅气泡显示时算可点). 跟 pet.tsx 气泡 maxWidth 对齐.
const BUBBLE_H: f64 = 96.0;

/// 气泡是否显示 (frontend 通过 pet_set_bubble_visible 命令更新).
static BUBBLE_VISIBLE: AtomicBool = AtomicBool::new(false);

/// 桌宠 hover tracker 是否已启动 (防重复 schedule).
static TRACKER_STARTED: AtomicBool = AtomicBool::new(false);

/// 让前端 (pet.tsx) 切气泡显示状态.
pub fn set_bubble_visible(visible: bool) {
    BUBBLE_VISIBLE.store(visible, Ordering::Relaxed);
}

/// app setup 时调一次, 起后台轮询. 重复调用第二次起静默忽略.
pub fn schedule_pet_hover_tracker(app: AppHandle) {
    if TRACKER_STARTED.swap(true, Ordering::SeqCst) {
        log::warn!("pet_hover: 已启动过, 跳过");
        return;
    }

    tauri::async_runtime::spawn(async move {
        log::info!("pet_hover: tracker 启动, tick={}ms", TICK_MS);
        // 桌宠刚启动可能没显示, 默认不忽略 (省一次切换). 第一 tick 会修正.
        let mut last_ignore: Option<bool> = None;

        loop {
            tokio::time::sleep(Duration::from_millis(TICK_MS)).await;

            let Some(pet) = app.get_webview_window("pet") else {
                continue;
            };

            // 桌宠没显示 → 不用做事
            if !pet.is_visible().unwrap_or(false) {
                continue;
            }

            // 全局鼠标位置 (Tauri 2 返 PhysicalPosition<f64>)
            let cursor = match app.cursor_position() {
                Ok(p) => p,
                Err(e) => {
                    log::trace!("cursor_position 失败: {e}");
                    continue;
                }
            };

            // 桌宠窗 outer_position (PhysicalPosition<i32>) + scale_factor
            let win_pos = match pet.outer_position() {
                Ok(p) => p,
                Err(_) => continue,
            };
            let scale = pet.scale_factor().unwrap_or(1.0);

            // 全部转 logical (员工感知坐标)
            let cx = cursor.x / scale;
            let cy = cursor.y / scale;
            let win_x = win_pos.x as f64 / scale;
            let win_y = win_pos.y as f64 / scale;

            // 鲶鱼图区域: 200×200 窗里, 底部居中 96×114
            let pet_left = win_x + (WIN_W - PET_W) / 2.0;
            let pet_right = pet_left + PET_W;
            let pet_top = win_y + (WIN_H - PET_H);
            let pet_bottom = win_y + WIN_H;

            let in_pet = cx >= pet_left && cx <= pet_right && cy >= pet_top && cy <= pet_bottom;

            // 气泡区域 (仅气泡显示时算): 顶部 200×96
            let bubble_visible = BUBBLE_VISIBLE.load(Ordering::Relaxed);
            let in_bubble = bubble_visible
                && cx >= win_x
                && cx <= win_x + WIN_W
                && cy >= win_y
                && cy <= win_y + BUBBLE_H;

            let should_ignore = !(in_pet || in_bubble);

            // 只在状态变化时切, 减少系统调用
            if last_ignore != Some(should_ignore) {
                if let Err(e) = pet.set_ignore_cursor_events(should_ignore) {
                    log::warn!("set_ignore_cursor_events({should_ignore}) 失败: {e}");
                } else {
                    log::trace!(
                        "pet_hover: ignore={should_ignore} (in_pet={in_pet} in_bubble={in_bubble})"
                    );
                }
                last_ignore = Some(should_ignore);
            }
        }
    });
}
