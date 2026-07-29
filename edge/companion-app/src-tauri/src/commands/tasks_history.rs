//! BL-LONG-RUNNING-V1 (5/30) — 读 ~/.catfish/tasks.jsonl 历史任务.
//!
//! task_manager.py 把每条完成 (completed/failed) 的任务 append 到
//! ~/.catfish/tasks.jsonl. TasksCard 之前只看 in-memory list_active() —
//! 任务进 jsonl 后 24h 自动从 in-memory 清, Dashboard 就看不到了.
//!
//! 这个 Rust command 直读 jsonl, 跟 catfish_task_list MCP 合并去重, 让
//! Companion 重开 / 任务超 24h 后仍能看到历史.
//!
//! 跟 attachments.rs 同设计原则: 边缘本机 db, user_id 隔离 (jsonl 行不带
//! user_id 但本机文件物理隔离), 永不抛.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

fn tasks_jsonl_path() -> Option<PathBuf> {
    let catfish_home = std::env::var_os("CATFISH_HOME").map(PathBuf::from);
    let base = catfish_home.unwrap_or_else(|| {
        home_dir().map(|h| h.join(".catfish")).unwrap_or_default()
    });
    let p = base.join("tasks.jsonl");
    if p.exists() {
        Some(p)
    } else {
        None
    }
}

/// 一条历史任务记录, 对齐 task_manager.py:_persist_task_to_jsonl 写的格式.
#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "snake_case")]
pub struct TaskHistoryEntry {
    pub task_id: String,
    pub kind: String,
    pub label: String,
    pub status: String,       // "completed" | "failed"
    pub started_at: f64,
    pub finished_at: Option<f64>,
    pub elapsed_s: Option<f64>,
    pub error: Option<String>,
    pub result_preview: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TasksHistoryReadInput {
    /// 看过去多少小时 (默认 72 = 3 天)
    pub hours_back: Option<u64>,
    /// 最多返几条 (默认 200, 从 jsonl 末尾倒序读)
    pub limit: Option<u32>,
}

/// 读 ~/.catfish/tasks.jsonl, 返过去 N 小时内的任务列表.
/// 跟 task_manager.py:read_tasks_from_jsonl 同语义 (Companion 不走 MCP 中转直读).
#[tauri::command(rename_all = "camelCase")]
pub async fn tasks_history_read(
    input: TasksHistoryReadInput,
) -> Result<Vec<TaskHistoryEntry>, String> {
    tokio::task::spawn_blocking(move || {
        let path = match tasks_jsonl_path() {
            Some(p) => p,
            None => return Ok::<Vec<TaskHistoryEntry>, String>(vec![]),
        };
        let hours_back = input.hours_back.unwrap_or(72);
        let limit = input.limit.unwrap_or(200).clamp(1, 1000) as usize;

        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0);
        let cutoff = now - hours_back as f64 * 3600.0;

        let raw = match std::fs::read_to_string(&path) {
            Ok(s) => s,
            Err(e) => {
                // 文件不存在 / 权限 → 返空, 不报错
                if e.kind() == std::io::ErrorKind::NotFound {
                    return Ok(vec![]);
                }
                return Err(format!("读 tasks.jsonl 失败: {e}"));
            }
        };

        let mut out: Vec<TaskHistoryEntry> = Vec::new();
        for line in raw.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            match serde_json::from_str::<TaskHistoryEntry>(line) {
                Ok(entry) => {
                    // 时间过滤
                    if entry.started_at >= cutoff {
                        out.push(entry);
                    }
                }
                Err(_) => {
                    // 单行损坏不致命, 跳过 (跟 task_manager.py 同语义)
                }
            }
        }

        // BL-LONG-RUNNING-V1-PHASE-C-DEDUP (6/1): jsonl 现在每 task 多行
        // (pending submit row + completed/failed row + 可能的 interrupted row).
        // 6/1 早 PHASE-C-A 加的 _persist_task_started_to_jsonl 引入. 这里按
        // task_id group, 保留**最新一条** (finished_at 优先, 没就 started_at).
        // 否则 TasksCard 渲染同 task_id 多 row → React 报 key 重复警告.
        out.sort_by(|a, b| {
            let ta = a.finished_at.unwrap_or(a.started_at);
            let tb = b.finished_at.unwrap_or(b.started_at);
            tb.partial_cmp(&ta).unwrap_or(std::cmp::Ordering::Equal)
        });
        let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
        let dedup: Vec<TaskHistoryEntry> = out
            .into_iter()
            .filter(|e| seen.insert(e.task_id.clone()))
            .collect();
        let mut out = dedup;
        out.truncate(limit);

        Ok(out)
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::util::test_env::ENV_LOCK;
    use tempfile::TempDir;

    fn setup() -> (TempDir, std::sync::MutexGuard<'static, ()>) {
        let guard = ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
        let tmp = TempDir::new().expect("tempdir");
        std::env::set_var("CATFISH_HOME", tmp.path());
        (tmp, guard)
    }

    fn write_jsonl(home: &std::path::Path, lines: &[&str]) {
        let path = home.join("tasks.jsonl");
        std::fs::write(&path, lines.join("\n") + "\n").unwrap();
    }

    #[tokio::test]
    async fn empty_when_no_file() {
        let (_t, _g) = setup();
        let out = tasks_history_read(TasksHistoryReadInput {
            hours_back: None,
            limit: None,
        })
        .await
        .unwrap();
        assert!(out.is_empty());
    }

    #[tokio::test]
    async fn parses_recent_entries() {
        let (t, _g) = setup();
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap();
        let recent = serde_json::json!({
            "task_id": "task_recent",
            "kind": "execute_code",
            "label": "跑脚本",
            "status": "completed",
            "started_at": now - 60.0,
            "finished_at": now,
            "elapsed_s": 60.0,
            "error": null,
            "result_preview": "rc=0 stdout='hello'",
        });
        let old = serde_json::json!({
            "task_id": "task_old",
            "kind": "execute_code",
            "label": "老任务",
            "status": "completed",
            "started_at": now - 100.0 * 3600.0,  // 100 小时前
            "finished_at": now - 100.0 * 3600.0 + 10.0,
            "elapsed_s": 10.0,
            "error": null,
            "result_preview": "rc=0",
        });
        write_jsonl(t.path(), &[&recent.to_string(), &old.to_string()]);

        // 默认 72h, 老任务被滤掉
        let out = tasks_history_read(TasksHistoryReadInput {
            hours_back: None,
            limit: None,
        })
        .await
        .unwrap();
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].task_id, "task_recent");
        assert_eq!(out[0].status, "completed");

        // 加大 hours_back, 老任务也回来
        let out2 = tasks_history_read(TasksHistoryReadInput {
            hours_back: Some(200),
            limit: None,
        })
        .await
        .unwrap();
        assert_eq!(out2.len(), 2);
        // 倒序 — 新的在前
        assert_eq!(out2[0].task_id, "task_recent");
        assert_eq!(out2[1].task_id, "task_old");
    }

    #[tokio::test]
    async fn limit_respected() {
        let (t, _g) = setup();
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap();
        let lines: Vec<String> = (0..10)
            .map(|i| {
                serde_json::json!({
                    "task_id": format!("task_{}", i),
                    "kind": "x",
                    "label": format!("t{}", i),
                    "status": "completed",
                    "started_at": now - i as f64,
                    "finished_at": now - i as f64 + 1.0,
                    "elapsed_s": 1.0,
                    "error": null,
                    "result_preview": null,
                })
                .to_string()
            })
            .collect();
        let refs: Vec<&str> = lines.iter().map(|s| s.as_str()).collect();
        write_jsonl(t.path(), &refs);

        let out = tasks_history_read(TasksHistoryReadInput {
            hours_back: None,
            limit: Some(3),
        })
        .await
        .unwrap();
        assert_eq!(out.len(), 3);
        // 最新 3 个 (i=0, 1, 2)
        assert_eq!(out[0].task_id, "task_0");
        assert_eq!(out[2].task_id, "task_2");
    }

    #[tokio::test]
    async fn malformed_lines_skipped() {
        let (t, _g) = setup();
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap();
        let good = serde_json::json!({
            "task_id": "good",
            "kind": "x",
            "label": "ok",
            "status": "completed",
            "started_at": now - 10.0,
            "finished_at": now,
            "elapsed_s": 10.0,
            "error": null,
            "result_preview": null,
        })
        .to_string();
        write_jsonl(t.path(), &[
            "not json",
            "",
            &good,
            "{garbage",
        ]);

        let out = tasks_history_read(TasksHistoryReadInput {
            hours_back: None,
            limit: None,
        })
        .await
        .unwrap();
        // 只 good 这条成功 parse
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].task_id, "good");
    }
}
