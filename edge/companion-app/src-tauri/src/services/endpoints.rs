//! 端口 / 主机 / URL 集中读取 —— 所有"硬编码地址"都在这一个文件。
//!
//! 设计 (BL-WIN9 / DEPLOY1, 5/8 改):
//!     1. 配置优先级 (高 → 低):
//!        a. **`~/.catfish/companion.yaml` 的 `endpoints` 段** — 客户改这个
//!           (mac .app + Win .exe 双击都能读 yaml, 不依赖 shell env)
//!        b. **env var** — dev / 测试 / 同时多客户端用同 yaml 但局部 override
//!        c. **默认值** — catfish 自家约定俗成 (gateway 127.0.0.1:8999 等)
//!     2. 前端 / Python 侧用同名 env, 一份配置全栈生效 (前端读 VITE_ env,
//!        Python 读 process env, Rust 读 yaml + env).
//!
//! Yaml 配置 (`~/.catfish/companion.yaml` endpoints 段):
//!     endpoints:
//!       gateway_url: http://10.10.40.50:8999     # 网关装到中央服务器
//!       chrome_debug_url: http://127.0.0.1:9222  # Chrome 一般本机
//!
//! Env vars (override yaml 局部, 跟 catfish/.env.example 对齐):
//!     CATFISH_GATEWAY_HOST       默认 "127.0.0.1"
//!     CATFISH_GATEWAY_PORT       默认 8999
//!     CATFISH_GATEWAY_URL        完整 URL, 优先级最高 (env 中)
//!     CATFISH_CHROME_DEBUG_HOST  默认 "127.0.0.1"
//!     CATFISH_CHROME_DEBUG_PORT  默认 9222
//!
//! 实现:
//!     std::sync::OnceLock 在第一次访问时读 yaml + env, 之后不变 (进程内不可改).
//!     Companion 是桌面应用, 不需要 hot-reload 端口。改 yaml 后重启 Companion 生效。

use std::sync::OnceLock;
use serde::Deserialize;

const DEFAULT_GATEWAY_HOST: &str = "127.0.0.1";
const DEFAULT_GATEWAY_PORT: u16 = 8999;
const DEFAULT_CHROME_HOST: &str = "127.0.0.1";
const DEFAULT_CHROME_PORT: u16 = 9222;
// P29 (6/5 鸿波) — secret-broker 中央部署改 IP 时要跟着改, 加 yaml/env 配置.
const DEFAULT_SECRET_BROKER_HOST: &str = "127.0.0.1";
const DEFAULT_SECRET_BROKER_PORT: u16 = 8995;
// BL-ARCH2 fix2 (5/10): catfish-web 中央门户. 不再前端硬编码 localhost:5173,
// 走跟 gateway 同款 yaml 配置. 默认 dev 5173 (vite), 生产改 nginx 同域 (跟
// gateway 同 origin) 或独立 web 服务器, 通过 yaml endpoints.web_url override.
const DEFAULT_WEB_HOST: &str = "127.0.0.1";
const DEFAULT_WEB_PORT: u16 = 5173;

#[derive(Debug, Clone)]
pub struct Endpoints {
    pub gateway_host: String,
    pub gateway_port: u16,
    pub chrome_host: String,
    pub chrome_port: u16,
    /// BL-ARCH2 fix2 (5/10): 完整 web URL (包含 scheme), 用 String 而非
    /// host/port 因为生产可能 https + 路径前缀 (e.g. https://catfish.client.com).
    pub web_url: String,
    /// P29 (6/5 鸿波): secret-broker URL (员工 SSO 拿 secret).
    pub secret_broker_host: String,
    pub secret_broker_port: u16,
}

impl Endpoints {
    pub fn gateway_base(&self) -> String {
        format!("http://{}:{}", self.gateway_host, self.gateway_port)
    }

    pub fn chrome_base(&self) -> String {
        format!("http://{}:{}", self.chrome_host, self.chrome_port)
    }

    pub fn web_base(&self) -> String {
        self.web_url.clone()
    }

    /// P29 (6/5): secret-broker base URL.
    pub fn secret_broker_base(&self) -> String {
        format!("http://{}:{}", self.secret_broker_host, self.secret_broker_port)
    }
}

fn read_port(env_name: &str, default: u16) -> u16 {
    std::env::var(env_name)
        .ok()
        .and_then(|s| s.trim().parse::<u16>().ok())
        .unwrap_or(default)
}

fn read_host(env_name: &str, default: &str) -> String {
    std::env::var(env_name)
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| default.to_string())
}

// ── BL-WIN9 / DEPLOY1 (5/8): yaml 配置 ─────────────────────────
//
// 客户网关装到 中央服务器 (e.g. 10.10.40.50:8999) 时, 客户端要能配置.
// mac .app + Win .exe 双击启动**不读 shell env**, 所以走 yaml 文件
// (~/.catfish/companion.yaml).

#[derive(Debug, Deserialize)]
struct CompanionYamlFile {
    endpoints: Option<EndpointsYaml>,
}

#[derive(Debug, Deserialize)]
struct EndpointsYaml {
    /// 完整 gateway URL (e.g. "http://10.10.40.50:8999"). 跟 host/port 二选一.
    gateway_url: Option<String>,
    /// 跟 gateway_url 二选一. 只设 host/port 时 scheme 默认 http://
    gateway_host: Option<String>,
    gateway_port: Option<u16>,
    /// Chrome CDP 端点 (一般本机 127.0.0.1:9222 不动)
    chrome_debug_url: Option<String>,
    chrome_debug_host: Option<String>,
    chrome_debug_port: Option<u16>,
    /// BL-ARCH2 fix2 (5/10): 中央 web 门户 URL.
    ///   dev:  http://localhost:5173 (vite, 默认)
    ///   prod: https://catfish.client.com (nginx 同域 / 跟 gateway 同 host 不同 path)
    /// 不设 → 自动从 gateway 推 (gateway 是 127.0.0.1 时假设 web 也本机, 端口 5173;
    /// gateway 是远程时假设 web 跟 gateway 同 host, port 80/443).
    web_url: Option<String>,
    web_host: Option<String>,
    web_port: Option<u16>,
    /// P29 (6/5 鸿波): secret-broker URL — 员工 SSO 拿 secret. 中央部署改.
    secret_broker_url: Option<String>,
    secret_broker_host: Option<String>,
    secret_broker_port: Option<u16>,
    /// P29 (6/5 鸿波): identity-server (OIDC issuer) URL. 显式重复 oidc.issuer
    /// 以便 yaml 真**`endpoints 段一目了然 5 服务`**真, 但 oauth.rs 仍读 oidc.issuer
    /// (跟历史保持兼容). 优先级: endpoints.identity_url > oidc.issuer.
    identity_url: Option<String>,
}

fn yaml_path() -> Option<std::path::PathBuf> {
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .ok()?;
    Some(std::path::PathBuf::from(home).join(".catfish").join("companion.yaml"))
}

/// 读 yaml 文件的 endpoints 段. 不存在 / 解析失败 / 没 endpoints 段都返 None.
/// 所有 yaml 失败都 log warn 不抛 — yaml 是 "可选 override", 失败回退 env / default.
fn read_yaml() -> Option<EndpointsYaml> {
    let path = yaml_path()?;
    if !path.exists() {
        return None;
    }
    let content = match std::fs::read_to_string(&path) {
        Ok(c) => c,
        Err(e) => {
            log::warn!("读 {} 失败 (走 env / default): {}", path.display(), e);
            return None;
        }
    };
    let parsed: CompanionYamlFile = match serde_yaml::from_str(&content) {
        Ok(p) => p,
        Err(e) => {
            log::warn!("解析 {} 失败 (走 env / default): {}", path.display(), e);
            return None;
        }
    };
    parsed.endpoints
}

/// 把完整 URL (e.g. "http://host:port") 解出 (host, port). 失败返 None.
fn parse_url_host_port(url: &str) -> Option<(String, u16)> {
    let parsed = url::Url::parse(url).ok()?;
    let host = parsed.host_str()?.to_string();
    // url 库默认端口 (http=80, https=443) 跟我们 8999 不符, 一律要求显式
    let port = parsed.port()?;
    Some((host, port))
}

fn build() -> Endpoints {
    let yaml = read_yaml();

    // 优先 yaml.gateway_url > yaml.host+port > env > default
    let (gateway_host, gateway_port) = (|| -> Option<(String, u16)> {
        let y = yaml.as_ref()?;
        if let Some(url) = &y.gateway_url {
            if let Some((h, p)) = parse_url_host_port(url) {
                log::info!("BL-WIN9 endpoints: gateway 走 yaml gateway_url={}", url);
                return Some((h, p));
            }
            log::warn!("yaml endpoints.gateway_url={} 解析失败, 退 yaml host/port", url);
        }
        let h = y.gateway_host.clone()?;
        let p = y.gateway_port?;
        log::info!("BL-WIN9 endpoints: gateway 走 yaml host={} port={}", h, p);
        Some((h, p))
    })()
    .unwrap_or_else(|| (
        read_host("CATFISH_GATEWAY_HOST", DEFAULT_GATEWAY_HOST),
        read_port("CATFISH_GATEWAY_PORT", DEFAULT_GATEWAY_PORT),
    ));

    let (chrome_host, chrome_port) = (|| -> Option<(String, u16)> {
        let y = yaml.as_ref()?;
        if let Some(url) = &y.chrome_debug_url {
            if let Some((h, p)) = parse_url_host_port(url) {
                return Some((h, p));
            }
        }
        let h = y.chrome_debug_host.clone()?;
        let p = y.chrome_debug_port?;
        Some((h, p))
    })()
    .unwrap_or_else(|| (
        read_host("CATFISH_CHROME_DEBUG_HOST", DEFAULT_CHROME_HOST),
        read_port("CATFISH_CHROME_DEBUG_PORT", DEFAULT_CHROME_PORT),
    ));

    // BL-ARCH2 fix2 (5/10): web URL — yaml.web_url > yaml.host+port > env > 推导.
    // 推导规则: gateway 是 localhost/127.0.0.1 → web 也本机 :5173 (vite dev);
    //          gateway 是远程 → web 跟 gateway 同 host (假设 nginx 同域反代),
    //          但 port 留空让 URL 用默认 (生产一般 443/80, 不带 port).
    let web_url = (|| -> Option<String> {
        let y = yaml.as_ref()?;
        if let Some(url) = &y.web_url {
            log::info!("BL-ARCH2 endpoints: web 走 yaml web_url={}", url);
            return Some(url.trim_end_matches('/').to_string());
        }
        let h = y.web_host.clone()?;
        let p = y.web_port?;
        log::info!("BL-ARCH2 endpoints: web 走 yaml host={} port={}", h, p);
        Some(format!("http://{}:{}", h, p))
    })()
    .or_else(|| std::env::var("CATFISH_WEB_URL").ok().map(|s| s.trim_end_matches('/').to_string()))
    .unwrap_or_else(|| {
        // BL-ARCH2 fix3 (5/10): 默认本机 5173 (vite). 不再假设 "gateway 远程 →
        // web 跟 gateway 同 host" — gateway 跟 web 是两个独立服务, 同 origin
        // 只有 nginx 反代了 web/ 路径才成立, 一般客户分开部 (web 走 80/443
        // 自己的子域 e.g. catfish.client.com), 必须 yaml 显式配 web_url.
        // 自动 fallback 到 gateway origin 会变成"链接全 404" (鸿波反馈).
        let h = read_host("CATFISH_WEB_HOST", DEFAULT_WEB_HOST);
        let p = read_port("CATFISH_WEB_PORT", DEFAULT_WEB_PORT);
        format!("http://{}:{}", h, p)
    });
    log::info!("BL-ARCH2 endpoints: web_url={}", web_url);

    // P29 (6/5 鸿波): secret-broker — yaml.secret_broker_url > yaml.host/port > env > default
    let (secret_broker_host, secret_broker_port) = (|| -> Option<(String, u16)> {
        let y = yaml.as_ref()?;
        if let Some(url) = &y.secret_broker_url {
            if let Some((h, p)) = parse_url_host_port(url) {
                log::info!("P29 endpoints: secret-broker 走 yaml URL={}", url);
                return Some((h, p));
            }
        }
        let h = y.secret_broker_host.clone()?;
        let p = y.secret_broker_port?;
        Some((h, p))
    })()
    .unwrap_or_else(|| (
        read_host("CATFISH_SECRET_BROKER_HOST", DEFAULT_SECRET_BROKER_HOST),
        read_port("CATFISH_SECRET_BROKER_PORT", DEFAULT_SECRET_BROKER_PORT),
    ));

    Endpoints {
        gateway_host,
        gateway_port,
        chrome_host,
        chrome_port,
        web_url,
        secret_broker_host,
        secret_broker_port,
    }
}

static ENDPOINTS: OnceLock<Endpoints> = OnceLock::new();

/// 进程内单例 —— 第一次调时从 env 读, 之后冻结。
pub fn endpoints() -> &'static Endpoints {
    ENDPOINTS.get_or_init(build)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn read_port_uses_default_when_unset() {
        // 假定测试 runner 没设这个奇葩 env
        std::env::remove_var("__CATFISH_TEST_PORT_NEVER_SET");
        assert_eq!(
            read_port("__CATFISH_TEST_PORT_NEVER_SET", 4242),
            4242
        );
    }

    #[test]
    fn read_port_parses_env() {
        std::env::set_var("__CATFISH_TEST_PORT_OK", "9999");
        assert_eq!(read_port("__CATFISH_TEST_PORT_OK", 1), 9999);
        std::env::remove_var("__CATFISH_TEST_PORT_OK");
    }

    #[test]
    fn read_port_falls_back_on_garbage() {
        std::env::set_var("__CATFISH_TEST_PORT_BAD", "not-a-number");
        assert_eq!(read_port("__CATFISH_TEST_PORT_BAD", 7), 7);
        std::env::remove_var("__CATFISH_TEST_PORT_BAD");
    }

    #[test]
    fn read_host_strips_whitespace() {
        std::env::set_var("__CATFISH_TEST_HOST_PADDED", "  example.com  ");
        assert_eq!(
            read_host("__CATFISH_TEST_HOST_PADDED", "fallback"),
            "example.com"
        );
        std::env::remove_var("__CATFISH_TEST_HOST_PADDED");
    }

    #[test]
    fn read_host_empty_falls_back() {
        std::env::set_var("__CATFISH_TEST_HOST_EMPTY", "   ");
        assert_eq!(
            read_host("__CATFISH_TEST_HOST_EMPTY", "default-host"),
            "default-host"
        );
        std::env::remove_var("__CATFISH_TEST_HOST_EMPTY");
    }

    #[test]
    fn endpoints_default_compose_correctly() {
        let ep = Endpoints {
            gateway_host: "127.0.0.1".into(),
            gateway_port: 8999,
            chrome_host: "127.0.0.1".into(),
            chrome_port: 9222,
            web_url: "http://127.0.0.1:8999".into(),
            secret_broker_host: "127.0.0.1".into(),
            secret_broker_port: 8995,
        };
        assert_eq!(ep.gateway_base(), "http://127.0.0.1:8999");
        assert_eq!(ep.chrome_base(), "http://127.0.0.1:9222");
        assert_eq!(ep.secret_broker_base(), "http://127.0.0.1:8995");
    }
}
