//! 扫码直传跟中央说话的那一层 (central device_transfer_router.py): 开 / 查 / 一块块传 / 一块块收。
//!
//! 连哪个中央、用谁的令牌放在 [`Conn`] 里: Companion 里用当前登录 ([`Conn::live`]);
//! 端到端测试指到本机起的临时中央 ([`Conn::fixed`], tests_direct_snapshots.rs)。

use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use serde_json::{json, Value};

pub const CHUNK: u64 = 1024 * 1024;

#[derive(Clone)]
pub struct Conn {
    base: String,
    /// None = 每次取当前登录的令牌 (快过期了自动刷新)
    token: Option<String>,
}

impl Conn {
    pub fn live() -> Conn {
        Conn { base: crate::services::endpoints::endpoints().gateway_base().trim_end_matches('/').to_string(), token: None }
    }

    #[cfg(test)]
    pub fn fixed(base: &str, token: &str) -> Conn {
        Conn { base: base.trim_end_matches('/').to_string(), token: Some(token.to_string()) }
    }

    pub fn base(&self) -> &str {
        &self.base
    }

    async fn client(&self, timeout: u64) -> Result<(reqwest::Client, String), String> {
        let token = match &self.token {
            Some(t) => t.clone(),
            None => crate::services::oauth::ensure_fresh_access_token().await.ok_or("没登录 (或登录过期了), 请先登录")?,
        };
        let c = crate::util::http_client::trust_central(reqwest::Client::builder().timeout(Duration::from_secs(timeout)))
            .build()
            .map_err(|e| e.to_string())?;
        Ok((c, token))
    }

    /// JSON 请求; 4xx/5xx 转成人话。
    pub async fn api(&self, method: reqwest::Method, path: &str, body: Option<Value>) -> Result<Value, String> {
        let (c, token) = self.client(30).await?;
        let mut req = c.request(method, format!("{}{path}", self.base)).bearer_auth(token);
        if let Some(b) = body {
            req = req.json(&b);
        }
        let r = req.send().await.map_err(|e| format!("连不上公司服务器: {e}"))?;
        if !r.status().is_success() {
            return Err(err_of(r).await);
        }
        r.json().await.map_err(|e| e.to_string())
    }

    /// PUT 一块: Ok(true) 传上了, Ok(false) 窗口满了 (429) 等一下再传。
    async fn put_chunk(&self, id: &str, i: u64, data: Vec<u8>) -> Result<bool, String> {
        let (c, token) = self.client(120).await?;
        let r = c
            .put(format!("{}/api/device-transfer/{id}/chunks/{i}", self.base))
            .bearer_auth(token)
            .header("content-type", "application/octet-stream")
            .body(data)
            .send()
            .await
            .map_err(|e| format!("连不上公司服务器: {e}"))?;
        match r.status().as_u16() {
            200..=299 => Ok(true),
            429 => Ok(false),
            _ => Err(err_of(r).await),
        }
    }

    /// GET 一块: Ok(None) 还没到。
    async fn get_chunk(&self, id: &str, i: u64) -> Result<Option<Vec<u8>>, String> {
        let (c, token) = self.client(120).await?;
        let r = c
            .get(format!("{}/api/device-transfer/{id}/chunks/{i}", self.base))
            .bearer_auth(token)
            .send()
            .await
            .map_err(|e| format!("连不上公司服务器: {e}"))?;
        match r.status().as_u16() {
            200..=299 => Ok(Some(r.bytes().await.map_err(|e| e.to_string())?.to_vec())),
            404 => Ok(None),
            _ => Err(err_of(r).await),
        }
    }

    pub async fn info(&self, id: &str) -> Result<Value, String> {
        self.api(reqwest::Method::GET, &format!("/api/device-transfer/{id}"), None).await
    }

    pub async fn cancel(&self, id: &str) {
        let _ = self.api(reqwest::Method::POST, &format!("/api/device-transfer/{id}/cancel"), None).await;
    }
}

/// 中央回的 4xx/5xx → 人话 (FastAPI 的 detail)。
async fn err_of(r: reqwest::Response) -> String {
    let code = r.status().as_u16();
    let detail = r.json::<Value>().await.ok().and_then(|v| v.get("detail").and_then(Value::as_str).map(str::to_string));
    match (code, detail.as_deref()) {
        // 老中央没有这组接口: FastAPI 回的是 {"detail": "Not Found"}
        (404, None | Some("Not Found")) => "公司服务器还不支持换机直传, 请让管理员升级中央 (或者先用「导出成文件」)".into(),
        (_, Some(d)) => d.to_string(),
        (c, None) => format!("公司服务器出错 ({c})"),
    }
}

/// 网络抖一下不算失败: 连续出错 5 次才放弃。
pub struct Retry(u32);

impl Retry {
    pub fn new() -> Retry {
        Retry(0)
    }

    pub async fn again(&mut self, e: String) -> Result<(), String> {
        self.0 += 1;
        if self.0 >= 5 {
            return Err(e);
        }
        tokio::time::sleep(Duration::from_secs(2 * self.0 as u64)).await;
        Ok(())
    }
}

pub fn s(v: &Value, k: &str) -> String {
    v.get(k).and_then(Value::as_str).unwrap_or("").to_string()
}

pub fn u(v: &Value, k: &str) -> u64 {
    v.get(k).and_then(Value::as_u64).unwrap_or(0)
}

/// 收的进度
pub enum Got {
    Started { size: u64, n: u64, from: String },
    Chunk(u64),
}

/// 新电脑: 等旧电脑开始传 → 一块块收进 `part`, 收一块删一块 → 收齐报 done。被叫停返回 Ok(None)。
pub async fn download(conn: &Conn, id: &str, part: &Path, stop: &AtomicBool, on: impl Fn(Got)) -> Result<Option<u64>, String> {
    let mut retry = Retry::new();
    let (size, n) = loop {
        if stop.load(Ordering::SeqCst) {
            return Ok(None);
        }
        match conn.info(id).await {
            Ok(v) => {
                retry = Retry::new();
                match s(&v, "state").as_str() {
                    "waiting" => {}
                    "sending" => {
                        let (size, n) = (u(&v, "size"), u(&v, "n"));
                        on(Got::Started { size, n, from: s(&v, "from_device") });
                        break (size, n);
                    }
                    "expired" => return Err("等了半小时旧电脑没开始传, 这次作废了。要传的话重新开始".into()),
                    other => return Err(format!("这次直传已经结束 ({other})")),
                }
            }
            Err(e) => retry.again(e).await?,
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    };
    let r = async {
        let mut out = File::create(part).map_err(|e| format!("写临时文件失败: {e}"))?;
        for i in 0..n {
            let mut retry = Retry::new();
            loop {
                if stop.load(Ordering::SeqCst) {
                    return Ok(None);
                }
                match conn.get_chunk(id, i).await {
                    Ok(Some(data)) => {
                        out.write_all(&data).map_err(|e| format!("写临时文件失败 (磁盘满了?): {e}"))?;
                        conn.api(reqwest::Method::POST, &format!("/api/device-transfer/{id}/chunks/{i}/ack"), None).await?;
                        break;
                    }
                    Ok(None) => tokio::time::sleep(Duration::from_millis(300)).await,
                    Err(e) => retry.again(e).await?,
                }
            }
            on(Got::Chunk(i + 1));
        }
        out.flush().map_err(|e| e.to_string())?;
        drop(out);
        if std::fs::metadata(part).map(|m| m.len()).unwrap_or(0) != size {
            return Err("收到的大小跟旧电脑说的不一样, 请重新传".to_string());
        }
        conn.api(reqwest::Method::POST, &format!("/api/device-transfer/{id}/done"), None).await?;
        Ok(Some(size))
    }
    .await;
    if !matches!(r, Ok(Some(_))) {
        let _ = std::fs::remove_file(part);
    }
    r
}

/// 发的进度
pub enum Sent {
    Started { size: u64, n: u64 },
    Chunk(u64),
    Acked(u64),
}

/// 旧电脑: 开始 → 把 `pkg` 一块块传上去 (窗口满了等新电脑取)。不等新电脑收完。被叫停返回 Ok(None)。
pub async fn upload(conn: &Conn, id: &str, pkg: &Path, from_device: &str, stop: &AtomicBool, on: impl Fn(Sent)) -> Result<Option<u64>, String> {
    let size = std::fs::metadata(pkg).map_err(|e| e.to_string())?.len();
    let n = size.div_ceil(CHUNK);
    conn.api(reqwest::Method::POST, &format!("/api/device-transfer/{id}/start"), Some(json!({ "size": size, "n": n, "from_device": from_device })))
        .await?;
    on(Sent::Started { size, n });
    let mut f = OpenOptions::new().read(true).open(pkg).map_err(|e| e.to_string())?;
    for i in 0..n {
        let len = CHUNK.min(size - i * CHUNK) as usize;
        let mut buf = vec![0u8; len];
        f.seek(SeekFrom::Start(i * CHUNK)).and_then(|_| f.read_exact(&mut buf)).map_err(|e| e.to_string())?;
        let mut retry = Retry::new();
        loop {
            if stop.load(Ordering::SeqCst) {
                return Ok(None);
            }
            match conn.put_chunk(id, i, buf.clone()).await {
                Ok(true) => break,
                Ok(false) => tokio::time::sleep(Duration::from_millis(400)).await, // 窗口满了, 等新电脑取
                Err(e) => retry.again(e).await?,
            }
        }
        on(Sent::Chunk(i + 1));
        if i % 16 == 15 {
            if let Ok(v) = conn.info(id).await {
                on(Sent::Acked(u(&v, "acked")));
            }
        }
    }
    Ok(Some(size))
}
