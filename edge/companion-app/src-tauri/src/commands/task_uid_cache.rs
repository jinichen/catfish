//! P3.5.91 (6/23 鸿波): 早安 task → canonical taskUid 客户端 cache.
//!
//! # 真因 (P3.5.90 audit 完整)
//!
//! 早安 advisor task 的 taskUid 是 LLM 6 字符生成, 跨 refresh 复用靠 prev cache
//! 注入 + LLM 推理稳定. 真实情况 LLM 可能给同业务任务一个新 uid (briefing_advisor.ts:62
//! "如无 prev cache 可生成新值"), 导致:
//!   1. 早安 refresh #1 → LLM 给 task taskUid="a1b2c3"
//!   2. 员工 chat → sessionCreate + sessionSetTaskUid("a1b2c3")
//!   3. 早安 refresh #2 → LLM 给同 task title 一个新 uid "x9y8z7"
//!   4. mount sessionGetByTaskUid("x9y8z7") 返 null → 显空白
//!   5. session 还在 db, 但跟旧 "a1b2c3" 关联 → 历史消息 UI 找不到
//!
//! # 修法 (C 路径)
//!
//! 客户端按 normalized title 维护 cache: 第一次见同 title task → 记 LLM 当时给的
//! taskUid 为 canonical. 之后 advisor refresh 给同 title 不同 uid → ignore LLM 的,
//! 用 cache 的 canonical uid 做 session 查找 / 关联.
//!
//! LLM 输出 taskUid 当 suggestion, 客户端 final decision.
//!
//! # 数据 schema (~/.catfish/task_uid_cache.json)
//!
//! ```json
//! {
//!   "<normalized-title>": {
//!     "task_uid": "<first-seen-uid>",
//!     "first_seen_at": "<ISO8601>",
//!     "last_used_at": "<ISO8601>"
//!   }
//! }
//! ```
//!
//! normalize 规则在 TS 端 (lib/task_uid_cache.ts:normalizeTaskTitle):
//!   - 去 emoji / 标点 / 空格 / 数字
//!   - 只保留中文 + 英文字母
//!
//! # Atomic write
//!
//! tmp + rename pattern. 跟 picker_state.rs 同款.

use std::collections::HashMap;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct TaskUidCacheEntry {
    pub task_uid: String,
    pub first_seen_at: String,
    pub last_used_at: String,
}

fn cache_path() -> Result<PathBuf, String> {
    let home = std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .ok_or("HOME 未设")?;
    let dir = PathBuf::from(home).join(".catfish");
    std::fs::create_dir_all(&dir).map_err(|e| format!("创建 ~/.catfish/ 失败: {e}"))?;
    Ok(dir.join("task_uid_cache.json"))
}

fn now_iso8601() -> String {
    chrono::Utc::now().to_rfc3339()
}

fn read_cache() -> Result<HashMap<String, TaskUidCacheEntry>, String> {
    let path = cache_path()?;
    if !path.exists() {
        return Ok(HashMap::new());
    }
    let text = std::fs::read_to_string(&path)
        .map_err(|e| format!("读 task_uid_cache.json 失败: {e}"))?;
    if text.trim().is_empty() {
        return Ok(HashMap::new());
    }
    serde_json::from_str(&text).map_err(|e| {
        log::warn!("task_uid_cache.json parse 失败, 重置: {e}");
        format!("parse 失败: {e}")
    })
}

fn write_cache(cache: &HashMap<String, TaskUidCacheEntry>) -> Result<(), String> {
    let path = cache_path()?;
    let text = serde_json::to_string_pretty(cache)
        .map_err(|e| format!("serialize 失败: {e}"))?;
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, text).map_err(|e| format!("写 tmp 失败: {e}"))?;
    std::fs::rename(&tmp, &path).map_err(|e| format!("rename atomic 失败: {e}"))?;
    Ok(())
}

/// 按 normalized title 查 cache canonical taskUid.
///
/// 返 Some 表示这 title 之前见过, 用 cache 的 task_uid 而不是 LLM 新给的.
/// 返 None 表示第一次见, caller 应该 putTaskUid 把 LLM 给的 uid 当 canonical 存进 cache.
#[tauri::command(rename_all = "camelCase")]
pub async fn task_uid_cache_get(normalized_title: String) -> Result<Option<String>, String> {
    let title = normalized_title.trim().to_string();
    if title.is_empty() {
        return Ok(None);
    }
    tokio::task::spawn_blocking(move || {
        let mut cache = read_cache().unwrap_or_default();
        if let Some(entry) = cache.get(&title).cloned() {
            // 更新 last_used_at (best-effort, 失败 silent)
            if let Some(e) = cache.get_mut(&title) {
                e.last_used_at = now_iso8601();
                let _ = write_cache(&cache);
            }
            Ok::<Option<String>, String>(Some(entry.task_uid))
        } else {
            Ok(None)
        }
    })
    .await
    .map_err(|e| format!("task_uid_cache_get join 失败: {e}"))?
}

/// 第一次见 title 时, 把 LLM 给的 taskUid 存进 cache.
///
/// 已存在 → no-op (保留第一次的 canonical, 不被 advisor refresh 覆盖).
#[tauri::command(rename_all = "camelCase")]
pub async fn task_uid_cache_put(
    normalized_title: String,
    task_uid: String,
) -> Result<(), String> {
    let title = normalized_title.trim().to_string();
    let uid = task_uid.trim().to_string();
    if title.is_empty() || uid.is_empty() {
        return Err("normalized_title / task_uid 不能为空".into());
    }
    tokio::task::spawn_blocking(move || {
        let mut cache = read_cache().unwrap_or_default();
        let now = now_iso8601();
        cache.entry(title.clone()).or_insert(TaskUidCacheEntry {
            task_uid: uid.clone(),
            first_seen_at: now.clone(),
            last_used_at: now,
        });
        // 即使 entry 已存在, 我们也要写一遍 (更新 last_used_at)
        // 但用 entry().or_insert() 只插入新的. 已存在的就保留原 task_uid (canonical).
        // last_used_at 更新由 get 路径做.
        write_cache(&cache)
    })
    .await
    .map_err(|e| format!("task_uid_cache_put join 失败: {e}"))?
}

/// 调试用 — dump 全 cache.
#[tauri::command(rename_all = "camelCase")]
pub async fn task_uid_cache_dump() -> Result<HashMap<String, TaskUidCacheEntry>, String> {
    tokio::task::spawn_blocking(read_cache)
        .await
        .map_err(|e| format!("dump join 失败: {e}"))?
}
