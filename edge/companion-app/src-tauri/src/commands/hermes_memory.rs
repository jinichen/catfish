//! BL-DASHBOARD-HERMES-MEMORY-CARD (5/16) — 读 hermes 0.13 真活的 memory 文件.
//!
//! 数据源:
//!   ~/.hermes/memories/USER.md   (target=user 写的: 员工身份/关系/偏好)
//!   ~/.hermes/memories/MEMORY.md (target=memory 写的: 项目/技术/操作事实)
//!
//! 内容格式: § 分隔 entries (hermes ENTRY_DELIMITER = "\n§\n")
//!
//! 设计立场: 跟 RelationCard / UserProfileCard 同思路 — 鲶鱼写的事**员工必须能看**.
//! BL-MEMORY-BRIDGE-STORE (5/16) 修通 hermes memory 工具后, 真活数据落这两文件,
//! 但 Dashboard 之前没 UI 入口, 员工要 cat 文件才看到. 加这个卡让员工 in-glance.

use serde::Serialize;
use std::fs;

const ENTRY_DELIMITER: &str = "\n§\n";

#[derive(Debug, Serialize)]
pub struct HermesMemoryView {
    /// USER.md 里的 entries (target=user 写的, 员工身份/关系/偏好)
    pub user_entries: Vec<String>,
    /// MEMORY.md 里的 entries (target=memory 写的, 项目/技术事实)
    pub memory_entries: Vec<String>,
    /// 两文件总字节
    pub total_bytes: u64,
    /// 单个 entry 字符上限 (来自 hermes config.yaml, 兜底 1375/2200)
    pub user_char_limit: usize,
    pub memory_char_limit: usize,
    /// 文件路径 (员工想去 Finder 看)
    pub user_file_path: String,
    pub memory_file_path: String,
}

fn home_dir() -> Option<std::path::PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(std::path::PathBuf::from)
}

fn parse_entries(content: &str) -> Vec<String> {
    if content.is_empty() {
        return Vec::new();
    }
    content
        .split(ENTRY_DELIMITER)
        .filter_map(|s| {
            let trimmed = s.trim();
            if trimmed.is_empty() {
                None
            } else {
                Some(trimmed.to_string())
            }
        })
        .collect()
}

#[tauri::command]
pub fn hermes_memory_read() -> Result<HermesMemoryView, String> {
    let home = home_dir().ok_or_else(|| "HOME 环境变量缺失".to_string())?;
    let mem_dir = home.join(".hermes").join("memories");
    let user_path = mem_dir.join("USER.md");
    let memory_path = mem_dir.join("MEMORY.md");

    let user_content = fs::read_to_string(&user_path).unwrap_or_default();
    let memory_content = fs::read_to_string(&memory_path).unwrap_or_default();

    let user_size = fs::metadata(&user_path).map(|m| m.len()).unwrap_or(0);
    let memory_size = fs::metadata(&memory_path).map(|m| m.len()).unwrap_or(0);

    Ok(HermesMemoryView {
        user_entries: parse_entries(&user_content),
        memory_entries: parse_entries(&memory_content),
        total_bytes: user_size + memory_size,
        // hermes config.yaml 默认值, UI 显示 usage 比例用
        user_char_limit: 1375,
        memory_char_limit: 2200,
        user_file_path: user_path.to_string_lossy().to_string(),
        memory_file_path: memory_path.to_string_lossy().to_string(),
    })
}
