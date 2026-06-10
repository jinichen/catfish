//! P3.3.7 Phase 2 (6/10 鸿波): task-scoped chat 持久化.
//!
//! 早安 tab detail pane 嵌入的 task chat (P3.3.7 Phase 1 in-memory) 持久化到
//! ~/.catfish/task_chat/<task_key>.jsonl. Append-only, 每行一条 JSON.
//!
//! task_key = P3.3.9 后用 task.taskUid (LLM 生成 6 字符稳定 uid), 跨 refresh 不漂.
//! P3.3.9 之前是 sanitize(task.title), 老 jsonl 文件仍能 load (DetailPane 兜底回退).
//!
//! 跟 BL-CENTRAL-EDGE: 员工本机数据, 不出端.
//!
//! P3.3.11 (6/10) schema 扩:
//!   - role 增加 "tool" (LLM 调 tool 后写 tool result row)
//!   - tool_calls?: Vec<serde_json::Value> — assistant 消息附带的 tool 调用列表
//!     (id / name / args / status / result / error 字段, 跟 TS ToolCall 同款)
//!   - tool_call_id?: String — tool 角色消息关联到 assistant 的 tool_calls[i].id
//!   老 jsonl 没这俩字段 — serde 默认 None, 向后兼容.
//!
//! Tauri commands:
//!   - task_chat_get(taskKey) → Vec<TaskChatMsg>
//!   - task_chat_append(taskKey, role, content, toolCalls?, toolCallId?) → ()
//!   - task_chat_clear(taskKey) → ()  // 给"重新开始"按钮用

use std::path::PathBuf;
use chrono::Utc;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskChatMsg {
    pub role: String,    // "user" / "assistant" / "tool" / "system"
    pub content: String,
    pub ts: String,      // ISO-8601
    /// P3.3.11: assistant 消息附带的 tool calls. None / 缺字段 = 没 tool calls.
    /// 用 Value 不绑死类型 — TS ToolCall shape 演化时 Rust 不用跟改.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_calls: Option<Vec<serde_json::Value>>,
    /// P3.3.11: tool 角色消息关联到 assistant.tool_calls[i].id.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
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
/// P3.3.11: 加 tool_calls / tool_call_id 可选参数 (assistant 含 tool 调用 / tool 角色用).
#[tauri::command(rename_all = "camelCase")]
pub async fn task_chat_append(
    task_key: String,
    role: String,
    content: String,
    tool_calls: Option<Vec<serde_json::Value>>,
    tool_call_id: Option<String>,
) -> Result<(), String> {
    if role != "user" && role != "assistant" && role != "system" && role != "tool" {
        return Err(format!("invalid role: {role}"));
    }
    let path = chat_file_for(&task_key)?;
    let msg = TaskChatMsg {
        role,
        content,
        ts: Utc::now().to_rfc3339(),
        tool_calls,
        tool_call_id,
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
