//! BL-PROACTIVE-DECOUPLE (5/26): 给 /api/proactive/* 端点准备 header 透传数据.
//!
//! # 背景
//!
//! 5/26 audit 砍 gateway 自己读员工本机 fs (违反 "中央 0 字节" 边界). gateway
//! `/api/proactive/starter` + `/api/proactive/contextual` 改成接 header:
//!   - `X-Catfish-Journal-Tail-B64`: base64(UTF-8 of ~/.catfish/employee_journal.md 末尾 ~8KB)
//!   - `X-Catfish-Last-Model`:        员工最近 session 用的 model name
//!
//! 数据本来就在员工 mac 上, Companion (跑在员工 mac) 读自己的文件天然合规.
//! Companion 没传这俩 header → gateway 自动 fallback 模板 (主动闲聊体验退化).
//!
//! # 设计
//!
//! 一个原子命令 `proactive_context()` 同时返 journal_tail + last_model.
//! 前端调一次拿俩字段, 减少 invoke 次数 (主动闲聊触发可能很频繁).
//!
//! # 与现有命令的关系
//!
//! - `journal_read_recent` (5KB, BL-JOURNAL-TODO-EXTRACT 5/20): 给 LLM 抽 TODO 用,
//!   场景独立, 不复用 (TODO 提取偏好 5KB 控 context, proactive 想要 8KB 偏好上下文).
//! - `identity_info.active_session_model` (BL-IDENTITY 5/15): 只看 `ended_at IS NULL`
//!   的活跃 session. proactive 要的是 "最近一次 session 用了啥 model" — 不管活
//!   不活跃, 所以这里走独立 query (drop ended_at filter).

use serde::Serialize;
use std::fs;
use std::path::PathBuf;

/// proactive_context() 返回结构.
#[derive(Debug, Serialize)]
pub struct ProactiveContext {
    /// 员工本机 employee_journal.md 末尾 ~8KB 内容 (UTF-8 char-boundary 切, 不切坏中文).
    /// 文件不存在 / 空 → ""
    pub journal_tail: String,

    /// 员工最近 session 用的 model name (无论 session 现在是否活跃).
    /// state.db 不存在 / 表空 / model 字段 NULL → null.
    pub last_model: Option<String>,
}

const TAIL_BYTES: usize = 8000;

fn journal_path() -> Option<PathBuf> {
    let home = crate::util::paths::home_env().ok()?;
    Some(PathBuf::from(home).join(".catfish/employee_journal.md"))
}

fn hermes_state_db() -> Option<PathBuf> {
    crate::services::catfish_paths::hermes_state_db_path()
}

/// 读 journal 末尾 ~8KB. 找最近 utf-8 char 边界 + 再往前找最近换行,
/// 不切坏中文也不从段中间切.
fn read_journal_tail() -> String {
    let Some(path) = journal_path() else {
        return String::new();
    };
    if !path.exists() {
        return String::new();
    }
    let Ok(text) = fs::read_to_string(&path) else {
        return String::new();
    };
    if text.len() <= TAIL_BYTES {
        return text;
    }
    let start = text.len() - TAIL_BYTES;
    let mut safe_start = start;
    while safe_start < text.len() && !text.is_char_boundary(safe_start) {
        safe_start += 1;
    }
    let tail = &text[safe_start..];
    // 找最近换行, 不从段中间切 (员工看上下文不连贯, LLM 也容易误解)
    if let Some(nl) = tail.find('\n') {
        tail[nl + 1..].to_string()
    } else {
        tail.to_string()
    }
}

/// 读 hermes state.db 最近一条 session 的 model 字段.
/// 跟 identity::read_active_session 区别: 不限 `ended_at IS NULL`,
/// 主动闲聊要看的是 "员工最近用啥", 而不是 "现在有没活跃 session".
fn read_last_session_model() -> Option<String> {
    let db_path = hermes_state_db()?;
    if !db_path.exists() {
        return None;
    }
    let conn = rusqlite::Connection::open(&db_path).ok()?;
    let row: rusqlite::Result<Option<String>> = conn.query_row(
        "SELECT model FROM sessions
         WHERE model IS NOT NULL AND model != ''
         ORDER BY started_at DESC
         LIMIT 1",
        [],
        |row| row.get(0),
    );
    row.ok().flatten()
}

/// 给 `/api/proactive/*` 调用前准备 header 用. 一次返 journal_tail + last_model.
///
/// **副作用 0** — 只读, 不写. journal / state.db 都开只读模式, 不影响 hermes 进程.
#[tauri::command]
pub async fn proactive_context() -> Result<ProactiveContext, String> {
    tokio::task::spawn_blocking(|| {
        Ok::<ProactiveContext, String>(ProactiveContext {
            journal_tail: read_journal_tail(),
            last_model: read_last_session_model(),
        })
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}
