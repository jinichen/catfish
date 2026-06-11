//! task_chat jsonl → hermes state.db 一次性 migration (P3.3.19 C Phase 4, 6/11).
//!
//! 把 P3.3.7-11 累积的 ~/.catfish/task_chat/<taskUid>.jsonl 一次性 import 进
//! ~/.hermes/state.db 的 sessions + messages + catfish_session_metadata 三表.
//!
//! 启动时 fire-and-forget 跑 (lib.rs setup 调). flag 文件 ~/.catfish/migration_v1_done
//! 存在则跳过. 已存在 task_uid 关联 session 也跳过 (防重复 import).
//!
//! 量级估: 8 file / 40KB / 200 msg → 200 INSERT × WAL 模式 3000/s → < 300ms.
//! 即使 100 file / 几千 msg 也 < 5s. 后台跑, UI 不阻塞.
//!
//! 单测覆盖: TODO Phase 4 完成后跑实机. Rust 单测要 mock state.db, 暂留.

use std::fs;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

fn task_chat_dir() -> Option<PathBuf> {
    Some(home_dir()?.join(".catfish").join("task_chat"))
}

fn flag_path() -> Option<PathBuf> {
    Some(home_dir()?.join(".catfish").join("migration_v1_done"))
}

fn state_db_path() -> Option<PathBuf> {
    let path = home_dir()?.join(".hermes").join("state.db");
    if !path.exists() {
        return None;
    }
    Some(path)
}

#[derive(Debug, Deserialize)]
struct JsonlMsg {
    role: String,
    content: String,
    #[serde(default)]
    ts: String,
    #[serde(rename = "toolCalls")]
    tool_calls: Option<serde_json::Value>,
    #[serde(rename = "toolCallId")]
    tool_call_id: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MigrationReport {
    pub already_done: bool,
    pub jsonl_files_scanned: usize,
    pub sessions_created: usize,
    pub messages_imported: usize,
    pub skipped_already_exists: usize,
    pub failed_files: Vec<String>,
    pub elapsed_ms: u64,
}

/// 跑 migration. 已 done 直接返 already_done=true 不动 state.db.
/// 失败 file 不阻塞其他 — log + 累加 failed_files.
pub fn run_migration() -> Result<MigrationReport, String> {
    let start = SystemTime::now();
    let flag = flag_path().ok_or("HOME 拿不到")?;
    if flag.exists() {
        return Ok(MigrationReport {
            already_done: true,
            jsonl_files_scanned: 0,
            sessions_created: 0,
            messages_imported: 0,
            skipped_already_exists: 0,
            failed_files: Vec::new(),
            elapsed_ms: 0,
        });
    }

    let task_dir = task_chat_dir().ok_or("HOME 拿不到")?;
    if !task_dir.exists() {
        // 没 jsonl 也算"已迁完", 写 flag
        let _ = fs::write(&flag, b"no-task-chat-found\n");
        return Ok(MigrationReport {
            already_done: false,
            jsonl_files_scanned: 0,
            sessions_created: 0,
            messages_imported: 0,
            skipped_already_exists: 0,
            failed_files: Vec::new(),
            elapsed_ms: 0,
        });
    }

    let db_path = state_db_path().ok_or("state.db 不存在 (hermes 先跑一次)")?;
    let mut conn = Connection::open(&db_path)
        .map_err(|e| format!("打开 state.db 失败: {e}"))?;
    conn.busy_timeout(std::time::Duration::from_millis(5000))
        .map_err(|e| format!("设 busy_timeout 失败: {e}"))?;

    // 跑前 ensure sidecar 表存在 (跟 session_write.rs 同款)
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
    .map_err(|e| format!("建 sidecar 表失败: {e}"))?;

    let mut scanned = 0usize;
    let mut sessions_created = 0usize;
    let mut messages_imported = 0usize;
    let mut skipped = 0usize;
    let mut failed_files: Vec<String> = Vec::new();

    let dir_entries = fs::read_dir(&task_dir)
        .map_err(|e| format!("read_dir {task_dir:?} 失败: {e}"))?;

    for entry in dir_entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|s| s.to_str()) != Some("jsonl") {
            continue;
        }
        scanned += 1;

        let task_uid = match path.file_stem().and_then(|s| s.to_str()) {
            Some(s) => s.to_string(),
            None => {
                failed_files.push(format!("{path:?} (无 stem)"));
                continue;
            }
        };

        // 检查这 task_uid 是否已有 session — 有就跳 (防重复)
        let existing: Option<String> = conn
            .query_row(
                "SELECT session_id FROM catfish_session_metadata WHERE task_uid = ?1 LIMIT 1",
                params![task_uid],
                |row| row.get(0),
            )
            .ok();
        if existing.is_some() {
            skipped += 1;
            continue;
        }

        // 读 jsonl
        let content = match fs::read_to_string(&path) {
            Ok(c) => c,
            Err(e) => {
                failed_files.push(format!("{path:?} read 失败: {e}"));
                continue;
            }
        };

        let mut msgs: Vec<JsonlMsg> = Vec::new();
        for line in content.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            match serde_json::from_str::<JsonlMsg>(line) {
                Ok(m) => msgs.push(m),
                Err(_) => {
                    // 单行坏不阻塞整 file
                }
            }
        }

        if msgs.is_empty() {
            // 空文件不建 session
            continue;
        }

        // 在事务里建 session + insert messages + sidecar 关联
        let tx = match conn.transaction() {
            Ok(t) => t,
            Err(e) => {
                failed_files.push(format!("{path:?} transaction 失败: {e}"));
                continue;
            }
        };

        // session_id 用 hermes 兼容格式 (跟 session_write.rs:generate_session_id 同款)
        // 但加 "_migrated" 后缀让员工肉眼能区分
        let now_secs = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        // 简单时间戳, 不用 chrono (跟 session_write.rs Local::now 不一样, 但 ID 唯一性足够)
        let session_id = format!("migrated_{now_secs}_{task_uid}");
        let started_at = now_secs as f64;
        let title = format!("[迁移] task {task_uid}");

        // 算从最早 msg ts 取 started_at (尽量接近原对话时间)
        // f64 没 Ord, 用 fold 加 f64::min 替代 .min()
        let earliest = msgs
            .iter()
            .filter_map(|m| parse_iso_to_unix(&m.ts))
            .fold(f64::INFINITY, f64::min);
        let earliest = if earliest.is_finite() { earliest } else { started_at };

        let r = tx.execute(
            r#"
            INSERT INTO sessions (
                id, source, model, model_config, system_prompt,
                started_at, message_count, tool_call_count,
                input_tokens, output_tokens, cache_read_tokens,
                cache_write_tokens, reasoning_tokens, title
            ) VALUES (
                ?1, 'companion-migrated', 'unknown', '{}', NULL,
                ?2, 0, 0,
                0, 0, 0,
                0, 0, ?3
            )
        "#,
            params![session_id, earliest, title],
        );
        if let Err(e) = r {
            failed_files.push(format!("{path:?} INSERT session 失败: {e}"));
            let _ = tx.rollback();
            continue;
        }

        let mut local_msg_count = 0i64;
        let mut tool_count = 0i64;
        let mut file_failed = false;

        for m in &msgs {
            let ts = parse_iso_to_unix(&m.ts).unwrap_or(earliest);
            // tool_calls JSON 字符串化 (sessions.messages.tool_calls 字段是 TEXT)
            let tool_calls_str: Option<String> = m.tool_calls.as_ref().map(|v| v.to_string());

            let r = tx.execute(
                r#"
                INSERT INTO messages (
                    session_id, role, content, tool_calls, tool_call_id, timestamp
                ) VALUES (?1, ?2, ?3, ?4, ?5, ?6)
            "#,
                params![
                    session_id,
                    m.role,
                    m.content,
                    tool_calls_str,
                    m.tool_call_id,
                    ts,
                ],
            );
            if let Err(e) = r {
                failed_files.push(format!("{path:?} INSERT message 失败: {e}"));
                file_failed = true;
                break;
            }
            local_msg_count += 1;
            if m.role == "tool" {
                tool_count += 1;
            }
        }

        if file_failed {
            let _ = tx.rollback();
            continue;
        }

        // 更新 session message_count + tool_call_count
        let r = tx.execute(
            "UPDATE sessions SET message_count = ?1, tool_call_count = ?2 WHERE id = ?3",
            params![local_msg_count, tool_count, session_id],
        );
        if let Err(e) = r {
            failed_files.push(format!("{path:?} UPDATE counts 失败: {e}"));
            let _ = tx.rollback();
            continue;
        }

        // 写 sidecar 关联
        let r = tx.execute(
            r#"
            INSERT INTO catfish_session_metadata (session_id, task_uid, created_at, updated_at)
            VALUES (?1, ?2, ?3, ?3)
        "#,
            params![session_id, task_uid, started_at],
        );
        if let Err(e) = r {
            failed_files.push(format!("{path:?} INSERT sidecar 失败: {e}"));
            let _ = tx.rollback();
            continue;
        }

        if let Err(e) = tx.commit() {
            failed_files.push(format!("{path:?} commit 失败: {e}"));
            continue;
        }

        sessions_created += 1;
        messages_imported += local_msg_count as usize;
    }

    // 写 flag (即使有失败 file 也写, 避免下次启动重试无限循环;
    //  failed_files 记录给员工自查)
    let flag_content = format!(
        "migration_v1_done\nscanned={scanned}\nsessions={sessions_created}\nmessages={messages_imported}\nskipped={skipped}\nfailed={}\n",
        failed_files.len()
    );
    let _ = fs::write(&flag, flag_content);

    let elapsed_ms = SystemTime::now()
        .duration_since(start)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0);

    Ok(MigrationReport {
        already_done: false,
        jsonl_files_scanned: scanned,
        sessions_created,
        messages_imported,
        skipped_already_exists: skipped,
        failed_files,
        elapsed_ms,
    })
}

/// "2026-06-10T12:34:56.789Z" → unix 秒 (浮点). 解失败返 None.
fn parse_iso_to_unix(ts: &str) -> Option<f64> {
    if ts.is_empty() {
        return None;
    }
    // 用 chrono 解 (依赖已有, session_write.rs 用)
    chrono::DateTime::parse_from_rfc3339(ts)
        .ok()
        .map(|dt| dt.timestamp() as f64 + (dt.timestamp_subsec_millis() as f64 / 1000.0))
}

#[tauri::command]
pub async fn task_chat_migrate_to_state_db() -> Result<MigrationReport, String> {
    tokio::task::spawn_blocking(run_migration)
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}
