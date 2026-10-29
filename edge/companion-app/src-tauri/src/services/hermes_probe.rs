//! 10/29: 服务状态里「Hermes Agent」那一行的探测。
//!
//! # 原来的问题
//!
//! 1. 只问 `/health` —— hermes 那边是写死的 `{"status":"ok"}`, HTTP 服务一起来就返回, 不看内部状态。
//!    正在停止 (drain 30 秒+)、刚起来还在预热、微信断了、模型没配, 卡片都是绿的。
//! 2. 网关真停了 (端口没人听), 卡片只是黄点「检测失败」+ 一段 reqwest Debug 英文。10/28 鸿波 Windows
//!    机网关一直没起来, 卡片上看不出来。
//! 3. 停了也不说为什么 —— hermes 每次启动 / 退出写 `gateway_state.json` (startup_failed + exit_reason),
//!    而「一启动就退出」(10/28 那次: 依赖环境没建好) 连这个文件都来不及写, 原因只在 gateway-stdio.log。
//!
//! # 现在
//!
//! 有 API_SERVER_KEY (聊天本来就用它) 就问 `/health/detailed` —— hermes 自己的就绪检查 (模型 / 状态库 /
//! 会话存储 / 配置 / 磁盘 / 网关状态) + 各消息平台的连接状态 + 进行中的对话数。全部 ok 才绿, 否则黄并写
//! 原因; 端口没人听 → 红「未运行」+ 上次失败原因。老 hermes 没有 detailed (404) 或没 key 时退回 `/health`。

use std::time::Duration;

use serde_json::Value;

use crate::commands::types::ServiceStatus;

/// 平台连接状态里算「已连接」的值 (同 hermes gateway/readiness.py 的 _CONNECTED_STATES)。
const CONNECTED: [&str; 3] = ["connected", "running", "ok"];
/// 磁盘剩余低于这个才报 (hermes 自己按已用百分比报, 大硬盘上会误报)。
const LOW_DISK_BYTES: u64 = 2 * 1024 * 1024 * 1024;
const GIB: f64 = 1024.0 * 1024.0 * 1024.0;

pub async fn hermes_status() -> Result<ServiceStatus, String> {
    let cfg = super::hermes_api_config::hermes_api_config();
    let url = reqwest::Url::parse(&cfg.url).map_err(|e| format!("Hermes 地址无效: {e}"))?;
    let port = url.port_or_known_default();
    let Some(key) = cfg.key.as_deref() else {
        return basic(&cfg.url, port).await;
    };
    let detailed = format!("{}/health/detailed", cfg.url.trim_end_matches('/'));
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(5))
        .no_proxy()
        .build()
        .map_err(|e| format!("创建检测客户端失败: {e}"))?;
    let response = match client.get(&detailed).bearer_auth(key).send().await {
        Ok(r) => r,
        Err(e) => {
            let f = crate::util::service_probe::classify(&e);
            if f.refused {
                return Ok(ServiceStatus::down(port, not_running_message(&f.human)));
            }
            return Err(format!("{} — {}；运行状态未确认", cfg.url, f.human));
        }
    };
    let code = response.status();
    if code == reqwest::StatusCode::NOT_FOUND {
        return basic(&cfg.url, port).await; // 老 hermes 没有 /health/detailed
    }
    if code == reqwest::StatusCode::UNAUTHORIZED || code == reqwest::StatusCode::FORBIDDEN {
        return Ok(running(port, false,
            "在运行，但 Companion 和 hermes 的 API 密钥对不上（聊天也会失败）—— 重新登录一次可以同步".into()));
    }
    if !code.is_success() {
        return Ok(running(port, false, format!("在运行，但健康检查返回 HTTP {code}")));
    }
    match response.json::<Value>().await {
        Ok(json) => {
            let (healthy, message) = interpret(&json);
            Ok(running(port, healthy, message))
        }
        Err(_) => Ok(running(port, false, "在运行，但健康检查返回的不是 JSON".into())),
    }
}

/// 没 key / 老 hermes: 只能问 `/health` (只说明 HTTP 服务在)。
async fn basic(base: &str, port: Option<u16>) -> Result<ServiceStatus, String> {
    let mut status = crate::util::service_probe::http_health(base, "/health", port, false).await?;
    if !status.running {
        status.message = Some(not_running_message("端口上没有服务在监听"));
    }
    Ok(status)
}

fn running(port: Option<u16>, healthy: bool, message: String) -> ServiceStatus {
    ServiceStatus { running: true, healthy, pid: None, port, message: Some(message) }
}

/// 把 `/health/detailed` 翻成 (是否全绿, 一句人话)。纯函数, 好测。
pub(crate) fn interpret(json: &Value) -> (bool, String) {
    let mut problems: Vec<String> = Vec::new();
    match json.get("gateway_state").and_then(Value::as_str).unwrap_or("running") {
        "running" => {}
        "starting" => problems.push("正在启动".into()),
        "draining" | "stopping" => problems.push("正在停止（等手头的回答写完）".into()),
        "startup_failed" => problems.push("启动失败".into()),
        "degraded" => problems.push("运行异常".into()),
        other => problems.push(format!("状态 {other}")),
    }
    if let Some(checks) = json.pointer("/readiness/checks").and_then(Value::as_object) {
        for (name, check) in checks {
            if matches!(name.as_str(), "gateway" | "background_queues") {
                continue; // 网关状态上面单独说了; 队列只是计数
            }
            let status = check.get("status").and_then(Value::as_str).unwrap_or("ok");
            if name == "disk" {
                // 10/29: hermes 按「已用 ≥ 90%」判 degraded —— 鸿波 Mac 已用 93.6% 但还剩 29.6 GB,
                // 1.1.98 照翻成「磁盘空间不足」亮黄, 误报。只在真剩得少 (< 2 GB) 时才算问题。
                if let Some(free) = check.get("free_bytes").and_then(Value::as_u64) {
                    if free < LOW_DISK_BYTES {
                        problems.push(format!("磁盘只剩 {:.1} GB", free as f64 / GIB));
                    }
                }
                continue;
            }
            if status != "ok" {
                problems.push(match (name.as_str(), status) {
                    ("model", _) => "没有配置模型".into(),
                    ("state_db", _) => "状态数据库读不了".into(),
                    ("session_store", "retrying") => "会话存储正在重连".into(),
                    ("session_store", _) => "会话存储不可用".into(),
                    ("config", _) => "配置文件格式有误".into(),
                    (other, _) => format!("{other} 检查未通过"),
                });
            }
        }
    }
    let mut connected: Vec<&str> = Vec::new();
    if let Some(platforms) = json.get("platforms").and_then(Value::as_object) {
        for (name, p) in platforms {
            if name == "api_server" {
                continue; // 就是我们正在问的这个
            }
            let state = p.get("state").or_else(|| p.get("status")).and_then(Value::as_str).unwrap_or("");
            if state.is_empty() {
                continue;
            }
            let label = platform_label(name);
            if CONNECTED.contains(&state.to_lowercase().as_str()) {
                connected.push(label);
            } else {
                problems.push(format!("{label}未连接"));
            }
        }
    }
    let active = match json.get("active_agents") {
        Some(Value::Number(n)) => n.as_u64().unwrap_or(0),
        Some(Value::Array(a)) => a.len() as u64,
        _ => 0,
    };
    if problems.is_empty() {
        let mut parts = vec!["运行中".to_string()];
        if !connected.is_empty() {
            parts.push(format!("{}已连接", connected.join("、")));
        }
        if active > 0 {
            parts.push(format!("{active} 个对话进行中"));
        }
        (true, parts.join(" · "))
    } else {
        (false, format!("在运行，但{}", problems.join("、")))
    }
}

fn platform_label(name: &str) -> &str {
    match name {
        "weixin" => "微信",
        "wecom" => "企业微信",
        "dingtalk" => "钉钉",
        "feishu" | "lark" => "飞书",
        "telegram" => "Telegram",
        other => other,
    }
}

/// 端口没人听时的说明: 「未运行」+ 上次失败的原因 (能找到的话)。
fn not_running_message(detail: &str) -> String {
    match crate::services::catfish_paths::hermes_home().and_then(|h| last_failure_reason(&h)) {
        Some(reason) => format!("未运行（{detail}）· 上次失败原因：{reason}"),
        None => format!("未运行（{detail}）"),
    }
}

/// 先看 hermes 自己留的 `gateway_state.json` (startup_failed / degraded + exit_reason);
/// 「一启动就退出」来不及写它, 再看 `logs/gateway-stdio.log` 最后一条 `hermes:` 开头的错误 (比状态文件新才算)。
pub(crate) fn last_failure_reason(hermes_home: &std::path::Path) -> Option<String> {
    let state_path = hermes_home.join("gateway_state.json");
    let stdio_path = hermes_home.join("logs").join("gateway-stdio.log");
    let mtime = |p: &std::path::Path| std::fs::metadata(p).and_then(|m| m.modified()).ok();
    let from_state = std::fs::read_to_string(&state_path)
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .and_then(|v| {
            let state = v.get("gateway_state").and_then(Value::as_str)?;
            if !matches!(state, "startup_failed" | "degraded") {
                return None;
            }
            let why = v.get("exit_reason").and_then(Value::as_str)
                .or_else(|| v.get("error_message").and_then(Value::as_str))
                .unwrap_or(state);
            Some(format!("{}（{why}）", if state == "startup_failed" { "启动失败" } else { "运行异常退出" }))
        });
    let stdio_newer = match (mtime(&stdio_path), mtime(&state_path)) {
        (Some(a), Some(b)) => a > b,
        (Some(_), None) => true,
        _ => false,
    };
    if stdio_newer {
        if let Some(line) = last_hermes_error_line(&stdio_path) {
            return Some(line);
        }
    }
    from_state
}

/// stdio 日志末尾 16KB 里最后一条 `hermes:` 开头的行 (hermes CLI 自己报的致命错误)。认得的翻成中文。
fn last_hermes_error_line(path: &std::path::Path) -> Option<String> {
    use std::io::{Read, Seek, SeekFrom};
    let mut file = std::fs::File::open(path).ok()?;
    let len = file.metadata().ok()?.len();
    file.seek(SeekFrom::Start(len.saturating_sub(16 * 1024))).ok()?;
    let mut buf = Vec::new();
    file.read_to_end(&mut buf).ok()?;
    let text = String::from_utf8_lossy(&buf);
    let line = text.lines().rev().map(str::trim).find(|l| l.starts_with("hermes:"))?;
    Some(if line.contains("no dependency environment is committed") {
        "hermes 的依赖环境没建好（重启 Companion 会自动处理）".to_string()
    } else {
        line.chars().take(160).collect()
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn 全部正常_说清连了哪些平台和进行中的对话() {
        let (ok, msg) = interpret(&json!({
            "status": "ok", "gateway_state": "running", "active_agents": 2,
            "readiness": {"status": "ok", "checks": {"model": {"status": "ok"}, "disk": {"status": "ok"},
                          "gateway": {"status": "ok"}, "background_queues": {"status": "ok"}}},
            "platforms": {"api_server": {"state": "connected"}, "weixin": {"state": "connected"}}
        }));
        assert!(ok);
        assert_eq!(msg, "运行中 · 微信已连接 · 2 个对话进行中");
    }

    #[test]
    fn 正在停止_微信断了_模型没配_都要说出来() {
        let (ok, msg) = interpret(&json!({
            "gateway_state": "draining",
            "readiness": {"checks": {"model": {"status": "degraded"}, "gateway": {"status": "ok"}}},
            "platforms": {"weixin": {"state": "disconnected"}}
        }));
        assert!(!ok);
        assert!(msg.contains("正在停止") && msg.contains("没有配置模型") && msg.contains("微信未连接"), "{msg}");
    }

    #[test]
    fn 磁盘按剩余空间判_已用比例高但剩得多不算问题() {
        // 鸿波 Mac: 已用 93.6% (hermes 判 degraded), 剩 29.6 GB
        let (ok, msg) = interpret(&json!({"gateway_state": "running", "readiness": {"checks": {
            "disk": {"status": "degraded", "used_percent": 93.6, "free_bytes": 31_782_174_720u64}}}}));
        assert!(ok, "{msg}");
        let (ok, msg) = interpret(&json!({"gateway_state": "running", "readiness": {"checks": {
            "disk": {"status": "degraded", "used_percent": 99.7, "free_bytes": 1_073_741_824u64}}}}));
        assert!(!ok && msg.contains("磁盘只剩 1.0 GB"), "{msg}");
    }

    #[test]
    fn 刚起来还在预热_不算绿() {
        let (ok, msg) = interpret(&json!({"gateway_state": "starting", "readiness": {"checks": {}}}));
        assert!(!ok && msg.contains("正在启动"));
    }

    #[test]
    fn 一启动就退出_从_stdio_日志认出原因() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("logs")).unwrap();
        std::fs::write(dir.path().join("gateway_state.json"), r#"{"gateway_state": "stopped"}"#).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(20));
        std::fs::write(
            dir.path().join("logs").join("gateway-stdio.log"),
            "2026-10-28 17:26:28,378 WARNING cron.jobs: x\nhermes: no dependency environment is committed for this install; run `hermes pm repair`\n",
        )
        .unwrap();
        assert_eq!(last_failure_reason(dir.path()).as_deref(), Some("hermes 的依赖环境没建好（重启 Companion 会自动处理）"));
    }

    #[test]
    fn 状态文件写着启动失败_就用它() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            dir.path().join("gateway_state.json"),
            r#"{"gateway_state": "startup_failed", "exit_reason": "port 8642 in use"}"#,
        )
        .unwrap();
        assert_eq!(last_failure_reason(dir.path()).as_deref(), Some("启动失败（port 8642 in use）"));
        let clean = tempfile::tempdir().unwrap();
        assert!(last_failure_reason(clean.path()).is_none());
    }
}
