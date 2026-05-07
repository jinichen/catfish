//! BL-CR Curator 集成 (5/7) — Tauri 命令.
//!
//! 前端调:
//!   get_curator_config() → 读 ~/.hermes/config.yaml curator 段
//!   set_curator_config(...) → 写 curator 段
//!   ensure_curator_default() → 第一次启动 / install.sh 调一次, 写保守默认 (已存在不动)
//!   get_curator_state() → 读 ~/.hermes/skills/.curator_state, 给 Dashboard CuratorCard

use crate::services::{curator_config, curator_state};

#[tauri::command]
pub fn get_curator_config() -> Result<curator_config::CuratorConfig, String> {
    curator_config::load().map_err(|e| e.to_string())
}

#[tauri::command]
pub fn set_curator_config(
    enabled: bool,
    interval_hours: u32,
    min_idle_hours: u32,
    stale_after_days: u32,
    archive_after_days: u32,
) -> Result<curator_config::CuratorConfig, String> {
    let cfg = curator_config::CuratorConfig {
        enabled,
        interval_hours,
        min_idle_hours,
        stale_after_days,
        archive_after_days,
    };
    curator_config::save(&cfg).map_err(|e| e.to_string())?;
    curator_config::load().map_err(|e| e.to_string())
}

#[tauri::command]
pub fn ensure_curator_default() -> Result<bool, String> {
    curator_config::ensure_default().map_err(|e| e.to_string())
}

#[tauri::command]
pub fn get_curator_state() -> Result<curator_state::CuratorStateView, String> {
    curator_state::load().map_err(|e| e.to_string())
}
