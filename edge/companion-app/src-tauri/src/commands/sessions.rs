//! Hermes 会话 —— 读 ~/.hermes/state.db SQLite。
//!
//! 数据源是 Hermes 的 `state.db`，关键表：
//!   - sessions  —— 会话元数据（id / model / started_at / token 统计 / title）
//!   - messages  —— 消息明细（session_id / role / content / timestamp）
//!
//! NOTE: Hermes 不记录会话的 cwd / 项目目录，前端用 title 替代。

use std::path::PathBuf;

use chrono::DateTime;
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};

/// 5/5 鸿波拍板: 之前 MAX_SESSIONS=100 让 sidebar 永远显"100", 误解为总数.
/// 改全量拉, sidebar 已有 overflow: auto 支持垂直滚动.
/// 安全上限 10000 防 sqlite 瞎查 (sessions 历史几千条, sqlite ORDER BY started_at
/// 索引秒回; IPC ~200B/条 × 10000 = 2MB, Tauri webview 接得住).
/// 真到 10K 上限时 UX 也得改 (virtualize) — 但现在远远没到.
const MAX_SESSIONS: usize = 10000;
/// 单条消息内容截断 —— 避免几万字的长文档把 IPC 撑爆
const MAX_MESSAGE_CHARS: usize = 4000;

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionMeta {
    pub id: String,
    pub title: Option<String>,
    pub model: String,
    /// ISO-8601 UTC
    pub started_at: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ended_at: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub end_reason: Option<String>,
    pub message_count: u32,
    /// input + output + cache_read + cache_write + reasoning 之和
    pub total_tokens: u64,
    /// "cli" (Hermes 起的) / "companion" (Companion 起的) / null (老数据)
    /// 给左侧 sidebar 区分来源加 badge 用
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
}

/// 完整消息 —— 给 ChatPanel resume 历史用 (Plan C Week 3)
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionMessage {
    /// SQLite rowid 当 React key
    pub id: i64,
    /// "user" / "assistant" / "system" / "tool"
    pub role: String,
    /// 可能为空 (assistant 仅做 tool_call 时)
    pub content: String,
    /// JSON string of OpenAI tool_calls array, None 表示这条不是工具调用
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_calls: Option<String>,
    /// 工具结果消息会有 tool_call_id 关联回 assistant 的 tool_calls[].id
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
    /// 给 tool 角色的工具名 (有些场景需要按工具分组渲染)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_name: Option<String>,
    /// ISO-8601 UTC
    pub timestamp: String,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionDetail {
    pub meta: SessionMeta,
    /// 全部消息 —— 给 resume / 完整查看用; 按 timestamp asc
    pub messages: Vec<SessionMessage>,
    /// 老接口字段, 保留兼容现有 SessionsTab 的 SessionDetail UI
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_user_message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_assistant_message: Option<String>,
}

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
            "Hermes state.db 不存在 — {}（hermes 可能还没运行过）",
            path.display()
        ));
    }
    Ok(path)
}

fn open_db() -> Result<Connection, String> {
    let path = state_db_path()?;
    Connection::open(&path).map_err(|e| format!("打开 state.db 失败: {e}"))
}

/// Hermes 的 started_at 是 Unix epoch 秒（带小数毫秒）
fn unix_to_iso(unix_secs: f64) -> String {
    let secs = unix_secs.trunc() as i64;
    let nanos = (unix_secs.fract().abs() * 1e9) as u32;
    DateTime::from_timestamp(secs, nanos)
        .map(|dt| dt.to_rfc3339())
        .unwrap_or_default()
}

/// 截断超长消息，避免 IPC 数据爆炸
fn truncate(s: String) -> String {
    if s.chars().count() <= MAX_MESSAGE_CHARS {
        s
    } else {
        let truncated: String = s.chars().take(MAX_MESSAGE_CHARS).collect();
        format!("{truncated}\n\n…（已截断，原长度 {} 字符）", s.chars().count())
    }
}

// ============================================================
// Queries
// ============================================================

fn list_blocking() -> Result<Vec<SessionMeta>, String> {
    let conn = open_db()?;
    let mut stmt = conn
        .prepare(
            r#"
            SELECT
                id, title, model,
                started_at, ended_at, end_reason,
                message_count,
                COALESCE(input_tokens, 0) +
                COALESCE(output_tokens, 0) +
                COALESCE(cache_read_tokens, 0) +
                COALESCE(cache_write_tokens, 0) +
                COALESCE(reasoning_tokens, 0) AS total_tokens,
                source
            FROM sessions
            ORDER BY started_at DESC
            LIMIT ?1
        "#,
        )
        .map_err(|e| format!("SQL 准备失败: {e}"))?;

    let rows = stmt
        .query_map(params![MAX_SESSIONS as i64], row_to_meta)
        .map_err(|e| format!("SQL 查询失败: {e}"))?;

    let mut sessions = Vec::with_capacity(MAX_SESSIONS);
    for row in rows {
        sessions.push(row.map_err(|e| format!("行解析失败: {e}"))?);
    }
    Ok(sessions)
}

fn detail_blocking(id: String) -> Result<SessionDetail, String> {
    let conn = open_db()?;
    let meta = meta_by_id(&conn, &id)?;
    let messages = messages_blocking(&conn, &id)?;
    let last_user = last_message(&conn, &id, "user")?;
    let last_assistant = last_message(&conn, &id, "assistant")?;
    Ok(SessionDetail {
        meta,
        messages,
        last_user_message: last_user,
        last_assistant_message: last_assistant,
    })
}

fn meta_by_id(conn: &Connection, id: &str) -> Result<SessionMeta, String> {
    conn.query_row(
        r#"
        SELECT
            id, title, model,
            started_at, ended_at, end_reason,
            message_count,
            COALESCE(input_tokens, 0) +
            COALESCE(output_tokens, 0) +
            COALESCE(cache_read_tokens, 0) +
            COALESCE(cache_write_tokens, 0) +
            COALESCE(reasoning_tokens, 0) AS total_tokens,
            source
        FROM sessions
        WHERE id = ?1
    "#,
        params![id],
        row_to_meta,
    )
    .map_err(|e| match e {
        rusqlite::Error::QueryReturnedNoRows => format!("找不到会话 {id}"),
        other => format!("查询失败: {other}"),
    })
}

/// 取该会话最后一条指定 role 的消息 content（过滤 NULL / 空）
///
/// 例：assistant 在仅做 tool_call 时 content 可能为空，
/// 我们要的是真有文字回复的那条。
fn last_message(conn: &Connection, id: &str, role: &str) -> Result<Option<String>, String> {
    let mut stmt = conn
        .prepare(
            r#"
            SELECT content
            FROM messages
            WHERE session_id = ?1
              AND role = ?2
              AND content IS NOT NULL
              AND content != ''
            ORDER BY timestamp DESC
            LIMIT 1
        "#,
        )
        .map_err(|e| format!("SQL 准备失败: {e}"))?;

    match stmt.query_row(params![id, role], |row| row.get::<_, String>(0)) {
        Ok(content) => Ok(Some(truncate(content))),
        Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
        Err(e) => Err(format!("查询消息失败: {e}")),
    }
}

/// SQL row → SessionMeta 的共享转换器
/// 列序: id, title, model, started_at, ended_at, end_reason, message_count, total_tokens, source
fn row_to_meta(row: &rusqlite::Row) -> rusqlite::Result<SessionMeta> {
    Ok(SessionMeta {
        id: row.get(0)?,
        title: row.get(1)?,
        model: row
            .get::<_, Option<String>>(2)?
            .unwrap_or_else(|| "(无 model)".into()),
        started_at: unix_to_iso(row.get(3)?),
        ended_at: row.get::<_, Option<f64>>(4)?.map(unix_to_iso),
        end_reason: row.get(5)?,
        message_count: row.get::<_, i64>(6)?.max(0) as u32,
        total_tokens: row.get::<_, i64>(7)?.max(0) as u64,
        source: row.get(8)?,
    })
}

/// 拉某 session 的全部 messages, 给 resume 用。按 timestamp asc + rowid asc 稳定排序。
fn messages_blocking(conn: &Connection, id: &str) -> Result<Vec<SessionMessage>, String> {
    let mut stmt = conn
        .prepare(
            r#"
            SELECT
                rowid, role, COALESCE(content, ''),
                tool_calls, tool_call_id, tool_name, timestamp
            FROM messages
            WHERE session_id = ?1
            ORDER BY timestamp ASC, rowid ASC
        "#,
        )
        .map_err(|e| format!("SQL 准备失败: {e}"))?;

    let rows = stmt
        .query_map(params![id], |row| {
            Ok(SessionMessage {
                id: row.get(0)?,
                role: row.get(1)?,
                content: truncate(row.get::<_, String>(2)?),
                tool_calls: row.get(3)?,
                tool_call_id: row.get(4)?,
                tool_name: row.get(5)?,
                timestamp: unix_to_iso(row.get(6)?),
            })
        })
        .map_err(|e| format!("SQL 查询失败: {e}"))?;

    let mut out = Vec::new();
    for r in rows {
        out.push(r.map_err(|e| format!("行解析失败: {e}"))?);
    }
    Ok(out)
}

// ============================================================
// Tauri commands
// ============================================================

#[tauri::command]
pub async fn sessions_list() -> Result<Vec<SessionMeta>, String> {
    tokio::task::spawn_blocking(list_blocking)
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}

/// 5/5 鸿波报"会话计数永远显 100" 修: sessions_list 有 MAX_SESSIONS=100 上限,
/// 但 state.db 里实际可能几百个 session. UI 要区分"显示数 vs 总数".
/// 这个命令返 sessions 表真实总行数 (轻量, 单 SQL COUNT(*)).
#[tauri::command]
pub async fn sessions_count() -> Result<u64, String> {
    tokio::task::spawn_blocking(count_blocking)
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}

fn count_blocking() -> Result<u64, String> {
    let conn = open_db()?;
    let count: i64 = conn
        .query_row("SELECT COUNT(*) FROM sessions", [], |row| row.get(0))
        .map_err(|e| format!("SQL count 失败: {e}"))?;
    Ok(count.max(0) as u64)
}

#[tauri::command]
pub async fn sessions_get(id: String) -> Result<SessionDetail, String> {
    tokio::task::spawn_blocking(move || detail_blocking(id))
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}
