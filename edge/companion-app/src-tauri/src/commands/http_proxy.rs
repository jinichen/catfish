//! P3.6.X (7/18 鸿波): Rust reqwest HTTP 代理 · 让前端 fetch 走 Rust · CSP connect-src 保持严格.
//!
//! 背景 (BL-CSP-PROXY, 7/18 鸿波 catch 面板改 199 chat 全挂):
//!   Tauri CSP connect-src 白名单只有 http://127.0.0.1:* + http://localhost:*.
//!   达华 POC 员工输达华 gateway IP (e.g. http://10.10.40.50:8999) 时 WebView fetch 被
//!   CSP 拦, 返 TypeError: Load failed (outbound_log.db 4085 records 全是这个 error).
//!
//! 方案 (B'):
//!   前端所有 fetch 走 tauri command → Rust reqwest 转发 → 返给前端.
//!   CSP 保持严格 (只 self / tauri: / ipc: / localhost) — 无外网白名单.
//!   Rust 端 reqwest 不受 CSP 限制, 能连任意 URL.
//!
//! 命令:
//!   http_proxy         — 一次性 request/response (JSON 类, 非流)
//!   http_proxy_abortable — 可由 request_id 取消的一次性请求 (advisor 等长调用)
//!   http_proxy_stream  — SSE streaming (chat completions 用), Tauri event bridge
//!   http_proxy_abort   — 取消进行中的 stream / abortable request
//!
//! Streaming event 命名:
//!   http_proxy_chunk_{request_id} — data chunk (可能多次)
//!   http_proxy_done_{request_id}  — 结束
//!   http_proxy_error_{request_id} — 网络挂
//!
//! ⚠ transparent_log (outbound_log.db) 由前端 wrapper 记 (me.ts logOutbound), Rust 端不管.

use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

use base64::Engine as _;
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter};
use tokio::sync::oneshot;

// ─────────────────────────────────────────────────────────────────────────────
// Request/Response types
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HttpProxyRequest {
    /// 完整 URL (含 scheme + host + port + path + query).
    pub url: String,
    /// GET / POST / PUT / DELETE 等. 默认 GET.
    #[serde(default = "default_method")]
    pub method: String,
    /// HTTP headers.
    #[serde(default)]
    pub headers: HashMap<String, String>,
    /// Body. 若 `body_base64=true` 则内容是 base64 编码的二进制; 否则 UTF-8 字符串.
    #[serde(default)]
    pub body: Option<String>,
    /// body 是否 base64. 默认 false (即 UTF-8 字符串 body).
    #[serde(default)]
    pub body_base64: bool,
    /// timeout 毫秒. 默认 30s (非 stream) / 600s (stream 单独默认).
    #[serde(default)]
    pub timeout_ms: Option<u64>,
}

fn default_method() -> String {
    "GET".to_string()
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HttpProxyResponse {
    pub status: u16,
    pub headers: HashMap<String, String>,
    /// 若 `body_base64=true` 则 base64 encoded; 否则 UTF-8 字符串.
    /// 二进制 body (非 UTF-8) 自动走 base64.
    pub body: String,
    pub body_base64: bool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HttpProxyStreamStart {
    pub status: u16,
    pub headers: HashMap<String, String>,
}

// ─────────────────────────────────────────────────────────────────────────────
// Global abort registry — request_id → oneshot sender
//
// 前端 chat.ts abort 时调 http_proxy_abort(request_id) → send () 触发 stream loop 退出.
// 参照 services/email_scheduler.rs OnceLock<Mutex<HashMap>> pattern.
// ─────────────────────────────────────────────────────────────────────────────

static ACTIVE_STREAMS: OnceLock<Mutex<HashMap<String, oneshot::Sender<()>>>> = OnceLock::new();

fn active_streams() -> &'static Mutex<HashMap<String, oneshot::Sender<()>>> {
    ACTIVE_STREAMS.get_or_init(|| Mutex::new(HashMap::new()))
}

/// 注册新 stream · 返 receiver 供后台 loop select!.
fn register_stream(request_id: &str) -> oneshot::Receiver<()> {
    let (tx, rx) = oneshot::channel();
    let mut m = active_streams()
        .lock()
        .expect("ACTIVE_STREAMS mutex poisoned");
    // 若同 request_id 已注册 (前端 bug 复用 id), 老的 sender drop → 老 rx 收到 Cancelled
    m.insert(request_id.to_string(), tx);
    rx
}

/// 注销 (stream 完成 · 无论正常还是 error). 幂等.
fn unregister_stream(request_id: &str) {
    if let Ok(mut m) = active_streams().lock() {
        m.remove(request_id);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

fn build_client(timeout_ms: u64) -> Result<reqwest::Client, String> {
    // P3.5.80 (7/28 鸿波达华现场): 这里原来有一句注释写着
    //     "5/19 BL-COMPANION-AUTH: ... 不 verify 证书严"
    // 但**代码里从来没有对应的那一行** —— builder 只设了 timeout, reqwest 默认
    // 是完整证书校验. 注释描述了一个不存在的行为, 于是中央端启用 HTTPS 后
    // 没人怀疑到 TLS 上:
    //   仪表盘「中央门户」显示"连不上", 同一地址浏览器打得开、curl -k 也通,
    //   因为门户探活和服务器连通性检测都走本函数建的 client, 而中央端用的是
    //   自签证书 —— 浏览器点一次"继续前往"就记住了, reqwest 不吃这套.
    //
    // 现在改成显式的信任模型: 只额外信任 IT 放到 ~/.catfish/server-ca.pem
    // 的那张证书, 公网请求照常严格校验. 详见 util::http_client.
    crate::util::http_client::trust_central(
        reqwest::Client::builder().timeout(Duration::from_millis(timeout_ms)),
    )
    .build()
    .map_err(|e| format!("reqwest build 失败: {e}"))
}

/// 把错误的整条 source 链拼成一行 (P3.5.80 · 7/28 鸿波达华现场).
///
/// 为什么需要: `reqwest::Error` 的 `Display` **只打最外层**. 一次证书失败
/// 打出来只有
///     error sending request for url (https://192.168.31.199/)
/// 真正的原因 —— 证书不受信 / DNS 解不出 / 连接被拒 / 代理挡了 —— 全在
/// `.source()` 链里. 之前 `format!("请求失败: {e}")` 把这些整个丢了.
///
/// 后果不只是日志难看: 面板拿到的错误里没有任何可判别的信息, 只能一律显示
/// "连不上", 说不出为什么, 员工和 IT 都无从下手. 现场表现就是
/// "浏览器打得开、面板说连不上", 排查方向一开始就被带偏.
fn error_chain(e: &(dyn std::error::Error + 'static)) -> String {
    let mut parts = vec![e.to_string()];
    let mut src = e.source();
    // 设上限防个别库把错误链做成环 / 超长
    let mut depth = 0;
    while let Some(s) = src {
        parts.push(s.to_string());
        src = s.source();
        depth += 1;
        if depth >= 8 {
            parts.push("…(错误链过长, 已截断)".to_string());
            break;
        }
    }
    parts.join(" ← ")
}

fn parse_method(m: &str) -> Result<reqwest::Method, String> {
    reqwest::Method::from_bytes(m.as_bytes()).map_err(|e| format!("invalid method {m}: {e}"))
}

fn build_request_body(req: &HttpProxyRequest) -> Result<Option<Vec<u8>>, String> {
    let Some(body) = &req.body else {
        return Ok(None);
    };
    if req.body_base64 {
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(body)
            .map_err(|e| format!("body base64 decode 失败: {e}"))?;
        Ok(Some(bytes))
    } else {
        Ok(Some(body.as_bytes().to_vec()))
    }
}

fn collect_response_headers(resp: &reqwest::Response) -> HashMap<String, String> {
    let mut headers = HashMap::new();
    for (k, v) in resp.headers() {
        if let Ok(v_str) = v.to_str() {
            headers.insert(k.to_string(), v_str.to_string());
        }
    }
    headers
}

fn auth_log_state(headers: &HashMap<String, String>) -> &'static str {
    if headers.keys().any(|key| key.eq_ignore_ascii_case("authorization")) {
        "<PRESENT>"
    } else {
        "<MISSING>"
    }
}

/// 日志不记录 query / fragment / URL 凭据，避免 OAuth code、签名参数等落盘。
fn safe_url_for_log(raw: &str) -> String {
    let Ok(mut parsed) = url::Url::parse(raw) else {
        return "<invalid-url>".to_string();
    };
    parsed.set_query(None);
    parsed.set_fragment(None);
    let _ = parsed.set_username("");
    let _ = parsed.set_password(None);
    parsed.to_string()
}

async fn perform_http_request(req: &HttpProxyRequest) -> Result<HttpProxyResponse, String> {
    let timeout_ms = req.timeout_ms.unwrap_or(30_000);
    let client = build_client(timeout_ms)?;
    let method = parse_method(&req.method)?;

    let header_keys: Vec<&str> = req.headers.keys().map(|k| k.as_str()).collect();
    log::info!(
        "[http_proxy] {} {} · headers_keys={:?} · auth={}",
        req.method,
        safe_url_for_log(&req.url),
        header_keys,
        auth_log_state(&req.headers)
    );

    let mut request = client.request(method, &req.url);
    for (k, v) in &req.headers {
        request = request.header(k, v);
    }
    if let Some(body_bytes) = build_request_body(req)? {
        request = request.body(body_bytes);
    }

    let resp = request.send().await.map_err(|e| {
        let detail = error_chain(&e);
        log::warn!(
            "[http_proxy] {} {} 失败: {detail}",
            req.method,
            safe_url_for_log(&req.url)
        );
        format!("请求失败: {detail}")
    })?;
    let status = resp.status().as_u16();
    let headers = collect_response_headers(&resp);
    let body_bytes = resp
        .bytes()
        .await
        .map_err(|e| format!("读 body 失败: {e}"))?;
    let (body, body_base64) = match std::str::from_utf8(&body_bytes) {
        Ok(s) => (s.to_string(), false),
        Err(_) => (
            base64::engine::general_purpose::STANDARD.encode(&body_bytes),
            true,
        ),
    };
    Ok(HttpProxyResponse {
        status,
        headers,
        body,
        body_base64,
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// http_proxy (非 stream)
// ─────────────────────────────────────────────────────────────────────────────

/// 一次性 request/response 代理. 用于非流场景 (fetchCatalog / fetchMe / etc).
///
/// 返完整 body. 若 body 是二进制 (非 UTF-8), 自动 base64 编码.
#[tauri::command]
pub async fn http_proxy(req: HttpProxyRequest) -> Result<HttpProxyResponse, String> {
    perform_http_request(&req).await
}

/// 可取消的一次性请求。用于 `stream:false` 但可能运行数分钟的 advisor 调用。
#[tauri::command]
pub async fn http_proxy_abortable(
    req: HttpProxyRequest,
    request_id: String,
) -> Result<HttpProxyResponse, String> {
    let mut cancel_rx = register_stream(&request_id);
    let result = tokio::select! {
        biased;
        _ = &mut cancel_rx => {
            log::info!("[http_proxy] request {} aborted by client", request_id);
            Err("请求已取消".to_string())
        }
        response = perform_http_request(&req) => response,
    };
    unregister_stream(&request_id);
    result
}

// ─────────────────────────────────────────────────────────────────────────────
// http_proxy_stream (SSE)
// ─────────────────────────────────────────────────────────────────────────────

/// SSE 流式代理. 用于 /v1/chat/completions.
///
/// 立即返 `{ status, headers }` 让前端能判 4xx/5xx 早退. 后台 tokio 任务读 chunk,
/// 逐块 emit `http_proxy_chunk_{request_id}` event. 结束 emit `_done_`, 挂 emit `_error_`.
///
/// 前端**必须** listen 这 3 个 event, 再 invoke, 否则可能漏 chunk (Tauri event 是 fire-and-forget).
#[tauri::command]
pub async fn http_proxy_stream(
    app: AppHandle,
    req: HttpProxyRequest,
    request_id: String,
) -> Result<HttpProxyStreamStart, String> {
    // SSE 场景 default 10 分钟 — chat.ts 里再套 idle timer.
    let timeout_ms = req.timeout_ms.unwrap_or(600_000);
    let client = build_client(timeout_ms)?;
    let method = parse_method(&req.method)?;

    // 只记录 Authorization 是否存在，绝不记录 token 内容。
    let header_keys: Vec<&str> = req.headers.keys().map(|k| k.as_str()).collect();
    log::info!(
        "[http_proxy_stream] {} {} · headers_keys={:?} · auth={} · req_id={}",
        req.method,
        safe_url_for_log(&req.url),
        header_keys,
        auth_log_state(&req.headers),
        request_id
    );

    let mut request = client.request(method, &req.url);
    for (k, v) in &req.headers {
        request = request.header(k, v);
    }
    if let Some(body_bytes) = build_request_body(&req)? {
        request = request.body(body_bytes);
    }

    let resp = request
        .send()
        .await
        .map_err(|e| {
            // P3.5.80 (7/28): 用 error_chain 而不是 {e} —— 见该函数说明.
            let detail = error_chain(&e);
            log::warn!(
                "[http_proxy] {} {} 失败: {detail}",
                req.method,
                safe_url_for_log(&req.url)
            );
            format!("请求失败: {detail}")
        })?;

    let status = resp.status().as_u16();
    let headers = collect_response_headers(&resp);

    let mut cancel_rx = register_stream(&request_id);

    // 后台读 stream, emit events.
    let chunk_event = format!("http_proxy_chunk_{request_id}");
    let done_event = format!("http_proxy_done_{request_id}");
    let err_event = format!("http_proxy_error_{request_id}");
    let request_id_owned = request_id.clone();

    tokio::spawn(async move {
        let mut resp = resp;
        loop {
            tokio::select! {
                biased;
                // abort 触发 → sender.send(()) 或 sender drop → rx 到 Ok/Err 都退出.
                // Response drop 时 reqwest 关连接.
                _ = &mut cancel_rx => {
                    log::info!("[http_proxy] stream {} aborted by client", request_id_owned);
                    let _ = app.emit(&done_event, ());
                    break;
                }
                chunk = resp.chunk() => match chunk {
                    Ok(Some(bytes)) => {
                        // SSE 是 text/event-stream, 直接 UTF-8. 非 UTF-8 fallback base64
                        // (不该走到这里, 但防死).
                        let chunk_str = match std::str::from_utf8(&bytes) {
                            Ok(s) => s.to_string(),
                            Err(_) => {
                                log::warn!("[http_proxy] stream {} 非 UTF-8 chunk, 走 base64", request_id_owned);
                                base64::engine::general_purpose::STANDARD.encode(&bytes)
                            }
                        };
                        if let Err(e) = app.emit(&chunk_event, chunk_str) {
                            log::warn!("[http_proxy] emit chunk 失败: {e}");
                        }
                    }
                    Ok(None) => {
                        // stream 自然结束
                        let _ = app.emit(&done_event, ());
                        break;
                    }
                    Err(e) => {
                        let msg = format!("stream 读挂: {e}");
                        log::warn!("[http_proxy] {}", msg);
                        let _ = app.emit(&err_event, msg);
                        break;
                    }
                }
            }
        }
        unregister_stream(&request_id_owned);
    });

    Ok(HttpProxyStreamStart { status, headers })
}

// ─────────────────────────────────────────────────────────────────────────────
// http_proxy_abort
// ─────────────────────────────────────────────────────────────────────────────

/// 取消进行中的 stream 或一次性长请求. 前端 AbortSignal 触发时调.
///
/// 幂等: 已结束 / 不存在的 request_id 也返 OK.
#[tauri::command]
pub fn http_proxy_abort(request_id: String) -> Result<(), String> {
    let mut m = active_streams()
        .lock()
        .map_err(|e| format!("ACTIVE_STREAMS mutex 挂: {e}"))?;
    if let Some(sender) = m.remove(&request_id) {
        // send() 失败 (rx 已 drop, stream 早退) 也 OK — 幂等.
        let _ = sender.send(());
        log::info!("[http_proxy] abort stream {}", request_id);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn auth_log_never_contains_token() {
        let mut headers = HashMap::new();
        headers.insert("Authorization".to_string(), "Bearer secret-token".to_string());
        assert_eq!(auth_log_state(&headers), "<PRESENT>");
        assert!(!auth_log_state(&headers).contains("secret-token"));
    }

    #[test]
    fn logged_url_drops_query_fragment_and_credentials() {
        let safe = safe_url_for_log(
            "https://alice:secret@example.com/oauth/callback?code=private#token",
        );
        assert_eq!(safe, "https://example.com/oauth/callback");
    }
}
