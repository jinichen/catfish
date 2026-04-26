//! 端口 / 主机 / URL 集中读取 —— 所有"硬编码地址"都在这一个文件。
//!
//! 设计:
//!     1. 默认值是 catfish 自家"约定俗成"的端口 (gateway 8999, Chrome 9222),
//!        用户什么都不配也能起。
//!     2. 任何端口都可以通过 env var 覆写, 不用改代码不用重编译。
//!     3. 前端 / Python 侧用同名 env, 一份配置全栈生效。
//!
//! Env vars (跟 catfish/.env.example 对齐):
//!     CATFISH_GATEWAY_HOST       默认 "127.0.0.1"
//!     CATFISH_GATEWAY_PORT       默认 8999
//!     CATFISH_CHROME_DEBUG_HOST  默认 "127.0.0.1"
//!     CATFISH_CHROME_DEBUG_PORT  默认 9222
//!
//! 实现:
//!     std::sync::OnceLock 在第一次访问时读 env, 之后不变 (进程内不可改).
//!     Companion 是桌面应用, 不需要 hot-reload 端口。

use std::sync::OnceLock;

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

fn build() -> Endpoints {
    Endpoints {
        gateway_host: read_host("CATFISH_GATEWAY_HOST", DEFAULT_GATEWAY_HOST),
        gateway_port: read_port("CATFISH_GATEWAY_PORT", DEFAULT_GATEWAY_PORT),
        chrome_host: read_host("CATFISH_CHROME_DEBUG_HOST", DEFAULT_CHROME_HOST),
        chrome_port: read_port("CATFISH_CHROME_DEBUG_PORT", DEFAULT_CHROME_PORT),
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
