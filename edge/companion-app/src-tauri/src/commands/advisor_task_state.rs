//! BL-ADVISOR-TASK-STATE (5/22 鸿波): 主菜任务状态持久化.
//!
//! 5/22 鸿波 UX 反馈: AdvisorView 列主菜后, 没法标 完成/推迟/不做, 第二天还会再出.
//! 加任务级状态 — ~/.catfish/advisor_task_state.json by date+title:
//!
//! ```json
//! {
//!   "2026-05-22": {
//!     "资质全流程管理：中电人员材料收集": {
//!       "status": "done",
//!       "ts": "2026-05-22T20:15:00+08:00"
//!     },
//!     "ISO 现场审核收尾总结": {
//!       "status": "snoozed",
//!       "ts": "2026-05-22T20:16:00+08:00"
//!     }
//!   }
//! }
//! ```
//!
//! 状态语义:
//!   - "done"     → 今天和明天都不再出
//!   - "ignored"  → 不出, 但不留长期记忆 (一次性砍)
//!   - "snoozed"  → 今天不出, 明天 advisor 会重新出 (并知道"昨天推的", 加重要性)
//!
//! 设计点: by title (sanitized) 不 by id 因为 advisor 每次重算 id 会变, title 比较稳.

use std::collections::HashMap;
use std::path::PathBuf;

use chrono::{Local, Utc};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskStateEntry {
    /// "done" / "ignored" / "snoozed"
    pub status: String,
    /// ISO-8601 timestamp 员工切状态时.
    pub ts: String,
}

/// 整文件: date (YYYY-MM-DD, local TZ) → title → entry.
type TaskStateMap = HashMap<String, HashMap<String, TaskStateEntry>>;

fn task_state_path() -> Result<PathBuf, String> {
    let home = crate::util::paths::home_env().map_err(|e| format!("HOME 未设: {e}"))?;
    Ok(PathBuf::from(home)
        .join(".catfish")
        .join("advisor_task_state.json"))
}

fn today_local() -> String {
    Local::now().format("%Y-%m-%d").to_string()
}

fn yesterday_local() -> String {
    (Local::now() - chrono::Duration::days(1))
        .format("%Y-%m-%d")
        .to_string()
}

fn read_map() -> TaskStateMap {
    let path = match task_state_path() {
        Ok(p) => p,
        Err(_) => return HashMap::new(),
    };
    if !path.exists() {
        return HashMap::new();
    }
    match std::fs::read_to_string(&path) {
        Ok(s) => serde_json::from_str(&s).unwrap_or_default(),
        Err(_) => HashMap::new(),
    }
}

fn write_map(map: &TaskStateMap) -> Result<(), String> {
    let path = task_state_path()?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("创建目录失败: {e}"))?;
    }
    let json = serde_json::to_string_pretty(map)
        .map_err(|e| format!("序列化失败: {e}"))?;
    std::fs::write(&path, json).map_err(|e| format!("写文件失败: {e}"))
}

/// 拉今天所有任务状态. UI 启动时调一次 seed.
/// 同时返昨天 snoozed (今天该重新出但要标记"昨天推的") — 让 caller 知道.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskStateFetch {
    /// 今天 done / ignored / snoozed (key: title)
    pub today: HashMap<String, TaskStateEntry>,
    /// 昨天 snoozed 的 title 列表 — 今天 advisor 出来时该标"⏰ 昨天推的"
    pub yesterday_snoozed: Vec<String>,
}

#[tauri::command]
pub async fn advisor_task_state_get() -> Result<TaskStateFetch, String> {
    let map = read_map();
    let today = map.get(&today_local()).cloned().unwrap_or_default();
    let yesterday_snoozed: Vec<String> = map
        .get(&yesterday_local())
        .map(|m| {
            m.iter()
                .filter(|(_, v)| v.status == "snoozed")
                .map(|(k, _)| k.clone())
                .collect()
        })
        .unwrap_or_default();
    Ok(TaskStateFetch {
        today,
        yesterday_snoozed,
    })
}

/// 切某 task 状态. status ∈ {"done", "ignored", "snoozed"}.
/// 空 status / 非法值 → 报错不写.
#[tauri::command]
pub async fn advisor_task_state_set(
    task_title: String,
    status: String,
) -> Result<(), String> {
    let title = task_title.trim();
    if title.is_empty() {
        return Err("task_title 不能空".into());
    }
    if title.len() > 200 {
        return Err("task_title 太长 (>200 字)".into());
    }
    if !matches!(status.as_str(), "done" | "ignored" | "snoozed") {
        return Err(format!(
            "非法 status: {status}. 只接 done/ignored/snoozed"
        ));
    }

    let mut map = read_map();
    let today = today_local();
    let day_map = map.entry(today).or_default();
    day_map.insert(
        title.to_string(),
        TaskStateEntry {
            status,
            ts: Utc::now().to_rfc3339(),
        },
    );
    write_map(&map)
}

/// 清某 task 的状态 (员工误标 done 想恢复 pending 时用).
#[tauri::command]
pub async fn advisor_task_state_clear(task_title: String) -> Result<(), String> {
    let title = task_title.trim();
    if title.is_empty() {
        return Err("task_title 不能空".into());
    }
    let mut map = read_map();
    let today = today_local();
    if let Some(day_map) = map.get_mut(&today) {
        day_map.remove(title);
        if day_map.is_empty() {
            map.remove(&today);
        }
    }
    write_map(&map)
}

/// 清掉 7 天以前的旧记录 (定期清理 GDPR / 防文件膨胀).
/// AdvisorView 启动时调一次.
#[tauri::command]
pub async fn advisor_task_state_prune_old() -> Result<u32, String> {
    let cutoff = (Local::now() - chrono::Duration::days(7))
        .format("%Y-%m-%d")
        .to_string();
    let mut map = read_map();
    let before = map.len();
    map.retain(|date, _| date.as_str() >= cutoff.as_str());
    let removed = (before - map.len()) as u32;
    if removed > 0 {
        write_map(&map)?;
    }
    Ok(removed)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn today_yesterday_format() {
        let today = today_local();
        let yesterday = yesterday_local();
        assert_eq!(today.len(), 10); // "YYYY-MM-DD"
        assert_eq!(yesterday.len(), 10);
        assert_ne!(today, yesterday);
    }

    #[test]
    fn status_entry_serializes() {
        // 确认 camelCase 序列化 — UI 那边 TaskStateEntry.ts 字段是 status/ts
        let entry = TaskStateEntry {
            status: "done".to_string(),
            ts: "2026-05-22T12:00:00Z".to_string(),
        };
        let json = serde_json::to_string(&entry).unwrap();
        assert!(json.contains("\"status\":\"done\""));
        assert!(json.contains("\"ts\":\"2026-05-22T12:00:00Z\""));
    }

    #[test]
    fn fetch_serializes_camelcase() {
        // TaskStateFetch.yesterdaySnoozed (TS) ↔ yesterday_snoozed (Rust) 通过 camelCase
        let fetch = TaskStateFetch {
            today: HashMap::new(),
            yesterday_snoozed: vec!["主菜 X".to_string()],
        };
        let json = serde_json::to_string(&fetch).unwrap();
        assert!(json.contains("yesterdaySnoozed"));
        assert!(!json.contains("yesterday_snoozed"));
    }
}
