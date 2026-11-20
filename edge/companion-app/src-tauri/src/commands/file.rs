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
        if let Ok(home) = crate::util::paths::home_env() {
            return PathBuf::from(home).join(rest);
        }
    }
    if path == "~" {
        if let Ok(home) = crate::util::paths::home_env() {
            return PathBuf::from(home);
        }
    }
    PathBuf::from(path)
}

/// 验证 + 展开路径, 拿到一个真正能用的 absolute PathBuf.
///
/// 错误信息直接给前端展示, 别带 PII.
pub(crate) fn resolve_path(raw: &str) -> Result<PathBuf, String> {
    let mut expanded = expand_tilde(raw);
    if !expanded.is_absolute() {
        return Err(format!("路径必须是绝对路径: {raw}"));
    }
    if !expanded.exists() {
        match expand_ellipsis(&expanded) {
            Some(found) => expanded = found,
            None => return Err(format!("文件不存在: {raw}")),
        }
    }
    let lower = expanded.to_string_lossy().to_lowercase();
    if is_blocked_path(&lower) {
        return Err(format!("拒绝打开敏感路径: {raw}"));
    }
    Ok(expanded)
}

/// 小鲶嫌文件名太长, 回答里常写成 `…二级）-...第三期集中采购项目.docx` (11/20 鸿波「取地址有BUG」):
/// 名字里带 `...` / `…` 的那一段, 到上一级目录里按「省略号两边的字依次出现」找真文件。
/// 只认唯一匹配 —— 对上多个就不猜, 照旧报文件不存在。
pub(crate) fn expand_ellipsis(path: &std::path::Path) -> Option<PathBuf> {
    let mut out = PathBuf::new();
    let mut changed = false;
    for comp in path.components() {
        let name = comp.as_os_str().to_string_lossy();
        let next = out.join(comp.as_os_str());
        if next.exists() || !(name.contains("...") || name.contains('…')) {
            out = next;
            continue;
        }
        let parts: Vec<String> = name
            .replace('…', "...")
            .split("...")
            .map(|p| p.to_string())
            .collect();
        let matches: Vec<PathBuf> = std::fs::read_dir(&out)
            .ok()?
            .filter_map(|e| e.ok())
            .filter(|e| ellipsis_matches(&e.file_name().to_string_lossy(), &parts))
            .map(|e| e.path())
            .collect();
        if matches.len() != 1 {
            return None;
        }
        out = matches.into_iter().next()?;
        changed = true;
    }
    (changed && out.exists()).then_some(out)
}

/// `parts` = 名字按省略号切开的几段: 第一段是开头, 最后一段是结尾, 中间的按顺序出现。
fn ellipsis_matches(candidate: &str, parts: &[String]) -> bool {
    let (Some(first), Some(last)) = (parts.first(), parts.last()) else {
        return false;
    };
    if parts.len() < 2 || (first.is_empty() && last.is_empty()) {
        return false;
    }
    if !candidate.starts_with(first.as_str()) || !candidate.ends_with(last.as_str()) {
        return false;
    }
    if candidate.len() < first.len() + last.len() {
        return false;
    }
    let mut rest = &candidate[first.len()..candidate.len() - last.len()];
    for mid in &parts[1..parts.len() - 1] {
        match rest.find(mid.as_str()) {
            Some(i) => rest = &rest[i + mid.len()..],
            None => return false,
        }
    }
    true
}

/// 在 Finder 里选中文件 (`open -R <path>`).
///
/// macOS `open -R` / Windows `explorer /select,` / Linux 打开父目录。
#[tauri::command]
pub async fn reveal_in_finder(path: String) -> Result<(), String> {
    let resolved = resolve_path(&path)?;
    // Linux 分支用的是父目录, 不用它
    #[cfg(any(target_os = "macos", target_os = "windows"))]
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
///
/// BL-COMPANION-FILE-PATH-CLICKABLE 修 (5/19 晚): `.status()` 只 catch IO 层 spawn
/// 失败, **不 catch** `open` 自身非 0 退出 (文件不存在 / 没权限 / 没默认 app 绑定 .xlsx).
/// 之前 silent fail, 前端 await 拿 Ok 还以为打开了. 现在显式检查 exit code + 用
/// stderr 拼错误, 让 console.warn 真有信息.
#[tauri::command]
pub async fn open_file(path: String) -> Result<(), String> {
    let resolved = resolve_path(&path)?;
    let path_str = resolved.to_string_lossy().to_string();

    #[cfg(target_os = "macos")]
    {
        let out = Command::new("open")
            .arg(&path_str)
            .output()
            .map_err(|e| format!("open 命令 spawn 失败: {e}"))?;
        if !out.status.success() {
            let stderr = String::from_utf8_lossy(&out.stderr).trim().to_string();
            return Err(format!(
                "open 退出码 {} ({}): {}",
                out.status.code().unwrap_or(-1),
                path_str,
                if stderr.is_empty() { "无 stderr 输出".into() } else { stderr },
            ));
        }
        return Ok(());
    }

    #[cfg(target_os = "linux")]
    {
        let out = Command::new("xdg-open")
            .arg(&path_str)
            .output()
            .map_err(|e| format!("xdg-open spawn 失败: {e}"))?;
        if !out.status.success() {
            let stderr = String::from_utf8_lossy(&out.stderr).trim().to_string();
            return Err(format!("xdg-open 退出码 {}: {}", out.status.code().unwrap_or(-1), stderr));
        }
        return Ok(());
    }

    #[cfg(target_os = "windows")]
    {
        let out = crate::services::process::background_command("cmd")
            .args(["/C", "start", "", &path_str])
            .output()
            .map_err(|e| format!("start spawn 失败: {e}"))?;
        if !out.status.success() {
            let stderr = String::from_utf8_lossy(&out.stderr).trim().to_string();
            return Err(format!("start 退出码 {}: {}", out.status.code().unwrap_or(-1), stderr));
        }
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


#[cfg(test)]
mod ellipsis_tests {
    use super::*;

    #[test]
    fn 省略号写法找回真文件_对上多个不猜() {
        let dir = tempfile::tempdir().unwrap();
        let real = "评分标准（标包二：通信工程施工总承包二级）-2026-2028年中电福富资质能力服务第三期集中采购项目.docx";
        std::fs::write(dir.path().join(real), b"x").unwrap();
        std::fs::write(dir.path().join("技术规范书（标包二）-2026-2028年第三期集中采购项目.docx"), b"x").unwrap();
        let short = dir.path().join("评分标准（标包二：通信工程施工总承包二级）-...第三期集中采购项目.docx");
        assert_eq!(expand_ellipsis(&short), Some(dir.path().join(real)));
        let short2 = dir.path().join("评分标准…采购项目.docx");
        assert_eq!(resolve_path(short2.to_str().unwrap()).unwrap(), dir.path().join(real));
        // 两个都以 .docx 结尾、都含「第三期」: 不猜
        assert_eq!(expand_ellipsis(&dir.path().join("...第三期集中采购项目.docx")), None);
        assert!(resolve_path(dir.path().join("不存在.docx").to_str().unwrap()).unwrap_err().contains("文件不存在"));
    }

    #[test]
    fn 匹配规则() {
        let p = |s: &str| s.replace('…', "...").split("...").map(String::from).collect::<Vec<_>>();
        assert!(ellipsis_matches("abcXYZdef", &p("abc...def")));
        assert!(ellipsis_matches("abc1mid2def", &p("abc...mid...def")));
        assert!(!ellipsis_matches("abcdef", &p("abcd...cdef")));
        assert!(!ellipsis_matches("anything", &p("...")));
    }
}
