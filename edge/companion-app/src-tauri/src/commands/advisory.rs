//! Advisory local state — 6/7 BL-MANIFESTO-ADVISORY-PHASE1.
//!
//! Spec: docs/ADVISORY-FEED-SPEC.md
//!
//! # 设计
//!
//! 前端走 fetchWithAuth 直接调 gateway `/advisory/feed.json` 拿 feed, 本机
//! installed_skills 匹配也在前端 (用 fetchInstalledSkills + catfish 版本).
//!
//! 这个 module 只管**员工本机 advisory state** — 谁 ack 了 / dismiss 了 /
//! snooze 到几点. 数据留员工本机 SQLite, 中央不知道.
//!
//! 跟 catfish-central-manifesto 公理 2/4 一致:
//!   - 公理 2: state 留本机, 不上报 (除非员工自愿调 advisory_ack endpoint, Phase 2)
//!   - 公理 4: 这是员工自助管理状态, 没远程触发能力
//!
//! # SQLite schema
//!
//! `~/.catfish/advisory_state.db` 单表:
//!
//! ```sql
//! CREATE TABLE IF NOT EXISTS advisory_local_state (
//!   advisory_id TEXT PRIMARY KEY,
//!   status TEXT NOT NULL,           -- unseen | seen | snoozed | acked | dismissed
//!   last_shown TEXT,                -- ISO 8601, NULL 表示从未显
//!   snooze_until TEXT,              -- ISO 8601, status=snoozed 时有效
//!   acked_at TEXT,                  -- ISO 8601, status=acked|dismissed 时记录
//!   upload_consent INTEGER DEFAULT 0  -- 是否员工同意上报 ack 到中央 (Phase 2)
//! );
//! ```
//!
//! # Phase 1 vs Phase 2 边界
//!
//! Phase 1 (现在): 本机 state only, 中央不知道处理状态. 员工有完全主权.
//! Phase 2 (BL): 加 `advisory_ack_upload` Tauri command — 员工**主动**点
//!   "把我的处理状态汇报给公司", 才走 POST /v1/advisory/ack. Phase 1 不做.

use std::path::PathBuf;

use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

fn advisory_db_path() -> Result<PathBuf, String> {
    let home = home_dir().ok_or("找不到 HOME")?;
    let dir = home.join(".catfish");
    std::fs::create_dir_all(&dir).map_err(|e| format!("创建 ~/.catfish 失败: {e}"))?;
    Ok(dir.join("advisory_state.db"))
}

fn open_db() -> Result<Connection, String> {
    let path = advisory_db_path()?;
    let conn = Connection::open(&path).map_err(|e| format!("SQLite open 失败: {e}"))?;
    conn.execute(
        "CREATE TABLE IF NOT EXISTS advisory_local_state (
            advisory_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            last_shown TEXT,
            snooze_until TEXT,
            acked_at TEXT,
            upload_consent INTEGER DEFAULT 0
        )",
        [],
    )
    .map_err(|e| format!("创建 table 失败: {e}"))?;
    Ok(conn)
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AdvisoryLocalState {
    pub advisory_id: String,
    /// `unseen` | `seen` | `snoozed` | `acked` | `dismissed`
    pub status: String,
    pub last_shown: Option<String>,
    pub snooze_until: Option<String>,
    pub acked_at: Option<String>,
    pub upload_consent: bool,
}

fn row_to_state(row: &rusqlite::Row<'_>) -> rusqlite::Result<AdvisoryLocalState> {
    Ok(AdvisoryLocalState {
        advisory_id: row.get(0)?,
        status: row.get(1)?,
        last_shown: row.get(2)?,
        snooze_until: row.get(3)?,
        acked_at: row.get(4)?,
        upload_consent: row.get::<_, i64>(5)? != 0,
    })
}

fn list_local_states_blocking() -> Result<Vec<AdvisoryLocalState>, String> {
    let conn = open_db()?;
    let mut stmt = conn
        .prepare(
            "SELECT advisory_id, status, last_shown, snooze_until, acked_at, upload_consent \
             FROM advisory_local_state",
        )
        .map_err(|e| format!("prepare: {e}"))?;
    let rows = stmt
        .query_map([], row_to_state)
        .map_err(|e| format!("query: {e}"))?;
    let mut out = Vec::new();
    for r in rows {
        out.push(r.map_err(|e| format!("row: {e}"))?);
    }
    Ok(out)
}

fn get_local_state_blocking(advisory_id: String) -> Result<Option<AdvisoryLocalState>, String> {
    let conn = open_db()?;
    let mut stmt = conn
        .prepare(
            "SELECT advisory_id, status, last_shown, snooze_until, acked_at, upload_consent \
             FROM advisory_local_state WHERE advisory_id = ?1",
        )
        .map_err(|e| format!("prepare: {e}"))?;
    let mut rows = stmt
        .query(params![advisory_id])
        .map_err(|e| format!("query: {e}"))?;
    if let Some(row) = rows.next().map_err(|e| format!("next: {e}"))? {
        Ok(Some(row_to_state(row).map_err(|e| format!("row: {e}"))?))
    } else {
        Ok(None)
    }
}

/// upsert (advisory_id, status, ...) — caller 自己保证 status 字符串合法.
fn upsert_local_state_blocking(state: AdvisoryLocalState) -> Result<(), String> {
    let conn = open_db()?;
    conn.execute(
        "INSERT INTO advisory_local_state \
         (advisory_id, status, last_shown, snooze_until, acked_at, upload_consent) \
         VALUES (?1, ?2, ?3, ?4, ?5, ?6) \
         ON CONFLICT(advisory_id) DO UPDATE SET \
           status = excluded.status, \
           last_shown = COALESCE(excluded.last_shown, last_shown), \
           snooze_until = excluded.snooze_until, \
           acked_at = COALESCE(excluded.acked_at, acked_at), \
           upload_consent = excluded.upload_consent",
        params![
            state.advisory_id,
            state.status,
            state.last_shown,
            state.snooze_until,
            state.acked_at,
            state.upload_consent as i64,
        ],
    )
    .map_err(|e| format!("upsert: {e}"))?;
    Ok(())
}

#[tauri::command]
pub async fn advisory_list_local_states() -> Result<Vec<AdvisoryLocalState>, String> {
    tokio::task::spawn_blocking(list_local_states_blocking)
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}

#[tauri::command]
pub async fn advisory_get_local_state(
    advisory_id: String,
) -> Result<Option<AdvisoryLocalState>, String> {
    tokio::task::spawn_blocking(move || get_local_state_blocking(advisory_id))
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}

/// 标记 advisory 已显示给员工 (banner pop 时调). 不改 status.
#[tauri::command]
pub async fn advisory_mark_shown(advisory_id: String) -> Result<(), String> {
    let now = chrono::Utc::now().to_rfc3339();
    tokio::task::spawn_blocking(move || -> Result<(), String> {
        let existing = get_local_state_blocking(advisory_id.clone())?;
        let state = AdvisoryLocalState {
            advisory_id,
            status: existing.as_ref().map(|s| s.status.clone()).unwrap_or_else(|| "seen".into()),
            last_shown: Some(now),
            snooze_until: existing.as_ref().and_then(|s| s.snooze_until.clone()),
            acked_at: existing.as_ref().and_then(|s| s.acked_at.clone()),
            upload_consent: existing.map(|s| s.upload_consent).unwrap_or(false),
        };
        upsert_local_state_blocking(state)
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

/// 员工点 [我已了解] — advisory 标 acked.
#[tauri::command]
pub async fn advisory_ack(advisory_id: String) -> Result<(), String> {
    let now = chrono::Utc::now().to_rfc3339();
    tokio::task::spawn_blocking(move || -> Result<(), String> {
        upsert_local_state_blocking(AdvisoryLocalState {
            advisory_id,
            status: "acked".into(),
            last_shown: None, // 保留已有的
            snooze_until: None,
            acked_at: Some(now),
            upload_consent: false, // Phase 2 接员工选择上报
        })
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

/// 员工点 [稍后提醒] — advisory 标 snoozed 到 snooze_until.
#[tauri::command]
pub async fn advisory_snooze(advisory_id: String, hours: u32) -> Result<(), String> {
    let snooze_until = chrono::Utc::now() + chrono::Duration::hours(hours as i64);
    let snooze_str = snooze_until.to_rfc3339();
    tokio::task::spawn_blocking(move || -> Result<(), String> {
        upsert_local_state_blocking(AdvisoryLocalState {
            advisory_id,
            status: "snoozed".into(),
            last_shown: None,
            snooze_until: Some(snooze_str),
            acked_at: None,
            upload_consent: false,
        })
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

/// 员工点 [忽略] — advisory 标 dismissed. critical advisory 24h 后会重新弹.
#[tauri::command]
pub async fn advisory_dismiss(advisory_id: String) -> Result<(), String> {
    let now = chrono::Utc::now().to_rfc3339();
    tokio::task::spawn_blocking(move || -> Result<(), String> {
        upsert_local_state_blocking(AdvisoryLocalState {
            advisory_id,
            status: "dismissed".into(),
            last_shown: None,
            snooze_until: None,
            acked_at: Some(now),
            upload_consent: false,
        })
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}
