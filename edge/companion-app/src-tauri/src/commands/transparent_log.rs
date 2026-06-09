//! BL-EMPLOYEE-SELF-SERVE A4 (6/8 鸿波): 数据外发日志 ⭐
//!
//! Spec: docs/EMPLOYEE-SELF-SERVE-TOOLS-SPEC.md §A4
//!
//! # 用例
//!
//! 让员工**自己审计** catfish 客户端发往中央服务的每个 HTTP 请求 — 是 catfish
//! "数据零出端" 承诺 (manifesto 公理 2) 的**可证明 enforcement** 层.
//!
//! # 工作流
//!
//! 1. 前端 fetchWithAuth 调用 (84 caller 唯一 outbound 入口)
//! 2. me.ts 加 middleware wrapper, 调用 Tauri command `transparent_log_record`
//!    把 metadata + payload preview 写本机 SQLite
//! 3. Dashboard 隐私 tab 加 "我的数据外发记录" 卡 / 入口, 调
//!    `transparent_log_query` 拿历史展示
//!
//! # SQLite schema
//!
//! ~/.catfish/outbound_log.db 单表:
//!
//! ```sql
//! CREATE TABLE outbound_log (
//!   id INTEGER PRIMARY KEY AUTOINCREMENT,
//!   ts_request TEXT NOT NULL,            -- ISO 8601
//!   ts_response TEXT,                     -- ISO 8601, null = 没收到 response
//!   method TEXT NOT NULL,                 -- GET/POST/PUT/...
//!   url TEXT NOT NULL,
//!   request_bytes INTEGER NOT NULL,
//!   response_bytes INTEGER NOT NULL,
//!   status INTEGER,                       -- HTTP 状态码, null = 网络错
//!   request_payload_preview TEXT,         -- 截 4KB preview, 全 payload 走 fs
//!   request_payload_path TEXT,            -- > 4KB 时 ~/.catfish/outbound_log_payloads/<id>.bin
//!   response_summary TEXT,                -- 简短 summary (e.g. "OK 1.2KB JSON"), 不存 response body
//!   error TEXT,                           -- 网络错时的 err msg
//!   category TEXT                         -- metering | advisory | identity | chat | other
//! );
//! ```
//!
//! # 跟 manifesto 公理一致
//!
//! - 数据 100% 员工本机 (~/.catfish/outbound_log.db)
//! - 中央**不知道**员工查没查 log (没 endpoint)
//! - 员工可 export log 文件交给 IT 审计, 但**主动**给, 不是中央 push 取
//!
//! # 关键设计
//!
//! - request payload preview (4KB) 入 SQLite, 全 payload 走 fs (~/.catfish/outbound_log_payloads/)
//! - **response body 不存** (隐私 + 大). 只存 status + length + 简短 summary.
//! - 9 天 GC (跟员工 chat history 默认保留期一致)

use std::path::PathBuf;

use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

fn db_path() -> Result<PathBuf, String> {
    let home = home_dir().ok_or("找不到 HOME")?;
    let dir = home.join(".catfish");
    std::fs::create_dir_all(&dir).map_err(|e| format!("创建 ~/.catfish 失败: {e}"))?;
    Ok(dir.join("outbound_log.db"))
}

fn payloads_dir() -> Result<PathBuf, String> {
    let home = home_dir().ok_or("找不到 HOME")?;
    let dir = home.join(".catfish").join("outbound_log_payloads");
    std::fs::create_dir_all(&dir).map_err(|e| format!("创建 payloads dir 失败: {e}"))?;
    Ok(dir)
}

fn open_db() -> Result<Connection, String> {
    let path = db_path()?;
    let conn = Connection::open(&path).map_err(|e| format!("SQLite open: {e}"))?;
    conn.execute_batch(
        "
        CREATE TABLE IF NOT EXISTS outbound_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_request TEXT NOT NULL,
            ts_response TEXT,
            method TEXT NOT NULL,
            url TEXT NOT NULL,
            request_bytes INTEGER NOT NULL DEFAULT 0,
            response_bytes INTEGER NOT NULL DEFAULT 0,
            status INTEGER,
            request_payload_preview TEXT,
            request_payload_path TEXT,
            response_summary TEXT,
            error TEXT,
            category TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_outbound_log_ts ON outbound_log(ts_request DESC);
        CREATE INDEX IF NOT EXISTS ix_outbound_log_category ON outbound_log(category);
        ",
    )
    .map_err(|e| format!("init table: {e}"))?;
    Ok(conn)
}

// preview 字节上限 (写 SQLite). 超过的全文存 fs.
const PREVIEW_BYTES: usize = 4096;
// 9 天 GC (跟 chat history 默认保留一致)
const GC_DAYS: i64 = 9;

// ── 数据模型 ──────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RecordReq {
    pub method: String,
    pub url: String,
    pub request_body: Option<String>,
    pub status: Option<u16>,
    pub response_bytes: Option<u64>,
    pub response_summary: Option<String>,
    pub error: Option<String>,
    pub category: Option<String>,
}

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct LogEntry {
    pub id: i64,
    pub ts_request: String,
    pub ts_response: Option<String>,
    pub method: String,
    pub url: String,
    pub request_bytes: u64,
    pub response_bytes: u64,
    pub status: Option<u16>,
    pub request_payload_preview: Option<String>,
    pub request_payload_full_available: bool,
    pub response_summary: Option<String>,
    pub error: Option<String>,
    pub category: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct QueryResult {
    pub entries: Vec<LogEntry>,
    pub total: u64,
    pub bytes_uploaded_total: u64,
    pub bytes_downloaded_total: u64,
}

// ── 内部 helper ──────────────────────────────────────────────

fn classify_category(url: &str) -> &'static str {
    if url.contains("/v1/metering/") || url.contains("/api/audit/") || url.contains("/api/quota/") {
        "metering"
    } else if url.contains("/advisory/") {
        "advisory"
    } else if url.contains("/v1/chat/") {
        "chat"
    } else if url.contains("/authorize") || url.contains("/token") || url.contains("/userinfo")
        || url.contains("/api/me")
    {
        "identity"
    } else if url.contains("/healthz") || url.contains("/v1/catalog") {
        "health"
    } else {
        "other"
    }
}

// ── 公开 API ───────────────────────────────────────────────────

fn record_blocking(req: RecordReq) -> Result<i64, String> {
    let conn = open_db()?;
    let now = chrono::Utc::now().to_rfc3339();

    // 处理 body — preview 短的进 SQLite, 长的全文存 fs
    let body = req.request_body.unwrap_or_default();
    let body_bytes = body.len() as u64;
    let (preview, fs_path) = if body.len() <= PREVIEW_BYTES {
        (Some(body), None::<String>)
    } else {
        let p = preview_bytes(&body, PREVIEW_BYTES);
        let payloads = payloads_dir()?;
        // path: <id>.bin — 但 id 还没分配, 先用 hash + ts
        let hash = simple_hash(&body);
        let filename = format!("{}-{hash:x}.bin", now.replace(":", "-"));
        let full = payloads.join(&filename);
        std::fs::write(&full, body.as_bytes()).map_err(|e| format!("写 payload fs: {e}"))?;
        (Some(p), Some(full.to_string_lossy().into_owned()))
    };

    let category = req
        .category
        .unwrap_or_else(|| classify_category(&req.url).to_string());

    let response_bytes = req.response_bytes.unwrap_or(0) as i64;
    let ts_response = if req.status.is_some() || req.error.is_some() {
        Some(now.clone())
    } else {
        None
    };

    let mut stmt = conn
        .prepare(
            "INSERT INTO outbound_log
             (ts_request, ts_response, method, url, request_bytes, response_bytes,
              status, request_payload_preview, request_payload_path,
              response_summary, error, category)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
        )
        .map_err(|e| format!("prepare insert: {e}"))?;
    stmt.execute(params![
        now,
        ts_response,
        req.method,
        req.url,
        body_bytes as i64,
        response_bytes,
        req.status.map(|s| s as i64),
        preview,
        fs_path,
        req.response_summary,
        req.error,
        category,
    ])
    .map_err(|e| format!("insert: {e}"))?;

    Ok(conn.last_insert_rowid())
}

fn preview_bytes(s: &str, max: usize) -> String {
    if s.len() <= max {
        return s.to_string();
    }
    // P3.3.5 (6/9): byte slice 必须落在 UTF-8 char boundary, 不然 panic.
    // 中文 prompt 含 3-byte char (e.g. '情' 在 bytes 4095..4098), max=4096
    // 切到字符中间. 往前找最近 char boundary.
    let mut cut = max.min(s.len());
    while cut > 0 && !s.is_char_boundary(cut) {
        cut -= 1;
    }
    format!("{}\n...[truncated, full payload in fs]", &s[..cut])
}

fn simple_hash(s: &str) -> u64 {
    let mut h: u64 = 14695981039346656037;
    for b in s.bytes() {
        h ^= b as u64;
        h = h.wrapping_mul(1099511628211);
    }
    h
}

fn query_blocking(
    since: Option<String>,
    category_filter: Option<String>,
    url_filter: Option<String>,
    limit: u32,
    offset: u32,
) -> Result<QueryResult, String> {
    let conn = open_db()?;

    let limit = limit.clamp(1, 1000);
    let mut where_parts: Vec<String> = vec![];
    let mut params_vec: Vec<rusqlite::types::Value> = vec![];

    if let Some(s) = since {
        where_parts.push("ts_request >= ?".into());
        params_vec.push(s.into());
    }
    if let Some(c) = category_filter {
        where_parts.push("category = ?".into());
        params_vec.push(c.into());
    }
    if let Some(u) = url_filter {
        let pat = if u.contains('%') { u } else { format!("%{u}%") };
        where_parts.push("url LIKE ?".into());
        params_vec.push(pat.into());
    }

    let where_clause = if where_parts.is_empty() {
        String::new()
    } else {
        format!("WHERE {}", where_parts.join(" AND "))
    };

    let sql = format!(
        "SELECT id, ts_request, ts_response, method, url, request_bytes,
                response_bytes, status, request_payload_preview, request_payload_path,
                response_summary, error, category
         FROM outbound_log {where_clause}
         ORDER BY ts_request DESC LIMIT ? OFFSET ?"
    );
    let mut all_params = params_vec.clone();
    all_params.push((limit as i64).into());
    all_params.push((offset as i64).into());

    let mut stmt = conn.prepare(&sql).map_err(|e| format!("prepare query: {e}"))?;
    let rows = stmt
        .query_map(
            rusqlite::params_from_iter(all_params),
            |row| -> rusqlite::Result<LogEntry> {
                let fs_path: Option<String> = row.get(9)?;
                Ok(LogEntry {
                    id: row.get(0)?,
                    ts_request: row.get(1)?,
                    ts_response: row.get(2)?,
                    method: row.get(3)?,
                    url: row.get(4)?,
                    request_bytes: row.get::<_, i64>(5)? as u64,
                    response_bytes: row.get::<_, i64>(6)? as u64,
                    status: row.get::<_, Option<i64>>(7)?.map(|s| s as u16),
                    request_payload_preview: row.get(8)?,
                    request_payload_full_available: fs_path.is_some(),
                    response_summary: row.get(10)?,
                    error: row.get(11)?,
                    category: row.get(12)?,
                })
            },
        )
        .map_err(|e| format!("query: {e}"))?;

    let mut entries: Vec<LogEntry> = vec![];
    for r in rows {
        entries.push(r.map_err(|e| format!("row: {e}"))?);
    }

    // totals: 跟 query filter 一致 (filter 后的 count + 上下行) — 让"共 N 条"
    // 跟 paginate 总数一致, 不然分页器算页数会跟"全库 total" 错位.
    let count_sql = format!(
        "SELECT COUNT(*), COALESCE(SUM(request_bytes), 0), COALESCE(SUM(response_bytes), 0)
         FROM outbound_log {where_clause}"
    );
    let (total, bytes_up, bytes_down): (u64, u64, u64) = conn
        .query_row(
            &count_sql,
            rusqlite::params_from_iter(params_vec.clone()),
            |row| {
                Ok((
                    row.get::<_, i64>(0)? as u64,
                    row.get::<_, i64>(1)? as u64,
                    row.get::<_, i64>(2)? as u64,
                ))
            },
        )
        .unwrap_or((0, 0, 0));

    Ok(QueryResult {
        entries,
        total,
        bytes_uploaded_total: bytes_up,
        bytes_downloaded_total: bytes_down,
    })
}

/// 把整个 outbound_log 表 (按当前 filter 限定) 导出 CSV 到 output_path.
/// 列: id, ts_request, ts_response, method, url, request_bytes, response_bytes,
///   status, category, response_summary, error.
/// payload preview / payload_path 不入 CSV (隐私 + 大). 想看 payload 用 sqlite3.
fn export_csv_blocking(
    output_path: String,
    since: Option<String>,
    category_filter: Option<String>,
    url_filter: Option<String>,
) -> Result<u64, String> {
    let conn = open_db()?;

    let mut where_parts: Vec<String> = vec![];
    let mut params_vec: Vec<rusqlite::types::Value> = vec![];
    if let Some(s) = since {
        where_parts.push("ts_request >= ?".into());
        params_vec.push(s.into());
    }
    if let Some(c) = category_filter {
        where_parts.push("category = ?".into());
        params_vec.push(c.into());
    }
    if let Some(u) = url_filter {
        let pat = if u.contains('%') { u } else { format!("%{u}%") };
        where_parts.push("url LIKE ?".into());
        params_vec.push(pat.into());
    }
    let where_clause = if where_parts.is_empty() {
        String::new()
    } else {
        format!("WHERE {}", where_parts.join(" AND "))
    };

    let sql = format!(
        "SELECT id, ts_request, ts_response, method, url, request_bytes,
                response_bytes, status, category, response_summary, error
         FROM outbound_log {where_clause}
         ORDER BY ts_request DESC"
    );

    let mut stmt = conn.prepare(&sql).map_err(|e| format!("prepare csv: {e}"))?;
    let rows = stmt
        .query_map(
            rusqlite::params_from_iter(params_vec),
            |row| -> rusqlite::Result<Vec<String>> {
                Ok(vec![
                    row.get::<_, i64>(0)?.to_string(),
                    row.get::<_, String>(1)?,
                    row.get::<_, Option<String>>(2)?.unwrap_or_default(),
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, i64>(5)?.to_string(),
                    row.get::<_, i64>(6)?.to_string(),
                    row.get::<_, Option<i64>>(7)?
                        .map(|s| s.to_string())
                        .unwrap_or_default(),
                    row.get::<_, Option<String>>(8)?.unwrap_or_default(),
                    row.get::<_, Option<String>>(9)?.unwrap_or_default(),
                    row.get::<_, Option<String>>(10)?.unwrap_or_default(),
                ])
            },
        )
        .map_err(|e| format!("csv query: {e}"))?;

    // 写 CSV — 不依赖 csv crate, RFC 4180 简实现 ("" escape, 含逗号/引号/换行就裹引号)
    let header = "id,ts_request,ts_response,method,url,request_bytes,response_bytes,status,category,response_summary,error\n";
    let mut buf = String::with_capacity(4096);
    buf.push_str(header);
    let mut count: u64 = 0;
    for r in rows {
        let cells = r.map_err(|e| format!("csv row: {e}"))?;
        for (i, cell) in cells.iter().enumerate() {
            if i > 0 {
                buf.push(',');
            }
            buf.push_str(&csv_quote(cell));
        }
        buf.push('\n');
        count += 1;
    }

    std::fs::write(&output_path, &buf).map_err(|e| format!("写 csv: {e}"))?;
    Ok(count)
}

fn csv_quote(s: &str) -> String {
    if s.contains(',') || s.contains('"') || s.contains('\n') || s.contains('\r') {
        let escaped = s.replace('"', "\"\"");
        format!("\"{escaped}\"")
    } else {
        s.to_string()
    }
}

fn gc_blocking() -> Result<u64, String> {
    let conn = open_db()?;
    let cutoff = chrono::Utc::now() - chrono::Duration::days(GC_DAYS);
    let cutoff_str = cutoff.to_rfc3339();

    // 先拿要删的 fs payload path
    let mut stmt = conn
        .prepare("SELECT request_payload_path FROM outbound_log WHERE ts_request < ? AND request_payload_path IS NOT NULL")
        .map_err(|e| format!("prepare gc select: {e}"))?;
    let paths: Vec<String> = stmt
        .query_map([&cutoff_str], |row| row.get::<_, String>(0))
        .map_err(|e| format!("gc query: {e}"))?
        .filter_map(|r| r.ok())
        .collect();
    drop(stmt);  // 释放 stmt 锁让 execute 能跑

    for p in &paths {
        let _ = std::fs::remove_file(p);
    }

    let deleted = conn
        .execute("DELETE FROM outbound_log WHERE ts_request < ?", [&cutoff_str])
        .map_err(|e| format!("gc delete: {e}"))?;

    Ok(deleted as u64)
}

#[tauri::command]
pub async fn transparent_log_record(req: RecordReq) -> Result<i64, String> {
    tokio::task::spawn_blocking(move || record_blocking(req))
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}

#[tauri::command]
pub async fn transparent_log_query(
    since: Option<String>,
    category: Option<String>,
    url_filter: Option<String>,
    limit: Option<u32>,
    offset: Option<u32>,
) -> Result<QueryResult, String> {
    tokio::task::spawn_blocking(move || {
        query_blocking(
            since,
            category,
            url_filter,
            limit.unwrap_or(100),
            offset.unwrap_or(0),
        )
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

#[tauri::command]
pub async fn transparent_log_export_csv(
    output_path: String,
    since: Option<String>,
    category: Option<String>,
    url_filter: Option<String>,
) -> Result<u64, String> {
    tokio::task::spawn_blocking(move || {
        export_csv_blocking(output_path, since, category, url_filter)
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

#[tauri::command]
pub async fn transparent_log_gc() -> Result<u64, String> {
    tokio::task::spawn_blocking(gc_blocking)
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}
