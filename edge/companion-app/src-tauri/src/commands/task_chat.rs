//! P3.3.7 Phase 2 (6/10 鸿波): task-scoped chat 持久化.
//!
//! 早安 tab detail pane 嵌入的 task chat (P3.3.7 Phase 1 in-memory) 持久化到
//! ~/.catfish/task_chat/<task_key>.jsonl. Append-only, 每行一条 JSON {role, content, ts}.
//!
//! task_key = task.title sanitize 后 (path-unsafe 字符替换 _). 跨天同标题 task
//! 共享 chat 历史 (LLM 重生成同标题 task 时仍能看到之前进度).
//!
//! 跟 BL-CENTRAL-EDGE: 员工本机数据, 不出端.
//!
//! Tauri commands:
//!   - task_chat_get(taskKey) → Vec<TaskChatMsg>
//!   - task_chat_append(taskKey, role, content) → ()
//!   - task_chat_clear(taskKey) → ()  // 给"重新开始"按钮用

use std::path::PathBuf;
use chrono::Utc;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskChatMsg {
    pub role: String,    // "user" / "assistant" / "system"
    pub content: String,
    pub ts: String,      // ISO-8601
}

// ── 路径 + sanitize ──────────────────────────────────────────────────

fn task_chat_dir() -> Result<PathBuf, String> {
    let home = std::env::var("HOME").map_err(|e| format!("HOME 未设: {e}"))?;
    let dir = PathBuf::from(home).join(".catfish").join("task_chat");
    std::fs::create_dir_all(&dir)
        .map_err(|e| format!("创建 ~/.catfish/task_chat/ 失败: {e}"))?;
    Ok(dir)
}

/// sanitize task title 为 path-safe 文件名.
/// 替换 / \ : * ? " < > | 等 → _, 限长 100 char, 防 path traversal.
fn sanitize_key(raw: &str) -> String {
    let bad: &[char] = &['/', '\\', ':', '*', '?', '"', '<', '>', '|', '\n', '\r', '\t', '.'];
    let cleaned: String = raw
        .chars()
        .take(100)
        .map(|c| if bad.contains(&c) { '_' } else { c })
        .collect();
    let trimmed = cleaned.trim_matches('_').trim();
    if trimmed.is_empty() {
        "_unnamed_".to_string()
    } else {
        trimmed.to_string()
    }
}

fn chat_file_for(task_key: &str) -> Result<PathBuf, String> {
    let key = sanitize_key(task_key);
    if key.is_empty() || key.contains("..") {
        return Err(format!("invalid task_key: {task_key}"));
    }
    let dir = task_chat_dir()?;
    Ok(dir.join(format!("{key}.jsonl")))
}

// ── Tauri commands ────────────────────────────────────────────────────

/// 读 task chat 全部历史. 没文件返空列表.
#[tauri::command(rename_all = "camelCase")]
pub async fn task_chat_get(task_key: String) -> Result<Vec<TaskChatMsg>, String> {
    let path = chat_file_for(&task_key)?;
    if !path.exists() {
        return Ok(Vec::new());
    }
    let content = std::fs::read_to_string(&path)
        .map_err(|e| format!("读 {path:?} 失败: {e}"))?;
    let mut out = Vec::new();
    for (i, line) in content.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        match serde_json::from_str::<TaskChatMsg>(line) {
            Ok(msg) => out.push(msg),
            Err(e) => {
                // skip 单行解析错, 不让一条坏 row 把整条历史阻断
                eprintln!("[task_chat] {path:?} 第 {} 行解析失败: {e}", i + 1);
            }
        }
    }
    Ok(out)
}

/// Append 一条 message 到 task chat. 自动填 ts (UTC).
#[tauri::command(rename_all = "camelCase")]
pub async fn task_chat_append(
    task_key: String,
    role: String,
    content: String,
) -> Result<(), String> {
    if role != "user" && role != "assistant" && role != "system" {
        return Err(format!("invalid role: {role}"));
    }
    let path = chat_file_for(&task_key)?;
    let msg = TaskChatMsg {
        role,
        content,
        ts: Utc::now().to_rfc3339(),
    };
    let line = serde_json::to_string(&msg)
        .map_err(|e| format!("serialize 失败: {e}"))?;

    use std::io::Write;
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|e| format!("打开 {path:?} 失败: {e}"))?;
    writeln!(file, "{line}")
        .map_err(|e| format!("写 {path:?} 失败: {e}"))?;
    Ok(())
}

/// 清除 task chat 历史 (rm file). 给"重新开始" 按钮用. 不存在不报错.
#[tauri::command(rename_all = "camelCase")]
pub async fn task_chat_clear(task_key: String) -> Result<(), String> {
    let path = chat_file_for(&task_key)?;
    if path.exists() {
        std::fs::remove_file(&path)
            .map_err(|e| format!("删 {path:?} 失败: {e}"))?;
    }
    Ok(())
}
