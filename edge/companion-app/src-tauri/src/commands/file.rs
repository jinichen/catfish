//! 文件系统操作命令 — 给前端"在 Finder 显示" / "用默认 app 打开" 用.
//!
//! 安全:
//!   - 只接受 absolute path 或 `~` 前缀路径 (自动展开)
//!   - 拒绝 ~/.ssh / ~/.aws / 等敏感目录 (Phase 2 完善白名单)
//!   - macOS `open` 命令本身受系统沙盒, 打不开员工没权限的文件
//!
//! 注意: LLM 经常生成 `~/Desktop/foo.docx` 形式的路径 (人类习惯写法).
//! 前端不知道 HOME 目录, 所以波浪号展开放在 Rust 端做最稳.

use std::path::PathBuf;
use std::process::Command;

/// 把 `~/foo` / `~` 展开为绝对路径. 不带 `~` 直接原样返回.
///
/// 失败 → 原样返回, 让上层 `is_absolute()` 检查继续走错误分支.
fn expand_tilde(path: &str) -> PathBuf {
    if let Some(rest) = path.strip_prefix("~/") {
        if let Ok(home) = std::env::var("HOME") {
            return PathBuf::from(home).join(rest);
        }
    }
    if path == "~" {
        if let Ok(home) = std::env::var("HOME") {
            return PathBuf::from(home);
        }
    }
    PathBuf::from(path)
}

/// 验证 + 展开路径, 拿到一个真正能用的 absolute PathBuf.
///
/// 错误信息直接给前端展示, 别带 PII.
fn resolve_path(raw: &str) -> Result<PathBuf, String> {
    let expanded = expand_tilde(raw);
    if !expanded.is_absolute() {
        return Err(format!("路径必须是绝对路径: {raw}"));
    }
    if !expanded.exists() {
        return Err(format!("文件不存在: {raw}"));
    }
    let lower = expanded.to_string_lossy().to_lowercase();
    if is_blocked_path(&lower) {
        return Err(format!("拒绝打开敏感路径: {raw}"));
    }
    Ok(expanded)
}

/// 在 Finder 里选中文件 (`open -R <path>`).
///
/// macOS only. Windows / Linux Phase 2 加.
#[tauri::command]
pub async fn reveal_in_finder(path: String) -> Result<(), String> {
    let resolved = resolve_path(&path)?;
    let path_str = resolved.to_string_lossy().to_string();

    #[cfg(target_os = "macos")]
    {
        Command::new("open")
            .args(["-R", &path_str])
            .status()
            .map_err(|e| format!("open 命令失败: {e}"))?;
        return Ok(());
    }

    #[cfg(target_os = "linux")]
    {
        // Linux 没有 -R 等价命令, 退回打开父目录
        let parent = resolved
            .parent()
            .map(|p| p.to_path_buf())
            .unwrap_or_else(|| PathBuf::from("/"));
        Command::new("xdg-open")
            .arg(&parent)
            .status()
            .map_err(|e| format!("xdg-open 失败: {e}"))?;
        return Ok(());
    }

    #[cfg(target_os = "windows")]
    {
        // Windows 用 explorer /select,
        Command::new("explorer")
            .args(["/select,", &path_str])
            .status()
            .map_err(|e| format!("explorer 失败: {e}"))?;
        return Ok(());
    }

    #[allow(unreachable_code)]
    Err("不支持的操作系统".into())
}

/// 用默认 app 打开文件 (`open <path>`).
#[tauri::command]
pub async fn open_file(path: String) -> Result<(), String> {
    let resolved = resolve_path(&path)?;
    let path_str = resolved.to_string_lossy().to_string();

    #[cfg(target_os = "macos")]
    {
        Command::new("open")
            .arg(&path_str)
            .status()
            .map_err(|e| format!("open 失败: {e}"))?;
        return Ok(());
    }

    #[cfg(target_os = "linux")]
    {
        Command::new("xdg-open")
            .arg(&path_str)
            .status()
            .map_err(|e| format!("xdg-open 失败: {e}"))?;
        return Ok(());
    }

    #[cfg(target_os = "windows")]
    {
        Command::new("cmd")
            .args(["/C", "start", "", &path_str])
            .status()
            .map_err(|e| format!("start 失败: {e}"))?;
        return Ok(());
    }

    #[allow(unreachable_code)]
    Err("不支持的操作系统".into())
}

/// 拒绝敏感路径白名单. Phase 2 加更细粒度策略.
///
/// 入参已经 lower-case 过 (resolve_path 里做了), 这里直接 substring 比对.
fn is_blocked_path(lower: &str) -> bool {
    const BLOCKED_SUBSTR: &[&str] = &[
        "/.ssh/",
        "/.aws/",
        "/.gnupg/",
        "/.kube/",
        "/etc/passwd",
        "/etc/shadow",
        "/system/library/keychains",
        "/library/keychains/login.keychain",
    ];
    BLOCKED_SUBSTR.iter().any(|s| lower.contains(s))
}

