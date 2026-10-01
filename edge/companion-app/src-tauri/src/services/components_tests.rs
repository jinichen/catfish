use super::*;
use sha2::{Digest, Sha256};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

fn entry(data: &[u8]) -> ComponentEntry {
    ComponentEntry {
        name: "meeting-asr".into(),
        version: "1.0.0".into(),
        platform: "mac-arm64".into(),
        file: "meeting-asr-1.0.0-mac-arm64.tar.gz".into(),
        size: data.len() as u64,
        sha256: hex::encode(Sha256::digest(data)),
    }
}

#[derive(Clone, Copy)]
enum Mode {
    /// 正常, 认 Range
    Ranged,
    /// 不认 Range, 永远 200 全量
    NoRange,
    /// 第一个连接只发前 `n` 字节就断, 之后正常
    DropFirstAfter(usize),
}

/// 起一个只服务 /components/<file> 的 HTTP 服务, 返回 base url。
async fn serve(data: Vec<u8>, mode: Mode) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        let mut first = true;
        loop {
            let Ok((mut sock, _)) = listener.accept().await else { return };
            let mut buf = vec![0u8; 4096];
            let n = sock.read(&mut buf).await.unwrap_or(0);
            let req = String::from_utf8_lossy(&buf[..n]).to_string();
            let start = req
                .lines()
                .find_map(|l| l.to_ascii_lowercase().strip_prefix("range: bytes=").map(|s| s.to_string()))
                .and_then(|s| s.trim_end_matches('-').parse::<usize>().ok());
            let (status, body) = match (mode, start) {
                (Mode::NoRange, _) | (_, None) => ("200 OK", data.clone()),
                (_, Some(s)) => ("206 Partial Content", data[s..].to_vec()),
            };
            let head = format!(
                "HTTP/1.1 {status}\r\ncontent-length: {}\r\nconnection: close\r\n\r\n",
                body.len()
            );
            let _ = sock.write_all(head.as_bytes()).await;
            let cut = match mode {
                Mode::DropFirstAfter(k) if first => k.min(body.len()),
                _ => body.len(),
            };
            first = false;
            let _ = sock.write_all(&body[..cut]).await;
            let _ = sock.shutdown().await;
        }
    });
    format!("http://{addr}")
}

fn data(n: usize) -> Vec<u8> {
    (0..n).map(|i| (i * 31 % 251) as u8).collect()
}

fn plain_client() -> reqwest::Client {
    reqwest::Client::builder().no_proxy().build().unwrap()
}

#[test]
fn manifest_rejects_unknown_schema_and_path_in_filename() {
    assert!(parse_manifest(r#"{"schema":2,"components":[]}"#).unwrap_err().contains("schema"));
    let bad = r#"{"schema":1,"components":[{"name":"x","version":"1.0.0","platform":"mac-arm64",
        "file":"../evil.tar.gz","size":1,"sha256":"00000000000000000000000000000000000000000000000000000000000000aa"}]}"#;
    assert!(parse_manifest(bad).unwrap_err().contains("不合法"));
    let ok = r#"{"schema":1,"generated_at":"x","components":[{"name":"x","version":"1.0.0","platform":"mac-arm64",
        "file":"x-1.0.0-mac-arm64.tar.gz","size":1,"sha256":"00000000000000000000000000000000000000000000000000000000000000aa"}]}"#;
    let m = parse_manifest(ok).unwrap();
    assert!(find_entry(&m, "x", "mac-arm64").is_some());
    assert!(find_entry(&m, "x", "windows-x64").is_none());
}

#[tokio::test]
async fn download_verifies_and_marks_ready() {
    let d = data(3 << 20);
    let e = entry(&d);
    let base = serve(d.clone(), Mode::Ranged).await;
    let dir = tempfile::tempdir().unwrap();
    assert_eq!(local_status(dir.path(), &e), LocalStatus::Missing);
    let mut phases = vec![];
    download(&plain_client(), &base, dir.path(), &e, Arc::new(AtomicBool::new(false)), |p, _| phases.push(p))
        .await
        .unwrap();
    assert_eq!(std::fs::read(dir.path().join(&e.file)).unwrap(), d);
    assert_eq!(local_status(dir.path(), &e), LocalStatus::Ready);
    assert!(!dir.path().join(format!("{}.part", e.file)).exists());
    assert_eq!(phases.last(), Some(&"done"));
    assert!(phases.contains(&"verifying"));
}

#[tokio::test]
async fn interrupted_download_resumes_with_range() {
    let d = data(2 << 20);
    let e = entry(&d);
    let base = serve(d.clone(), Mode::DropFirstAfter(700_000)).await;
    let dir = tempfile::tempdir().unwrap();
    let err = download(&plain_client(), &base, dir.path(), &e, Arc::new(AtomicBool::new(false)), |_, _| {})
        .await
        .unwrap_err();
    assert!(err.contains("大小不对") || err.contains("中断"), "{err}");
    assert_eq!(local_status(dir.path(), &e), LocalStatus::Partial { downloaded: 700_000 });

    download(&plain_client(), &base, dir.path(), &e, Arc::new(AtomicBool::new(false)), |_, _| {})
        .await
        .unwrap();
    assert_eq!(std::fs::read(dir.path().join(&e.file)).unwrap(), d);
}

#[tokio::test]
async fn server_without_range_support_restarts_from_zero() {
    let d = data(1 << 20);
    let e = entry(&d);
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join(format!("{}.part", e.file)), &d[..1000]).unwrap();
    let base = serve(d.clone(), Mode::NoRange).await;
    download(&plain_client(), &base, dir.path(), &e, Arc::new(AtomicBool::new(false)), |_, _| {})
        .await
        .unwrap();
    // 不能把 200 的全量接在 1000 字节后面
    assert_eq!(std::fs::read(dir.path().join(&e.file)).unwrap(), d);
}

#[tokio::test]
async fn sha256_mismatch_deletes_part_and_never_marks_ready() {
    let d = data(500_000);
    let mut e = entry(&d);
    e.sha256 = "ab".repeat(32);
    let base = serve(d, Mode::Ranged).await;
    let dir = tempfile::tempdir().unwrap();
    let err = download(&plain_client(), &base, dir.path(), &e, Arc::new(AtomicBool::new(false)), |_, _| {})
        .await
        .unwrap_err();
    assert!(err.contains("校验失败"), "{err}");
    assert!(!dir.path().join(&e.file).exists());
    assert!(!dir.path().join(format!("{}.part", e.file)).exists());
    assert_eq!(local_status(dir.path(), &e), LocalStatus::Missing);
}

#[test]
fn it_dropped_offline_package_is_unverified_until_checked() {
    let d = data(10_000);
    let e = entry(&d);
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join(&e.file), &d).unwrap();
    assert_eq!(local_status(dir.path(), &e), LocalStatus::Unverified);
    verify_existing(dir.path(), &e, &AtomicBool::new(false)).unwrap();
    assert_eq!(local_status(dir.path(), &e), LocalStatus::Ready);

    // 同大小的坏文件: verify_existing 必须报错 (sidecar 只比哈希串, 安装前要靠它把关)
    let mut bad = d.clone();
    bad[0] ^= 0xff;
    std::fs::write(dir.path().join(&e.file), &bad).unwrap();
    assert!(verify_existing(dir.path(), &e, &AtomicBool::new(false)).unwrap_err().contains("对不上"));
}

#[test]
fn platform_string_matches_manifest_vocabulary() {
    assert!(["mac-arm64", "mac-x64", "windows-x64"].contains(&current_platform()));
}
