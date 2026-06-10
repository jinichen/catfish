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

use std::path::PathBuf;
use serde::{Deserialize, Serialize};

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
    /// P3.3.12 (6/10): task chat summary cache — key=task_uid, val={summary,
    /// jsonlSize, computedAt}. jsonl size 没变就复用, 不再调 LLM. Rust 不解析直接
    /// 透传 (跟 result 同款不绑死 schema, TS 端定义形状).
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub task_chat_summaries: Option<serde_json::Value>,
}

fn cache_path() -> Result<PathBuf, String> {
    let home = std::env::var("HOME").map_err(|e| format!("HOME 未设: {e}"))?;
    let dir = PathBuf::from(home).join(".catfish");
    std::fs::create_dir_all(&dir).map_err(|e| format!("创建 ~/.catfish/ 失败: {e}"))?;
    Ok(dir.join("advisor_cache.json"))
}

/// 读 cache. 不存在返 None.
#[tauri::command]
pub async fn advisor_cache_get() -> Result<Option<AdvisorCache>, String> {
    let path = cache_path()?;
    if !path.exists() {
        return Ok(None);
    }
    let text = std::fs::read_to_string(&path)
        .map_err(|e| format!("读 advisor_cache.json 失败: {e}"))?;
    let c: AdvisorCache = serde_json::from_str(&text)
        .map_err(|e| format!("解析 advisor_cache.json 失败 (schema 不匹配?): {e}"))?;
    Ok(Some(c))
}

/// 写 cache. 原子写 (tmp + rename).
#[tauri::command]
pub async fn advisor_cache_save(cache: AdvisorCache) -> Result<(), String> {
    let target = cache_path()?;
    let tmp = target.with_extension("json.tmp");
    let text = serde_json::to_string_pretty(&cache)
        .map_err(|e| format!("serialize cache 失败: {e}"))?;
    std::fs::write(&tmp, text)
        .map_err(|e| format!("写 cache.json.tmp 失败: {e}"))?;
    std::fs::rename(&tmp, &target)
        .map_err(|e| format!("rename cache.json 失败: {e}"))?;
    Ok(())
}

/// 清缓存. 调试或员工显式重置时用.
#[tauri::command]
pub async fn advisor_cache_clear() -> Result<(), String> {
    let path = cache_path()?;
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
    fn cache_minimal_no_optional() {
        let minimal = AdvisorCache {
            computed_at: "2026-05-22T12:00:00+08:00".to_string(),
            result: serde_json::json!({}),
            model: None,
            prompt_tokens: None,
        };
        let json = serde_json::to_value(&minimal).expect("serialize");
        // None 应被 skip
        assert!(json.get("model").is_none());
        assert!(json.get("promptTokens").is_none());
    }
}
