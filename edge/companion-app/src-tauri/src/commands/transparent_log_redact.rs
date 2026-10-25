//! 数据外发记录的统一脱敏: 打码规则变严之后, 旧记录按新规则重新打码、重算指纹 (10/25 鸿波)
//!
//! # 为什么
//!
//! 第一版只认 15 个固定的密钥字段名, 微信 iLink 请求里的会话票据 (typing_ticket / context_token) 和
//! 用户标识 (ilink_user_id / from_user_id / to_user_id) 原样存了下来。规则改了以后新记录没问题,
//! 旧记录要补 —— 鸿波定: 旧记录统一脱敏并重算指纹。
//!
//! # 难点: 指纹要重算, 又不能变成「自己证明自己没被改过」
//!
//! 内容一改, 内容哈希 → 这一行指纹 → 后面每一行的指纹全变; 中央每天收的链头 (锚点) 也就对不上了。
//! 做法:
//!   · 重算前先完整校验一遍 —— 链本来就对不上 (已经被动过) 就不重算, 不替篡改洗白
//!   · 每条被重算的记录把原指纹存进 outbound_old_hash, 再记一条 outbound_rechain (何时、为什么、
//!     重算几条、前后链头)。校验时, 统一脱敏之前报给中央的锚点拿当时的指纹 (留存的原指纹) 比 ——
//!     证明脱敏之前没人动过; 之后的锚点照常比现在的指纹
//!   · 校验报告里写明做过几次统一脱敏, 不悄悄抹掉
//!   · 清掉「上次报指纹」, Companion 下一轮就把新链头报给中央
//!
//! 不靠「做过一次」的标记: 启动时 Companion 先同步插件再重启 hermes, 这之前旧插件可能又写了几条
//! 没打码的。所以每次启动 / 每 6 小时都看一遍有没有在新规则下会变的记录, 有才重算 (平时什么都不做)。

use std::collections::HashMap;

use rusqlite::{params, Connection, TransactionBehavior};
use serde_json::Value;

use super::transparent_log::open_db;
use super::transparent_log_store::{
    meta_get, redact, row_hash, sha_hex, sort_keys, unzip, verify_with, zip, ChainRow, GENESIS,
};

const REASON: &str = "统一脱敏: 会话票据 (ticket / token / session 类字段) 打码, 用户标识 (user_id 类字段) 只留前后几位";

pub(crate) fn ensure_tables(conn: &Connection) -> Result<(), String> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS outbound_rechain (at TEXT NOT NULL, reason TEXT NOT NULL, rows INTEGER NOT NULL,
             changed INTEGER NOT NULL, old_head TEXT NOT NULL, new_head TEXT NOT NULL);
         CREATE TABLE IF NOT EXISTS outbound_old_hash (id INTEGER NOT NULL, at TEXT NOT NULL, old_row_hash TEXT NOT NULL,
             PRIMARY KEY (id, at));",
    )
    .map_err(|e| format!("init rechain: {e}"))
}

/// 按新规则重新打码。没变返回 None (原文和原指纹都不动)。
fn redacted(text: &str, remap: &HashMap<String, String>) -> Option<String> {
    let mut v: Value = serde_json::from_str(text).ok()?;
    let orig = v.clone();
    if !remap.is_empty() {
        swap_blobs(&mut v, remap);
    }
    redact(&mut v);
    if v == orig {
        return None;
    }
    sort_keys(&mut v);
    Some(v.to_string())
}

fn swap_blobs(v: &mut Value, remap: &HashMap<String, String>) {
    match v {
        Value::Object(m) if m.len() == 1 && m.get("$blob").map(Value::is_string).unwrap_or(false) => {
            if let Some(new) = remap.get(m["$blob"].as_str().unwrap_or_default()) {
                m.insert("$blob".into(), Value::String(new.clone()));
            }
        }
        Value::Object(m) => m.values_mut().for_each(|x| swap_blobs(x, remap)),
        Value::Array(a) => a.iter_mut().for_each(|x| swap_blobs(x, remap)),
        _ => {}
    }
}

struct Row {
    id: i64,
    chain: ChainRow,
    row_hash: String,
    body: Option<Vec<u8>>,
}

fn load_rows(conn: &Connection) -> Result<Vec<Row>, String> {
    let mut st = conn
        .prepare(
            "SELECT l.id, ts_request, ts_response, method, url, request_bytes, response_bytes, status, error,
                    origin, dest_kind, dest_host, purpose, body_sha256, row_hash, p.body
             FROM outbound_log l LEFT JOIN outbound_payload p ON p.log_id = l.id
             WHERE row_hash IS NOT NULL ORDER BY l.id",
        )
        .map_err(|e| format!("rechain: {e}"))?;
    let rows = st
        .query_map([], |r| {
            Ok(Row {
                id: r.get(0)?,
                chain: ChainRow {
                    ts_request: r.get(1)?,
                    ts_response: r.get(2)?,
                    method: r.get(3)?,
                    url: r.get(4)?,
                    request_bytes: r.get(5)?,
                    response_bytes: r.get(6)?,
                    status: r.get(7)?,
                    error: r.get(8)?,
                    origin: r.get(9)?,
                    dest_kind: r.get(10)?,
                    dest_host: r.get(11)?,
                    purpose: r.get(12)?,
                    body_sha256: r.get::<_, Option<String>>(13)?.unwrap_or_default(),
                },
                row_hash: r.get(14)?,
                body: r.get(15)?,
            })
        })
        .map_err(|e| format!("rechain: {e}"))?;
    rows.collect::<Result<_, _>>().map_err(|e| format!("rechain row: {e}"))
}

/// 有在新规则下会变的记录就统一脱敏、重算指纹; 返回给日志看的一句话, 没什么要做返回 None。
/// 链本来就校验不过时报错且不动任何东西。
pub(crate) fn redact_existing(conn: &mut Connection) -> Result<Option<String>, String> {
    // 先看有没有要改的 (平时走到这里就返回)
    let blobs: Vec<(String, Vec<u8>, String)> = conn
        .prepare("SELECT hash, data, last_ts FROM outbound_blob")
        .and_then(|mut st| st.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?.collect())
        .map_err(|e| format!("rechain blob: {e}"))?;
    let mut remap: HashMap<String, String> = HashMap::new();
    let mut new_blobs: Vec<(String, String, String)> = vec![];
    for (h, data, ts) in &blobs {
        if let Some(text) = unzip(data).and_then(|t| redacted(&t, &HashMap::new())) {
            let nh = sha_hex(text.as_bytes());
            remap.insert(h.clone(), nh.clone());
            new_blobs.push((nh, text, ts.clone()));
        }
    }
    let rows = load_rows(conn)?;
    let needs = !remap.is_empty()
        || rows.iter().any(|r| r.body.as_deref().and_then(unzip).and_then(|t| redacted(&t, &remap)).is_some());
    if !needs {
        return Ok(None);
    }
    let pre = verify_with(conn, &[])?;
    if !pre.ok {
        return Err(format!(
            "本机外发记录的指纹链本来就对不上 ({}), 不做统一脱敏, 旧记录保持原样",
            pre.problems.first().cloned().unwrap_or_default()
        ));
    }

    let now = chrono::Utc::now().to_rfc3339();
    let tx = conn
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|e| format!("begin: {e}"))?;
    for (nh, text, ts) in &new_blobs {
        tx.execute("INSERT OR IGNORE INTO outbound_blob (hash, data, last_ts) VALUES (?1, ?2, ?3)", params![nh, zip(text), ts])
            .map_err(|e| format!("rechain blob: {e}"))?;
    }
    // 事务里重新读一遍: 预查之后可能又有新行接上来
    let rows = load_rows(&tx)?;
    let mut prev = meta_get(&tx, "gc_last_hash").unwrap_or_else(|| GENESIS.to_string());
    let (mut changed, mut rehashed) = (0u64, 0u64);
    let old_head = rows.last().map(|r| r.row_hash.clone()).unwrap_or_default();
    for mut r in rows {
        if let Some(text) = r.body.as_deref().and_then(unzip).and_then(|t| redacted(&t, &remap)) {
            r.chain.body_sha256 = sha_hex(text.as_bytes());
            tx.execute("UPDATE outbound_payload SET body = ?1 WHERE log_id = ?2", params![zip(&text), r.id])
                .map_err(|e| format!("rechain payload: {e}"))?;
            changed += 1;
        }
        let rh = row_hash(&prev, &r.chain);
        if rh != r.row_hash {
            tx.execute(
                "INSERT OR IGNORE INTO outbound_old_hash (id, at, old_row_hash) VALUES (?1, ?2, ?3)",
                params![r.id, now, r.row_hash],
            )
            .map_err(|e| format!("rechain old: {e}"))?;
            tx.execute(
                "UPDATE outbound_log SET body_sha256 = ?1, prev_hash = ?2, row_hash = ?3 WHERE id = ?4",
                params![r.chain.body_sha256, prev, rh, r.id],
            )
            .map_err(|e| format!("rechain row: {e}"))?;
            rehashed += 1;
        }
        prev = rh;
    }
    for old in remap.keys() {
        tx.execute("DELETE FROM outbound_blob WHERE hash = ?1", [old]).map_err(|e| format!("rechain blob: {e}"))?;
    }
    tx.execute(
        "INSERT INTO outbound_rechain (at, reason, rows, changed, old_head, new_head) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
        params![now, REASON, rehashed, changed, old_head, prev],
    )
    .map_err(|e| format!("rechain log: {e}"))?;
    // 新链头尽快报给中央 (不等满一天)
    tx.execute("DELETE FROM outbound_meta WHERE key = 'anchor_last_sent'", []).map_err(|e| format!("rechain meta: {e}"))?;
    tx.commit().map_err(|e| format!("commit: {e}"))?;
    Ok(Some(format!("统一脱敏: {changed} 条内容重新打码, 重算了 {rehashed} 条的指纹")))
}

pub(crate) fn redact_existing_blocking() -> Result<Option<String>, String> {
    redact_existing(&mut open_db()?)
}

/// 中央在 `received_at` 收到的第 `id` 条锚点, 当时那一条的指纹: 之后做过统一脱敏、而这条被重算过,
/// 就是第一次重算前留存的那份; 否则 None (跟现在的一样)。
pub(crate) fn hash_when_anchored(conn: &Connection, id: i64, received_at: &str) -> Option<String> {
    let received = chrono::DateTime::parse_from_rfc3339(received_at).ok()?.with_timezone(&chrono::Utc);
    let mut st = conn.prepare("SELECT at, old_row_hash FROM outbound_old_hash WHERE id = ?1 ORDER BY at").ok()?;
    let rows: Vec<(String, String)> = st
        .query_map([id], |r| Ok((r.get(0)?, r.get(1)?)))
        .ok()?
        .filter_map(Result::ok)
        .collect();
    rows.into_iter()
        .find(|(at, _)| chrono::DateTime::parse_from_rfc3339(at).map(|t| t.with_timezone(&chrono::Utc) > received).unwrap_or(false))
        .map(|(_, h)| h)
}

/// 校验报告里的「做过的统一脱敏」
pub(crate) fn rechain_notes(conn: &Connection) -> Vec<String> {
    let Ok(mut st) = conn.prepare("SELECT at, reason, rows, changed FROM outbound_rechain ORDER BY at") else {
        return vec![];
    };
    st.query_map([], |r| {
        let at: String = r.get(0)?;
        let reason: String = r.get(1)?;
        let (rows, changed): (i64, i64) = (r.get(2)?, r.get(3)?);
        let day = chrono::DateTime::parse_from_rfc3339(&at)
            .map(|t| t.with_timezone(&chrono::Local).format("%Y-%m-%d").to_string())
            .unwrap_or_else(|_| at.get(..10).unwrap_or(&at).to_string());
        Ok(format!("{day} {reason} —— {changed} 条内容重新打码, 重算了 {rows} 条的指纹 (之前报给公司的指纹按当时的值核对)"))
    })
    .map(|it| it.filter_map(Result::ok).collect())
    .unwrap_or_default()
}

#[cfg(test)]
#[path = "transparent_log_redact_tests.rs"]
mod tests;
