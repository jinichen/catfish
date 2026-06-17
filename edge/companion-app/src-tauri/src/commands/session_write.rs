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

use chrono::Local;
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

    // P3.3.19 (6/11): catfish sidecar 表 — 不动 hermes 上游 sessions/messages,
    // 加自家表存 task_uid 关联 (跟 hermes 0.16 schema 隔离, 上游升级不冲突).
    ensure_catfish_sidecar_schema(&conn)?;

    Ok(conn)
}

/// P3.3.19 (6/11): 建 catfish 自家 sidecar 表 (IF NOT EXISTS 幂等).
/// 不动 hermes sessions / messages. 跟 hermes 0.16 升级隔离 (公理: monkey-patch
/// 不 fork 上游). 表前缀 catfish_ 防撞.
fn ensure_catfish_sidecar_schema(conn: &Connection) -> Result<(), String> {
    conn.execute_batch(
        r#"
        CREATE TABLE IF NOT EXISTS catfish_session_metadata (
            session_id TEXT PRIMARY KEY,
            task_uid TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_catfish_metadata_task_uid
            ON catfish_session_metadata(task_uid);
        "#,
    )
    .map_err(|e| format!("建 catfish_session_metadata 表失败: {e}"))?;
    Ok(())
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
        //
        // P3.4.E.8 (6/15 鸿波): 撞 UNIQUE constraint 自动 retry with title 后缀.
        //   真因: hermes 创建 state.db 时加了 `CREATE UNIQUE INDEX idx_sessions_title_unique
        //   ON sessions(title) WHERE title IS NOT NULL` (见 hermes session-storage.md:69).
        //   场景:
        //     1. BriefingTwoColumn 切 task A → sessionCreate(title='task A') 成功
        //     2. advisor refresh, LLM 重生成 task list, 新 taskUid 但 LLM 给同 title (业务一致)
        //     3. mount 新 taskUid → sessionGetByTaskUid 返 null → sessionCreate(title='task A') 撞
        //     4. 或 React StrictMode dev 双 mount race — 两次同时跑 sessionCreate(title 同) 撞
        //   不该 silent fail (caller 要 id) 不该 ON CONFLICT(title) DO UPDATE (新业务不该串老 session)
        //   也不该改 hermes schema (manifesto: monkey-patch 不 fork). retry with suffix " (2)" 最稳.
        //
        //   注: title 为 None 时, hermes UNIQUE INDEX `WHERE title IS NOT NULL` 不约束 NULL,
        //   多个 NULL title session 共存合法, 不会撞 UNIQUE, retry 也不影响.
        let base_title = input.title.clone();
        let mut attempt_title = base_title.clone();
        let mut suffix = 1u32;
        loop {
            let r = conn.execute(
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
                    attempt_title,
                ],
            );
            match r {
                Ok(_) => break,
                Err(e) => {
                    // string match "UNIQUE constraint" 比 rusqlite::ErrorCode::ConstraintViolation
                    // 更兼容 rusqlite 版本变动. SQLite UNIQUE 错文案稳定 "UNIQUE constraint failed".
                    let s = e.to_string();
                    if s.contains("UNIQUE constraint") {
                        // base title None 不该撞 UNIQUE (hermes index WHERE title IS NOT NULL),
                        // 如果撞了说明 schema 真坏 — 直接报错 (不无限循环).
                        let Some(base) = base_title.as_ref() else {
                            return Err(format!(
                                "插入 session 失败: title=NULL 仍撞 UNIQUE (hermes schema 异常?): {e}"
                            ));
                        };
                        suffix += 1;
                        if suffix > 20 {
                            return Err(format!(
                                "插入 session 失败: title '{}' 重试 20 次仍撞 UNIQUE (DB 真坏?)",
                                base
                            ));
                        }
                        let new_title = format!("{} ({})", base, suffix);
                        log::info!(
                            "[session_write] P3.4.E.8 title '{}' 撞 UNIQUE, retry with '{}'",
                            base,
                            new_title
                        );
                        attempt_title = Some(new_title);
                    } else {
                        return Err(format!("插入 session 失败: {e}"));
                    }
                }
            }
        }

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
///
/// P3.5.21 (6/17 鸿波): assistant role 加 idempotent — INSERT 前 query 同 session
/// 最后一条 assistant, content + tool_calls 完全一样 → skip + 返已有 rowid.
///
/// 真因: Companion useChat.ts:332 之前 `if (!refs.viaHermes && ctx.sessionId)` 走
/// hermes 路径跳过 persist 信任 hermes 自己写. 但 hermes 真有不 persist 的 case
/// (鸿波 6/17 12:46 turn end hermes log "history=58" 应该 59, 差 1 = 上 turn assistant
/// 没 persist 到老 session). Companion 切走再回来从 state.db load 不见 → 丢数据.
///
/// 修法: Companion 改总 persist (useChat.ts:332 拆 !viaHermes 守门). 风险 = 5/23
/// BL-COMPANION-HERMES-SESSION-REUSE 撞过双写 UI 重复 (hermes 也写 + Companion 也写).
/// 这里 idempotent 是双写防护底: 同 session 最后一条 assistant + 同 content + 同
/// tool_calls JSON → 第 2 次 INSERT skip 返已有 rowid.
///
/// 不动 user / tool role — user "继续" 真会反复发 (鸿波长程任务习惯), 不能 dedup.
/// tool 是 hermes 独自 emit, Companion 不会写, 不会双写.
#[tauri::command(rename_all = "camelCase")]
pub async fn session_message_append(
    input: MessageAppendInput,
) -> Result<i64, String> {
    tokio::task::spawn_blocking(move || {
        let mut conn = open_db_for_write()?;
        let ts = now_unix();

        // P3.5.21 + P3.5.24 (6/17 鸿波 "回答都是重复的") idempotent guard:
        // assistant role + 同 session 最后一条 assistant 内容相同 → skip insert.
        //
        // P3.5.24 改进 2 条:
        //   1. **trim() 兜 byte 差**: hermes _persist_session vs Companion onDone
        //      写的 content 可能末尾 trailing newline/whitespace 一字之差, 严格 ==
        //      不命中导致 idempotent 失效, UI 显双写. 改成 trim() 后字符串比.
        //   2. **BEGIN IMMEDIATE transaction 兜 race**: hermes 跟 Companion 真并发
        //      onDone 后都跑 persist, query SELECT 时 race window 没命中 last row →
        //      都 INSERT → 双写. BEGIN IMMEDIATE 锁 db, query+INSERT 原子, race 消失.
        let tx = conn
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)
            .map_err(|e| format!("BEGIN IMMEDIATE 失败: {e}"))?;

        if input.role == "assistant" {
            let last: Option<(i64, Option<String>, Option<String>)> = tx
                .query_row(
                    "SELECT id, content, tool_calls FROM messages \
                     WHERE session_id = ?1 AND role = 'assistant' \
                     ORDER BY id DESC LIMIT 1",
                    params![input.session_id],
                    |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
                )
                .ok();
            if let Some((existing_id, existing_content, existing_tool_calls)) = last {
                // P3.5.24: 用 trim() 兜 trailing whitespace/newline 差异.
                let existing_trimmed = existing_content.as_deref().map(|s| s.trim());
                let input_trimmed = input.content.as_str().trim();
                let content_same = existing_trimmed == Some(input_trimmed);
                let tools_same = existing_tool_calls.as_deref() == input.tool_calls.as_deref();
                if content_same && tools_same {
                    log::info!(
                        "P3.5.21+24 idempotent skip: session={} assistant content+tool_calls 跟 last (rowid={}) 等 (trim 后), 防双写",
                        input.session_id, existing_id,
                    );
                    // commit transaction (no-op, 没 INSERT) — 防 tx Drop 时 ROLLBACK warn.
                    let _ = tx.commit();
                    return Ok::<i64, String>(existing_id);
                }
            }
        }

        tx.execute(
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
        tx.execute(
            "UPDATE sessions SET message_count = message_count + 1 WHERE id = ?1",
            params![input.session_id],
        )
        .map_err(|e| format!("更新 session.message_count 失败: {e}"))?;

        // 如果是 tool_calls 消息, 增加 tool_call_count
        if input.tool_calls.is_some() {
            tx.execute(
                "UPDATE sessions SET tool_call_count = tool_call_count + 1 WHERE id = ?1",
                params![input.session_id],
            )
            .ok();
        }

        let row_id = tx.last_insert_rowid();
        // P3.5.24: 提交 transaction 释放 IMMEDIATE 锁, 让别的进程能写.
        tx.commit().map_err(|e| format!("transaction commit 失败: {e}"))?;
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

// ============================================================
// P3.3.19 (6/11): catfish_session_metadata sidecar — task_uid ↔ session_id 关联
//
// 设计:
//   - 1 task 可关联 N session (老 task chat 历史 + 新对话不强制合并)
//   - latest session 走 ORDER BY created_at DESC LIMIT 1
//   - DetailPane / ChatTab 都查 sidecar 找 task 的 session, 在哪边发都进同一 session
//   - manifesto 兼容: 这是员工本机, 中央不读 (公理 4)
// ============================================================

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct SessionTaskAssoc {
    pub session_id: String,
    pub task_uid: Option<String>,
    pub created_at: f64,
    pub updated_at: f64,
}

/// 绑/改 一条 session 的 task_uid. 同 session_id 多次调 → UPDATE.
#[tauri::command(rename_all = "camelCase")]
pub async fn session_set_task_uid(
    session_id: String,
    task_uid: Option<String>,
) -> Result<(), String> {
    tokio::task::spawn_blocking(move || {
        let conn = open_db_for_write()?;
        let now = now_unix();
        conn.execute(
            r#"
            INSERT INTO catfish_session_metadata (session_id, task_uid, created_at, updated_at)
            VALUES (?1, ?2, ?3, ?3)
            ON CONFLICT(session_id) DO UPDATE SET
                task_uid = excluded.task_uid,
                updated_at = excluded.updated_at
            "#,
            params![session_id, task_uid, now],
        )
        .map_err(|e| format!("写 catfish_session_metadata 失败: {e}"))?;
        Ok::<(), String>(())
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

/// 查 session_id 对应的 task_uid (None = 没绑或老 session).
#[tauri::command(rename_all = "camelCase")]
pub async fn session_get_task_uid(session_id: String) -> Result<Option<String>, String> {
    tokio::task::spawn_blocking(move || {
        let conn = open_db_for_write()?;
        let task_uid: Option<String> = conn
            .query_row(
                "SELECT task_uid FROM catfish_session_metadata WHERE session_id = ?1",
                params![session_id],
                |row| row.get(0),
            )
            .optional()
            .map_err(|e| format!("查 task_uid 失败: {e}"))?
            .flatten();
        Ok::<Option<String>, String>(task_uid)
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

/// 查 task_uid 对应的 latest session_id (一个 task 可能多 session, 取 created_at desc 第一).
/// 返 None = 这 task 还没 session, caller 应 sessionCreate + session_set_task_uid.
#[tauri::command(rename_all = "camelCase")]
pub async fn session_get_by_task_uid(task_uid: String) -> Result<Option<String>, String> {
    tokio::task::spawn_blocking(move || {
        let conn = open_db_for_write()?;
        let session_id: Option<String> = conn
            .query_row(
                r#"
                SELECT m.session_id
                FROM catfish_session_metadata m
                JOIN sessions s ON s.id = m.session_id
                WHERE m.task_uid = ?1
                ORDER BY s.started_at DESC
                LIMIT 1
                "#,
                params![task_uid],
                |row| row.get(0),
            )
            .optional()
            .map_err(|e| format!("查 session by task_uid 失败: {e}"))?;
        Ok::<Option<String>, String>(session_id)
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

/// 列 task_uid 对应的所有 session (按 started_at desc). 给 ChatSidebar 显 task 历史.
#[tauri::command(rename_all = "camelCase")]
pub async fn list_sessions_by_task_uid(
    task_uid: String,
) -> Result<Vec<SessionTaskAssoc>, String> {
    tokio::task::spawn_blocking(move || {
        let conn = open_db_for_write()?;
        let mut stmt = conn
            .prepare(
                r#"
                SELECT m.session_id, m.task_uid, m.created_at, m.updated_at
                FROM catfish_session_metadata m
                JOIN sessions s ON s.id = m.session_id
                WHERE m.task_uid = ?1
                ORDER BY s.started_at DESC
                "#,
            )
            .map_err(|e| format!("prepare 失败: {e}"))?;
        let rows = stmt
            .query_map(params![task_uid], |row| {
                Ok(SessionTaskAssoc {
                    session_id: row.get(0)?,
                    task_uid: row.get(1)?,
                    created_at: row.get(2)?,
                    updated_at: row.get(3)?,
                })
            })
            .map_err(|e| format!("query_map 失败: {e}"))?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row.map_err(|e| format!("row decode 失败: {e}"))?);
        }
        Ok::<Vec<SessionTaskAssoc>, String>(out)
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

// ============================================================
// 单测 —— 重点验证: id 唯一性, append/finalize/title 4 happy paths,
//          + 并发写不挂 (模拟 hermes 同时写 state.db 的场景)
//
// 跑法: cd src-tauri && cargo test --lib commands::session_write
//
// 不依赖真 hermes —— 用一个临时目录假装是 $HOME, 自己建个 schema 兼容的 db。
// ============================================================

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;
    use std::sync::Mutex;
    use tempfile::TempDir;

    /// 测试用建一个跟 hermes state.db 兼容的最小 schema
    /// (只建 session_write.rs 用到的字段, 比真 schema 少很多)
    fn create_test_schema(conn: &Connection) {
        conn.execute_batch(
            r#"
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                source TEXT,
                model TEXT,
                model_config TEXT,
                system_prompt TEXT,
                started_at REAL,
                ended_at REAL,
                end_reason TEXT,
                message_count INTEGER DEFAULT 0,
                tool_call_count INTEGER DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cache_read_tokens INTEGER DEFAULT 0,
                cache_write_tokens INTEGER DEFAULT 0,
                reasoning_tokens INTEGER DEFAULT 0,
                title TEXT
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                tool_call_id TEXT,
                tool_calls TEXT,
                tool_name TEXT,
                timestamp REAL,
                token_count INTEGER,
                finish_reason TEXT
            );
            -- P3.4.E.8 (6/15 鸿波): 跟 hermes 真实 db 一致, 加 partial UNIQUE INDEX on title.
            -- 见 hermes session-storage.md:69 — "CREATE UNIQUE INDEX IF NOT EXISTS
            -- idx_sessions_title_unique ON sessions(title) WHERE title IS NOT NULL".
            -- 不加这个 index 测不出 P3.4.E.8 retry 行为.
            CREATE UNIQUE INDEX idx_sessions_title_unique
                ON sessions(title) WHERE title IS NOT NULL;
            "#,
        )
        .expect("create_test_schema");
    }

    /// HOME env 是进程级共享, 多 test 并发会互相覆盖 —— 用一个 mutex 串行化。
    /// (这是 Rust test 跑 env-mutating code 的标准 workaround)
    static ENV_LOCK: Mutex<()> = Mutex::new(());

    /// 设 HOME 指向 tmp_dir, 在那建好 .hermes/state.db, 返回 TempDir 把所有权
    /// 交给调用者 (drop 时清理目录)。
    fn setup_test_env() -> (TempDir, std::sync::MutexGuard<'static, ()>) {
        let guard = ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
        let tmp = TempDir::new().expect("tempdir");
        let hermes = tmp.path().join(".hermes");
        std::fs::create_dir_all(&hermes).unwrap();
        let db_path = hermes.join("state.db");
        let conn = Connection::open(&db_path).unwrap();
        create_test_schema(&conn);
        std::env::set_var("HOME", tmp.path());
        (tmp, guard)
    }

    // ---------- random_hex_6 ----------

    #[test]
    fn random_hex_6_format() {
        let s = random_hex_6();
        assert_eq!(s.len(), 6);
        assert!(s.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn random_hex_6_not_constant() {
        // 跑 100 次, 至少要有 5 个不同结果 (LCG 不是密码学随机, 但快速连续调
        // 也不该全相同 —— 历史 bug: 之前用 nanos%16 在 6 次连续 <1μs 内输出
        // 全相同, 改 LCG 后修复)。
        let mut seen = std::collections::HashSet::new();
        for _ in 0..100 {
            seen.insert(random_hex_6());
        }
        assert!(
            seen.len() >= 5,
            "random_hex_6 太弱, 100 次只产出 {} 个不同值",
            seen.len()
        );
    }

    #[test]
    fn generate_session_id_format() {
        let id = generate_session_id();
        // YYYYMMDD_HHMMSS_xxxxxx —— 总长 8+1+6+1+6 = 22
        assert_eq!(id.len(), 22, "id should be 22 chars: {id}");
        let parts: Vec<&str> = id.split('_').collect();
        assert_eq!(parts.len(), 3);
        assert_eq!(parts[0].len(), 8); // date
        assert_eq!(parts[1].len(), 6); // time
        assert_eq!(parts[2].len(), 6); // hex
    }

    // ---------- happy paths ----------

    /// P3.4.E.8 (6/15 鸿波): 验证 UNIQUE constraint 撞了能自动 retry with suffix.
    /// 场景: hermes 真实 db 有 `idx_sessions_title_unique`, BriefingTwoColumn mount
    /// 同 title 第二次会撞. 老代码直接 'UNIQUE constraint failed' 报错, UI 红条.
    /// 新代码 retry → 第二次自动用 'title (2)' 不撞, caller 拿到 id.
    #[tokio::test]
    async fn session_create_retries_on_unique_title_collision() {
        let (_tmp, _g) = setup_test_env();
        // 第一次: title='巡视巡察整改回头看确认' → 成功, title 不变
        let out1 = session_create(SessionCreateInput {
            model: "test-model".into(),
            title: Some("巡视巡察整改回头看确认".into()),
            system_prompt: None,
        })
        .await
        .expect("first create");
        // 第二次: 同 title → 应该 retry 用 'title (2)'
        let out2 = session_create(SessionCreateInput {
            model: "test-model".into(),
            title: Some("巡视巡察整改回头看确认".into()),
            system_prompt: None,
        })
        .await
        .expect("second create with same title should retry");
        // 第三次: 再撞 → 'title (3)'
        let out3 = session_create(SessionCreateInput {
            model: "test-model".into(),
            title: Some("巡视巡察整改回头看确认".into()),
            system_prompt: None,
        })
        .await
        .expect("third create with same title should retry to (3)");

        assert_ne!(out1.id, out2.id, "id 必须不同");
        assert_ne!(out2.id, out3.id, "id 必须不同");

        let conn = Connection::open(state_db_path().unwrap()).unwrap();
        let title1: String = conn
            .query_row(
                "SELECT title FROM sessions WHERE id = ?1",
                params![out1.id],
                |row| row.get(0),
            )
            .unwrap();
        let title2: String = conn
            .query_row(
                "SELECT title FROM sessions WHERE id = ?1",
                params![out2.id],
                |row| row.get(0),
            )
            .unwrap();
        let title3: String = conn
            .query_row(
                "SELECT title FROM sessions WHERE id = ?1",
                params![out3.id],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(title1, "巡视巡察整改回头看确认");
        assert_eq!(title2, "巡视巡察整改回头看确认 (2)");
        assert_eq!(title3, "巡视巡察整改回头看确认 (3)");
    }

    /// P3.4.E.8: title=None 时多次插入仍 OK (hermes UNIQUE INDEX `WHERE title IS NOT NULL`
    /// 不约束 NULL, retry 不该影响 None title 场景).
    #[tokio::test]
    async fn session_create_null_title_no_collision() {
        let (_tmp, _g) = setup_test_env();
        let out1 = session_create(SessionCreateInput {
            model: "m".into(),
            title: None,
            system_prompt: None,
        })
        .await
        .expect("first null-title create");
        let out2 = session_create(SessionCreateInput {
            model: "m".into(),
            title: None,
            system_prompt: None,
        })
        .await
        .expect("second null-title create (should not retry, NULL not in UNIQUE)");
        assert_ne!(out1.id, out2.id);
    }

    #[tokio::test]
    async fn session_create_inserts_row() {
        let (_tmp, _g) = setup_test_env();
        let out = session_create(SessionCreateInput {
            model: "test-model".into(),
            title: Some("hi".into()),
            system_prompt: Some("be nice".into()),
        })
        .await
        .expect("create");
        assert!(!out.id.is_empty());
        assert!(out.started_at > 0.0);

        let conn = Connection::open(state_db_path().unwrap()).unwrap();
        let (model, source, title): (String, String, String) = conn
            .query_row(
                "SELECT model, source, title FROM sessions WHERE id = ?1",
                params![out.id],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .unwrap();
        assert_eq!(model, "test-model");
        assert_eq!(source, "companion");
        assert_eq!(title, "hi");
    }

    #[tokio::test]
    async fn session_message_append_increments_counts() {
        let (_tmp, _g) = setup_test_env();
        let s = session_create(SessionCreateInput {
            model: "m".into(),
            title: None,
            system_prompt: None,
        })
        .await
        .unwrap();

        // 普通消息, 不计 tool_call_count
        session_message_append(MessageAppendInput {
            session_id: s.id.clone(),
            role: "user".into(),
            content: "hi".into(),
            tool_calls: None,
            tool_call_id: None,
            tool_name: None,
            token_count: None,
            finish_reason: None,
        })
        .await
        .unwrap();

        // 带 tool_calls 的消息, 计 tool_call_count
        session_message_append(MessageAppendInput {
            session_id: s.id.clone(),
            role: "assistant".into(),
            content: String::new(),
            tool_calls: Some("[{\"name\":\"x\"}]".into()),
            tool_call_id: None,
            tool_name: None,
            token_count: None,
            finish_reason: None,
        })
        .await
        .unwrap();

        let conn = Connection::open(state_db_path().unwrap()).unwrap();
        let (mc, tcc): (i64, i64) = conn
            .query_row(
                "SELECT message_count, tool_call_count FROM sessions WHERE id = ?1",
                params![s.id],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap();
        assert_eq!(mc, 2);
        assert_eq!(tcc, 1);
    }

    #[tokio::test]
    async fn session_finalize_writes_ended_and_tokens() {
        let (_tmp, _g) = setup_test_env();
        let s = session_create(SessionCreateInput {
            model: "m".into(),
            title: None,
            system_prompt: None,
        })
        .await
        .unwrap();

        session_finalize(SessionFinalizeInput {
            session_id: s.id.clone(),
            end_reason: Some("user_close".into()),
            input_tokens: Some(100),
            output_tokens: Some(200),
        })
        .await
        .unwrap();

        let conn = Connection::open(state_db_path().unwrap()).unwrap();
        let (ended, reason, in_tok, out_tok): (Option<f64>, String, i64, i64) = conn
            .query_row(
                "SELECT ended_at, end_reason, input_tokens, output_tokens FROM sessions WHERE id = ?1",
                params![s.id],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
            )
            .unwrap();
        assert!(ended.is_some());
        assert_eq!(reason, "user_close");
        assert_eq!(in_tok, 100);
        assert_eq!(out_tok, 200);
    }

    #[tokio::test]
    async fn session_update_title_and_check() {
        let (_tmp, _g) = setup_test_env();
        let s = session_create(SessionCreateInput {
            model: "m".into(),
            title: None,
            system_prompt: None,
        })
        .await
        .unwrap();

        session_update_title(s.id.clone(), "新标题".into())
            .await
            .unwrap();

        let conn = Connection::open(state_db_path().unwrap()).unwrap();
        let title: String = conn
            .query_row(
                "SELECT title FROM sessions WHERE id = ?1",
                params![s.id],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(title, "新标题");

        // session_check 应该返回 source = companion
        let src = session_check(s.id.clone()).await.unwrap();
        assert_eq!(src, Some("companion".into()));

        // 不存在的 id → None
        let none = session_check("nope".into()).await.unwrap();
        assert_eq!(none, None);
    }

    // ---------- 并发: 模拟 hermes 也在写, busy_timeout 挡得住 ----------

    #[tokio::test]
    async fn concurrent_appends_dont_lose_messages() {
        let (_tmp, _g) = setup_test_env();
        let s = session_create(SessionCreateInput {
            model: "m".into(),
            title: None,
            system_prompt: None,
        })
        .await
        .unwrap();

        // 10 条消息 spawn 出去并发 append
        let id = s.id.clone();
        let mut handles = Vec::new();
        for i in 0..10 {
            let id = id.clone();
            handles.push(tokio::spawn(async move {
                session_message_append(MessageAppendInput {
                    session_id: id,
                    role: "user".into(),
                    content: format!("msg {i}"),
                    tool_calls: None,
                    tool_call_id: None,
                    tool_name: None,
                    token_count: None,
                    finish_reason: None,
                })
                .await
            }));
        }
        for h in handles {
            h.await.unwrap().expect("append succeeded");
        }

        let conn = Connection::open(state_db_path().unwrap()).unwrap();
        let count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?1",
                params![s.id],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(count, 10);

        let mc: i64 = conn
            .query_row(
                "SELECT message_count FROM sessions WHERE id = ?1",
                params![s.id],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(mc, 10);
    }
}
