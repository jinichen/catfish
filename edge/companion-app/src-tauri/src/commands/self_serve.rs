//! BL-EMPLOYEE-SELF-SERVE Phase 1 (6/8 鸿波 ship): 员工自助工具集.
//!
//! Spec: docs/EMPLOYEE-SELF-SERVE-TOOLS-SPEC.md
//!
//! # 跟 manifesto 公理 1 (员工主权) 关系
//!
//! 这 module 提供"员工自己管自己 catfish 数据"的 Tauri command 集合 — IT 没
//! 远程触发能力 (跟 advisory feed pull-based 同哲学). 公理 1 的产品落地.
//!
//! # A1+A2 (本次 ship)
//!
//! - A1 reset: 移 ~/.catfish/ 到 ~/.catfish-reset-trash/<ts>/, 5s undo
//! - A2 export: 打包 ~/.catfish/ 到员工选的输出路径
//!
//! # A3 (BL): import (跟 A2 对称, 从 .zip 还原)
//! # A4 (本次 ship 在 transparent_log.rs): 数据外发日志

use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

fn catfish_home() -> Result<PathBuf, String> {
    Ok(home_dir().ok_or("找不到 HOME")?.join(".catfish"))
}

// ── A1: 重置 ──────────────────────────────────────────────────

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ResetSummary {
    pub conversations_deleted: u64,
    pub recordings_deleted: u64,
    pub wiki_files_deleted: u64,
    pub skills_deleted: u64,
    pub bytes_freed_total: u64,
    pub trash_path: String, // ~/.catfish-reset-trash/<ts>/, 5s undo
}

fn count_files_recursive(path: &Path) -> (u64, u64) {
    // (count, bytes)
    if !path.exists() {
        return (0, 0);
    }
    let mut count = 0u64;
    let mut bytes = 0u64;
    if let Ok(entries) = fs::read_dir(path) {
        for entry in entries.flatten() {
            let p = entry.path();
            if p.is_file() {
                count += 1;
                if let Ok(meta) = p.metadata() {
                    bytes += meta.len();
                }
            } else if p.is_dir() {
                let (sub_c, sub_b) = count_files_recursive(&p);
                count += sub_c;
                bytes += sub_b;
            }
        }
    }
    (count, bytes)
}

fn preview_reset_blocking() -> Result<ResetSummary, String> {
    let home = catfish_home()?;

    // ~/.catfish/conversations/ 等子目录统计
    let (conversations, _) = count_files_recursive(&home.join("conversations"));
    let (recordings, _recording_bytes) = count_files_recursive(&home.join("recordings"));
    let (wiki, _) = count_files_recursive(&home.join("wiki"));
    let (skills, _) = count_files_recursive(&home.join("skills"));
    let (_, total_bytes) = count_files_recursive(&home);

    Ok(ResetSummary {
        conversations_deleted: conversations,
        recordings_deleted: recordings,
        wiki_files_deleted: wiki,
        skills_deleted: skills,
        bytes_freed_total: total_bytes,
        trash_path: String::new(), // preview 不真移, trash_path 空
    })
}

fn execute_reset_blocking() -> Result<ResetSummary, String> {
    let home = home_dir().ok_or("找不到 HOME")?;
    let catfish = home.join(".catfish");

    if !catfish.exists() {
        return Ok(ResetSummary {
            conversations_deleted: 0,
            recordings_deleted: 0,
            wiki_files_deleted: 0,
            skills_deleted: 0,
            bytes_freed_total: 0,
            trash_path: String::new(),
        });
    }

    // preview 先算数 (在移动前)
    let mut summary = preview_reset_blocking()?;

    // 移到 trash dir (跟 E7.P2 uninstall_skill 同 pattern)
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let trash = home.join(".catfish-reset-trash").join(format!("{ts}"));
    fs::create_dir_all(&trash).map_err(|e| format!("创建 trash 失败: {e}"))?;
    let trash_dest = trash.join("catfish");

    fs::rename(&catfish, &trash_dest).map_err(|e| format!("移到 trash 失败: {e}"))?;

    summary.trash_path = trash_dest.to_string_lossy().into_owned();
    Ok(summary)
}

fn restore_reset_blocking(trash_path: String) -> Result<(), String> {
    let trash_p = PathBuf::from(&trash_path);
    if !trash_p.exists() {
        return Err(format!("trash 路径不存在 (可能已 5s 过期被自动 GC): {trash_path}"));
    }
    let home = home_dir().ok_or("找不到 HOME")?;
    let catfish = home.join(".catfish");
    if catfish.exists() {
        return Err("~/.catfish/ 已存在 (你已经重新装了 catfish 数据), 不能 restore".into());
    }
    fs::rename(&trash_p, &catfish).map_err(|e| format!("恢复失败: {e}"))?;
    Ok(())
}

#[tauri::command]
pub async fn self_serve_preview_reset() -> Result<ResetSummary, String> {
    tokio::task::spawn_blocking(preview_reset_blocking)
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}

#[tauri::command]
pub async fn self_serve_execute_reset(
    confirmation: String,
) -> Result<ResetSummary, String> {
    if confirmation != "我确认" {
        return Err("二次确认字符串错 (必须输入 '我确认')".into());
    }
    tokio::task::spawn_blocking(execute_reset_blocking)
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}

#[tauri::command]
pub async fn self_serve_restore_reset(trash_path: String) -> Result<(), String> {
    tokio::task::spawn_blocking(move || restore_reset_blocking(trash_path))
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}

// ── A2: 导出 ──────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExportOptions {
    pub include_conversations: bool,
    pub include_recordings: bool,
    pub include_wiki: bool,
    pub include_skills: bool,
    pub include_strategic_docs: bool,
    pub include_config: bool,
}

impl Default for ExportOptions {
    fn default() -> Self {
        Self {
            include_conversations: true,
            include_recordings: true,
            include_wiki: true,
            include_skills: true,
            include_strategic_docs: true,
            include_config: true,
        }
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ExportResult {
    pub output_path: String,
    pub bytes_written: u64,
    pub files_included: u64,
}

/// 用系统 tar 命令打包. macOS / Linux 自带, Windows 10+ 自带 (PATH 默认).
/// 不引 zip / walkdir crate, 减依赖足迹.
fn execute_export_blocking(
    options: ExportOptions,
    output_path: String,
) -> Result<ExportResult, String> {
    let home = catfish_home()?;
    if !home.exists() {
        return Err("~/.catfish/ 不存在, 没数据可导出".into());
    }

    // 选要包的子目录
    let mut subpaths: Vec<&str> = vec![];
    if options.include_conversations && home.join("conversations").exists() {
        subpaths.push("conversations");
    }
    if options.include_recordings && home.join("recordings").exists() {
        subpaths.push("recordings");
    }
    if options.include_wiki && home.join("wiki").exists() {
        subpaths.push("wiki");
    }
    if options.include_skills && home.join("skills").exists() {
        subpaths.push("skills");
    }
    if options.include_strategic_docs && home.join("strategic_docs").exists() {
        subpaths.push("strategic_docs");
    }
    if options.include_config && home.join("companion.yaml").exists() {
        subpaths.push("companion.yaml");
    }
    if subpaths.is_empty() {
        return Err("没选任何 export 内容 (~/.catfish/ 子目录都不存在或都没勾选)".into());
    }

    // 先写 MANIFEST.json 进 ~/.catfish/ 临时位置, 加进 tar 让 export 自带 metadata
    let manifest = serde_json::json!({
        "format": "catfish-export-v1",
        "exported_at": chrono::Utc::now().to_rfc3339(),
        "catfish_version": env!("CARGO_PKG_VERSION"),
        "options": {
            "conversations": options.include_conversations,
            "recordings": options.include_recordings,
            "wiki": options.include_wiki,
            "skills": options.include_skills,
            "strategic_docs": options.include_strategic_docs,
            "config": options.include_config,
        },
    });
    let manifest_path = home.join(".export-manifest.json");
    fs::write(&manifest_path, manifest.to_string()).map_err(|e| format!("写 MANIFEST 失败: {e}"))?;

    // 跑 tar -czf <output> -C ~/.catfish <subpaths> .export-manifest.json
    let mut args: Vec<String> = vec![
        "-czf".to_string(),
        output_path.clone(),
        "-C".to_string(),
        home.to_string_lossy().into_owned(),
        ".export-manifest.json".to_string(),
    ];
    for sub in &subpaths {
        args.push(sub.to_string());
    }

    let output = std::process::Command::new("tar")
        .args(&args)
        .output()
        .map_err(|e| format!("启动 tar 失败 (确认 PATH 有 tar): {e}"))?;

    // 清 manifest 临时文件
    let _ = fs::remove_file(&manifest_path);

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("tar 失败: {stderr}"));
    }

    let bytes = fs::metadata(&output_path).map(|m| m.len()).unwrap_or(0);

    Ok(ExportResult {
        output_path,
        bytes_written: bytes,
        files_included: subpaths.len() as u64, // 顶层子目录数 (不递归数 file)
    })
}

#[tauri::command]
pub async fn self_serve_export_data(
    options: ExportOptions,
    output_path: String,
) -> Result<ExportResult, String> {
    tokio::task::spawn_blocking(move || execute_export_blocking(options, output_path))
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}
