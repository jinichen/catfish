//! Protocol-first, read-only diagnostics. Timeouts are not proof that a process is dead.
use std::time::Duration;
use crate::commands::types::ServiceStatus;

pub async fn http_health(base: &str, path: &str, port: Option<u16>, cdp: bool) -> Result<ServiceStatus, String> {
    let url = format!("{}{path}", base.trim_end_matches('/'));
    let result = async {
        let client = super::http_client::trust_central(reqwest::Client::builder())
            .timeout(Duration::from_secs(5))
            .build().map_err(|e| format!("创建检测客户端失败: {e}"))?;
        let response = client.get(&url).send().await
            .map_err(|e| format!("连接检测失败: {e:?}"))?;
        if !response.status().is_success() {
            return Ok::<_, String>((false, format!("HTTP {}（服务有响应）", response.status())));
        }
        let json: serde_json::Value = response.json().await
            .map_err(|e| format!("服务有响应，但健康接口不是有效 JSON: {e}"))?;
        let valid = if cdp {
            json.get("webSocketDebuggerUrl").and_then(|v| v.as_str())
                .is_some_and(|s| s.starts_with("ws://") || s.starts_with("wss://"))
        } else {
            json.get("status").and_then(|v| v.as_str())
                .is_some_and(|s| matches!(s, "ok" | "healthy" | "running"))
                || json.get("ok").and_then(|v| v.as_bool()) == Some(true)
        };
        Ok((valid, if valid { "健康接口已就绪".into() } else { "健康响应不符合预期，请查看服务日志".into() }))
    }.await;
    match result {
        Ok((healthy, message)) => Ok(ServiceStatus {
            running: true, healthy, pid: None, port,
            message: Some(format!("{url} — {message}")),
        }),
        // Do not infer process state from TLS, authentication, DNS or timeout failures.
        Err(error) => Err(format!("{url} — {error}；运行状态未确认")),
    }
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
}
