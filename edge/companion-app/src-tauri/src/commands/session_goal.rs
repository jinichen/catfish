//! /goal UI 路径 — BL-BRIEFING-GOAL-INPUT (5/20 鸿波).
//!
//! BriefingCard 加 "🎯 今日重点" 输入框, 写 ~/.catfish/session_goal.txt. gateway
//! 端 inject_session_goal (228 LOC) 仍读同一文件, chat 链路自动 inject 进 system
//! 末尾让 LLM 锚定. CLI `/goal xxx` 命令仍工作 (走 gateway detect_goal_command),
//! 两个入口共享同一存储, 鸿波想用 UI / CLI 随便选.
//!
//! 红线 (跟 gateway/session_goals.py 对齐):
//!   - 同一文件 ~/.catfish/session_goal.txt (单行文本)
//!   - 最大长度 500 字符防 prompt injection
//!   - 仅写本机 ~/.catfish/, 不上中央

use std::fs;
use std::path::PathBuf;

/// 跟 gateway session_goals.py MAX_GOAL_LEN 对齐
const MAX_GOAL_LEN: usize = 500;

fn goal_path() -> Option<PathBuf> {
    let home = std::env::var("HOME").ok()?;
    Some(PathBuf::from(home).join(".catfish/session_goal.txt"))
}

/// 读当前 session_goal — 返 Some(text) / None (没设过 / 文件不存在 / 空).
#[tauri::command]
pub fn session_goal_read() -> Result<Option<String>, String> {
    let path = goal_path().ok_or("HOME env not set")?;
    if !path.exists() {
        return Ok(None);
    }
    match fs::read_to_string(&path) {
        Ok(text) => {
            let trimmed = text.trim().to_string();
            if trimmed.is_empty() {
                Ok(None)
            } else if trimmed.chars().count() > MAX_GOAL_LEN {
                // 截断显示, 防 prompt injection 塞超长 fake goal. gateway 同截.
                let truncated: String = trimmed.chars().take(MAX_GOAL_LEN).collect();
                Ok(Some(truncated + "…[截断]"))
            } else {
                Ok(Some(trimmed))
            }
        }
        Err(e) => Err(format!("读 session_goal.txt 失败: {e}")),
    }
}

/// 写新 session_goal — 覆盖旧.
#[tauri::command]
pub fn session_goal_write(text: String) -> Result<(), String> {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return Err("goal 不能为空 (要清除请调 session_goal_clear)".to_string());
    }
    if trimmed.chars().count() > MAX_GOAL_LEN {
        return Err(format!("goal 不能超过 {MAX_GOAL_LEN} 字符 (当前 {} 字符)", trimmed.chars().count()));
    }
    let path = goal_path().ok_or("HOME env not set")?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("建 ~/.catfish 失败: {e}"))?;
    }
    // 原子写: tmp + rename (防 Companion 崩溃半写)
    let tmp = path.with_extension("txt.tmp");
    fs::write(&tmp, trimmed).map_err(|e| format!("写 tmp 失败: {e}"))?;
    fs::rename(&tmp, &path).map_err(|e| format!("rename 失败: {e}"))?;
    Ok(())
}

/// 清除当前 session_goal (删文件).
#[tauri::command]
pub fn session_goal_clear() -> Result<(), String> {
    let path = goal_path().ok_or("HOME env not set")?;
    if path.exists() {
        fs::remove_file(&path).map_err(|e| format!("删 session_goal.txt 失败: {e}"))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;
    use tempfile::tempdir;

    fn with_temp_home<F: FnOnce()>(f: F) {
        let dir = tempdir().expect("tempdir");
        let old = env::var("HOME").ok();
        env::set_var("HOME", dir.path());
        f();
        if let Some(o) = old {
            env::set_var("HOME", o);
        } else {
            env::remove_var("HOME");
        }
    }

    #[test]
    fn read_when_not_set_returns_none() {
        with_temp_home(|| {
            assert_eq!(session_goal_read().unwrap(), None);
        });
    }

    #[test]
    fn write_then_read() {
        with_temp_home(|| {
            session_goal_write("写季度汇报".to_string()).unwrap();
            assert_eq!(session_goal_read().unwrap(), Some("写季度汇报".to_string()));
        });
    }

    #[test]
    fn write_trims_whitespace() {
        with_temp_home(|| {
            session_goal_write("  写季度汇报  ".to_string()).unwrap();
            assert_eq!(session_goal_read().unwrap(), Some("写季度汇报".to_string()));
        });
    }

    #[test]
    fn write_empty_rejected() {
        with_temp_home(|| {
            assert!(session_goal_write("".to_string()).is_err());
            assert!(session_goal_write("   ".to_string()).is_err());
        });
    }

    #[test]
    fn write_too_long_rejected() {
        with_temp_home(|| {
            let huge = "x".repeat(MAX_GOAL_LEN + 1);
            assert!(session_goal_write(huge).is_err());
        });
    }

    #[test]
    fn read_truncates_oversize_file() {
        // 手写文件超 500 字符 (绕过 write 校验), read 应截断
        with_temp_home(|| {
            let path = goal_path().unwrap();
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(&path, "x".repeat(MAX_GOAL_LEN + 100)).unwrap();
            let result = session_goal_read().unwrap().unwrap();
            assert!(result.ends_with("…[截断]"));
            assert!(result.chars().count() <= MAX_GOAL_LEN + 5);
        });
    }

    #[test]
    fn clear_removes_file() {
        with_temp_home(|| {
            session_goal_write("test goal".to_string()).unwrap();
            assert!(session_goal_read().unwrap().is_some());
            session_goal_clear().unwrap();
            assert_eq!(session_goal_read().unwrap(), None);
        });
    }

    #[test]
    fn clear_when_not_exists_ok() {
        with_temp_home(|| {
            // 不存在调 clear 不应挂
            assert!(session_goal_clear().is_ok());
        });
    }

    #[test]
    fn write_overwrites() {
        with_temp_home(|| {
            session_goal_write("第一个目标".to_string()).unwrap();
            session_goal_write("第二个目标".to_string()).unwrap();
            assert_eq!(session_goal_read().unwrap(), Some("第二个目标".to_string()));
        });
    }
}
