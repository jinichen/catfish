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
    /// BL-SESSION-MGMT A (5/15): 首条 user message 前 80 字, 给 sidebar 在 title
    /// 还没生成时当 fallback 显示, 比 timestamp `(20260515_xxx)` 友好多了.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub first_user_message: Option<String>,
    /// BL-LONG-RUNNING-V1-PHASE-E (6/1): "可能在 hermes 后台仍 streaming" 的 heuristic.
    ///
    /// Companion 重开后 streamRegistry 内存丢, 但 hermes 是独立 launchctl 服务,
    /// 长任务还在跑. 没法直接查 hermes 内存, 用 state.db 推断:
    ///   - ended_at IS NULL AND end_reason IS NULL  (hermes 没正式 end)
    ///   - 最近 message < 5 分钟前 (太老的大概率 hermes crash 没清 ended_at)
    ///
    /// sidebar 给这种 session 一个 ⏳ 标识, 提示用户切回看看.
    /// 跟内存里 streamRegistry 标识区分 (那个是 100% 在跑, 这个是"可能").
    pub is_possibly_streaming: bool,
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
    // BL-SESSION-MGMT C (5/15): ALTER TABLE 加 deleted_at (幂等 — 已存在 SQLite 报
    // duplicate column 我们 ignore). 老 hermes state.db 没这列, 第一次跑加, 后续略.
    let _ = conn.execute(
        "ALTER TABLE sessions ADD COLUMN deleted_at INTEGER",
        [],
    );

    // 7/17 BL-SESSIONS-INDEX 鸿波 catch (2761 sessions 时 sidebar 慢): sessions_list
    // 大 SQL 用 2 个 subquery (first_user_message + MAX(timestamp)) 找每 session 的
    // 首消息和最新消息. 无 index 时每子查询 O(N messages), 总 O(sessions × messages).
    // 5 万 msg 时已经 ~30 秒, 20 万 msg 时 5-10 分钟, 完全卡死.
    //
    // Fix: 4 个 idempotent CREATE INDEX (`IF NOT EXISTS`, 已存在 no-op).
    // 首次 Companion 启动跑一次 (~ 秒级 build), 之后 O(log N) 查询.
    //
    // Hermes 上游 state.db 默认可能没建这些 index (Companion 侧的读 pattern 是我们独有).
    // 幂等 CREATE, 若 hermes 已建同样 index 也 no-op, 不冲突.
    let _ = conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_session_role_ts \
         ON messages(session_id, role, timestamp)",
        [],
    );
    let _ = conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_session_ts \
         ON messages(session_id, timestamp)",
        [],
    );
    let _ = conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_source_deleted_started \
         ON sessions(source, deleted_at, started_at DESC)",
        [],
    );
    let _ = conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_started_desc \
         ON sessions(started_at DESC)",
        [],
    );
    // BL-SESSION-MGMT A (5/15): JOIN 子查询拉每个 session 的首条 user message,
    // sidebar 在 title 还没生成时用这条 fallback (避免显裸 timestamp).
    // BL-SESSION-MGMT C: WHERE deleted_at IS NULL 默认过滤已软删的.
    // BL-SESSION-LIST-FILTER-DISTILL (2026-06-03): 过滤掉后台 session —
    // catfish-memory plugin distill 直接 POST /v1/chat/completions 创建的 session
    // (source='api_server', 标题=  '# 长期记忆 distilled_facts <!-- Generated by ... -->'),
    // 加 cron / 其他 internal source. 只显示员工真发起的对话渠道:
    //   - companion: Companion app 真对话
    //   - weixin: 微信接入员工对话
    // 后台 source (api_server / cron) 全部 hide. 后续如果加新员工对话渠道
    // (cli / vscode-plugin 等), 在这里加白名单.
    let mut stmt = conn
        .prepare(
            r#"
            SELECT
                s.id, s.title, s.model,
                s.started_at, s.ended_at, s.end_reason,
                s.message_count,
                COALESCE(s.input_tokens, 0) +
                COALESCE(s.output_tokens, 0) +
                COALESCE(s.cache_read_tokens, 0) +
                COALESCE(s.cache_write_tokens, 0) +
                COALESCE(s.reasoning_tokens, 0) AS total_tokens,
                s.source,
                (SELECT m.content FROM messages m
                 WHERE m.session_id = s.id AND m.role = 'user'
                   AND m.content IS NOT NULL AND m.content != ''
                 ORDER BY m.timestamp ASC, m.rowid ASC LIMIT 1) AS first_user_message,
                -- BL-LONG-RUNNING-V1-PHASE-E (6/1): heuristic is_possibly_streaming.
                -- hermes 没 end + 最近 5 分钟有消息 → 可能仍在跑.
                -- messages.timestamp 是 unix sec (float), 跟 sessions.started_at 同存储.
                -- 用 strftime('%s','now') 拿当前 unix sec, CAST 防 SQLite TEXT/REAL 比较 quirk.
                CASE
                    WHEN s.ended_at IS NULL AND s.end_reason IS NULL
                         AND (SELECT MAX(m.timestamp) FROM messages m
                              WHERE m.session_id = s.id)
                             > CAST(strftime('%s', 'now') AS REAL) - 300.0
                    THEN 1 ELSE 0
                END AS is_possibly_streaming
            FROM sessions s
            WHERE s.deleted_at IS NULL
              AND s.source IN ('companion', 'weixin')
            ORDER BY s.started_at DESC
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
    Ok(SessionDetail { meta, messages })
}

fn meta_by_id(conn: &Connection, id: &str) -> Result<SessionMeta, String> {
    // BL-SESSION-MGMT A (5/15): SELECT 跟 list_blocking 对齐, 多 col 9 first_user_message
    conn.query_row(
        r#"
        SELECT
            s.id, s.title, s.model,
            s.started_at, s.ended_at, s.end_reason,
            s.message_count,
            COALESCE(s.input_tokens, 0) +
            COALESCE(s.output_tokens, 0) +
            COALESCE(s.cache_read_tokens, 0) +
            COALESCE(s.cache_write_tokens, 0) +
            COALESCE(s.reasoning_tokens, 0) AS total_tokens,
            s.source,
            (SELECT m.content FROM messages m
             WHERE m.session_id = s.id AND m.role = 'user'
               AND m.content IS NOT NULL AND m.content != ''
             ORDER BY m.timestamp ASC, m.rowid ASC LIMIT 1) AS first_user_message,
            -- BL-LONG-RUNNING-V1-PHASE-E (6/1): 同 list_blocking heuristic
            CASE
                WHEN s.ended_at IS NULL AND s.end_reason IS NULL
                     AND (SELECT MAX(m.timestamp) FROM messages m
                          WHERE m.session_id = s.id)
                         > CAST(strftime('%s', 'now') AS REAL) - 300.0
                THEN 1 ELSE 0
            END AS is_possibly_streaming
        FROM sessions s
        WHERE s.id = ?1
    "#,
        params![id],
        row_to_meta,
    )
    .map_err(|e| match e {
        rusqlite::Error::QueryReturnedNoRows => format!("找不到会话 {id}"),
        other => format!("查询失败: {other}"),
    })
}

// P3.5.146 (6/30 鸿波"sessions.rs:88 兼容字段 dead 删"):
// 老 `last_message` fn + `last_user_message` / `last_assistant_message` 字段砍 —
// 给 dead SessionsTab UI 用的, 那条 dead 树在 P3.5.141 砍完后, 这两字段
// 也是 dead. truncate fn 保留 (messages_blocking 还在用).

/// SQL row → SessionMeta 的共享转换器
/// 列序: id, title, model, started_at, ended_at, end_reason, message_count,
///       total_tokens, source, first_user_message, is_possibly_streaming
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
        // BL-SESSION-MGMT A (5/15): SQL col 9 是首条 user message (可能 None),
        // 截前 80 字防超长 (e.g. 用户粘大段文档). 这只给 sidebar 显示用,
        // 完整消息走 sessions_get.
        first_user_message: row.get::<_, Option<String>>(9)?.map(|s| {
            let t = s.trim();
            if t.chars().count() > 80 {
                t.chars().take(80).collect::<String>() + "…"
            } else {
                t.to_string()
            }
        }).filter(|s| !s.is_empty()),
        // BL-LONG-RUNNING-V1-PHASE-E (6/1): col 10 = 0/1 (SQLite CASE 结果)
        is_possibly_streaming: row.get::<_, i64>(10).unwrap_or(0) != 0,
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

// ============================================================
// BL-SESSION-MGMT C (5/15): 删除 + bulk 清理
// ============================================================

/// 软删某 session — 标 deleted_at = now. UI 默认隐藏, 30 天后另起 cron 真删.
/// 错误条件: session 不存在 / 已 deleted.
#[tauri::command]
pub async fn session_soft_delete(id: String) -> Result<(), String> {
    tokio::task::spawn_blocking(move || soft_delete_blocking(id))
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}

fn soft_delete_blocking(id: String) -> Result<(), String> {
    let conn = open_db()?;
    // 幂等加列 (老 db 没 deleted_at)
    let _ = conn.execute("ALTER TABLE sessions ADD COLUMN deleted_at INTEGER", []);
    let rowcount = conn
        .execute(
            "UPDATE sessions SET deleted_at = strftime('%s', 'now') \
             WHERE id = ?1 AND deleted_at IS NULL",
            params![id],
        )
        .map_err(|e| format!("UPDATE 失败: {e}"))?;
    if rowcount == 0 {
        return Err(format!("session {id} 不存在或已删除"));
    }
    Ok(())
}

/// 恢复软删的 session (清 deleted_at). 让员工 oops 删错能 undo.
#[tauri::command]
pub async fn session_restore(id: String) -> Result<(), String> {
    tokio::task::spawn_blocking(move || restore_blocking(id))
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}

fn restore_blocking(id: String) -> Result<(), String> {
    let conn = open_db()?;
    let _ = conn.execute("ALTER TABLE sessions ADD COLUMN deleted_at INTEGER", []);
    let rowcount = conn
        .execute(
            "UPDATE sessions SET deleted_at = NULL WHERE id = ?1",
            params![id],
        )
        .map_err(|e| format!("UPDATE 失败: {e}"))?;
    if rowcount == 0 {
        return Err(format!("session {id} 不存在"));
    }
    Ok(())
}

/// Bulk 软删 ≤max_messages 条 + 距今 ≤max_age_hours 的 session.
/// preview=true: 只返要删的 id 清单, 不真删 (UI 弹窗预览用).
/// preview=false: 真删, 返删了几个.
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BulkDeleteResult {
    /// preview 时返清单, 真删时返删了的 id (前 50 个)
    pub session_ids: Vec<String>,
    /// 总数 (即使 preview / 实际删的数 也都是这个总数)
    pub total: u32,
    /// 跟 sessions_bulk_delete_short 入参对齐, 让前端能展示用了什么条件
    pub max_messages: u32,
    pub max_age_hours: u32,
}

#[tauri::command]
pub async fn sessions_bulk_delete_short(
    max_messages: u32,
    max_age_hours: u32,
    preview: bool,
) -> Result<BulkDeleteResult, String> {
    tokio::task::spawn_blocking(move || {
        bulk_delete_short_blocking(max_messages, max_age_hours, preview)
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

fn bulk_delete_short_blocking(
    max_messages: u32,
    max_age_hours: u32,
    preview: bool,
) -> Result<BulkDeleteResult, String> {
    let conn = open_db()?;
    let _ = conn.execute("ALTER TABLE sessions ADD COLUMN deleted_at INTEGER", []);

    // 找候选: message_count <= max_messages AND age <= max_age_hours AND deleted_at IS NULL
    let cutoff_secs = (max_age_hours as i64) * 3600;
    let mut stmt = conn
        .prepare(
            r#"
            SELECT id FROM sessions
            WHERE message_count <= ?1
              AND deleted_at IS NULL
              AND started_at >= (strftime('%s', 'now') - ?2)
            ORDER BY started_at DESC
        "#,
        )
        .map_err(|e| format!("SQL prepare 失败: {e}"))?;

    let ids: Vec<String> = stmt
        .query_map(params![max_messages as i64, cutoff_secs], |row| row.get(0))
        .map_err(|e| format!("SQL 查询失败: {e}"))?
        .filter_map(|r| r.ok())
        .collect();

    let total = ids.len() as u32;
    let session_ids_for_return: Vec<String> = ids.iter().take(50).cloned().collect();

    if !preview && !ids.is_empty() {
        // 真删 — 一次性 UPDATE 用 IN
        let placeholders = ids.iter().map(|_| "?").collect::<Vec<_>>().join(",");
        let sql = format!(
            "UPDATE sessions SET deleted_at = strftime('%s', 'now') \
             WHERE id IN ({placeholders}) AND deleted_at IS NULL"
        );
        let params_vec: Vec<&dyn rusqlite::ToSql> = ids
            .iter()
            .map(|id| id as &dyn rusqlite::ToSql)
            .collect();
        conn.execute(&sql, params_vec.as_slice())
            .map_err(|e| format!("批量 UPDATE 失败: {e}"))?;
    }

    Ok(BulkDeleteResult {
        session_ids: session_ids_for_return,
        total,
        max_messages,
        max_age_hours,
    })
}
