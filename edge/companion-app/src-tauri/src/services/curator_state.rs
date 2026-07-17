//! BL-CR Curator 集成 步骤 4 (5/7): 读 ~/.hermes/skills/.curator_state JSON.
//!
//! Curator 每次跑完写这个文件 (hermes 0.12 agent/curator.py:_default_state):
//!   {
//!     "last_run_at": "2026-05-07T03:14:00Z",   // ISO 8601, null 表示从没跑过
//!     "last_run_duration_seconds": 12.4,
//!     "last_run_summary": "Archived 2 stale skills, merged 1 duplicate.",
//!     "paused": false,
//!     "run_count": 3
//!   }
//!
//! 给 Dashboard CuratorCard 用. 文件不存在 → never_run 状态 (Curator 还没满 idle 触发).

use anyhow::{anyhow, Result};
use serde::Serialize;
use std::fs;
use std::path::{Path, PathBuf};

/// 给前端的 view (跟原 .curator_state JSON 字段名保持一致, 多加 `never_run` 标记).
#[derive(Debug, Clone, Serialize)]
pub struct CuratorStateView {
    pub never_run: bool,
    pub last_run_at: Option<String>,
    pub last_run_duration_seconds: Option<f64>,
    pub last_run_summary: Option<String>,
    pub paused: bool,
    pub run_count: u64,
}

impl Default for CuratorStateView {
    fn default() -> Self {
        Self {
            never_run: true,
            last_run_at: None,
            last_run_duration_seconds: None,
            last_run_summary: None,
            paused: false,
            run_count: 0,
        }
    }
}

fn state_path() -> Result<PathBuf> {
    if let Ok(home) = std::env::var("HERMES_HOME") {
        return Ok(PathBuf::from(home).join("skills").join(".curator_state"));
    }
    let home = crate::util::paths::home_env()
        .or_else(|_| std::env::var("USERPROFILE"))
        .map_err(|_| anyhow!("找不到 HOME 环境变量"))?;
    Ok(PathBuf::from(home)
        .join(".hermes")
        .join("skills")
        .join(".curator_state"))
}

/// 读 .curator_state. 文件不存在 / 解析失败 → 返 never_run 默认. (内部版, 测试直接调.)
pub fn load_at(path: &Path) -> Result<CuratorStateView> {
    if !path.exists() {
        return Ok(CuratorStateView::default());
    }
    let raw = match fs::read_to_string(path) {
        Ok(r) => r,
        Err(_) => return Ok(CuratorStateView::default()),
    };
    let v: serde_json::Value = match serde_json::from_str(&raw) {
        Ok(v) => v,
        Err(_) => return Ok(CuratorStateView::default()),
    };

    // last_run_at 字段: hermes 写 ISO string 或 null
    let last_run_at = v
        .get("last_run_at")
        .and_then(|x| x.as_str())
        .map(|s| s.to_string());
    let never_run = last_run_at.is_none();

    Ok(CuratorStateView {
        never_run,
        last_run_at,
        last_run_duration_seconds: v
            .get("last_run_duration_seconds")
            .and_then(|x| x.as_f64()),
        last_run_summary: v
            .get("last_run_summary")
            .and_then(|x| x.as_str())
            .map(|s| {
                // 5/7 BL-CR: hermes summary 字段可能含 "~/.hermes/skills/..."
                // 路径, 跟 adapter.scrub_brand_in_result 保持一致, Card 这边也脱敏一下
                s.replace("~/.hermes", "鲶鱼本机存储")
                    .replace("/.hermes", "/鲶鱼本机存储")
                    .to_string()
            }),
        paused: v
            .get("paused")
            .and_then(|x| x.as_bool())
            .unwrap_or(false),
        run_count: v
            .get("run_count")
            .and_then(|x| x.as_u64())
            .unwrap_or(0),
    })
}

/// 公开版: 用默认 state_path() (env 解析). 生产代码用这个.
pub fn load() -> Result<CuratorStateView> {
    load_at(&state_path()?)
}

#[cfg(test)]
mod tests {
    // 跟 curator_config 一样: 测试直接调 load_at(&path), 不动 env, parallel-safe.
    use super::*;
    use tempfile::TempDir;

    fn st_path(tmp: &TempDir) -> PathBuf {
        tmp.path().join(".curator_state")
    }

    #[test]
    fn load_never_run_when_file_missing() {
        let tmp = TempDir::new().unwrap();
        let v = load_at(&st_path(&tmp)).unwrap();
        assert!(v.never_run);
        assert_eq!(v.run_count, 0);
        assert!(v.last_run_at.is_none());
    }

    #[test]
    fn load_parses_valid_state() {
        let tmp = TempDir::new().unwrap();
        let p = st_path(&tmp);
        fs::write(
            &p,
            r#"{"last_run_at":"2026-05-07T03:14:00Z","last_run_duration_seconds":12.4,"last_run_summary":"Archived 2 stale skills","paused":false,"run_count":3}"#,
        )
        .unwrap();
        let v = load_at(&p).unwrap();
        assert!(!v.never_run);
        assert_eq!(v.last_run_at.as_deref(), Some("2026-05-07T03:14:00Z"));
        assert_eq!(v.run_count, 3);
        assert_eq!(v.last_run_duration_seconds, Some(12.4));
        assert!(v.last_run_summary.unwrap().contains("Archived 2"));
    }

    #[test]
    fn load_scrubs_hermes_path_in_summary() {
        let tmp = TempDir::new().unwrap();
        let p = st_path(&tmp);
        fs::write(
            &p,
            r#"{"last_run_at":"2026-05-07T03:14:00Z","last_run_summary":"Archived ~/.hermes/skills/old_script","paused":false,"run_count":1}"#,
        )
        .unwrap();
        let v = load_at(&p).unwrap();
        let summary = v.last_run_summary.unwrap();
        assert!(!summary.contains("~/.hermes"));
        assert!(summary.contains("鲶鱼本机存储"));
    }

    #[test]
    fn load_returns_default_on_corrupt_json() {
        let tmp = TempDir::new().unwrap();
        let p = st_path(&tmp);
        fs::write(&p, "not valid json {{{").unwrap();
        let v = load_at(&p).unwrap();
        assert!(v.never_run);
    }

    #[test]
    fn load_handles_missing_optional_fields() {
        let tmp = TempDir::new().unwrap();
        let p = st_path(&tmp);
        // 只有 last_run_at, 别的都没 (Curator 第一次跑后状态)
        fs::write(&p, r#"{"last_run_at":"2026-05-07T03:14:00Z"}"#).unwrap();
        let v = load_at(&p).unwrap();
        assert!(!v.never_run);
        assert_eq!(v.run_count, 0); // missing → 0
        assert_eq!(v.paused, false); // missing → false
        assert!(v.last_run_summary.is_none());
    }
}
