//! BL-ADVISOR-CACHE (5/22 Phase 7 + cold start v3): advisor 结果缓存.
//!
//! ~/.catfish/advisor_cache.json — 存 fetchBriefingAdvisor 上次跑出的 result.
//!
//! AdvisorView 进 tab 时:
//!   1. cache 没过期 → 直接显, 不调 LLM
//!   2. cache 过期 / 不存在 / 员工点刷新 → 调 LLM, 写 cache
//!
//! 后台 setInterval 每分钟检查 now 跨时段 → 触发 LLM 后台跑.
//!
//! 5/22 鸿波: 每次切早安 tab 重算浪费 token, 这是修法.

use std::fs::{File, OpenOptions};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use fs2::FileExt;
use serde::{Deserialize, Serialize};

static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(0);

/// 缓存条目. result 字段是不透明 JSON (跟 TS AdvisorResult 对齐, Rust 不解析).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AdvisorCache {
    /// 算出来的时刻 (ISO-8601 ws tz).
    pub computed_at: String,
    /// fetchBriefingAdvisor 返的 JSON (AdvisorResult), Rust 端不解析直接透传.
    pub result: serde_json::Value,
    /// 用的 model 名 (debug 用 — 跟员工 chat 同款).
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub model: Option<String>,
    /// 用了多少 token (可选, debug 用).
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub prompt_tokens: Option<u32>,
    /// 当前输入来源指纹与自然周窗口。Rust 不解析，原样透传给前端。
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub source_meta: Option<serde_json::Value>,
    /// P3.3.12 (6/10): task chat summary cache — key=task_uid, val={summary,
    /// jsonlSize, computedAt}. jsonl size 没变就复用, 不再调 LLM. Rust 不解析直接
    /// 透传 (跟 result 同款不绑死 schema, TS 端定义形状).
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub task_chat_summaries: Option<serde_json::Value>,
}

fn cache_path() -> Result<PathBuf, String> {
    let home = crate::util::paths::home_env().map_err(|e| format!("HOME 未设: {e}"))?;
    let dir = PathBuf::from(home).join(".catfish");
    std::fs::create_dir_all(&dir).map_err(|e| format!("创建 ~/.catfish/ 失败: {e}"))?;
    Ok(dir.join("advisor_cache.json"))
}

fn lock_cache(target: &Path, exclusive: bool) -> Result<File, String> {
    let lock_path = target.with_extension("json.lock");
    let file = OpenOptions::new()
        .create(true)
        .read(true)
        .write(true)
        .open(&lock_path)
        .map_err(|e| format!("打开 {} 失败: {e}", lock_path.display()))?;
    let result = if exclusive {
        FileExt::lock_exclusive(&file)
    } else {
        FileExt::lock_shared(&file)
    };
    result.map_err(|e| format!("锁定 {} 失败: {e}", lock_path.display()))?;
    Ok(file)
}

fn unique_temp_path(target: &Path) -> PathBuf {
    let sequence = TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    target.with_extension(format!("json.tmp.{}.{}", std::process::id(), sequence))
}

fn replace_cache_file(tmp: &Path, target: &Path) -> Result<(), std::io::Error> {
    #[cfg(target_os = "windows")]
    {
        // Windows 的 rename 不会覆盖已存在的目标文件。copy 在锁内执行，
        // 所有 Companion 读写都经过同一把锁，因此不会读到半截 JSON。
        std::fs::copy(tmp, target)?;
        std::fs::remove_file(tmp)?;
        return Ok(());
    }

    #[cfg(not(target_os = "windows"))]
    {
        std::fs::rename(tmp, target)
    }
}

fn write_cache_text(target: &Path, text: &str) -> Result<(), String> {
    let _lock = lock_cache(target, true)?;
    let tmp = unique_temp_path(target);
    std::fs::write(&tmp, text).map_err(|e| format!("写 {} 失败: {e}", tmp.display()))?;
    if let Err(e) = replace_cache_file(&tmp, target) {
        let _ = std::fs::remove_file(&tmp);
        return Err(format!("替换 {} 失败，旧缓存已保留: {e}", target.display()));
    }
    Ok(())
}

/// 读 cache. 不存在返 None.
#[tauri::command]
pub async fn advisor_cache_get() -> Result<Option<AdvisorCache>, String> {
    let path = cache_path()?;
    let _lock = lock_cache(&path, false)?;
    if !path.exists() {
        return Ok(None);
    }
    let text = std::fs::read_to_string(&path)
        .map_err(|e| format!("读 advisor_cache.json 失败: {e}"))?;
    let c: AdvisorCache = serde_json::from_str(&text)
        .map_err(|e| format!("解析 advisor_cache.json 失败 (schema 不匹配?): {e}"))?;
    Ok(Some(c))
}

/// 写 cache：跨进程文件锁串行化，先写唯一临时文件；Unix 用 rename，Windows
/// 用锁内覆盖复制，保证经过本模块读取的读者看不到半截 JSON。
#[tauri::command]
pub async fn advisor_cache_save(cache: AdvisorCache) -> Result<(), String> {
    let target = cache_path()?;
    let text = serde_json::to_string_pretty(&cache)
        .map_err(|e| format!("serialize cache 失败: {e}"))?;
    write_cache_text(&target, &text)
}

/// 清缓存. 调试或员工显式重置时用.
#[tauri::command]
pub async fn advisor_cache_clear() -> Result<(), String> {
    let path = cache_path()?;
    let _lock = lock_cache(&path, true)?;
    if path.exists() {
        std::fs::remove_file(&path)
            .map_err(|e| format!("删 cache 失败: {e}"))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> AdvisorCache {
        AdvisorCache {
            computed_at: "2026-05-22T11:30:00+08:00".to_string(),
            result: serde_json::json!({
                "tier": "mid",
                "main_tasks": [],
                "handled_silently": []
            }),
            model: Some("catfish-public-deepseek-flash".to_string()),
            prompt_tokens: Some(4854),
            source_meta: None,
            task_chat_summaries: None,  // P3.3.12 字段; 老 test 没填, P3.3.51 跑 cargo test 暴露
        }
    }

    #[test]
    fn cache_roundtrip_json() {
        let c = sample();
        let json = serde_json::to_string(&c).expect("serialize");
        let back: AdvisorCache = serde_json::from_str(&json).expect("parse");
        assert_eq!(back.computed_at, c.computed_at);
        assert_eq!(back.model.as_deref(), Some("catfish-public-deepseek-flash"));
        assert_eq!(back.prompt_tokens, Some(4854));
        assert_eq!(back.result["tier"], "mid");
    }

    #[test]
    fn source_meta_survives_rust_roundtrip() {
        let mut c = sample();
        c.source_meta = Some(serde_json::json!({
            "schemaVersion": 2,
            "inputVersion": "active-sources-v1",
            "inputFingerprint": "deadbeef",
            "windowStart": "2026-08-31T00:00:00.000Z",
            "windowEnd": "2026-09-07T00:00:00.000Z",
        }));
        let json = serde_json::to_string(&c).expect("serialize");
        let back: AdvisorCache = serde_json::from_str(&json).expect("parse");
        assert_eq!(back.source_meta, c.source_meta);
    }

    #[test]
    fn cache_minimal_no_optional() {
        let minimal = AdvisorCache {
            computed_at: "2026-05-22T12:00:00+08:00".to_string(),
            result: serde_json::json!({}),
            model: None,
            prompt_tokens: None,
            source_meta: None,
            task_chat_summaries: None,  // P3.3.12 字段
        };
        let json = serde_json::to_value(&minimal).expect("serialize");
        // None 应被 skip
        assert!(json.get("model").is_none());
        assert!(json.get("promptTokens").is_none());
    }

    #[test]
    fn concurrent_writes_never_leave_partial_json() {
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("advisor_cache.json");
        let handles: Vec<_> = (0..12)
            .map(|value| {
                let target = target.clone();
                std::thread::spawn(move || {
                    write_cache_text(&target, &format!(r#"{{"value":{value}}}"#)).unwrap();
                })
            })
            .collect();
        for handle in handles {
            handle.join().unwrap();
        }
        let final_text = std::fs::read_to_string(&target).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&final_text).unwrap();
        assert!(parsed["value"].as_u64().is_some());
        let temp_files = dir
            .path()
            .read_dir()
            .unwrap()
            .filter_map(Result::ok)
            .filter(|entry| entry.file_name().to_string_lossy().contains(".tmp."))
            .count();
        assert_eq!(temp_files, 0);
    }

    #[test]
    fn replacement_overwrites_existing_cache() {
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("advisor_cache.json");
        std::fs::write(&target, r#"{"value":"old"}"#).unwrap();
        let tmp = dir.path().join("advisor_cache.json.tmp.test");
        std::fs::write(&tmp, r#"{"value":"new"}"#).unwrap();

        replace_cache_file(&tmp, &target).unwrap();

        assert_eq!(std::fs::read_to_string(&target).unwrap(), r#"{"value":"new"}"#);
        assert!(!tmp.exists());
    }
}
