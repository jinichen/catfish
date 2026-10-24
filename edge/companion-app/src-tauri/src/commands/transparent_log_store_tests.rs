use super::*;

fn with_home<T>(f: impl FnOnce(&std::path::Path) -> T) -> T {
    let _g = crate::util::test_env::env_lock();
    let prev = std::env::var_os("HOME");
    let dir = tempfile::tempdir().unwrap();
    std::env::set_var("HOME", dir.path());
    let out = f(dir.path());
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
        origin: Some("companion".into()),
        dest_kind: Some("central".into()),
        dest_host: Some("10.0.0.5".into()),
        purpose: Some("模型调用".into()),
        ..Default::default()
    }
}

#[test]
fn 指纹算法_跟python同一个测试向量() {
    // egress_log.py test_指纹算法_跟Rust同一个测试向量 钉的是同一个值
    let r = ChainRow {
        ts_request: "2026-10-25T00:00:00+00:00".into(),
        ts_response: Some("2026-10-25T00:00:01+00:00".into()),
        method: "POST".into(),
        url: "http://10.0.0.5:8999/v1/chat/completions".into(),
        request_bytes: 12,
        response_bytes: 0,
        status: Some(200),
        error: None,
        origin: Some("hermes".into()),
        dest_kind: Some("central".into()),
        dest_host: Some("10.0.0.5".into()),
        purpose: Some("模型调用".into()),
        body_sha256: "ab".repeat(32),
    };
    assert_eq!(row_hash(GENESIS, &r), "1a5e312e9f1f07a8ad5bbf86ac8aa5de5dd594987343375a23bdb33e34fe8fe7");
}

#[test]
fn 存内容_去密钥_大消息去重() {
    let big = "很长的系统提示".repeat(200);
    let body = serde_json::json!({"model": "m", "api_key": "sk-1", "max_tokens": 5,
        "messages": [{"role": "system", "content": big}, {"role": "user", "content": "第一句"}]});
    let (stored, blobs) = stored_body(body.to_string().as_bytes());
    assert!(!stored.contains("sk-1") && stored.contains(r#""api_key":"***""#));
    assert!(stored.contains(r#""max_tokens":5"#), "max_tokens 不是密钥");
    assert_eq!(blobs.len(), 1);
    assert!(stored.contains("第一句") && !stored.contains(&big));
    assert!(stored_body(b"\xff\xfe\x00binary").0.starts_with("[二进制内容 9 字节, sha256="));
}

#[test]
fn 指纹链_看内容_校验出篡改_锚点() {
    with_home(|_| {
        let mut conn = open_db().unwrap();
        let big = "系统提示".repeat(400);
        let mk = |n: &str| serde_json::json!({"messages": [{"role": "system", "content": big},
                                                            {"role": "user", "content": n}]}).to_string();
        let a = insert(&mut conn, row("http://10.0.0.5:8999/v1/chat/completions", "2026-10-25T01:00:00+00:00"),
                       Some(mk("第一句").as_bytes()), None, Some("content".into())).unwrap();
        let b = insert(&mut conn, row("http://10.0.0.5:8999/v1/chat/completions", "2026-10-25T02:00:00+00:00"),
                       Some(mk("第二句").as_bytes()), None, Some("content".into())).unwrap();
        let c = insert(&mut conn, row("https://wttr.in/x", "2026-10-25T03:00:00+00:00"), None, None, None).unwrap();
        let n_blob: i64 = conn.query_row("SELECT COUNT(*) FROM outbound_blob", [], |r| r.get(0)).unwrap();
        assert_eq!(n_blob, 1, "系统提示只存一份");

        let p = payload_blocking(b).unwrap();
        assert!(p.verified && p.is_json);
        let text = p.text.unwrap();
        assert!(text.contains("第二句") && text.contains("系统提示系统提示"), "blob 还原回去了");

        let rep = verify_with(&conn, &[]).unwrap();
        assert!(rep.ok, "{:?}", rep.problems);
        assert_eq!(rep.checked, 3);

        let head: String = conn.query_row("SELECT row_hash FROM outbound_log WHERE id = ?1", [c], |r| r.get(0)).unwrap();
        let anchor = |id: i64, h: &str| Anchor { last_id: id, last_hash: h.into(), received_at: chrono::Utc::now().to_rfc3339() };
        assert!(verify_with(&conn, &[anchor(c, &head)]).unwrap().ok, "锚点对得上");

        // 改字段
        conn.execute("UPDATE outbound_log SET url = 'http://x/' WHERE id = ?1", [a]).unwrap();
        let rep = verify_with(&conn, &[]).unwrap();
        assert!(!rep.ok && rep.first_bad_id == Some(a));
        conn.execute("UPDATE outbound_log SET url = 'http://10.0.0.5:8999/v1/chat/completions' WHERE id = ?1", [a]).unwrap();
        assert!(verify_with(&conn, &[]).unwrap().ok);

        // 改内容
        conn.execute("UPDATE outbound_payload SET body = ?1 WHERE log_id = ?2", params![zip("{}"), b]).unwrap();
        assert_eq!(verify_with(&conn, &[]).unwrap().first_bad_id, Some(b));
        assert!(!payload_blocking(b).unwrap().verified);

        // 删末尾: 本机链自己看不出, 中央锚点看得出
        conn.execute("DELETE FROM outbound_payload WHERE log_id >= ?1", [b]).unwrap();
        conn.execute("DELETE FROM outbound_log WHERE id >= ?1", [b]).unwrap();
        assert!(verify_with(&conn, &[]).unwrap().ok);
        let rep = verify_with(&conn, &[anchor(c, &head)]).unwrap();
        assert!(!rep.ok && rep.anchors_bad == 1, "{:?}", rep.problems);
    });
}

#[test]
fn 保留期_清理后链还接得上() {
    with_home(|_| {
        let mut conn = open_db().unwrap();
        let old = (chrono::Utc::now() - chrono::Duration::days(40)).to_rfc3339();
        let now = chrono::Utc::now().to_rfc3339();
        insert(&mut conn, row("http://10.0.0.5:8999/a", &old), Some(b"old"), None, None).unwrap();
        let keep = insert(&mut conn, row("http://10.0.0.5:8999/b", &now), Some(b"new"), None, None).unwrap();
        assert_eq!(retention_days(&conn), DEFAULT_RETENTION_DAYS);
        assert_eq!(gc(&mut conn).unwrap(), 0, "90 天内不清");
        meta_set(&conn, "retention_days", "30").unwrap();
        assert_eq!(gc(&mut conn).unwrap(), 1);
        let rep = verify_with(&conn, &[]).unwrap();
        assert!(rep.ok && rep.checked == 1, "{:?}", rep.problems);
        // 清掉的那条当时报过中央: 报的时间早于保留期 → 正常清理; 晚于 → 提前删 (中央时间带 +08:00)
        let gone = |at: chrono::DateTime<chrono::FixedOffset>| Anchor {
            last_id: 1, last_hash: "x".into(), received_at: at.to_rfc3339() };
        let cst = chrono::FixedOffset::east_opt(8 * 3600).unwrap();
        let old_at = (chrono::Utc::now() - chrono::Duration::days(35)).with_timezone(&cst);
        let new_at = (chrono::Utc::now() - chrono::Duration::days(5)).with_timezone(&cst);
        assert!(verify_with(&conn, &[gone(old_at)]).unwrap().ok);
        assert_eq!(verify_with(&conn, &[gone(new_at)]).unwrap().anchors_bad, 1);
        // 清理后新写的照样接在后面
        insert(&mut conn, row("http://10.0.0.5:8999/c", &now), Some(b"more"), None, None).unwrap();
        assert!(verify_with(&conn, &[]).unwrap().ok);
        assert!(payload_blocking(keep).unwrap().verified);
    });
}
