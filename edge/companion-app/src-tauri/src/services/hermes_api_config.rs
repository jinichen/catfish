//! hermes API server 配置 — BL-COMPANION-HERMES-API-CONFIG (5/19).
//!
//! Phase 2-2A of BL-MEMORY-OWNERSHIP-FIX. Companion 切到调 hermes API server
//! (端口 8642, OpenAI 兼容) 之前的配置层. 不动 chat.ts (那一层是 Phase 2-2B 大改).
//!
//! # 为啥独立一个 module
//!
//! Companion 当前调 catfish-gateway 8999 (endpoints.gateway_url), Phase 2 重构
//! 后调 hermes API server 8642. 两者并存, 不能直接覆盖 gateway_url. 加 hermes_api
//! 配置段, Phase 2-2B 实施时 chat.ts 切到读这个.
//!
//! # 配置优先级 (高 → 低)
//!
//! 1. `~/.catfish/companion.yaml` 的 `hermes_api:` 段 — 主路径
//! 2. env var `CATFISH_HERMES_API_URL` / `CATFISH_HERMES_API_KEY` — dev / 测试
//! 3. 代码默认 — http://localhost:8642 (但 key 没默认, 必须显式配)
//!
//! # yaml 例子
//!
//! ```yaml
//! endpoints:
//!   gateway_url: http://10.10.40.50:8999    # 老 gateway (Phase 2-3 删完后只内部用)
//!
//! hermes_api:
//!   enabled: true                          # 总开关. false 时仍调 gateway (灰度)
//!   url: http://localhost:8642             # hermes 内置 API server (固定默认)
//!   key: <random-token>                    # 跟 ~/.hermes/.env API_SERVER_KEY 共享
//! ```
//!
//! # 安全
//!
//! `key` 跟 `~/.hermes/.env` 的 API_SERVER_KEY **必须一致**. 安装期由
//! install-catfish-edge.sh 随机生成同一个 token 写两边 (Phase 2-2A 暂手动同步,
//! BL-COMPANION-HERMES-AUTH-DESIGN 定自动化方案). companion.yaml 跟 .env
//! 都 chmod 600 防别的进程读.

use std::sync::OnceLock;

use serde::Deserialize;

const DEFAULT_URL: &str = "http://localhost:8642";

#[derive(Debug, Clone)]
pub struct HermesApiConfig {
    /// 总开关. false 时 Companion 仍调老 gateway (灰度切换用).
    pub enabled: bool,
    /// hermes API server URL (默认 http://localhost:8642).
    pub url: String,
    /// API_SERVER_KEY, 跟 ~/.hermes/.env 共享.
    /// **None 时 enabled 会被 force false** — 没 key 没法调.
    pub key: Option<String>,
}

#[derive(Debug, Deserialize)]
struct YamlFile {
    hermes_api: Option<HermesApiYaml>,
}

#[derive(Debug, Deserialize)]
struct HermesApiYaml {
    enabled: Option<bool>,
    url: Option<String>,
    key: Option<String>,
}

fn yaml_path() -> Option<std::path::PathBuf> {
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .ok()?;
    Some(std::path::PathBuf::from(home).join(".catfish").join("companion.yaml"))
}

fn read_yaml() -> Option<HermesApiYaml> {
    let path = yaml_path()?;
    if !path.exists() {
        return None;
    }
    let content = std::fs::read_to_string(&path).ok()?;
    let parsed: YamlFile = serde_yaml::from_str(&content).ok()?;
    parsed.hermes_api
}

fn build() -> HermesApiConfig {
    let yaml = read_yaml();

    let url = yaml.as_ref()
        .and_then(|y| y.url.clone())
        .or_else(|| std::env::var("CATFISH_HERMES_API_URL").ok())
        .unwrap_or_else(|| DEFAULT_URL.to_string());

    let key = yaml.as_ref()
        .and_then(|y| y.key.clone())
        .or_else(|| std::env::var("CATFISH_HERMES_API_KEY").ok());

    // enabled 默认 false (Phase 2-2A 灰度阶段, 不动当前 gateway 流). Phase 2-2B
    // chat.ts 切完后默认 true. key 缺时 force false.
    let enabled_raw = yaml.as_ref()
        .and_then(|y| y.enabled)
        .or_else(|| {
            std::env::var("CATFISH_HERMES_API_ENABLED")
                .ok()
                .and_then(|s| match s.as_str() {
                    "1" | "true" | "yes" => Some(true),
                    "0" | "false" | "no" => Some(false),
                    _ => None,
                })
        })
        .unwrap_or(false);

    let enabled = enabled_raw && key.is_some();
    if enabled_raw && key.is_none() {
        log::warn!(
            "hermes_api.enabled=true 但没配 key, 强制 disable. 检查 \
             ~/.catfish/companion.yaml hermes_api.key 或 env CATFISH_HERMES_API_KEY \
             (跟 ~/.hermes/.env API_SERVER_KEY 同值)"
        );
    }

    HermesApiConfig { enabled, url, key }
}

static CONFIG: OnceLock<HermesApiConfig> = OnceLock::new();

/// 读 hermes API server 配置. 启动时算一次, 后续复用 (yaml 改了要重启 Companion).
pub fn hermes_api_config() -> &'static HermesApiConfig {
    CONFIG.get_or_init(build)
}

/// Tauri command — 暴露给 React 让 chat.ts 知道走哪个 endpoint.
/// 不返 key (key 在 Authorization header 里, Rust 端拼好不发给 JS, 防 XSS / 误 log).
#[tauri::command]
pub fn hermes_api_config_get() -> HermesApiConfigPublic {
    let cfg = hermes_api_config();
    HermesApiConfigPublic {
        enabled: cfg.enabled,
        url: cfg.url.clone(),
        // key 不暴露
        has_key: cfg.key.is_some(),
    }
}

#[derive(Debug, serde::Serialize)]
pub struct HermesApiConfigPublic {
    pub enabled: bool,
    pub url: String,
    pub has_key: bool,
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;

    fn clear_env() {
        env::remove_var("CATFISH_HERMES_API_URL");
        env::remove_var("CATFISH_HERMES_API_KEY");
        env::remove_var("CATFISH_HERMES_API_ENABLED");
    }

    #[test]
    fn default_url_when_no_yaml_no_env() {
        clear_env();
        // build() 读不到 yaml + 没 env → 走 DEFAULT_URL
        // (实际跑时如果 ~/.catfish/companion.yaml 存在且没 hermes_api 段, 也 fallback default)
        let cfg = build();
        assert_eq!(cfg.url, DEFAULT_URL);
    }

    #[test]
    fn enabled_force_false_when_no_key() {
        clear_env();
        env::set_var("CATFISH_HERMES_API_ENABLED", "true");
        // 没 key → enabled 强制 false
        let cfg = build();
        assert!(!cfg.enabled, "no key should force enabled=false");
    }

    #[test]
    fn enabled_true_when_both_set() {
        clear_env();
        env::set_var("CATFISH_HERMES_API_ENABLED", "true");
        env::set_var("CATFISH_HERMES_API_KEY", "test-key-abc");
        let cfg = build();
        assert!(cfg.enabled);
        assert_eq!(cfg.key.as_deref(), Some("test-key-abc"));
        clear_env();
    }

    #[test]
    fn env_url_override() {
        clear_env();
        env::set_var("CATFISH_HERMES_API_URL", "http://example.test:9999");
        let cfg = build();
        assert_eq!(cfg.url, "http://example.test:9999");
        clear_env();
    }
}
