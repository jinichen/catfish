//! 员工接入链接 `catfish://connect?web=…&code=…` (10/14 鸿波「地址和接入码合成一个链接」)。
//!
//! 管理员在门户「用户」页 (或 setup 脚本输出) 复制链接发给员工; 员工点一下, Companion 弹出确认框,
//! 地址和接入码都填好了, 点「连接」就自动导入证书 (接入码核对, 见 server_pairing.rs) 并保存服务器配置。
//! **不会不经确认就改配置** —— 链接谁都能造, 必须让员工看清是连到哪台服务器。
//!
//! 链接从三条路进来:
//!   · macOS: 系统把链接交给已开着 / 刚启动的 app → deep-link 插件 on_open_url
//!   · Windows 冷启动: 链接在命令行参数里 (MSI 写的 HKCU\Software\Classes\catfish)
//!   · Windows 已开着: 第二个进程拿不到单实例锁, 把链接写进 activate 文件交给第一个
//!     (services::windows_lifecycle), 不另起窗口
//! 不管哪条, 都先存进 PENDING 再发事件 —— 冷启动时前端还没挂上监听, 起来后自己来取。
use std::sync::Mutex;

use serde::Serialize;
use tauri::Emitter;

pub const EVENT: &str = "catfish-connect-link";
/// 默认端口跟 INSTALL.md「配服务器地址」那张表一致; 链接里带了 gw / id 就用链接的
const DEFAULT_GATEWAY_PORT: u16 = 8999;
const DEFAULT_IDENTITY_PORT: u16 = 8998;

static PENDING: Mutex<Option<ConnectLink>> = Mutex::new(None);

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ConnectLink {
    pub web_url: String,
    pub gateway_url: String,
    pub identity_url: String,
    /// HTTPS 部署才有
    pub code: Option<String>,
}

fn http_url(raw: &str, what: &str) -> Result<url::Url, String> {
    let u = url::Url::parse(raw.trim()).map_err(|_| format!("链接里的{what}不是合法地址: {raw}"))?;
    if !matches!(u.scheme(), "http" | "https") || u.host_str().is_none() {
        return Err(format!("链接里的{what}必须是 http:// 或 https:// 地址: {raw}"));
    }
    Ok(u)
}

fn clean(u: &url::Url) -> String {
    u.as_str().trim_end_matches('/').to_string()
}

pub fn parse(link: &str) -> Result<ConnectLink, String> {
    let u = url::Url::parse(link.trim()).map_err(|_| "不是鲶鱼接入链接".to_string())?;
    if u.scheme() != "catfish" || u.host_str() != Some("connect") {
        return Err("不是鲶鱼接入链接 (应以 catfish://connect 开头)".into());
    }
    let q: std::collections::HashMap<_, _> = u.query_pairs().into_owned().collect();
    let web = http_url(q.get("web").ok_or("链接里缺门户地址 (web=)")?, "门户地址")?;
    let host = web.host_str().unwrap_or_default().to_string();
    let host = if host.contains(':') { format!("[{host}]") } else { host }; // IPv6
    let gateway = match q.get("gw") {
        Some(g) => clean(&http_url(g, "网关地址")?),
        None => format!("http://{host}:{DEFAULT_GATEWAY_PORT}"),
    };
    let identity = match q.get("id") {
        Some(i) => clean(&http_url(i, "认证服务地址")?),
        None => format!("http://{host}:{DEFAULT_IDENTITY_PORT}"),
    };
    let code = match q.get("code").map(|c| c.trim()).filter(|c| !c.is_empty()) {
        Some(c) => Some(super::server_pairing::pretty(&super::server_pairing::normalize_code(c)?)),
        None => None,
    };
    if web.scheme() == "https" && code.is_none() {
        log::warn!("[connect-link] https 门户的链接没带接入码, 只能填地址, 证书要另外装");
    }
    Ok(ConnectLink { web_url: clean(&web), gateway_url: gateway, identity_url: identity, code })
}

/// 收到链接: 认得的存起来并通知前端, 认不出的只记日志 (别的 catfish:// 链接不弹框)。
pub fn handle(app: &tauri::AppHandle, link: &str) {
    match parse(link) {
        Ok(parsed) => {
            log::info!("[connect-link] 收到接入链接 → 门户 {}", parsed.web_url);
            if let Ok(mut p) = PENDING.lock() {
                *p = Some(parsed.clone());
            }
            let _ = app.emit(EVENT, parsed);
        }
        Err(e) => log::warn!("[connect-link] 忽略链接 {link:?}: {e}"),
    }
}

/// 命令行里的第一个 catfish:// 参数 (Windows 点链接启动时系统这样传)
pub fn from_args<I: IntoIterator<Item = String>>(args: I) -> Option<String> {
    args.into_iter().find(|a| a.to_ascii_lowercase().starts_with("catfish://"))
}

/// 前端起来后取走还没处理的链接 (冷启动时事件发得比监听早)
#[tauri::command]
pub fn connect_link_take() -> Option<ConnectLink> {
    PENDING.lock().ok().and_then(|mut p| p.take())
}

/// 员工把链接整段粘进「服务器配置」时用 —— 同一套解析
#[tauri::command]
pub fn connect_link_parse(link: String) -> Result<ConnectLink, String> {
    parse(&link)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn full_link_and_defaults() {
        let l = parse("catfish://connect?web=https%3A%2F%2F10.0.5.20&code=88dc-a0b2-b193-3921").unwrap();
        assert_eq!(l.web_url, "https://10.0.5.20");
        assert_eq!(l.gateway_url, "http://10.0.5.20:8999");
        assert_eq!(l.identity_url, "http://10.0.5.20:8998");
        assert_eq!(l.code.as_deref(), Some("88DC-A0B2-B193-3921"));
        let l = parse("catfish://connect?web=https://h.example:8443/&gw=http://h.example:9999&id=http://h.example:9998").unwrap();
        assert_eq!((l.web_url.as_str(), l.gateway_url.as_str(), l.identity_url.as_str()),
                   ("https://h.example:8443", "http://h.example:9999", "http://h.example:9998"));
        assert_eq!(l.code, None);
    }

    #[test]
    fn bad_links_are_refused() {
        for bad in ["https://10.0.5.20", "catfish://other?web=https://x", "catfish://connect",
                    "catfish://connect?web=javascript:alert(1)", "catfish://connect?web=https://x&code=123"] {
            assert!(parse(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn picks_the_link_out_of_argv() {
        let args = ["C:\\Program Files\\Catfish\\catfish.exe".to_string(), "catfish://connect?web=https://x".into()];
        assert_eq!(from_args(args).as_deref(), Some("catfish://connect?web=https://x"));
        assert_eq!(from_args(["app".to_string()]), None);
    }
}
