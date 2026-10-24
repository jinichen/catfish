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
//! # 10/24 改: 只记「带内容出本机」「发给第三方」的, 只记元数据
//!
//! 鸿波: 42 万条几乎全是界面每 3 秒问本机 hermes 的轮询, 真正把对话 / 资料带出去的 (hermes 调模型、
//! 鲶鱼插件、tool-bridge、网页搜索、微信) 一条没记。现在:
//!   · 记录方: hermes 出口 / tool-bridge (egress_log.py, 直接写本表) + Companion 界面 (fetchWithAuth)
//!     + Companion 后台 (record_internal: 远程向量、天气)
//!   · 标准: 发往本机不记; 发往中央只记带内容的 (POST/PUT/PATCH 有 body, 登录 / 健康检查除外);
//!     发往第三方都记
//!   · 多四列 origin (谁发的) / dest_kind (central | third_party) / dest_host / purpose (做什么)
//!   · ~~不再存请求内容~~ → 10/25 又存了, 而且防篡改: 见 transparent_log_store.rs
//!   · 老格式 (没有 origin) 的行一次清掉: 基本都是本机轮询
//!
//! # 跟 manifesto 公理一致
//!
//! - 数据 100% 员工本机 (~/.catfish/outbound_log.db)
//! - 中央**不知道**员工查没查 log (没 endpoint)
//! - 员工可 export log 文件交给 IT 审计, 但**主动**给, 不是中央 push 取
//!
//! # 关键设计
//!
//! - 请求内容 / 指纹链 / 保留期: transparent_log_store.rs (10/25; 老的 4KB preview + payload 文件不用了)
//! - **response body 不存** (隐私 + 大). 只存 status + length + 简短 summary.

use std::path::PathBuf;

use rusqlite::Connection;
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

pub(crate) fn open_db() -> Result<Connection, String> {
    let path = db_path()?;
    let conn = Connection::open(&path).map_err(|e| format!("SQLite open: {e}"))?;
    // hermes / tool-bridge 也写这个库 (接链要排队)
    let _ = conn.busy_timeout(std::time::Duration::from_secs(15));
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
    // 10/24: 新四列 (hermes 那边 egress_log.py 也会加, 谁先到谁加)
    let have: Vec<String> = conn
        .prepare("PRAGMA table_info(outbound_log)")
        .and_then(|mut st| st.query_map([], |r| r.get::<_, String>(1))?.collect())
        .map_err(|e| format!("table_info: {e}"))?;
    for col in ["origin", "dest_kind", "dest_host", "purpose"] {
        if !have.iter().any(|c| c == col) {
            let _ = conn.execute(&format!("ALTER TABLE outbound_log ADD COLUMN {col} TEXT"), []);
        }
    }
    // 老格式的行 (origin 为空) 基本都是本机轮询 (界面每 3 秒问 hermes), 不是外发 —— 清掉;
    // 老的请求内容文件 (~/.catfish/outbound_log_payloads/) 也不用了
    let _ = conn.execute("DELETE FROM outbound_log WHERE origin IS NULL", []);
    if let Some(home) = home_dir() {
        let old = home.join(".catfish").join("outbound_log_payloads");
        if old.exists() {
            let _ = std::fs::remove_dir_all(&old);
        }
    }
    super::transparent_log_store::ensure(&conn)?;
    Ok(conn)
}

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
    /// 10/24: 谁发的 (companion / companion-bg)、发给中央还是第三方、对方主机、做什么
    pub origin: Option<String>,
    pub dest_kind: Option<String>,
    pub dest_host: Option<String>,
    pub purpose: Option<String>,
    /// 请求体字节数 (request_body 是表单 / 文件说明时, 这里是原始大小)
    pub request_bytes: Option<u64>,
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
    pub origin: Option<String>,
    pub dest_kind: Option<String>,
    pub dest_host: Option<String>,
    pub purpose: Option<String>,
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
    let mut conn = open_db()?;
    let now = chrono::Utc::now().to_rfc3339();
    let body_bytes = req
        .request_bytes
        .unwrap_or_else(|| req.request_body.as_ref().map(|b| b.len() as u64).unwrap_or(0));
    let category = req
        .category
        .unwrap_or_else(|| classify_category(&req.url).to_string());
    let ts_response = if req.status.is_some() || req.error.is_some() {
        Some(now.clone())
    } else {
        None
    };
    let row = super::transparent_log_store::ChainRow {
        ts_request: now,
        ts_response,
        method: req.method,
        url: req.url,
        request_bytes: body_bytes as i64,
        response_bytes: req.response_bytes.unwrap_or(0) as i64,
        status: req.status.map(|s| s as i64),
        error: req.error,
        origin: Some(req.origin.unwrap_or_else(|| "companion".into())),
        dest_kind: req.dest_kind,
        dest_host: req.dest_host,
        purpose: req.purpose,
        body_sha256: String::new(),
    };
    super::transparent_log_store::insert(
        &mut conn,
        row,
        req.request_body.as_deref().map(str::as_bytes),
        req.response_summary,
        Some(category),
    )
}

/// 10/24: Companion 后台自己发出去的 (远程向量、天气) 也记一笔, 连内容; 失败不影响调用方。
pub(crate) fn record_internal(method: &str, url: &str, body: Option<String>, status: Option<u16>,
                              dest_kind: &str, purpose: &str) {
    let host = url.split("://").nth(1).and_then(|r| r.split(['/', ':', '?']).next()).unwrap_or("").to_string();
    let req = RecordReq {
        method: method.to_string(),
        url: url.split('?').next().unwrap_or(url).to_string(),
        request_bytes: body.as_ref().map(|b| b.len() as u64),
        request_body: body,
        status,
        response_bytes: None,
        response_summary: None,
        error: None,
        category: Some(if dest_kind == "third_party" { "third_party" } else { "content" }.to_string()),
        origin: Some("companion-bg".into()),
        dest_kind: Some(dest_kind.to_string()),
        dest_host: Some(host),
        purpose: Some(purpose.to_string()),
    };
    std::thread::spawn(move || {
        if let Err(e) = record_blocking(req) {
            log::debug!("[transparent_log] 后台记录失败: {e}");
        }
    });
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
                response_summary, error, category, origin, dest_kind, dest_host, purpose
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
                    origin: row.get(13)?,
                    dest_kind: row.get(14)?,
                    dest_host: row.get(15)?,
                    purpose: row.get(16)?,
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
                response_bytes, status, category, response_summary, error,
                origin, dest_kind, dest_host, purpose
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
                    row.get::<_, Option<String>>(11)?.unwrap_or_default(),
                    row.get::<_, Option<String>>(12)?.unwrap_or_default(),
                    row.get::<_, Option<String>>(13)?.unwrap_or_default(),
                    row.get::<_, Option<String>>(14)?.unwrap_or_default(),
                ])
            },
        )
        .map_err(|e| format!("csv query: {e}"))?;

    // 写 CSV — 不依赖 csv crate, RFC 4180 简实现 ("" escape, 含逗号/引号/换行就裹引号)
    let header = "id,ts_request,ts_response,method,url,request_bytes,response_bytes,status,category,response_summary,error,origin,dest_kind,dest_host,purpose\n";
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

/// 10/24: 页面上「发给谁、为什么」的汇总 —— 按 (dest_kind, purpose, dest_host) 分组。
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SummaryRow {
    pub dest_kind: String,
    pub purpose: String,
    pub dest_host: String,
    pub count: u64,
    pub bytes_up: u64,
    pub bytes_down: u64,
    pub last_ts: String,
}

fn summary_blocking(since: Option<String>) -> Result<Vec<SummaryRow>, String> {
    let conn = open_db()?;
    let mut stmt = conn
        .prepare(
            "SELECT COALESCE(dest_kind, ''), COALESCE(purpose, ''), COALESCE(dest_host, ''),
                    COUNT(*), COALESCE(SUM(request_bytes), 0), COALESCE(SUM(response_bytes), 0), MAX(ts_request)
             FROM outbound_log WHERE (?1 IS NULL OR ts_request >= ?1)
             GROUP BY 1, 2, 3 ORDER BY 1 DESC, 4 DESC",
        )
        .map_err(|e| format!("prepare summary: {e}"))?;
    let rows = stmt
        .query_map([since], |r| {
            Ok(SummaryRow {
                dest_kind: r.get(0)?,
                purpose: r.get(1)?,
                dest_host: r.get(2)?,
                count: r.get::<_, i64>(3)? as u64,
                bytes_up: r.get::<_, i64>(4)? as u64,
                bytes_down: r.get::<_, i64>(5)? as u64,
                last_ts: r.get::<_, Option<String>>(6)?.unwrap_or_default(),
            })
        })
        .map_err(|e| format!("summary: {e}"))?;
    rows.collect::<Result<Vec<_>, _>>().map_err(|e| format!("summary row: {e}"))
}

#[tauri::command]
pub async fn transparent_log_summary(since: Option<String>) -> Result<Vec<SummaryRow>, String> {
    tokio::task::spawn_blocking(move || summary_blocking(since))
        .await
        .map_err(|e| format!("内部错误: {e}"))?
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
    tokio::task::spawn_blocking(super::transparent_log_store::gc_blocking)
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 新列_存内容_老格式清掉_汇总() {
        let _g = crate::util::test_env::env_lock();
        let prev = std::env::var_os("HOME");
        let dir = tempfile::tempdir().unwrap();
        std::env::set_var("HOME", dir.path());
        // 老格式库: 没有 origin 列, 一条本机轮询
        std::fs::create_dir_all(dir.path().join(".catfish")).unwrap();
        let old = Connection::open(dir.path().join(".catfish/outbound_log.db")).unwrap();
        old.execute_batch(
            "CREATE TABLE outbound_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts_request TEXT NOT NULL,
             ts_response TEXT, method TEXT NOT NULL, url TEXT NOT NULL, request_bytes INTEGER NOT NULL DEFAULT 0,
             response_bytes INTEGER NOT NULL DEFAULT 0, status INTEGER, request_payload_preview TEXT,
             request_payload_path TEXT, response_summary TEXT, error TEXT, category TEXT);
             INSERT INTO outbound_log (ts_request, method, url) VALUES ('2026-10-24T00:00:00Z', 'GET',
               'http://localhost:8642/api/catfish/room-link/pending');",
        )
        .unwrap();
        drop(old);

        record_blocking(RecordReq {
            method: "POST".into(),
            url: "http://10.0.0.5:8999/api/proactive/contextual".into(),
            request_body: Some("员工的上下文".into()),
            status: Some(200),
            response_bytes: Some(10),
            response_summary: None,
            error: None,
            category: Some("content".into()),
            origin: None,
            dest_kind: Some("central".into()),
            dest_host: Some("10.0.0.5".into()),
            purpose: Some("主动提醒".into()),
            request_bytes: None,
        })
        .unwrap();
        let q = query_blocking(None, None, None, 100, 0).unwrap();
        assert_eq!(q.entries.len(), 1, "老格式的本机轮询被清掉");
        let e = &q.entries[0];
        assert_eq!(e.origin.as_deref(), Some("companion"));
        assert_eq!(e.purpose.as_deref(), Some("主动提醒"));
        assert!(e.request_payload_preview.is_none(), "内容不在老的 preview 列");
        assert_eq!(e.request_bytes, "员工的上下文".len() as u64);
        let p = crate::commands::transparent_log_store::payload_blocking(e.id).unwrap();
        assert_eq!(p.text.as_deref(), Some("员工的上下文"));
        assert!(p.verified);
        let sum = summary_blocking(None).unwrap();
        assert_eq!(sum.len(), 1);
        assert_eq!((sum[0].dest_kind.as_str(), sum[0].purpose.as_str(), sum[0].count), ("central", "主动提醒", 1));
        match prev {
            Some(h) => std::env::set_var("HOME", h),
            None => std::env::remove_var("HOME"),
        }
    }
}
