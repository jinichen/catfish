//! 数据外发记录: 存请求内容 + 防篡改指纹链 + 本机保留期 (10/25 鸿波)
//!
//! # 为什么
//!
//! 10/24 版只记元数据 (发给谁、做什么、多大)。鸿波: 有争议时要说清「到底发了什么」,
//! 不存内容就说不清; 只存内容又会被说「你事后改过」—— 所以内容 + 指纹链一起做。
//!
//! # 跟 hermes 那边是同一份格式
//!
//! 写这张表的有三方: hermes / tool-bridge 的出口钩子 (egress_log.py), Companion 界面 (me.ts →
//! transparent_log_record), Companion 后台 (record_internal)。三方必须一字不差:
//!
//! - 内容 `stored_body`: JSON 去掉密钥类字段、遮住用户标识 (`redact`, 10/25 第二次加了会话票据和
//!   user_id 类字段; 旧记录由 transparent_log_redact 统一脱敏重算) → messages 里超 1KB 的单条消息、
//!   tools 换成 `{"$blob": 哈希}` 单独存 outbound_blob (模型调用每次带整段历史, 不去重一天几百 MB)
//!   → 排序键紧凑序列化; 文本原样; 二进制只记大小 + sha256。zlib 压缩存 outbound_payload。
//! - 指纹 `row_hash` = sha256(上一行 row_hash + "\n" + 13 个字段用 \x1f 连), 首行接 "GENESIS",
//!   被清理过就接 outbound_meta.gc_last_hash。测试向量两边钉同一个值。
//! - 接链在 `BEGIN IMMEDIATE` 里做, 多进程串行。
//!
//! # 证明没被改过
//!
//! - 本机: `verify` 从头走一遍链 + 每条内容的哈希 + 每个 blob 的哈希。改、删中间任何一行都会断。
//! - 删掉末尾几行本机查不出 —— 所以 Companion 每天把链头 (last_id, last_hash) 报给中央
//!   (`/api/egress/anchor`, 只有指纹没有内容)。verify 时拿中央的记录对: 对不上 / 少了 = 被动过。
//!
//! # 保留期
//!
//! 员工在本机设 (outbound_meta.retention_days, 默认 90 天)。到期按 id 前缀整段清, 链头接到
//! gc_last_hash, 清过的部分中央锚点自然对不上 —— 所以锚点比保留期还早的算「正常清理」。

use std::collections::HashSet;
use std::io::{Read, Write};

use flate2::read::ZlibDecoder;
use flate2::write::ZlibEncoder;
use flate2::Compression;
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

use super::transparent_log::open_db;

pub(crate) const GENESIS: &str = "GENESIS";
pub(crate) const DEFAULT_RETENTION_DAYS: i64 = 90;
const BLOB_MIN: usize = 1024;
const MAX_TEXT: usize = 5 * 1024 * 1024;
/// 字段名里带这些就整个值打码 (跟 egress_log.py `_SECRET_KEY_PARTS` 一字不差)。
const SECRET_KEY_PARTS: &[&str] = &[
    "token", "ticket", "session", "secret", "password", "passwd", "api_key", "apikey", "api-key", "access_key",
    "private_key", "authorization",
];
/// 用户标识留前后几位 (egress_log.py `_ID_KEEP`)
const ID_KEEP: usize = 4;

pub(crate) fn ensure(conn: &Connection) -> Result<(), String> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS outbound_payload (log_id INTEGER PRIMARY KEY, body BLOB NOT NULL);
         CREATE TABLE IF NOT EXISTS outbound_blob (hash TEXT PRIMARY KEY, data BLOB NOT NULL, last_ts TEXT NOT NULL);
         CREATE TABLE IF NOT EXISTS outbound_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);",
    )
    .map_err(|e| format!("init store: {e}"))?;
    let have: Vec<String> = conn
        .prepare("PRAGMA table_info(outbound_log)")
        .and_then(|mut st| st.query_map([], |r| r.get::<_, String>(1))?.collect())
        .map_err(|e| format!("table_info: {e}"))?;
    for col in ["body_sha256", "prev_hash", "row_hash"] {
        if !have.iter().any(|c| c == col) {
            let _ = conn.execute(&format!("ALTER TABLE outbound_log ADD COLUMN {col} TEXT"), []);
        }
    }
    super::transparent_log_redact::ensure_tables(conn)
}

pub(crate) fn sha_hex(b: &[u8]) -> String {
    hex::encode(Sha256::digest(b))
}

pub(crate) fn zip(s: &str) -> Vec<u8> {
    let mut enc = ZlibEncoder::new(Vec::new(), Compression::default());
    let _ = enc.write_all(s.as_bytes());
    enc.finish().unwrap_or_default()
}

pub(crate) fn unzip(b: &[u8]) -> Option<String> {
    let mut out = String::new();
    ZlibDecoder::new(b).read_to_string(&mut out).ok()?;
    Some(out)
}

pub(crate) fn sort_keys(v: &mut Value) {
    match v {
        Value::Object(m) => {
            m.sort_keys();
            m.values_mut().for_each(sort_keys);
        }
        Value::Array(a) => a.iter_mut().for_each(sort_keys),
        _ => {}
    }
}

/// o9cq807yh8QQuk8jp4IEWb_900RE@im.wechat → o9cq…00RE@im.wechat (egress_log.py `mask_id`)。再遮一次不变。
pub(crate) fn mask_id(v: &str) -> String {
    let (local, domain) = match v.find('@') {
        Some(i) => (&v[..i], &v[i..]),
        None => (v, ""),
    };
    if local.contains('…') || local == "***" {
        return v.to_string();
    }
    let chars: Vec<char> = local.chars().collect();
    let masked = if chars.len() > 3 * ID_KEEP {
        let head: String = chars[..ID_KEEP].iter().collect();
        let tail: String = chars[chars.len() - ID_KEEP..].iter().collect();
        format!("{head}…{tail}")
    } else {
        "***".to_string()
    };
    format!("{masked}{domain}")
}

/// 字段名带密钥 / 票据字样的打码, 以 user_id 结尾的遮成前后几位 (egress_log.py `_redact`)。
pub(crate) fn redact(v: &mut Value) {
    match v {
        Value::Object(m) => {
            for (k, val) in m.iter_mut() {
                let key = k.to_lowercase();
                match val {
                    Value::String(_) if SECRET_KEY_PARTS.iter().any(|p| key.contains(p)) => {
                        *val = Value::String("***".into());
                    }
                    Value::String(s) if key.ends_with("user_id") => *s = mask_id(s),
                    _ => redact(val),
                }
            }
        }
        Value::Array(a) => a.iter_mut().for_each(redact),
        _ => {}
    }
}

/// 原始请求体 → (要存的文本, [(blob 哈希, blob 文本)])。跟 egress_log.py `stored_body` 同规则。
pub(crate) fn stored_body(raw: &[u8]) -> (String, Vec<(String, String)>) {
    if raw.is_empty() {
        return (String::new(), vec![]);
    }
    let Ok(text) = std::str::from_utf8(raw) else {
        return (format!("[二进制内容 {} 字节, sha256={}; 未保存原件]", raw.len(), sha_hex(raw)), vec![]);
    };
    let Ok(mut obj) = serde_json::from_str::<Value>(text) else {
        if raw.len() > MAX_TEXT {
            let head = String::from_utf8_lossy(&raw[..MAX_TEXT]).trim_end_matches('\u{FFFD}').to_string();
            return (format!("{head}\n[后面截掉; 全文 {} 字节, sha256={}]", raw.len(), sha_hex(raw)), vec![]);
        }
        return (text.to_string(), vec![]);
    };
    redact(&mut obj);
    sort_keys(&mut obj);
    let mut blobs = vec![];
    let mut to_ref = |v: &mut Value| {
        let s = v.to_string();
        if s.len() > BLOB_MIN {
            let h = sha_hex(s.as_bytes());
            *v = serde_json::json!({ "$blob": h });
            blobs.push((h, s));
        }
    };
    if let Value::Object(m) = &mut obj {
        if let Some(Value::Array(msgs)) = m.get_mut("messages") {
            msgs.iter_mut().for_each(&mut to_ref);
        }
        if let Some(t) = m.get_mut("tools") {
            to_ref(t);
        }
    }
    (obj.to_string(), blobs)
}

/// 一行记录里进指纹的部分 (顺序就是指纹的字段顺序)。
#[derive(Debug, Clone, Default)]
pub(crate) struct ChainRow {
    pub ts_request: String,
    pub ts_response: Option<String>,
    pub method: String,
    pub url: String,
    pub request_bytes: i64,
    pub response_bytes: i64,
    pub status: Option<i64>,
    pub error: Option<String>,
    pub origin: Option<String>,
    pub dest_kind: Option<String>,
    pub dest_host: Option<String>,
    pub purpose: Option<String>,
    pub body_sha256: String,
}

pub(crate) fn row_hash(prev: &str, r: &ChainRow) -> String {
    let o = |v: &Option<String>| v.clone().unwrap_or_default();
    let parts = [
        r.ts_request.clone(),
        o(&r.ts_response),
        r.method.clone(),
        r.url.clone(),
        r.request_bytes.to_string(),
        r.response_bytes.to_string(),
        r.status.map(|s| s.to_string()).unwrap_or_default(),
        o(&r.error),
        o(&r.origin),
        o(&r.dest_kind),
        o(&r.dest_host),
        o(&r.purpose),
        r.body_sha256.clone(),
    ];
    sha_hex(format!("{prev}\n{}", parts.join("\x1f")).as_bytes())
}

pub(crate) fn meta_get(conn: &Connection, key: &str) -> Option<String> {
    conn.query_row("SELECT value FROM outbound_meta WHERE key = ?1", [key], |r| r.get(0))
        .optional()
        .ok()
        .flatten()
}

pub(crate) fn meta_set(conn: &Connection, key: &str, value: &str) -> Result<(), String> {
    conn.execute(
        "INSERT INTO outbound_meta (key, value) VALUES (?1, ?2)
         ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        params![key, value],
    )
    .map(|_| ())
    .map_err(|e| format!("meta: {e}"))
}

fn chain_head(conn: &Connection) -> String {
    conn.query_row(
        "SELECT row_hash FROM outbound_log WHERE row_hash IS NOT NULL ORDER BY id DESC LIMIT 1",
        [],
        |r| r.get::<_, String>(0),
    )
    .optional()
    .ok()
    .flatten()
    .or_else(|| meta_get(conn, "gc_last_hash"))
    .unwrap_or_else(|| GENESIS.to_string())
}

/// 写一行 + 内容, 接到链尾。extra = (response_summary, category)。
pub(crate) fn insert(
    conn: &mut Connection,
    mut row: ChainRow,
    body: Option<&[u8]>,
    response_summary: Option<String>,
    category: Option<String>,
) -> Result<i64, String> {
    let (stored, blobs) = stored_body(body.unwrap_or_default());
    row.body_sha256 = if stored.is_empty() { String::new() } else { sha_hex(stored.as_bytes()) };
    let ts = row.ts_response.clone().unwrap_or_else(|| row.ts_request.clone());
    let tx = conn
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|e| format!("begin: {e}"))?;
    for (h, text) in &blobs {
        tx.execute(
            "INSERT OR IGNORE INTO outbound_blob (hash, data, last_ts) VALUES (?1, ?2, ?3)",
            params![h, zip(text), ts],
        )
        .map_err(|e| format!("blob: {e}"))?;
        tx.execute(
            "UPDATE outbound_blob SET last_ts = ?1 WHERE hash = ?2 AND last_ts < ?1",
            params![ts, h],
        )
        .map_err(|e| format!("blob ts: {e}"))?;
    }
    let prev = chain_head(&tx);
    let rh = row_hash(&prev, &row);
    tx.execute(
        "INSERT INTO outbound_log
         (ts_request, ts_response, method, url, request_bytes, response_bytes, status,
          response_summary, error, category, origin, dest_kind, dest_host, purpose,
          body_sha256, prev_hash, row_hash)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17)",
        params![
            row.ts_request, row.ts_response, row.method, row.url, row.request_bytes, row.response_bytes,
            row.status, response_summary, row.error, category, row.origin, row.dest_kind, row.dest_host,
            row.purpose, row.body_sha256, prev, rh,
        ],
    )
    .map_err(|e| format!("insert: {e}"))?;
    let id = tx.last_insert_rowid();
    if !stored.is_empty() {
        tx.execute("INSERT INTO outbound_payload (log_id, body) VALUES (?1, ?2)", params![id, zip(&stored)])
            .map_err(|e| format!("payload: {e}"))?;
    }
    tx.commit().map_err(|e| format!("commit: {e}"))?;
    Ok(id)
}

// ── 看内容 ─────────────────────────────────────────────

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PayloadView {
    /// 还原后的请求内容 (JSON 已排版); None = 这条没有请求体 / 已清理
    pub text: Option<String>,
    pub is_json: bool,
    /// 内容哈希跟记录里的对得上 (没被改过)
    pub verified: bool,
}

fn expand_blobs(conn: &Connection, v: &mut Value, ok: &mut bool) {
    match v {
        Value::Object(m) if m.len() == 1 && m.get("$blob").map(Value::is_string).unwrap_or(false) => {
            let h = m["$blob"].as_str().unwrap_or_default().to_string();
            let data: Option<Vec<u8>> = conn
                .query_row("SELECT data FROM outbound_blob WHERE hash = ?1", [&h], |r| r.get(0))
                .optional()
                .ok()
                .flatten();
            match data.as_deref().and_then(unzip) {
                Some(s) if sha_hex(s.as_bytes()) == h => {
                    *v = serde_json::from_str(&s).unwrap_or(Value::String(s));
                }
                Some(s) => {
                    *ok = false;
                    *v = serde_json::from_str(&s).unwrap_or(Value::String(s));
                }
                None => {
                    *ok = false;
                    *v = Value::String(format!("[这一段已清理或丢失: {h}]"));
                }
            }
        }
        Value::Object(m) => m.values_mut().for_each(|x| expand_blobs(conn, x, ok)),
        Value::Array(a) => a.iter_mut().for_each(|x| expand_blobs(conn, x, ok)),
        _ => {}
    }
}

pub(crate) fn payload_blocking(id: i64) -> Result<PayloadView, String> {
    let conn = open_db()?;
    let want: Option<String> = conn
        .query_row("SELECT body_sha256 FROM outbound_log WHERE id = ?1", [id], |r| r.get(0))
        .optional()
        .map_err(|e| format!("query: {e}"))?
        .flatten();
    let blob: Option<Vec<u8>> = conn
        .query_row("SELECT body FROM outbound_payload WHERE log_id = ?1", [id], |r| r.get(0))
        .optional()
        .map_err(|e| format!("query payload: {e}"))?;
    let Some(stored) = blob.as_deref().and_then(unzip) else {
        return Ok(PayloadView { text: None, is_json: false, verified: want.unwrap_or_default().is_empty() });
    };
    let mut verified = want.as_deref() == Some(sha_hex(stored.as_bytes()).as_str());
    match serde_json::from_str::<Value>(&stored) {
        Ok(mut v) => {
            expand_blobs(&conn, &mut v, &mut verified);
            let text = serde_json::to_string_pretty(&v).unwrap_or(stored);
            Ok(PayloadView { text: Some(text), is_json: true, verified })
        }
        Err(_) => Ok(PayloadView { text: Some(stored), is_json: false, verified }),
    }
}

// ── 校验 ───────────────────────────────────────────────

#[derive(Debug, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct Anchor {
    pub last_id: i64,
    pub last_hash: String,
    pub received_at: String,
}

#[derive(Debug, Serialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct VerifyReport {
    pub ok: bool,
    /// 走过指纹链的条数
    pub checked: u64,
    /// 老版本写的、没进链的条数 (升级前的记录)
    pub unchained: u64,
    pub first_bad_id: Option<i64>,
    pub problems: Vec<String>,
    pub anchors_checked: u64,
    pub anchors_bad: u64,
    /// 做过的统一脱敏 (哪天、为什么、重算了几条) —— 指纹重算过要说出来, 不悄悄抹掉
    pub rechains: Vec<String>,
}

fn problem(rep: &mut VerifyReport, id: Option<i64>, msg: String) {
    rep.ok = false;
    if rep.first_bad_id.is_none() {
        rep.first_bad_id = id;
    }
    if rep.problems.len() < 20 {
        rep.problems.push(msg);
    }
}

fn blob_refs(v: &Value, out: &mut Vec<String>) {
    match v {
        Value::Object(m) if m.len() == 1 && m.get("$blob").map(Value::is_string).unwrap_or(false) => {
            out.push(m["$blob"].as_str().unwrap_or_default().to_string());
        }
        Value::Object(m) => m.values().for_each(|x| blob_refs(x, out)),
        Value::Array(a) => a.iter().for_each(|x| blob_refs(x, out)),
        _ => {}
    }
}

pub(crate) fn verify_with(conn: &Connection, anchors: &[Anchor]) -> Result<VerifyReport, String> {
    let mut rep = VerifyReport { ok: true, rechains: super::transparent_log_redact::rechain_notes(conn), ..Default::default() };
    // blob 先各验一遍
    let mut good_blobs: HashSet<String> = HashSet::new();
    {
        let mut st = conn.prepare("SELECT hash, data FROM outbound_blob").map_err(|e| format!("blob: {e}"))?;
        let rows = st
            .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, Vec<u8>>(1)?)))
            .map_err(|e| format!("blob: {e}"))?;
        for r in rows {
            let (h, data) = r.map_err(|e| format!("blob row: {e}"))?;
            if unzip(&data).map(|s| sha_hex(s.as_bytes())) == Some(h.clone()) {
                good_blobs.insert(h);
            } else {
                problem(&mut rep, None, format!("对话分段 {}… 的内容跟指纹对不上", &h[..12.min(h.len())]));
            }
        }
    }
    let mut expect = meta_get(conn, "gc_last_hash").unwrap_or_else(|| GENESIS.to_string());
    let mut hashes: std::collections::HashMap<i64, String> = std::collections::HashMap::new();
    let (mut min_id, mut max_id) = (i64::MAX, 0i64);
    let mut st = conn
        .prepare(
            "SELECT l.id, ts_request, ts_response, method, url, request_bytes, response_bytes, status, error,
                    origin, dest_kind, dest_host, purpose, body_sha256, prev_hash, row_hash, p.body
             FROM outbound_log l LEFT JOIN outbound_payload p ON p.log_id = l.id ORDER BY l.id",
        )
        .map_err(|e| format!("verify: {e}"))?;
    let mut rows = st.query([]).map_err(|e| format!("verify: {e}"))?;
    while let Some(r) = rows.next().map_err(|e| format!("verify row: {e}"))? {
        let id: i64 = r.get(0).map_err(|e| e.to_string())?;
        let Some(rh): Option<String> = r.get(15).map_err(|e| e.to_string())? else {
            rep.unchained += 1;
            continue;
        };
        let row = ChainRow {
            ts_request: r.get(1).map_err(|e| e.to_string())?,
            ts_response: r.get(2).map_err(|e| e.to_string())?,
            method: r.get(3).map_err(|e| e.to_string())?,
            url: r.get(4).map_err(|e| e.to_string())?,
            request_bytes: r.get(5).map_err(|e| e.to_string())?,
            response_bytes: r.get(6).map_err(|e| e.to_string())?,
            status: r.get(7).map_err(|e| e.to_string())?,
            error: r.get(8).map_err(|e| e.to_string())?,
            origin: r.get(9).map_err(|e| e.to_string())?,
            dest_kind: r.get(10).map_err(|e| e.to_string())?,
            dest_host: r.get(11).map_err(|e| e.to_string())?,
            purpose: r.get(12).map_err(|e| e.to_string())?,
            body_sha256: r.get::<_, Option<String>>(13).map_err(|e| e.to_string())?.unwrap_or_default(),
        };
        let prev: String = r.get::<_, Option<String>>(14).map_err(|e| e.to_string())?.unwrap_or_default();
        rep.checked += 1;
        min_id = min_id.min(id);
        max_id = max_id.max(id);
        if prev != expect {
            problem(&mut rep, Some(id), format!("第 {id} 条前面的记录被删过或改过 (链接不上)"));
        }
        if row_hash(&prev, &row) != rh {
            problem(&mut rep, Some(id), format!("第 {id} 条的时间 / 地址 / 大小等字段被改过"));
        }
        if !row.body_sha256.is_empty() {
            let body: Option<Vec<u8>> = r.get(16).map_err(|e| e.to_string())?;
            match body.as_deref().and_then(unzip) {
                None => problem(&mut rep, Some(id), format!("第 {id} 条的请求内容被删了")),
                Some(s) if sha_hex(s.as_bytes()) != row.body_sha256 => {
                    problem(&mut rep, Some(id), format!("第 {id} 条的请求内容被改过"))
                }
                Some(s) => {
                    let mut refs = vec![];
                    if let Ok(v) = serde_json::from_str::<Value>(&s) {
                        blob_refs(&v, &mut refs);
                    }
                    if refs.iter().any(|h| !good_blobs.contains(h)) {
                        problem(&mut rep, Some(id), format!("第 {id} 条引用的对话分段缺失或被改过"));
                    }
                }
            }
        }
        hashes.insert(id, rh.clone());
        expect = rh;
    }
    // 中央锚点: 每天报上去的链头, 本机得对得上
    let retention = retention_days(conn);
    let cutoff = chrono::Utc::now() - chrono::Duration::days(retention);
    for a in anchors {
        // 中央给的时间带时区 (+08:00), 得解析了再比, 不能比字符串
        let past_retention = chrono::DateTime::parse_from_rfc3339(&a.received_at)
            .map(|t| t.with_timezone(&chrono::Utc) < cutoff)
            .unwrap_or(false);
        rep.anchors_checked += 1;
        // 统一脱敏之前报的锚点, 跟当时的指纹 (重算前留存的那份) 比
        let at_report = hashes
            .get(&a.last_id)
            .map(|h| super::transparent_log_redact::hash_when_anchored(conn, a.last_id, &a.received_at).unwrap_or_else(|| h.clone()));
        let bad = match at_report {
            Some(h) => (h != a.last_hash).then(|| format!("{} 报给中央的第 {} 条, 本机的指纹不一样了", day(&a.received_at), a.last_id)),
            None if a.last_id > max_id => Some(format!("{} 时已经有 {} 条, 现在本机只剩到第 {} 条 —— 末尾被删过", day(&a.received_at), a.last_id, max_id)),
            None if a.last_id < min_id && past_retention => None, // 过了保留期, 正常清理
            None => Some(format!("{} 报给中央的第 {} 条, 还没到保留期就不见了", day(&a.received_at), a.last_id)),
        };
        if let Some(msg) = bad {
            rep.anchors_bad += 1;
            problem(&mut rep, Some(a.last_id), msg);
        }
    }
    Ok(rep)
}

fn day(ts: &str) -> &str {
    ts.get(..10).unwrap_or(ts)
}

// ── 保留期 / 清理 / 链头 ───────────────────────────────

pub(crate) fn retention_days(conn: &Connection) -> i64 {
    meta_get(conn, "retention_days")
        .and_then(|v| v.parse().ok())
        .filter(|d: &i64| (1..=3650).contains(d))
        .unwrap_or(DEFAULT_RETENTION_DAYS)
}

/// 按保留期清: id 前缀整段删 (保证剩下的链还是连着的), 链头接到 gc_last_hash。
pub(crate) fn gc(conn: &mut Connection) -> Result<u64, String> {
    let cutoff = (chrono::Utc::now() - chrono::Duration::days(retention_days(conn))).to_rfc3339();
    let last: Option<i64> = conn
        .query_row("SELECT MAX(id) FROM outbound_log WHERE ts_request < ?1", [&cutoff], |r| r.get(0))
        .map_err(|e| format!("gc: {e}"))?;
    let tx = conn
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|e| format!("begin: {e}"))?;
    let mut deleted = 0;
    if let Some(last) = last {
        let tail: Option<String> = tx
            .query_row(
                "SELECT row_hash FROM outbound_log WHERE id <= ?1 AND row_hash IS NOT NULL ORDER BY id DESC LIMIT 1",
                [last],
                |r| r.get(0),
            )
            .optional()
            .map_err(|e| format!("gc: {e}"))?;
        if let Some(h) = tail {
            meta_set(&tx, "gc_last_hash", &h)?;
            meta_set(&tx, "gc_last_id", &last.to_string())?;
        }
        tx.execute("DELETE FROM outbound_payload WHERE log_id <= ?1", [last]).map_err(|e| format!("gc: {e}"))?;
        deleted = tx.execute("DELETE FROM outbound_log WHERE id <= ?1", [last]).map_err(|e| format!("gc: {e}"))?;
    }
    tx.execute("DELETE FROM outbound_blob WHERE last_ts < ?1", [&cutoff]).map_err(|e| format!("gc: {e}"))?;
    tx.commit().map_err(|e| format!("commit: {e}"))?;
    Ok(deleted as u64)
}

pub(crate) fn gc_blocking() -> Result<u64, String> {
    let mut conn = open_db()?;
    gc(&mut conn)
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StoreInfo {
    pub retention_days: i64,
    pub db_bytes: u64,
    pub rows: u64,
    pub last_id: i64,
    pub last_hash: String,
    pub anchor_last_sent: Option<String>,
}

fn info_blocking() -> Result<StoreInfo, String> {
    let conn = open_db()?;
    let path = conn.path().map(std::path::PathBuf::from);
    let size = |p: Option<std::path::PathBuf>| p.and_then(|p| std::fs::metadata(p).ok()).map(|m| m.len()).unwrap_or(0);
    let db_bytes = size(path.clone()) + size(path.map(|p| p.with_extension("db-wal")));
    let rows: i64 = conn.query_row("SELECT COUNT(*) FROM outbound_log", [], |r| r.get(0)).map_err(|e| e.to_string())?;
    let last_id: i64 = conn
        .query_row("SELECT COALESCE(MAX(id), 0) FROM outbound_log WHERE row_hash IS NOT NULL", [], |r| r.get(0))
        .map_err(|e| e.to_string())?;
    Ok(StoreInfo {
        retention_days: retention_days(&conn),
        db_bytes,
        rows: rows as u64,
        last_id,
        last_hash: chain_head(&conn),
        anchor_last_sent: meta_get(&conn, "anchor_last_sent"),
    })
}

// ── Tauri 命令 ─────────────────────────────────────────

async fn blocking<T: Send + 'static>(f: impl FnOnce() -> Result<T, String> + Send + 'static) -> Result<T, String> {
    tokio::task::spawn_blocking(f).await.map_err(|e| format!("内部错误: {e}"))?
}

#[tauri::command]
pub async fn transparent_log_payload(id: i64) -> Result<PayloadView, String> {
    blocking(move || payload_blocking(id)).await
}

#[tauri::command]
pub async fn transparent_log_verify(anchors: Option<Vec<Anchor>>) -> Result<VerifyReport, String> {
    blocking(move || verify_with(&open_db()?, &anchors.unwrap_or_default())).await
}

#[tauri::command]
pub async fn transparent_log_info() -> Result<StoreInfo, String> {
    blocking(info_blocking).await
}

/// 设保留期 (天), 设完马上按新期限清一遍。返回清掉的条数。
#[tauri::command]
pub async fn transparent_log_set_retention(days: i64) -> Result<u64, String> {
    if !(1..=3650).contains(&days) {
        return Err("保留期要在 1 到 3650 天之间".into());
    }
    blocking(move || {
        let mut conn = open_db()?;
        meta_set(&conn, "retention_days", &days.to_string())?;
        gc(&mut conn)
    })
    .await
}

/// 链头已报给中央 —— 记下时间, 一天报一次。
#[tauri::command]
pub async fn transparent_log_anchor_sent(at: String) -> Result<(), String> {
    blocking(move || meta_set(&open_db()?, "anchor_last_sent", &at)).await
}

#[cfg(test)]
#[path = "transparent_log_store_tests.rs"]
mod tests;
