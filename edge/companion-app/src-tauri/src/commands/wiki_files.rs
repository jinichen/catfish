//! 本机 Wiki 文件列表命令。

use super::wiki_read::{build_file_info, catfish_home, WikiFileInfo};
use std::fs;

#[tauri::command]
pub async fn wiki_list_files() -> Result<Vec<WikiFileInfo>, String> {
    let home = catfish_home()?;
    let mut out = Vec::new();
    for sub in &["wiki/entities", "wiki/concepts", "wiki/queries"] {
        let dir = home.join(sub);
        if !dir.is_dir() {
            continue;
        }
        let entries = fs::read_dir(&dir).map_err(|e| format!("read_dir {dir:?} 失败: {e}"))?;
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|s| s.to_str()) != Some("md") {
                continue;
            }
            if let Some(info) = build_file_info(&home, &path) {
                out.push(info);
            }
        }
    }
    out.sort_by(|a, b| b.mtime.partial_cmp(&a.mtime).unwrap_or(std::cmp::Ordering::Equal));
    Ok(out)
}
