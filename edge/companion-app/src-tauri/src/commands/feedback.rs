//! BL-MM6 显式 feedback (5/5 晚 ship).
//!
//! 员工对助手消息打 👍/👎/"改" 时, 记到 ~/.catfish/feedback.jsonl (append-only).
//! gateway 在下次 chat 时 inject 最近 feedback 摘要到 system prompt,
//! 让 LLM 看到员工偏好长期演化 (跟 BL-MM5 主动学习配合, 但是是 explicit 而非自觉).
//!
//! Schema (一行一条 JSONL):
//!   {
//!     "ts": 1714867200.0,
//!     "kind": "thumb_up" | "thumb_down" | "edit",
//!     "session_id": "20260505_220000_xxx",
//!     "message_id": "client-side uuid",
//!     "preview": "助手消息前 200 字",      // 给 LLM 看时知道是哪条
//!     "comment": "为啥不好 / 改成怎样"     // 可空 (thumb_up 一般没)
//!   }

use serde::{Deserialize, Serialize};
use std::fs::{self, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct FeedbackEvent {
    pub ts: f64,
    pub kind: String, // "thumb_up" | "thumb_down" | "edit"
    pub session_id: String,
    pub message_id: String,
    pub preview: String,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub comment: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct FeedbackSummary {
    pub total: u32,
    pub thumb_up: u32,
    pub thumb_down: u32,
    pub edit: u32,
    /// 最近 N 条 negative 反馈 (👎 + 改), 给员工 review
    pub recent_negative: Vec<FeedbackEvent>,
    /// 文件大小 (UI 显示)
    pub file_size_bytes: u64,
}

fn home_dir() -> Result<PathBuf, String> {
    std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .map(PathBuf::from)
        .map_err(|_| "找不到 HOME 环境变量".to_string())
}

fn feedback_path() -> Result<PathBuf, String> {
    Ok(home_dir()?.join(".catfish").join("feedback.jsonl"))
}

fn now_ts() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

#[tauri::command]
pub async fn feedback_record(
    kind: String,
    session_id: String,
    message_id: String,
    preview: String,
    comment: Option<String>,
) -> Result<(), String> {
    if !["thumb_up", "thumb_down", "edit"].contains(&kind.as_str()) {
        return Err(format!("kind 不合法: {kind}"));
    }
    let path = feedback_path()?;
    fs::create_dir_all(path.parent().unwrap())
        .map_err(|e| format!("建目录失败: {e}"))?;

    let ev = FeedbackEvent {
        ts: now_ts(),
        kind,
        session_id,
        message_id,
        // 防 preview 巨长撑爆文件
        preview: preview.chars().take(200).collect(),
        comment: comment.map(|s| s.chars().take(500).collect()),
    };

    let line = serde_json::to_string(&ev)
        .map_err(|e| format!("序列化失败: {e}"))?;

    let mut f = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|e| format!("打开 feedback.jsonl 失败: {e}"))?;
    writeln!(f, "{line}").map_err(|e| format!("写 feedback.jsonl 失败: {e}"))?;
    Ok(())
}

#[tauri::command]
pub async fn feedback_summary() -> Result<FeedbackSummary, String> {
    let path = feedback_path()?;
    if !path.exists() {
        return Ok(FeedbackSummary {
            total: 0,
            thumb_up: 0,
            thumb_down: 0,
            edit: 0,
            recent_negative: vec![],
            file_size_bytes: 0,
        });
    }

    let bytes = fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
    let f = fs::File::open(&path).map_err(|e| format!("打开 feedback.jsonl 失败: {e}"))?;
    let reader = BufReader::new(f);

    let mut total = 0u32;
    let mut thumb_up = 0u32;
    let mut thumb_down = 0u32;
    let mut edit = 0u32;
    let mut all_negative: Vec<FeedbackEvent> = vec![];

    for line in reader.lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => continue,
        };
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        let ev: FeedbackEvent = match serde_json::from_str(trimmed) {
            Ok(e) => e,
            Err(_) => continue, // 损坏行跳过
        };
        total += 1;
        match ev.kind.as_str() {
            "thumb_up" => thumb_up += 1,
            "thumb_down" => {
                thumb_down += 1;
                all_negative.push(ev);
            }
            "edit" => {
                edit += 1;
                all_negative.push(ev);
            }
            _ => {}
        }
    }

    // 最近 5 条 negative (按 ts 倒序)
    all_negative.sort_by(|a, b| b.ts.partial_cmp(&a.ts).unwrap_or(std::cmp::Ordering::Equal));
    all_negative.truncate(5);

    Ok(FeedbackSummary {
        total,
        thumb_up,
        thumb_down,
        edit,
        recent_negative: all_negative,
        file_size_bytes: bytes,
    })
}

/// 清空 feedback (跟 RelationCard / MemoryHistoryCard 同模式 - 隐私逃生口).
#[tauri::command]
pub async fn feedback_clear() -> Result<(), String> {
    let path = feedback_path()?;
    if path.exists() {
        fs::remove_file(&path).map_err(|e| format!("删 feedback.jsonl 失败: {e}"))?;
    }
    Ok(())
}
