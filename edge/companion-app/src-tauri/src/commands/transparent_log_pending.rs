//! 11/3: 数据外发记录「先登记、后落账」—— 请求发出前先记一笔, 回复到了再进防篡改链。
//!
//! # 病
//!
//! 鸿波 11/3 对照门户配额日志: 21:56 那次早安分析中央有记录 (21:56:06 收到 → 21:56:32 完成),
//! 本机一条都没有; 17:01 发出的那次本机记成 17:21:42。原因是 Companion 界面 (me.ts logOutbound)
//! **收到回复以后**才在后台补写一条, 写的时间就是写的那一刻、失败也不吭声:
//! - 窗口在后台被系统挂起, 补写拖到切回来那一刻 (晚了 17 分钟);
//! - 等回复的时候 Companion 被关掉 / 页面重新加载, 补写根本没发生, 这次外发就没了。
//! 这本账是用来证明「每一次外发都查得到」的, 漏一条就不成立。
//!
//! # 做法
//!
//! 链上的行是只追加的 (row_hash 里有状态 / 回复时间, 跟 hermes egress_log.py 同一个算法),
//! 不能先写再改。所以另起一张**不进链**的 `outbound_pending`:
//! - 发请求前 `transparent_log_begin` 登记 (连请求内容, 跟正式记录一样存);
//! - 回复到了 `transparent_log_record` 带上登记号, 用**发出时间**落进链, 删掉登记;
//! - 落不了账的 (Companion 启动时发现上次留下的; 运行中超过 2 小时还没回音的) 以「没收到回应」落账。

use std::sync::OnceLock;

use rusqlite::{params, Connection};

use super::transparent_log::{record_blocking, RecordReq};

/// 运行中超过这么久还没回音, 当作页面重新加载过、回复再也不会来了。
const STALE_HOURS: i64 = 2;

pub(crate) fn ensure(conn: &Connection) -> Result<(), String> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS outbound_pending (id TEXT PRIMARY KEY, ts_request TEXT NOT NULL, req TEXT NOT NULL);",
    )
    .map_err(|e| format!("init pending: {e}"))
}

pub(crate) fn begin_blocking(id: &str, mut req: RecordReq) -> Result<(), String> {
    let conn = super::transparent_log::open_db()?;
    let ts = req.ts_request.clone().unwrap_or_else(|| chrono::Utc::now().to_rfc3339());
    req.ts_request = Some(ts.clone());
    req.pending_id = None;
    let json = serde_json::to_string(&req).map_err(|e| format!("登记序列化: {e}"))?;
    conn.execute(
        "INSERT OR REPLACE INTO outbound_pending (id, ts_request, req) VALUES (?1, ?2, ?3)",
        params![id, ts, json],
    )
    .map_err(|e| format!("登记外发: {e}"))?;
    Ok(())
}

pub(crate) fn finish(conn: &Connection, id: &str) {
    let _ = conn.execute("DELETE FROM outbound_pending WHERE id = ?1", params![id]);
}

/// 把发出时间早于 `cutoff` 的登记以「没收到回应」落账。返回落了几条。
pub(crate) fn finalize_before(cutoff: &str, reason: &str) -> usize {
    let Ok(conn) = super::transparent_log::open_db() else { return 0 };
    let rows: Vec<(String, String)> = conn
        .prepare("SELECT id, req FROM outbound_pending WHERE ts_request < ?1 ORDER BY ts_request")
        .and_then(|mut st| st.query_map(params![cutoff], |r| Ok((r.get(0)?, r.get(1)?)))?.collect())
        .unwrap_or_default();
    let mut done = 0;
    for (id, json) in rows {
        if let Ok(mut req) = serde_json::from_str::<RecordReq>(&json) {
            req.status = None;
            req.response_bytes = None;
            req.response_summary = None;
            req.error = Some(reason.to_string());
            req.pending_id = None;
            if record_blocking(req).is_ok() {
                done += 1;
            }
        }
        finish(&conn, &id);
    }
    done
}

/// 本进程启动的时刻 (第一次调用时定下)。早于它的登记一定是上次留下的。
fn process_started() -> &'static str {
    static STARTED: OnceLock<String> = OnceLock::new();
    STARTED.get_or_init(|| chrono::Utc::now().to_rfc3339())
}

/// Companion 启动时调一次 (lib.rs setup): 上次关掉前还在等回复的, 落账。
pub(crate) fn finalize_from_previous_run() {
    let cutoff = process_started().to_string();
    std::thread::spawn(move || {
        let n = finalize_before(&cutoff, "没收到回应: 等回复时 Companion 被关掉了");
        if n > 0 {
            log::info!("[transparent_log] 上次没落账的外发 {n} 条, 以「没收到回应」记上");
        }
    });
}

/// 查记录前调: 运行中超过 STALE_HOURS 还没回音的落账 (页面重新加载过, 回复不会再来)。
pub(crate) fn finalize_stale_in_run() {
    let cutoff = (chrono::Utc::now() - chrono::Duration::hours(STALE_HOURS)).to_rfc3339();
    finalize_before(&cutoff, "没收到回应: 等了 2 小时 (页面可能重新加载过)");
}

#[cfg(test)]
mod tests {
    use super::*;

    fn req(pending: Option<&str>, status: Option<u16>) -> RecordReq {
        RecordReq {
            method: "POST".into(),
            url: "http://127.0.0.1:8999/v1/chat/completions?catfish_source=companion-advisor".into(),
            request_body: Some("{\"messages\":[]}".into()),
            status,
            response_bytes: status.map(|_| 10),
            response_summary: None,
            error: None,
            category: Some("content".into()),
            origin: Some("companion".into()),
            dest_kind: Some("central".into()),
            dest_host: Some("127.0.0.1".into()),
            purpose: Some("早安建议".into()),
            request_bytes: None,
            ts_request: Some("2026-11-03T09:01:06+00:00".into()),
            pending_id: pending.map(String::from),
        }
    }

    fn with_home<T>(f: impl FnOnce() -> T) -> T {
        let _g = crate::util::test_env::env_lock();
        let prev = std::env::var_os("HOME");
        let dir = tempfile::tempdir().unwrap();
        std::env::set_var("HOME", dir.path());
        let out = f();
        match prev {
            Some(h) => std::env::set_var("HOME", h),
            None => std::env::remove_var("HOME"),
        }
        out
    }

    fn rows() -> Vec<(String, Option<i64>, Option<String>)> {
        let conn = super::super::transparent_log::open_db().unwrap();
        let mut st = conn.prepare("SELECT ts_request, status, error FROM outbound_log ORDER BY id").unwrap();
        let out = st
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))
            .unwrap()
            .map(Result::unwrap)
            .collect();
        out
    }

    fn pending_count() -> i64 {
        let conn = super::super::transparent_log::open_db().unwrap();
        conn.query_row("SELECT COUNT(*) FROM outbound_pending", [], |r| r.get(0)).unwrap()
    }

    #[test]
    fn 先登记_回复到了按发出时间落账_登记删掉() {
        with_home(|| {
            begin_blocking("p1", req(None, None)).unwrap();
            assert_eq!(pending_count(), 1);
            assert!(rows().is_empty(), "登记不进链");
            record_blocking(req(Some("p1"), Some(200))).unwrap();
            assert_eq!(pending_count(), 0);
            assert_eq!(rows(), vec![("2026-11-03T09:01:06+00:00".to_string(), Some(200), None)]);
        });
    }

    #[test]
    fn 没等到回复的登记_以没收到回应落账() {
        with_home(|| {
            begin_blocking("p2", req(None, None)).unwrap();
            assert_eq!(finalize_before("2026-11-03T10:00:00+00:00", "没收到回应: 测试"), 1);
            assert_eq!(pending_count(), 0);
            let r = rows();
            assert_eq!(r.len(), 1);
            assert_eq!(r[0].0, "2026-11-03T09:01:06+00:00");
            assert_eq!(r[0].1, None);
            assert_eq!(r[0].2.as_deref(), Some("没收到回应: 测试"));
            // 比 cutoff 晚的 (还在等的) 不动
            begin_blocking("p3", RecordReq { ts_request: Some("2026-11-03T11:00:00+00:00".into()), ..req(None, None) }).unwrap();
            assert_eq!(finalize_before("2026-11-03T10:00:00+00:00", "x"), 0);
            assert_eq!(pending_count(), 1);
        });
    }
}
