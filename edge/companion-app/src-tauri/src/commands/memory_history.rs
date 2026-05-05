//! BL-MM4 v1 Dashboard "鲶鱼记的硬事实" 卡 (5/5 晚 ship).
//!
//! 数据源: `~/.catfish/session_facts.json` (BL-MM2 v2 schema 已 ship)
//!   {
//!     "key1": [
//!       {"value": "v1", "ts": 1714867200.0, "prev_value": null},
//!       {"value": "v2", "ts": 1714867260.0, "prev_value": "v1"}
//!     ],
//!     ...
//!   }
//!
//! 设计立场: 跟 RelationCard 一样, 鲶鱼记的事**员工必须能看到 + 删**, 否则 creepy.
//!   - 列每个 key + 当前值 + 历史 revision 数
//!   - 点开看时间线 + 每版本 prev_value diff (像 git log)
//!   - 单 key 删 / 全部清空
//!
//! 兼容: 旧 schema {"key": "string"} (BL-MM2 之前的) 自动当作单 revision 处理.

use serde::Serialize;
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Serialize)]
pub struct MemoryHistoryView {
    /// 所有 key 的 summary (按最新更新时间倒序)
    pub keys: Vec<KeySummary>,
    /// 文件总字节数 (UI 显示"共 X KB")
    pub file_size_bytes: u64,
    /// 文件路径 (员工想去 Finder 看的话)
    pub file_path: String,
}

#[derive(Debug, Serialize)]
pub struct KeySummary {
    pub key: String,
    pub current_value: String,
    pub revision_count: usize,
    /// 最新 revision 的 ts (Unix 秒)
    pub last_updated_at: f64,
    /// 完整 revision history, 时间倒序 (最新在前)
    pub revisions: Vec<Revision>,
}

#[derive(Debug, Serialize)]
pub struct Revision {
    pub value: String,
    pub ts: f64,
    /// 上一版本的 value (BL-MM2 prev_value 字段). 首次写 = null.
    pub prev_value: Option<String>,
}

fn home_dir() -> Result<PathBuf, String> {
    std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .map(PathBuf::from)
        .map_err(|_| "找不到 HOME 环境变量".to_string())
}

fn facts_path() -> Result<PathBuf, String> {
    Ok(home_dir()?.join(".catfish").join("session_facts.json"))
}

/// 解析一条 revision dict (兼容字段缺失). 不合法返 None.
fn parse_revision(v: &serde_json::Value) -> Option<Revision> {
    let obj = v.as_object()?;
    let value = obj.get("value")?.as_str()?.to_string();
    let ts = obj
        .get("ts")
        .and_then(|t| t.as_f64())
        .unwrap_or(0.0);
    let prev_value = obj
        .get("prev_value")
        .and_then(|p| p.as_str())
        .map(|s| s.to_string());
    Some(Revision { value, ts, prev_value })
}

#[tauri::command]
pub async fn memory_history_summary() -> Result<MemoryHistoryView, String> {
    let path = facts_path()?;
    let path_str = path.to_string_lossy().to_string();

    if !path.exists() {
        return Ok(MemoryHistoryView {
            keys: vec![],
            file_size_bytes: 0,
            file_path: path_str,
        });
    }

    let bytes = fs::metadata(&path)
        .map(|m| m.len())
        .unwrap_or(0);

    let raw = fs::read_to_string(&path)
        .map_err(|e| format!("读 session_facts.json 失败: {e}"))?;
    let data: serde_json::Value = serde_json::from_str(&raw)
        .map_err(|e| format!("session_facts.json JSON 解析失败: {e}"))?;
    let map = match data.as_object() {
        Some(m) => m,
        None => {
            return Ok(MemoryHistoryView {
                keys: vec![],
                file_size_bytes: bytes,
                file_path: path_str,
            });
        }
    };

    let mut keys: Vec<KeySummary> = Vec::with_capacity(map.len());

    for (key, val) in map.iter() {
        let revisions: Vec<Revision> = match val {
            // BL-MM2 v2: list of revision dicts
            serde_json::Value::Array(arr) => {
                arr.iter().filter_map(parse_revision).collect()
            }
            // 旧 schema: 单 string 当一个 revision
            serde_json::Value::String(s) => {
                vec![Revision {
                    value: s.clone(),
                    ts: 0.0,
                    prev_value: None,
                }]
            }
            _ => continue, // 其他类型跳过
        };

        if revisions.is_empty() {
            continue;
        }

        // current = 最后一条 (磁盘上时间正序). last_updated_at = 它的 ts.
        let last = revisions.last().unwrap();
        let current_value = last.value.clone();
        let last_updated_at = last.ts;

        // UI 时间倒序 (最新在前展开看)
        let mut revisions_desc: Vec<Revision> = revisions.into_iter().collect();
        revisions_desc.reverse();

        keys.push(KeySummary {
            key: key.clone(),
            current_value,
            revision_count: revisions_desc.len(),
            last_updated_at,
            revisions: revisions_desc,
        });
    }

    // 按最新更新时间倒序
    keys.sort_by(|a, b| {
        b.last_updated_at
            .partial_cmp(&a.last_updated_at)
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    Ok(MemoryHistoryView {
        keys,
        file_size_bytes: bytes,
        file_path: path_str,
    })
}

/// 删某一个 key (保留其他). 文件不存在 = 无操作不报错.
#[tauri::command]
pub async fn memory_history_clear_key(key: String) -> Result<(), String> {
    let path = facts_path()?;
    if !path.exists() {
        return Ok(());
    }

    let raw = fs::read_to_string(&path)
        .map_err(|e| format!("读 session_facts.json 失败: {e}"))?;
    let mut data: serde_json::Value = serde_json::from_str(&raw)
        .map_err(|e| format!("session_facts.json JSON 解析失败: {e}"))?;

    if let Some(obj) = data.as_object_mut() {
        obj.remove(&key);
    }

    let pretty = serde_json::to_string_pretty(&data)
        .map_err(|e| format!("序列化失败: {e}"))?;
    fs::write(&path, pretty).map_err(|e| format!("写回失败: {e}"))?;
    Ok(())
}

/// 清空全部 (rm 文件). 比空 dict 还彻底, 跟 RelationCard.relation_forget 同模式.
#[tauri::command]
pub async fn memory_history_forget_all() -> Result<(), String> {
    let path = facts_path()?;
    if path.exists() {
        fs::remove_file(&path)
            .map_err(|e| format!("删 session_facts.json 失败: {e}"))?;
    }
    Ok(())
}
