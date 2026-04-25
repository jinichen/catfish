//! 会话持久化 —— Companion 起的对话写入 ~/.hermes/state.db。
//!
//! Plan C Week 2: Companion ↔ Hermes 数据互通(决策 1c)
//!     Companion 起的会话标 source='companion',Hermes 起的标 source='cli'
//!     两者共享同一份 state.db,Sessions tab 列出所有,
//!     未来 hermes --resume <id> 也能继续 Companion 起的对话(同一 schema)。
//!
//! 多进程并发:
//!     ~/.hermes/state.db 可能被 hermes 同时写。SQLite WAL 模式 + busy_timeout
//!     5 秒,正常并发安全。极端冲突时 SQLITE_BUSY 错误 caller 重试一次。
//!
//! Session id 格式跟 hermes 对齐: "{YYYYMMDD}_{HHMMSS}_{6 位 hex 随机}"
//!     例如: 20260425_193045_a1b2c3
//!     这样 hermes --resume <id> 直接能识别。

use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use chrono::{Local, TimeZone};
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};

const BUSY_TIMEOUT_MS: u32 = 5000;

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

fn state_db_path() -> Result<PathBuf, String> {
    let home = home_dir().ok_or_else(|| "找不到 home 目录".to_string())?;
    let path = home.join(".hermes").join("state.db");
    if !path.exists() {
        return Err(format!(
            "state.db 不存在 — Hermes 至少要跑过一次, 才有这个 db 文件"
        ));
    }
    Ok(path)
}

fn open_db_for_write() -> Result<Connection, String> {
    let path = state_db_path()?;
    let conn = Connection::open(&path).map_err(|e| format!("打开 state.db 失败: {e}"))?;
    // WAL 让多进程读写并发, busy_timeout 让冲突自动重试
    conn.busy_timeout(std::time::Duration::from_millis(BUSY_TIMEOUT_MS as u64))
        .map_err(|e| format!("设 busy_timeout 失败: {e}"))?;
    // 不强制设 WAL —— hermes 自己起来时会设, 我们尊重它的配置
    Ok(conn)
}

fn now_unix() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// 生成 hermes 兼容的 session id: YYYYMMDD_HHMMSS_xxxxxx
fn generate_session_id() -> String {
    let now = Local::now();
    let date = now.format("%Y%m%d_%H%M%S");
    format!("{date}_{}", random_hex_6())
}

/// 6 位随机 hex —— 用 LCG 从 (nanos ^ pid) seed 出发迭代,
/// 比直接连续取 SystemTime::now() nanos 然后 mod 16 随机性强很多。
/// (之前的版本在 6 次连续调用里时间间隔 <1μs, 输出 hex 几乎全相同)
fn random_hex_6() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.subsec_nanos())
        .unwrap_or(0);
    let pid = std::process::id();
    let mut state: u32 = nanos.wrapping_mul(2654435761) ^ pid;

    let mut out = String::with_capacity(6);
    for _ in 0..6 {
        // glibc 经典 LCG
        state = state.wrapping_mul(1103515245).wrapping_add(12345);
        // 取中间位作为 hex(高位太规律,低位太接近 seed)
        let nibble = (state >> 16) & 0xF;
        out.push(std::char::from_digit(nibble, 16).unwrap_or('0'));
    }
    out
}

// ============================================================
// Tauri commands
// ============================================================

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionCreateInput {
    pub model: String,
    pub title: Option<String>,
    pub system_prompt: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionCreateOutput {
    pub id: String,
    pub started_at: f64,
}

/// 创建一个新 session, 返回 id 给前端后续 append message 用。
#[tauri::command(rename_all = "camelCase")]
pub async fn session_create(
    input: SessionCreateInput,
) -> Result<SessionCreateOutput, String> {
    tokio::task::spawn_blocking(move || {
        let conn = open_db_for_write()?;
        let id = generate_session_id();
        let started_at = now_unix();

        // user_id 留空, system_prompt 可选(SOUL 内容)
        // model_config 用空 JSON, 后续如有需要再扩展
        conn.execute(
            r#"
            INSERT INTO sessions (
                id, source, model, model_config, system_prompt,
                started_at, message_count, tool_call_count,
                input_tokens, output_tokens, cache_read_tokens,
                cache_write_tokens, reasoning_tokens, title
            ) VALUES (
                ?1, 'companion', ?2, '{}', ?3,
                ?4, 0, 0,
                0, 0, 0,
                0, 0, ?5
            )
        "#,
            params![
                id,
                input.model,
                input.system_prompt,
                started_at,
                input.title,
            ],
        )
        .map_err(|e| format!("插入 session 失败: {e}"))?;

        Ok::<SessionCreateOutput, String>(SessionCreateOutput { id, started_at })
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MessageAppendInput {
    pub session_id: String,
    pub role: String,
    pub content: String,
    /// JSON 字符串 (OpenAI tool_calls 格式), 没有则 None
    pub tool_calls: Option<String>,
    pub tool_call_id: Option<String>,
    pub tool_name: Option<String>,
    pub token_count: Option<i64>,
    pub finish_reason: Option<String>,
}

/// 追加一条消息到 messages 表, 顺便更新 sessions.message_count + token 统计。
#[tauri::command(rename_all = "camelCase")]
pub async fn session_message_append(
    input: MessageAppendInput,
) -> Result<i64, String> {
    tokio::task::spawn_blocking(move || {
        let conn = open_db_for_write()?;
        let ts = now_unix();

        conn.execute(
            r#"
            INSERT INTO messages (
                session_id, role, content, tool_call_id, tool_calls, tool_name,
                timestamp, token_count, finish_reason
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)
        "#,
            params![
                input.session_id,
                input.role,
                input.content,
                input.tool_call_id,
                input.tool_calls,
                input.tool_name,
                ts,
                input.token_count,
                input.finish_reason,
            ],
        )
        .map_err(|e| format!("插入 message 失败: {e}"))?;

        // 更新 session 的 message_count
        conn.execute(
            "UPDATE sessions SET message_count = message_count + 1 WHERE id = ?1",
            params![input.session_id],
        )
        .map_err(|e| format!("更新 session.message_count 失败: {e}"))?;

        // 如果是 tool_calls 消息, 增加 tool_call_count
        if input.tool_calls.is_some() {
            conn.execute(
                "UPDATE sessions SET tool_call_count = tool_call_count + 1 WHERE id = ?1",
                params![input.session_id],
            )
            .ok();
        }

        let row_id = conn.last_insert_rowid();
        Ok::<i64, String>(row_id)
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionFinalizeInput {
    pub session_id: String,
    pub end_reason: Option<String>,
    pub input_tokens: Option<i64>,
    pub output_tokens: Option<i64>,
}

/// 标记 session 结束 —— 写 ended_at + 累积 token 统计。
#[tauri::command(rename_all = "camelCase")]
pub async fn session_finalize(
    input: SessionFinalizeInput,
) -> Result<(), String> {
    tokio::task::spawn_blocking(move || {
        let conn = open_db_for_write()?;
        let ended_at = now_unix();
        conn.execute(
            r#"
            UPDATE sessions SET
                ended_at = ?1,
                end_reason = ?2,
                input_tokens = COALESCE(input_tokens, 0) + COALESCE(?3, 0),
                output_tokens = COALESCE(output_tokens, 0) + COALESCE(?4, 0)
            WHERE id = ?5
        "#,
            params![
                ended_at,
                input.end_reason.unwrap_or_else(|| "companion_close".into()),
                input.input_tokens,
                input.output_tokens,
                input.session_id,
            ],
        )
        .map_err(|e| format!("finalize session 失败: {e}"))?;
        Ok::<(), String>(())
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

/// 设置 session 标题 (LLM 第一句话之后,客户端可以推断标题再 update)
#[tauri::command(rename_all = "camelCase")]
pub async fn session_update_title(
    session_id: String,
    title: String,
) -> Result<(), String> {
    tokio::task::spawn_blocking(move || {
        let conn = open_db_for_write()?;
        conn.execute(
            "UPDATE sessions SET title = ?1 WHERE id = ?2",
            params![title, session_id],
        )
        .map_err(|e| format!("更新 title 失败: {e}"))?;
        Ok::<(), String>(())
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

/// 检查某个 session 是否存在 + 它是哪个 source (companion / cli) —— 给前端做幂等保护。
#[tauri::command(rename_all = "camelCase")]
pub async fn session_check(session_id: String) -> Result<Option<String>, String> {
    tokio::task::spawn_blocking(move || {
        let conn = open_db_for_write()?;
        let source: Option<String> = conn
            .query_row(
                "SELECT source FROM sessions WHERE id = ?1",
                params![session_id],
                |row| row.get(0),
            )
            .optional()
            .map_err(|e| format!("查 session 失败: {e}"))?;
        Ok::<Option<String>, String>(source)
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}
