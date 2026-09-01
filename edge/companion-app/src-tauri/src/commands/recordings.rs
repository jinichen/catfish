//! BL-RECMODE-DASHBOARD-UI (#75, 5/25 完整 ship): Companion "我的录屏" 卡的后端.
//!
//! 配套 #74 BL-RECMODE-NO-AUTO-DELETE — backend 撤了 cleanup daemon, 这里给员工
//! 提供"列 / 在 Finder 打开 / 删"的显式控制. catfish 不替员工决定删什么.
//!
//! ## 设计选择: fs 直读 vs gateway HTTP
//!
//! 选 fs 直读. 理由:
//!   - 录屏数据本来就 100% 本机 (~/.catfish/recordings/), gateway 没起也该能看
//!   - 跟 backend `list_recordings_with_meta` 逻辑同源, port 一份 Rust 不复杂
//!   - 跟 audit.rs / sessions.rs 同 pattern (跨 catfish-gateway / catfish-edge 一致)
//!
//! ## 跟 backend 一致性
//!
//! schema 字段名跟 `central/llm-gateway/.../recmode/cleanup.py::list_recordings_with_meta`
//! 完全对齐. 改一处两处都要改 (在 #78 EMPLOYEE-PRIVACY-VERIFICATION + plan doc 标了).
//!
//! ## 安全
//!
//! - `recordings_delete` 不做二次确认 — 那是 UI 层的事, Rust 收到 sessionId 就执行
//! - sessionId 用前先校验是合法 session 目录 (防 path traversal e.g. "../../etc")
//! - 不允许跨出 recordings root 删任何东西

use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};
#[cfg(target_os = "macos")]
use std::process::Command;

use serde::{Deserialize, Serialize};

/// 单个 session 的元数据 — 跟 cleanup.py::list_recordings_with_meta 字段对齐.
#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct RecordingMeta {
    pub session_id: String,
    /// unix timestamp (float, 秒). 0.0 = 没 meta.json 也读不到 mtime
    pub started_at: f64,
    pub size_bytes: u64,
    /// 老 _keep_forever flag — 5/25 BL-RECMODE-NO-AUTO-DELETE 后实际等于"opt-in 保留",
    /// 新录屏默认不删, 这字段保 backward compat (历史录屏可能勾过)
    pub kept_forever: bool,
    /// 该 session 生成的 draft skill 列表, e.g. ["productivity/weekly-report"]
    pub skill_drafts: Vec<String>,
    /// 绝对路径 — 给 Finder "show in" 用
    pub path: String,
}

/// recordings root: ~/.catfish/recordings/ (CATFISH_HOME env 覆盖, 跟 backend 同).
fn recordings_root() -> PathBuf {
    if let Ok(env) = std::env::var("CATFISH_HOME") {
        if !env.trim().is_empty() {
            return PathBuf::from(env).join("recordings");
        }
    }
    let home = crate::util::paths::home_env().unwrap_or_else(|_| ".".into());
    PathBuf::from(home).join(".catfish").join("recordings")
}

/// 递归算目录字节数. OS error 跳过, 不抛.
fn walk_size(path: &Path) -> u64 {
    let mut total: u64 = 0;
    if let Ok(rd) = fs::read_dir(path) {
        for entry in rd.flatten() {
            let p = entry.path();
            if let Ok(md) = entry.metadata() {
                if md.is_file() {
                    total += md.len();
                } else if md.is_dir() {
                    total += walk_size(&p);
                }
            }
        }
    }
    total
}

/// 检测一个 session_dir 是不是被员工标了"永久保留".
///
/// 跟 cleanup.py::_is_kept_forever 同算法:
///   1. session_dir/.keep_forever 文件存在
///   2. 任意 skill_draft/*/*/recmode_meta.json 里 _keep_forever=true
fn is_kept_forever(session_dir: &Path) -> bool {
    if session_dir.join(".keep_forever").exists() {
        return true;
    }
    // glob skill_draft/<ns>/<name>/recmode_meta.json
    let draft_root = session_dir.join("skill_draft");
    if !draft_root.is_dir() {
        return false;
    }
    if let Ok(ns_iter) = fs::read_dir(&draft_root) {
        for ns_entry in ns_iter.flatten() {
            let ns_dir = ns_entry.path();
            if !ns_dir.is_dir() {
                continue;
            }
            if let Ok(name_iter) = fs::read_dir(&ns_dir) {
                for name_entry in name_iter.flatten() {
                    let meta_path = name_entry.path().join("recmode_meta.json");
                    if let Ok(s) = fs::read_to_string(&meta_path) {
                        if let Ok(v) = serde_json::from_str::<serde_json::Value>(&s) {
                            if v.get("_keep_forever") == Some(&serde_json::Value::Bool(true)) {
                                return true;
                            }
                        }
                    }
                }
            }
        }
    }
    false
}

/// 列 session_dir 下的 draft skills, 返 "namespace/name" 字符串.
fn collect_skill_drafts(session_dir: &Path) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let draft_root = session_dir.join("skill_draft");
    if !draft_root.is_dir() {
        return out;
    }
    if let Ok(ns_iter) = fs::read_dir(&draft_root) {
        for ns_entry in ns_iter.flatten() {
            let ns_path = ns_entry.path();
            if !ns_path.is_dir() {
                continue;
            }
            let ns = ns_path.file_name().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
            if let Ok(name_iter) = fs::read_dir(&ns_path) {
                for name_entry in name_iter.flatten() {
                    let name_path = name_entry.path();
                    if !name_path.is_dir() {
                        continue;
                    }
                    let skill_md = name_path.join("SKILL.md");
                    if skill_md.exists() {
                        let name = name_path.file_name().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
                        out.push(format!("{ns}/{name}"));
                    }
                }
            }
        }
    }
    // de-dup + 稳定排序 (跟 backend 顺序不强对齐 — backend 用 glob 顺序, 我们字典序更稳)
    let set: HashSet<String> = out.into_iter().collect();
    let mut sorted: Vec<String> = set.into_iter().collect();
    sorted.sort();
    sorted
}

/// 一个 session 目录扫成 RecordingMeta. 损坏 meta.json 不抛, fallback 用 mtime.
fn scan_session(session_dir: &Path) -> Option<RecordingMeta> {
    let session_id = session_dir.file_name()?.to_string_lossy().to_string();

    // started_at: 优先 meta.json 的 started_at, fallback 目录 mtime
    let mut started_at: f64 = 0.0;
    let meta_path = session_dir.join("meta.json");
    if let Ok(s) = fs::read_to_string(&meta_path) {
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(&s) {
            if let Some(f) = v.get("started_at").and_then(|x| x.as_f64()) {
                started_at = f;
            }
        }
    }
    if started_at <= 0.0 {
        if let Ok(md) = fs::metadata(session_dir) {
            if let Ok(mtime) = md.modified() {
                if let Ok(d) = mtime.duration_since(std::time::SystemTime::UNIX_EPOCH) {
                    started_at = d.as_secs_f64();
                }
            }
        }
    }

    Some(RecordingMeta {
        session_id,
        started_at,
        size_bytes: walk_size(session_dir),
        kept_forever: is_kept_forever(session_dir),
        skill_drafts: collect_skill_drafts(session_dir),
        path: session_dir.to_string_lossy().to_string(),
    })
}

/// 校验 session_id 是合法的 (无 path traversal). 只允许 ASCII alphanumeric + - _ .
fn validate_session_id(sid: &str) -> Result<(), String> {
    if sid.is_empty() || sid.len() > 128 {
        return Err(format!("session_id 长度异常: {}", sid.len()));
    }
    for c in sid.chars() {
        if !(c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == '.') {
            return Err(format!("session_id 含非法字符 '{c}'"));
        }
    }
    // 防 ".." / "." 这种隐蔽 traversal
    if sid == "." || sid == ".." || sid.starts_with('.') {
        return Err(format!("session_id 不能以 '.' 开头: {sid}"));
    }
    Ok(())
}

// ─── Tauri commands ──────────────────────────────────────────

/// 列所有 session, 新的在前. ~/.catfish/recordings/ 不存在返空 (员工没开过 RecMode).
#[tauri::command]
pub async fn recordings_list() -> Result<Vec<RecordingMeta>, String> {
    let root = recordings_root();
    if !root.exists() {
        return Ok(vec![]);
    }
    let mut out: Vec<RecordingMeta> = Vec::new();
    let rd = fs::read_dir(&root).map_err(|e| format!("读 {} 失败: {e}", root.display()))?;
    for entry in rd.flatten() {
        let p = entry.path();
        if !p.is_dir() {
            continue;
        }
        if let Some(meta) = scan_session(&p) {
            out.push(meta);
        }
    }
    // 按 session_id 降序 (跟 backend 一致 — 目录名通常含时间戳前缀, 大的就是新的)
    out.sort_by(|a, b| b.session_id.cmp(&a.session_id));
    Ok(out)
}

/// 在 Finder 高亮选中 recordings/<sid>/ 目录.
///
/// macOS: `open -R <path>` (在 Finder 选中文件, 不是打开)
/// Linux / Windows: 不支持 (这是 macOS Companion 专用), 返 err
#[tauri::command]
pub async fn recordings_show_in_finder(path: String) -> Result<(), String> {
    // 防员工传 path 出 recordings root (防误操作 open Finder 别处)
    let root = recordings_root();
    let target = PathBuf::from(&path);
    // canonicalize 失败 (e.g. 路径不存在) 也允许 — 让 Finder 自己报"找不到"
    if let (Ok(root_real), Ok(target_real)) = (root.canonicalize(), target.canonicalize()) {
        if !target_real.starts_with(&root_real) {
            return Err(format!("路径 {} 不在 recordings 范围内", target_real.display()));
        }
    }

    #[cfg(target_os = "macos")]
    {
        Command::new("open")
            .args(["-R", &path])
            .spawn()
            .map_err(|e| format!("open -R 启动失败: {e}"))?;
        Ok(())
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = path; // 避免 unused 警告
        Err("recordings_show_in_finder 当前只支持 macOS".into())
    }
}

/// 删 recordings/<sid>/. 返释放的字节数. 失败抛错误字符串.
///
/// 调用前 UI 层必须做二次确认 — 这是不可逆删用户文件.
#[tauri::command]
pub async fn recordings_delete(session_id: String) -> Result<u64, String> {
    validate_session_id(&session_id)?;
    let root = recordings_root();
    let sd = root.join(&session_id);
    if !sd.exists() {
        return Err(format!("session {session_id} 不存在"));
    }
    if !sd.is_dir() {
        return Err(format!("{session_id} 不是目录"));
    }
    // 再防御一次: canonicalize 后必须 starts_with(root)
    if let (Ok(root_real), Ok(sd_real)) = (root.canonicalize(), sd.canonicalize()) {
        if !sd_real.starts_with(&root_real) {
            return Err(format!("session 目录 {} 越出 recordings root", sd_real.display()));
        }
    }
    let size = walk_size(&sd);
    fs::remove_dir_all(&sd).map_err(|e| format!("rmtree 失败: {e}"))?;
    Ok(size)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validate_session_id_allows_typical_pattern() {
        assert!(validate_session_id("20260525_120000_abc").is_ok());
        assert!(validate_session_id("session-1").is_ok());
        assert!(validate_session_id("a.b").is_ok());
    }

    #[test]
    fn validate_session_id_rejects_traversal() {
        assert!(validate_session_id("..").is_err());
        assert!(validate_session_id(".").is_err());
        assert!(validate_session_id(".hidden").is_err());
        assert!(validate_session_id("../etc").is_err());
        assert!(validate_session_id("a/b").is_err());
        assert!(validate_session_id("").is_err());
    }

    #[test]
    fn validate_session_id_rejects_unicode() {
        assert!(validate_session_id("会话1").is_err());
        assert!(validate_session_id("session 1").is_err()); // 空格非法
    }

    #[test]
    fn walk_size_handles_empty() {
        let tmp = std::env::temp_dir().join(format!("recordings_test_{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        assert_eq!(walk_size(&tmp), 0);
        fs::write(tmp.join("a.txt"), "hello").unwrap();
        assert_eq!(walk_size(&tmp), 5);
        let _ = fs::remove_dir_all(&tmp);
    }
}
