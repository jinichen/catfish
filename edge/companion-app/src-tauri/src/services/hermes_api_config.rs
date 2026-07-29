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
    let home = crate::util::paths::home_env()
        .or_else(|_| std::env::var("USERPROFILE"))
        .ok()?;
    Some(std::path::PathBuf::from(home).join(".catfish").join("companion.yaml"))
}

/// BL-HERMES-KEY-FROM-DOTENV (7/19 Task #21): 读 ~/.hermes/.env API_SERVER_KEY.
///
/// hermes install 时会往 ~/.hermes/.env 写 API_SERVER_KEY=<random> · 但 Companion
/// process 不加载 .env · 之前拿不到 key · enabled 被 force false · Chat 走 gateway
/// 直连绕过 hermes P15 approval SSE · 按钮永远不弹.
///
/// 此函数 line-based 读 dotenv · 找 `API_SERVER_KEY=<value>` 返 value. 找不到返 None.
fn read_hermes_env_api_key() -> Option<String> {
    let home = crate::util::paths::home_env()
        .or_else(|_| std::env::var("USERPROFILE"))
        .ok()?;
    let env_path = std::path::PathBuf::from(home).join(".hermes").join(".env");
    let content = std::fs::read_to_string(&env_path).ok()?;
    for line in content.lines() {
        let trimmed = line.trim();
        if let Some(rest) = trimmed.strip_prefix("API_SERVER_KEY=") {
            let value = rest.trim().trim_matches('"').trim_matches('\'');
            if !value.is_empty() {
                log::info!(
                    "[hermes_api_config] key 从 {} 拿到 (len={})",
                    env_path.display(), value.len()
                );
                return Some(value.to_string());
            }
        }
    }
    log::debug!(
        "[hermes_api_config] {} 无 API_SERVER_KEY 行 (hermes 未装 · 或未生成 key)",
        env_path.display()
    );
    None
}

fn read_yaml_from(path: &std::path::Path) -> Option<HermesApiYaml> {
    if !path.exists() {
        return None;
    }
    let content = std::fs::read_to_string(path).ok()?;
    let parsed: YamlFile = serde_yaml::from_str(&content).ok()?;
    parsed.hermes_api
}

/// P3.5.144 (6/30 鸿波): 测试可注入 yaml_path 路径绕开员工本机 yaml 污染.
/// 生产 `hermes_api_config()` 调 `build_with_yaml(None)` 走默认 `~/.catfish/companion.yaml`.
/// 测试调 `build_with_yaml(Some(&tempdir/不存在.yaml))` 让 yaml 永远 None, 走 env 测试.
fn build_with_yaml(yaml_path_override: Option<&std::path::Path>) -> HermesApiConfig {
    let yaml = match yaml_path_override {
        Some(p) => read_yaml_from(p),
        None => yaml_path().and_then(|p| read_yaml_from(&p)),
    };

    let url = yaml.as_ref()
        .and_then(|y| y.url.clone())
        .or_else(|| std::env::var("CATFISH_HERMES_API_URL").ok())
        .unwrap_or_else(|| DEFAULT_URL.to_string());

    // BL-HERMES-KEY-FROM-DOTENV (7/19 Task #21 修 · Companion Chat approval 按钮
    // 不弹根因): Companion 之前只从 yaml + env CATFISH_HERMES_API_KEY 读 key ·
    // 达华员工装 dmg 后 hermes install 生成 API_SERVER_KEY 写 ~/.hermes/.env ·
    // 但 Companion process env 不加载 .env · 拿不到 key · enabled force false ·
    // Chat 走 gateway 直连 · P15 SSE approval event 不到 · 按钮永远不弹.
    // 修 · fallback 从 ~/.hermes/.env 读 API_SERVER_KEY.
    let key = yaml.as_ref()
        .and_then(|y| y.key.clone())
        .or_else(|| std::env::var("CATFISH_HERMES_API_KEY").ok())
        .or_else(|| read_hermes_env_api_key());

    // BL-HERMES-DEFAULT-ENABLED (7/19 Task #21 根因修): 默认 enabled=false 让
    // Chat 走 gateway 直连 · P15 approval SSE 只在 hermes daemon 装 · gateway
    // 直连不触发 · Companion Chat approval 按钮永远不弹. 达华 POC blocker.
    // 修 · 默认 true · key 缺时 fallback disable (跟老逻辑一致 · 不破坏未装 hermes 场景).
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
        .unwrap_or(true);

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
    CONFIG.get_or_init(|| build_with_yaml(None))
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

/// 5/19 Phase 2-2B: 返完整 Authorization header value 给 chat.ts 用.
///
/// 不让 JS 拿 raw key — 返 "Bearer <key>" 拼好的字符串. JS 直接塞进 fetch
/// headers. 这样 key 不进 localStorage / 不在 console / log 误漏概率小.
/// (严格说还在 JS memory, 但比单纯返 raw key 安全, 也方便将来切 OIDC 时
/// Rust 端逻辑变 — JS 只调这一个命令拿 header.)
///
/// enabled=false 或没 key 时返 None — caller 应该 fallback 到老 gateway 路径.
#[tauri::command]
pub fn hermes_api_auth_header() -> Option<String> {
    let cfg = hermes_api_config();
    if !cfg.enabled {
        return None;
    }
    cfg.key.as_ref().map(|k| format!("Bearer {}", k))
}

/// BL-WECHAT-QR-HERMES-STANDALONE (7/18 鸿波 catch Task #1 后 WeChat 404):
/// hermes 独占功能 (WeChat QR 绑定 / catfish plugin memory) 走 /api/platforms/* namespace
/// 是 hermes 8642 独占 route · Task #60 后 hermes_api.enabled=false 走 gateway 直连时
/// caller 找 gateway 挂 404 (gateway 无 platforms 路由). 需要**无视 enabled**返 auth ·
/// 只要 key 有就返 Bearer · caller 自己配 URL 强 localhost:8642.
///
/// 语义: hermes 独占 endpoint 用. chat 主链路仍走 hermes_api_auth_header (受 enabled 管).
#[tauri::command]
pub fn hermes_api_auth_header_forced() -> Option<String> {
    let cfg = hermes_api_config();
    cfg.key.as_ref().map(|k| format!("Bearer {}", k))
}

/// 配套: 无视 enabled 返 hermes URL. hermes 独占 endpoint (WeChat QR 等) 强用.
#[tauri::command]
pub fn hermes_api_url_forced() -> String {
    hermes_api_config().url.clone()
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
    use crate::util::test_env::env_lock;
    use std::env;
    use tempfile::TempDir;

    fn clear_env() {
        env::remove_var("CATFISH_HERMES_API_URL");
        env::remove_var("CATFISH_HERMES_API_KEY");
        env::remove_var("CATFISH_HERMES_API_ENABLED");
    }

    /// 测试用 helper: 返一个不存在的 yaml 路径 (在 tempdir 内, 文件没 create).
    /// read_yaml_from `!path.exists() → None`, 所以 build_with_yaml 不读 yaml.
    fn fake_yaml_path(tmp: &TempDir) -> std::path::PathBuf {
        tmp.path().join("companion.yaml")
    }

    #[test]
    fn default_url_when_no_yaml_no_env() {
        let _guard = env_lock();
        clear_env();
        let tmp = TempDir::new().unwrap();
        let yaml = fake_yaml_path(&tmp);
        let cfg = build_with_yaml(Some(&yaml));
        assert_eq!(cfg.url, DEFAULT_URL);
    }

    #[test]
    fn enabled_force_false_when_no_key() {
        let _guard = env_lock();
        clear_env();
        env::set_var("CATFISH_HERMES_API_ENABLED", "true");
        // 没 key → enabled 强制 false
        let tmp = TempDir::new().unwrap();
        let yaml = fake_yaml_path(&tmp);
        let cfg = build_with_yaml(Some(&yaml));
        assert!(!cfg.enabled, "no key should force enabled=false");
        clear_env();
    }

    #[test]
    fn enabled_true_when_both_set() {
        let _guard = env_lock();
        clear_env();
        env::set_var("CATFISH_HERMES_API_ENABLED", "true");
        env::set_var("CATFISH_HERMES_API_KEY", "test-key-abc");
        let tmp = TempDir::new().unwrap();
        let yaml = fake_yaml_path(&tmp);
        let cfg = build_with_yaml(Some(&yaml));
        assert!(cfg.enabled);
        assert_eq!(cfg.key.as_deref(), Some("test-key-abc"));
        clear_env();
    }

    #[test]
    fn env_url_override() {
        let _guard = env_lock();
        clear_env();
        env::set_var("CATFISH_HERMES_API_URL", "http://example.test:9999");
        let tmp = TempDir::new().unwrap();
        let yaml = fake_yaml_path(&tmp);
        let cfg = build_with_yaml(Some(&yaml));
        assert_eq!(cfg.url, "http://example.test:9999");
        clear_env();
    }
}
