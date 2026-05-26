//! BL-ADVISOR-DRAFTS (5/21 Phase 7 第 2 步): 草稿存储.
//!
//! 设计稿 §6.1: ~/.catfish/outputs/<YYYY-MM-DD>/<task-type>-<key>.md
//!
//! 跟 BL-CENTRAL-EDGE (5/17): 草稿在员工本机, 不出端. catfish 不替员工发, 只起草到 outputs/,
//! 员工自己点开看/改/复制后自己发.
//!
//! 这一层 Rust 提供:
//!   - draft_save: LLM tool 调完 (e.g. draft_email_reply) 把草稿内容写到 outputs/<date>/
//!   - draft_read: UI 展开 DraftPreview 时读全文
//!   - draft_list_today: 列今天所有草稿元数据 (filename + size + mtime)
//!   - draft_open_in_editor: 调系统默认编辑器打开 (员工自己改 + 复制后发)
//!   - recent_outputs_list (5/26): 跨日期扫 outputs/<*>/<*>, 过去 N 小时改的文件,
//!     给 chat timeout toast 用 (BL-X: 替代砍掉的 gateway recent_outputs.list_recent)

use std::path::PathBuf;
use chrono::Utc;
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DraftRef {
    /// 文件名 (不含目录), e.g. "reply-laoli-balanced.md"
    pub filename: String,
    /// 完整路径 (debug 用 / 员工可拷)
    pub abs_path: String,
    /// ISO-8601 mtime
    pub modified_at: String,
    /// 大小字节
    pub bytes: u64,
}

// ── 路径 helpers ─────────────────────────────────────────────────────

fn outputs_root() -> Result<PathBuf, String> {
    let home = std::env::var("HOME").map_err(|e| format!("HOME 未设: {e}"))?;
    Ok(PathBuf::from(home).join(".catfish").join("outputs"))
}

fn today_dir() -> Result<PathBuf, String> {
    let date = Utc::now().format("%Y-%m-%d").to_string();
    Ok(outputs_root()?.join(date))
}

fn date_dir(date: &str) -> Result<PathBuf, String> {
    // date 必须是 YYYY-MM-DD 格式 — 防 path traversal
    if !date.chars().all(|c| c.is_ascii_digit() || c == '-') || date.len() != 10 {
        return Err(format!("date 格式非法 (期望 YYYY-MM-DD): {date}"));
    }
    Ok(outputs_root()?.join(date))
}

fn safe_filename(filename: &str) -> Result<&str, String> {
    // 防 path traversal: 不允许 / .. 等
    if filename.contains('/') || filename.contains("..") || filename.contains('\\') {
        return Err(format!("filename 含非法字符: {filename}"));
    }
    if filename.is_empty() || filename.len() > 200 {
        return Err(format!("filename 长度非法: {}", filename.len()));
    }
    Ok(filename)
}

// ── Tauri commands ───────────────────────────────────────────────────

/// LLM tool 写草稿. 路径自动 outputs/<today>/<filename>. 内容覆盖式写.
///
/// filename 限制: 不含 / \\ ..; 长度 1-200.
/// 返回写入的绝对路径 (前端 UI 用来显示 + 调 open_in_editor).
#[tauri::command]
pub async fn draft_save(filename: String, content: String) -> Result<String, String> {
    let fname = safe_filename(&filename)?;
    let dir = today_dir()?;
    std::fs::create_dir_all(&dir)
        .map_err(|e| format!("创建 outputs/<today>/ 失败: {e}"))?;

    let path = dir.join(fname);
    let tmp = path.with_extension(format!(
        "{}.tmp",
        path.extension().and_then(|s| s.to_str()).unwrap_or("part")
    ));

    std::fs::write(&tmp, content)
        .map_err(|e| format!("写 draft tmp 失败: {e}"))?;
    std::fs::rename(&tmp, &path)
        .map_err(|e| format!("rename draft 失败: {e}"))?;
    Ok(path.to_string_lossy().to_string())
}

/// 读草稿内容.
///
/// date: "YYYY-MM-DD" — 限定格式防 path traversal.
/// filename: 同 safe_filename 检查.
#[tauri::command]
pub async fn draft_read(date: String, filename: String) -> Result<String, String> {
    let fname = safe_filename(&filename)?;
    let dir = date_dir(&date)?;
    let path = dir.join(fname);
    if !path.exists() {
        return Err(format!("草稿不存在: {}", path.display()));
    }
    std::fs::read_to_string(&path)
        .map_err(|e| format!("读 draft 失败: {e}"))
}

/// 列今天所有草稿 (mtime 倒序). 没目录 / 空目录 → 空 Vec.
#[tauri::command]
pub async fn draft_list_today() -> Result<Vec<DraftRef>, String> {
    let dir = today_dir()?;
    if !dir.exists() {
        return Ok(Vec::new());
    }
    let entries = std::fs::read_dir(&dir)
        .map_err(|e| format!("read_dir 失败: {e}"))?;
    let mut out: Vec<DraftRef> = Vec::new();
    for e in entries.flatten() {
        let p = e.path();
        if !p.is_file() {
            continue;
        }
        let filename = match p.file_name().and_then(|n| n.to_str()) {
            Some(n) => n.to_string(),
            None => continue,
        };
        // 跳 tmp 半成品
        if filename.ends_with(".tmp") {
            continue;
        }
        let meta = match e.metadata() {
            Ok(m) => m,
            Err(_) => continue,
        };
        let modified_at = match meta.modified() {
            Ok(t) => {
                let dt: chrono::DateTime<chrono::Utc> = t.into();
                dt.to_rfc3339()
            }
            Err(_) => continue,
        };
        out.push(DraftRef {
            filename,
            abs_path: p.to_string_lossy().to_string(),
            modified_at,
            bytes: meta.len(),
        });
    }
    out.sort_by(|a, b| b.modified_at.cmp(&a.modified_at));
    Ok(out)
}

/// 调系统默认编辑器打开草稿. macOS = `open <path>`, 跟 Finder 双击同效.
///
/// 5/22 鸿波二修: 之前 `open` 命令对不存在的文件返 exit 1, 但前端 `console.warn` 吞了,
/// 员工看到 UI 显"草稿已打开" 而实际没弹编辑器. 加 pre-check 文件存在, 给具体错让
/// 前端能告诉员工是 LLM 编了 path 还是真打不开.
#[tauri::command]
pub async fn draft_open_in_editor(abs_path: String) -> Result<(), String> {
    // 防滥用: 只允许 outputs/ 下的路径
    let outputs = outputs_root()?;
    let outputs_str = outputs.to_string_lossy().to_string();
    if !abs_path.starts_with(&outputs_str) {
        return Err(format!("路径不在 outputs/ 下, 拒打开: {abs_path}"));
    }
    // 防 path traversal
    if abs_path.contains("..") {
        return Err(format!("路径含 ..: {abs_path}"));
    }

    // 5/22 二修: pre-check 文件存在 — open 命令对不存在文件虽然返非 0,
    // 但 stderr 可能空, 错信息不直观. 这里给清晰的中文错让前端能 hint
    // "LLM 编了路径没真落盘"
    let path = std::path::Path::new(&abs_path);
    if !path.exists() {
        return Err(format!(
            "文件不存在: {abs_path}. \
            可能 LLM 输出 draftPath 但没真调 catfish_draft_email_reply 落盘. \
            可在 chat 让 catfish 重新起草."
        ));
    }
    if !path.is_file() {
        return Err(format!("不是文件 (是目录?): {abs_path}"));
    }

    #[cfg(target_os = "macos")]
    {
        let out = std::process::Command::new("open")
            .arg(&abs_path)
            .output()
            .map_err(|e| format!("调 open 失败: {e}"))?;
        if !out.status.success() {
            let stderr = String::from_utf8_lossy(&out.stderr);
            return Err(format!(
                "macOS open 返非 0 (.md 默认应用关联可能挂): {}. \
                试在 Finder 双击 {abs_path} 看默认应用是啥.",
                if stderr.trim().is_empty() {
                    "(stderr 为空)".to_string()
                } else {
                    stderr.to_string()
                }
            ));
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        return Err("draft_open_in_editor 当前只支持 macOS".to_string());
    }
    Ok(())
}

// ── BL-X (5/26): chat timeout 自显本地 outputs ──────────────────────
//
// 5/26 audit 砍掉 gateway recent_outputs.list_recent (gateway 不再扫员工
// ~/.catfish/outputs/). 替代方案: Companion (跑员工 mac) 自己扫, 在 chat
// timeout 时 toast 列过去 N 小时改过的文件, 让员工看到鲶鱼写过哪些东西
// (而不是误以为白干).
//
// 跟 draft_list_today 区别: 这个跨日期目录 (outputs/<date>/), 按 mtime ≤ N
// 小时过滤, 不限当天.

/// `recent_outputs_list(24)` → 过去 24 小时改过的 outputs 文件, mtime 倒序.
///
/// 扫 `~/.catfish/outputs/*/` 下所有文件 (跨日期目录), 不递归更深.
/// 没目录 / 空 → 空 Vec. .tmp 跳过.
#[tauri::command]
pub async fn recent_outputs_list(hours: u64) -> Result<Vec<DraftRef>, String> {
    let root = outputs_root()?;
    if !root.exists() {
        return Ok(Vec::new());
    }
    let cutoff = std::time::SystemTime::now()
        .checked_sub(std::time::Duration::from_secs(hours.saturating_mul(3600)))
        .ok_or_else(|| "hours 太大 SystemTime 减法溢出".to_string())?;

    let mut out: Vec<DraftRef> = Vec::new();
    // 一层日期目录 (outputs/<YYYY-MM-DD>/)
    let date_dirs = std::fs::read_dir(&root)
        .map_err(|e| format!("read_dir {} 失败: {e}", root.display()))?;
    for date_entry in date_dirs.flatten() {
        let date_path = date_entry.path();
        if !date_path.is_dir() {
            continue;
        }
        // 二层文件
        let files = match std::fs::read_dir(&date_path) {
            Ok(it) => it,
            Err(_) => continue,
        };
        for file_entry in files.flatten() {
            let p = file_entry.path();
            if !p.is_file() {
                continue;
            }
            let filename = match p.file_name().and_then(|n| n.to_str()) {
                Some(n) => n.to_string(),
                None => continue,
            };
            if filename.ends_with(".tmp") {
                continue;
            }
            let meta = match file_entry.metadata() {
                Ok(m) => m,
                Err(_) => continue,
            };
            let mtime = match meta.modified() {
                Ok(t) => t,
                Err(_) => continue,
            };
            if mtime < cutoff {
                continue;
            }
            let dt: chrono::DateTime<chrono::Utc> = mtime.into();
            out.push(DraftRef {
                filename,
                abs_path: p.to_string_lossy().to_string(),
                modified_at: dt.to_rfc3339(),
                bytes: meta.len(),
            });
        }
    }
    out.sort_by(|a, b| b.modified_at.cmp(&a.modified_at));
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn safe_filename_rejects_traversal() {
        assert!(safe_filename("ok-file.md").is_ok());
        assert!(safe_filename("../escape.md").is_err());
        assert!(safe_filename("dir/file.md").is_err());
        assert!(safe_filename("").is_err());
        assert!(safe_filename(&"x".repeat(201)).is_err());
    }

    #[test]
    fn date_dir_rejects_malformed() {
        assert!(date_dir("2026-05-22").is_ok());
        assert!(date_dir("../etc").is_err());
        assert!(date_dir("2026/05/22").is_err());  // / 被过滤
        assert!(date_dir("20260522").is_err());   // 长度不对
    }
}
