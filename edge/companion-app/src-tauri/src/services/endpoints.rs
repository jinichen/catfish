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

#[derive(Debug, Clone)]
pub struct Endpoints {
    pub gateway_host: String,
    pub gateway_port: u16,
    pub chrome_host: String,
    pub chrome_port: u16,
}

impl Endpoints {
    pub fn gateway_base(&self) -> String {
        format!("http://{}:{}", self.gateway_host, self.gateway_port)
    }

    pub fn chrome_base(&self) -> String {
        format!("http://{}:{}", self.chrome_host, self.chrome_port)
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

    Endpoints {
        gateway_host,
        gateway_port,
        chrome_host,
        chrome_port,
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
        };
        assert_eq!(ep.gateway_base(), "http://127.0.0.1:8999");
        assert_eq!(ep.chrome_base(), "http://127.0.0.1:9222");
    }
}
