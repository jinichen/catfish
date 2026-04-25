//! 日志 tail —— 把日志文件的新增行通过 Tauri event 流式推给前端。
//!
//! 协议：前端 invoke `start_tail({ service })` → Rust 起后台任务 →
//! 每读到一行 emit `log:<service>` 事件 → 前端 listen 接收。

use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct LogLine {
    pub service: String,
    pub line: String,
    pub ts: String, // ISO-8601
}

#[tauri::command]
pub async fn tail(_service: String, _from_end: bool) -> Result<(), String> {
    // TODO: 解析 service → log file 路径，spawn 后台任务 tail
    Err("not implemented".into())
}
