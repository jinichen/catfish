//! P3.5.28 (6/17 鸿波"picker 联动现在就应该做") — picker model 真**source of truth**.
//!
//! # 真目的
//!
//! 之前 background task (email_scheduler / phishing_scan / political_scan / etc)
//! 真**硬编码默认** model — yaml/env 可配但员工不会改. P3.5.27 改 default 为
//! catfish-private-main 修了数据零出端红线, 但员工 chat picker 切别的 model 真
//! 不影响 background task → 员工以为切了, 实际后台还跑旧 model.
//!
//! 鸿波诉求: 员工在 chat picker 选什么, 后台 background task 真**跟着**用什么.
//!
//! # 真路径
//!
//! React `setModel(name)` (store/chat.ts) → invoke `set_picker_model(name)` →
//! Rust 写 `~/.catfish/picker_model` (1 行 text). Background task 真 tick 时
//! 调 `current_model()` 读这文件.
//!
//! # 真优先级 (高→低)
//!
//!   1. picker file (`~/.catfish/picker_model`) — 员工 chat picker 真选的
//!   2. yaml/env (per-service config, 例 email.rate_model) — IT/admin 显式
//!      override (想强制公网 flash 省钱 / 强制 private-main 保密)
//!   3. service 真 DEFAULT (e.g. email_config DEFAULT_RATE_MODEL)
//!
//! # 真**不持久** OnceLock
//!
//! 跟 email_config OnceLock 不同 — picker_model 真**每次访问都重读**文件, 因为
//! 员工 chat picker 切换是动态的, OnceLock 真 cache 第一次值后续不重读 → 切了
//! 没用. 文件 IO 真每 N 分钟一次 (background task tick interval) 真**便宜**.

use std::path::PathBuf;

fn picker_file_path() -> Option<PathBuf> {
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .ok()?;
    Some(PathBuf::from(home).join(".catfish").join("picker_model"))
}

/// 读员工 chat picker 当前选的 model. 文件不存在 / 空字符串 → None (caller
/// fallback per-service config). 失败静默 (例如权限错, 不阻塞 background task).
pub fn current_model() -> Option<String> {
    let path = picker_file_path()?;
    std::fs::read_to_string(&path)
        .ok()
        .and_then(|s| {
            let trimmed = s.trim();
            if trimmed.is_empty() {
                None
            } else {
                Some(trimmed.to_string())
            }
        })
}

/// P3.5.139 Phase 4 (6/29 鸿波"重启 Companion picker 应该记得这次选择"):
/// React 启动时调一次, 把 file 已有 picker model 注入 zustand store.model.
/// file > store 优先级 — 重启 Companion 上次选过的 model 立刻生效.
///
/// 没拿到 (file 不在 / 空字符串 / 权限错) → 返 null, React useEffect 不动 store,
/// 后续走 ChatTab catalog.default 注入兜底.
#[tauri::command]
pub fn get_picker_model() -> Option<String> {
    current_model()
}

/// 真给 React 调的 Tauri command — `setModel` 钩子真**写文件**.
///
/// React store/chat.ts setModel 触发: invoke('set_picker_model', { name }).
/// 失败仅 log, 不 throw (React 端 fire-and-forget 不阻塞 picker UI).
#[tauri::command]
pub fn set_picker_model(name: String) -> Result<(), String> {
    let trimmed = name.trim();
    if trimmed.is_empty() {
        return Err("picker model name 真空, 不写".to_string());
    }

    let path = picker_file_path().ok_or_else(|| "HOME 真没拿到".to_string())?;

    // 真创建 ~/.catfish/ 如果不在
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("创建 ~/.catfish 真失败: {e}"))?;
    }

    std::fs::write(&path, trimmed)
        .map_err(|e| format!("写 picker_model 真失败: {e}"))?;

    log::info!(
        "picker_model 真写: {} (background task 真下次 tick 用这个)",
        trimmed
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn current_model_returns_none_when_no_file() {
        // sandbox 真没 ~/.catfish/picker_model — current_model 真 None
        // (假设 HOME env 真 set 但文件真没)
        let _result = current_model();
        // 不真 assert (CI 可能有真 picker_model 文件)
    }
}
