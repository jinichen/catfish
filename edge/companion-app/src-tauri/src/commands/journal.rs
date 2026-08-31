//! 员工日志只读接口。
//!
//! 用户待办统一由 Hermes 的 Reminders 工具管理；本模块不再解析、修改或维护
//! `~/.catfish/current_todos.md`。`employee_journal.md` 仅保留为员工历史流水账，
//! 供仍需读取近期上下文的功能使用。

use std::fs;
use std::path::PathBuf;

fn journal_path() -> Option<PathBuf> {
    let home = crate::util::paths::home_env().ok()?;
    Some(PathBuf::from(home).join(".catfish/employee_journal.md"))
}

/// 读取员工日志最近 5KB；文件不存在时返回空字符串。
#[tauri::command]
pub async fn journal_read_recent() -> Result<String, String> {
    const MAX_BYTES: usize = 5000;
    let path = journal_path().ok_or_else(|| "HOME 没设".to_string())?;
    if !path.exists() {
        return Ok(String::new());
    }

    let text = fs::read_to_string(&path)
        .map_err(|error| format!("读 {} 失败: {}", path.display(), error))?;
    if text.len() <= MAX_BYTES {
        return Ok(text);
    }

    let mut safe_start = text.len() - MAX_BYTES;
    while safe_start < text.len() && !text.is_char_boundary(safe_start) {
        safe_start += 1;
    }

    let tail = &text[safe_start..];
    if let Some(newline) = tail.find('\n') {
        Ok(tail[newline + 1..].to_string())
    } else {
        Ok(tail.to_string())
    }
}
