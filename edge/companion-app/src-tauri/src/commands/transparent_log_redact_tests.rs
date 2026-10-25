use super::*;
use crate::commands::transparent_log_store::{payload_blocking, stored_body, Anchor};

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

fn row(url: &str, ts: &str) -> ChainRow {
    ChainRow {
        ts_request: ts.into(),
        ts_response: Some(ts.into()),
        method: "POST".into(),
        url: url.into(),
        request_bytes: 10,
        status: Some(200),
        origin: Some("hermes".into()),
        dest_kind: Some("third_party".into()),
        dest_host: Some("ilinkai.weixin.qq.com".into()),
        purpose: Some("微信".into()),
        ..Default::default()
    }
}

/// 按旧规则写下的一条 (内容没打码), 照常接链 —— 模拟升级前存下来的记录
fn insert_old(conn: &Connection, mut r: ChainRow, stored: &str) -> (i64, String) {
    r.body_sha256 = sha_hex(stored.as_bytes());
    let prev: String = conn
        .query_row("SELECT row_hash FROM outbound_log WHERE row_hash IS NOT NULL ORDER BY id DESC LIMIT 1", [], |x| x.get(0))
        .unwrap_or_else(|_| GENESIS.to_string());
    let rh = row_hash(&prev, &r);
    conn.execute(
        "INSERT INTO outbound_log (ts_request, ts_response, method, url, request_bytes, response_bytes, status, error,
             origin, dest_kind, dest_host, purpose, body_sha256, prev_hash, row_hash)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
        params![r.ts_request, r.ts_response, r.method, r.url, r.request_bytes, r.response_bytes, r.status, r.error,
                r.origin, r.dest_kind, r.dest_host, r.purpose, r.body_sha256, prev, rh],
    )
    .unwrap();
    let id = conn.last_insert_rowid();
    conn.execute("INSERT INTO outbound_payload (log_id, body) VALUES (?1, ?2)", params![id, zip(stored)]).unwrap();
    (id, rh)
}

const TYPING: &str = r#"{"base_info":{"channel_version":"2.2.0"},"ilink_user_id":"o9cq807yh8QQuk8jp4IEWb_900RE@im.wechat","status":1,"typing_ticket":"AARTBK8FAAAB=="}"#;
const MODEL: &str = r#"{"messages":[{"content":"帮我把议程理一下","role":"user"}],"model":"m"}"#;

fn anchor(id: i64, h: &str, at: chrono::DateTime<chrono::Utc>) -> Anchor {
    Anchor { last_id: id, last_hash: h.into(), received_at: at.to_rfc3339() }
}

#[test]
fn 旧记录统一脱敏_重算指纹_之前的锚点按当时的指纹核对() {
    with_home(|| {
        let mut conn = open_db().unwrap();
        let (a, _) = insert_old(&conn, row("https://ilinkai.weixin.qq.com/ilink/bot/sendtyping", "2026-10-25T01:00:00+00:00"), TYPING);
        let (b, _) = insert_old(&conn, row("http://10.0.0.5:8999/v1/chat/completions", "2026-10-25T02:00:00+00:00"), MODEL);
        let (c, old_head) = insert_old(&conn, row("https://ilinkai.weixin.qq.com/ilink/bot/sendtyping", "2026-10-25T03:00:00+00:00"), TYPING);
        let before = anchor(c, &old_head, chrono::Utc::now() - chrono::Duration::hours(1));
        assert!(verify_with(&conn, &[before.clone()]).unwrap().ok);
        conn.execute("INSERT INTO outbound_meta (key, value) VALUES ('anchor_last_sent', 'x')", []).unwrap();

        let msg = redact_existing(&mut conn).unwrap().expect("有要脱敏的");
        assert!(msg.contains("2 条内容重新打码") && msg.contains("重算了 3 条"), "{msg}");

        let text = payload_blocking(a).unwrap().text.unwrap();
        assert!(!text.contains("AARTBK8F") && text.contains(r#""typing_ticket": "***""#), "{text}");
        assert!(text.contains("o9cq…00RE@im.wechat") && !text.contains("807yh8"), "{text}");
        assert!(payload_blocking(b).unwrap().text.unwrap().contains("帮我把议程理一下"), "没敏感字段的内容原样");

        // 脱敏前报给中央的锚点: 拿留存的原指纹核对, 对得上
        let rep = verify_with(&conn, &[before.clone()]).unwrap();
        assert!(rep.ok && rep.anchors_checked == 1, "{:?}", rep.problems);
        assert_eq!(rep.rechains.len(), 1, "报告里写明做过统一脱敏");
        assert!(rep.rechains[0].contains("统一脱敏"));

        // 脱敏之后: 新链头对得上; 拿旧指纹冒充新报的锚点 → 对不上
        let new_head: String = conn.query_row("SELECT row_hash FROM outbound_log WHERE id = ?1", [c], |r| r.get(0)).unwrap();
        assert_ne!(new_head, old_head);
        let later = chrono::Utc::now() + chrono::Duration::seconds(5);
        assert!(verify_with(&conn, &[anchor(c, &new_head, later)]).unwrap().ok);
        assert!(!verify_with(&conn, &[anchor(c, &old_head, later)]).unwrap().ok);

        // 新链头要尽快报: 「上次报指纹」清掉了
        assert!(meta_get(&conn, "anchor_last_sent").is_none());
        // 再跑一次没什么要做
        assert_eq!(redact_existing(&mut conn).unwrap(), None);
    });
}

#[test]
fn 链本来就对不上_不做统一脱敏_什么都不动() {
    with_home(|| {
        let mut conn = open_db().unwrap();
        let (a, _) = insert_old(&conn, row("https://ilinkai.weixin.qq.com/ilink/bot/sendtyping", "2026-10-25T01:00:00+00:00"), TYPING);
        insert_old(&conn, row("https://ilinkai.weixin.qq.com/ilink/bot/sendtyping", "2026-10-25T02:00:00+00:00"), TYPING);
        conn.execute("UPDATE outbound_log SET url = 'https://x/' WHERE id = ?1", [a]).unwrap();
        let err = redact_existing(&mut conn).unwrap_err();
        assert!(err.contains("本来就对不上"), "{err}");
        assert!(payload_blocking(a).unwrap().text.unwrap().contains("AARTBK8F"), "不替篡改洗白: 一条都没动");
        let n: i64 = conn.query_row("SELECT COUNT(*) FROM outbound_rechain", [], |r| r.get(0)).unwrap();
        assert_eq!(n, 0);
    });
}

#[test]
fn 对话分段里有要打码的字段_换新哈希_引用跟着换() {
    with_home(|| {
        let mut conn = open_db().unwrap();
        let big = "系统提示".repeat(400);
        let msg = serde_json::json!({"content": big, "role": "system", "session_id": "s-123456"});
        let blob_text = msg.to_string();
        let h = sha_hex(blob_text.as_bytes());
        conn.execute("INSERT INTO outbound_blob (hash, data, last_ts) VALUES (?1, ?2, ?3)",
                     params![h, zip(&blob_text), "2026-10-25T01:00:00+00:00"]).unwrap();
        let stored = format!(r#"{{"messages":[{{"$blob":"{h}"}}],"model":"m"}}"#);
        let (id, _) = insert_old(&conn, row("http://10.0.0.5:8999/v1/chat/completions", "2026-10-25T01:00:00+00:00"), &stored);

        redact_existing(&mut conn).unwrap().expect("分段要改");
        let old_left: i64 = conn.query_row("SELECT COUNT(*) FROM outbound_blob WHERE hash = ?1", [&h], |r| r.get(0)).unwrap();
        assert_eq!(old_left, 0, "旧分段删掉");
        let p = payload_blocking(id).unwrap();
        assert!(p.verified, "引用换成新分段, 还原得回来");
        let text = p.text.unwrap();
        assert!(text.contains(r#""session_id": "***""#) && !text.contains("s-123456") && text.contains("系统提示系统提示"));
        assert!(verify_with(&conn, &[]).unwrap().ok);
    });
}

/// 跟 egress_log.py test_打码规则_跟Rust同一个测试向量 同一份输入、同一个输出
#[test]
fn 打码规则_跟python同一个测试向量() {
    let body = r#"{"ilink_user_id":"o9cq807yh8QQuk8jp4IEWb_900RE@im.wechat","typing_ticket":"AAR=","msg":{"context_token":"c1","to_user_id":"wxid_abcdefghijkl1234","text":"你好"},"session_id":"s","max_tokens":5,"user_id":"short","Authorization":"Bearer x"}"#;
    let (stored, _) = stored_body(body.as_bytes());
    assert_eq!(
        stored,
        r#"{"Authorization":"***","ilink_user_id":"o9cq…00RE@im.wechat","max_tokens":5,"msg":{"context_token":"***","text":"你好","to_user_id":"wxid…1234"},"session_id":"***","typing_ticket":"***","user_id":"***"}"#
    );
    assert_eq!(super::super::transparent_log_store::mask_id("o9cq…00RE@im.wechat"), "o9cq…00RE@im.wechat", "再遮一次不变");
}
