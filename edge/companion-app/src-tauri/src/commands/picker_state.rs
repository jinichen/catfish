//! P3.5.2 (6/16 鸿波 Dream Engine 配套): chat picker 持久化到 ~/.catfish/picker_state.json.
//!
//! # 设计 (方案 B, 6/16 鸿波拍)
//!
//! `chat.ts` 发请求前 fire-and-forget invoke `picker_state_save(model)`. Rust atomic write
//! 到 `~/.catfish/picker_state.json`. catfish-memory plugin (in-hermes) `_get_summarize_model`
//! 加新优先级 picker_state.json > yaml > env. plugin sync_turn 自动跟随 picker.
//!
//! 真因 (B 方案 audit): hermes MemoryProvider.sync_turn 签名是
//! `(user_content, assistant_content, session_id, messages?)`, **没 client request header /
//! picker model 入参**. plugin 拿不到 picker 状态 — yaml 静态是 hermes API 限制.
//! 文件中转 (companion 写 / plugin 读) 是绕开 hermes API 限制的最简方案.
//!
//! # 文件格式
//!
//! ```json
//! {
//!   "chat_model": "catfish-public-deepseek-flash",
//!   "updated_at": "2026-06-16T05:58:03+08:00"
//! }
//! ```
//!
//! # Atomic write
//!
//! tmp + rename pattern. write 失败 caller silent (chat send 不该被 picker_state write 阻塞).

use std::path::PathBuf;

use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct PickerState {
    pub chat_model: String,
    pub updated_at: String,
}

fn picker_state_path() -> Result<PathBuf, String> {
    let home = std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .ok_or("HOME 未设")?;
    let dir = PathBuf::from(home).join(".catfish");
    std::fs::create_dir_all(&dir).map_err(|e| format!("创建 ~/.catfish/ 失败: {e}"))?;
    Ok(dir.join("picker_state.json"))
}

fn now_iso8601() -> String {
    chrono::Utc::now().to_rfc3339()
}

/// 只写 picker_state.json 这一份 —— **不要直接调它**。
///
/// 8/9: 唯一该调的是 `services::picker_config::persist_picker_model`, 它会把
/// picker_model 和 picker_state.json 两个副本一起写。单独写一份正是 8/9 之前
/// 那个静默漂移的来源 (两条独立写入路径 → 两个文件各自为政)。
///
/// 这个函数保持 pub 只是为了给 persist_picker_model 调用; 它自己不做"要不要写
/// 另一份"的判断 —— 那是上层的职责, 分两处判断就又回到漂移。
pub(crate) fn write_picker_state_file(model: &str) -> Result<(), String> {
    let model = model.trim().to_string();
    if model.is_empty() {
        return Err("Picker state: model 为空, 不写".into());
    }

    let target = picker_state_path()?;
    let tmp = target.with_extension("json.tmp");

    let state = PickerState {
        chat_model: model,
        updated_at: now_iso8601(),
    };
    let text = serde_json::to_string_pretty(&state)
        .map_err(|e| format!("serialize picker_state 失败: {e}"))?;

    // Atomic: tmp + rename. 失败 fallback 直写 (跟 advisor_cache.rs P3.4.C 同款 pattern).
    if let Err(e) = std::fs::write(&tmp, &text) {
        log::warn!("[picker_state] tmp 写挂 ({e}), fallback 直写");
        return std::fs::write(&target, &text)
            .map_err(|e2| format!("fallback 直写 picker_state.json 失败: {e2}"));
    }
    if let Err(e) = std::fs::rename(&tmp, &target) {
        log::warn!("[picker_state] rename 挂 ({e}), fallback 直写");
        let _ = std::fs::remove_file(&tmp);
        return std::fs::write(&target, &text)
            .map_err(|e2| format!("fallback 直写 picker_state.json 失败: {e2}"));
    }
    Ok(())
}

/// P3.5.2.1 (6/16 鸿波): chat.ts / ChatModelPicker fire-and-forget 调.
///
/// 8/9 改成走 `persist_picker_model` —— 它把 picker_model 和 picker_state.json
/// 两份一起写。之前这里只写 json 那一份, 是两条独立写入路径之一。
#[tauri::command(rename_all = "camelCase")]
pub async fn picker_state_save(model: String) -> Result<(), String> {
    crate::services::picker_config::persist_picker_model(&model)
}

/// 调试用: 前端 cross-check 当前持久化的 picker model.
#[tauri::command(rename_all = "camelCase")]
pub async fn picker_state_get() -> Result<Option<PickerState>, String> {
    let path = picker_state_path()?;
    if !path.exists() {
        return Ok(None);
    }
    let text = std::fs::read_to_string(&path)
        .map_err(|e| format!("读 picker_state.json 失败: {e}"))?;
    let state: PickerState = serde_json::from_str(&text)
        .map_err(|e| format!("parse picker_state.json 失败 (schema 不匹配?): {e}"))?;
    Ok(Some(state))
}
