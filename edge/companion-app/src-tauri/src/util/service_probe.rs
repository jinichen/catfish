//! Protocol-first, read-only diagnostics. Timeouts are not proof that a process is dead.
//!
//! 10/29: 唯一能确定「没在跑」的是**连接被拒绝** (端口上没人监听 —— 本机就是进程不在) —— 这时直接判
//! `running: false` (界面红点「未运行」)。超时、证书、DNS 等说不准的仍然是错误 (黄点「状态未确认」),
//! 但给一句中文, 不再把 `reqwest::Error { kind: Request, … }` 整段 Debug 贴到员工界面上。
use std::time::Duration;
use crate::commands::types::ServiceStatus;

/// 连接失败的分类。`refused = true` 表示端口确定没人监听。
pub(crate) struct ConnectFailure {
    pub refused: bool,
    pub human: String,
}

/// 把 reqwest 的错误翻成一句中文, 并认出「连接被拒绝」(沿 source 链找 io::ErrorKind::ConnectionRefused)。
pub(crate) fn classify(error: &reqwest::Error) -> ConnectFailure {
    let mut refused = false;
    let mut root = error.to_string();
    let mut source: Option<&(dyn std::error::Error + 'static)> = std::error::Error::source(error);
    while let Some(e) = source {
        if let Some(io) = e.downcast_ref::<std::io::Error>() {
            if io.kind() == std::io::ErrorKind::ConnectionRefused {
                refused = true;
            }
        }
        root = e.to_string();
        source = e.source();
    }
    let lower = root.to_lowercase();
    let human = if refused {
        "端口上没有服务在监听".to_string()
    } else if error.is_timeout() {
        "连接超时（5 秒没有回应）".to_string()
    } else if lower.contains("certificate") || lower.contains("tls") || lower.contains("ssl") {
        "证书校验失败（服务器证书不受信任或已过期）".to_string()
    } else if lower.contains("dns") || lower.contains("resolve") || lower.contains("lookup") {
        "找不到这个服务器地址（域名解析失败）".to_string()
    } else {
        format!("连接失败（{root}）")
    };
    ConnectFailure { refused, human }
}

pub async fn http_health(base: &str, path: &str, port: Option<u16>, cdp: bool) -> Result<ServiceStatus, String> {
    let url = format!("{}{path}", base.trim_end_matches('/'));
    // no_proxy: 本机服务和内网中央服务都不该走员工机上配的代理 (原中央探测就是这么做的)
    let client = super::http_client::trust_central(reqwest::Client::builder().no_proxy())
        .timeout(Duration::from_secs(5))
        .build().map_err(|e| format!("创建检测客户端失败: {e}"))?;
    let response = match client.get(&url).send().await {
        Ok(r) => r,
        Err(e) => {
            let f = classify(&e);
            if f.refused {
                return Ok(ServiceStatus::down(port, format!("未运行（{url} {}）", f.human)));
            }
            // Do not infer process state from TLS, authentication, DNS or timeout failures.
            return Err(format!("{url} — {}；运行状态未确认", f.human));
        }
    };
    let (healthy, message) = if !response.status().is_success() {
        (false, format!("HTTP {}（服务有响应）", response.status()))
    } else {
        match response.json::<serde_json::Value>().await {
            Err(_) => (false, "服务有响应，但健康接口返回的不是 JSON".to_string()),
            Ok(json) => {
                let valid = if cdp {
                    json.get("webSocketDebuggerUrl").and_then(|v| v.as_str())
                        .is_some_and(|s| s.starts_with("ws://") || s.starts_with("wss://"))
                } else {
                    json.get("status").and_then(|v| v.as_str())
                        .is_some_and(|s| matches!(s, "ok" | "healthy" | "running"))
                        || json.get("ok").and_then(|v| v.as_bool()) == Some(true)
                };
                (valid, if valid { "健康接口已就绪".into() } else { "健康响应不符合预期，请查看服务日志".into() })
            }
        }
    };
    Ok(ServiceStatus { running: true, healthy, pid: None, port, message: Some(format!("{url} — {message}")) })
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    async fn server(status: &str, body: &str) -> String {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let response = format!("HTTP/1.1 {status}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
        tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut buffer = [0; 2048];
            let _ = stream.read(&mut buffer).await;
            stream.write_all(response.as_bytes()).await.unwrap();
        });
        format!("http://localhost:{}", address.port())
    }

    #[tokio::test]
    async fn localhost_ipv4_server_is_healthy() {
        let base = server("200 OK", r#"{"status":"ok"}"#).await;
        assert!(http_health(&base, "/health", None, false).await.unwrap().healthy);
    }
    #[tokio::test]
    async fn unauthorized_is_running_not_healthy() {
        let base = server("401 Unauthorized", "{}").await;
        let status = http_health(&base, "/health", None, false).await.unwrap();
        assert!(status.running && !status.healthy);
        assert!(status.message.unwrap().contains("401"));
    }
    #[tokio::test]
    async fn ordinary_web_page_is_not_chrome() {
        let base = server("200 OK", "{}").await;
        assert!(!http_health(&base, "/json/version", None, true).await.unwrap().healthy);
    }
    #[tokio::test]
    async fn valid_cdp_without_pages_is_healthy() {
        let base = server("200 OK", r#"{"webSocketDebuggerUrl":"ws://localhost/devtools/browser/1"}"#).await;
        assert!(http_health(&base, "/json/version", None, true).await.unwrap().healthy);
    }

    #[tokio::test]
    async fn refused_port_is_not_running_with_chinese_message() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        drop(listener); // 端口没人听 = 服务确实没在跑
        let status = http_health(&format!("http://127.0.0.1:{port}"), "/health", Some(port), false).await.unwrap();
        assert!(!status.running && !status.healthy);
        let message = status.message.unwrap();
        assert!(message.contains("未运行") && message.contains("没有服务在监听"), "{message}");
        assert!(!message.contains("reqwest::Error"), "{message}");
    }
}
