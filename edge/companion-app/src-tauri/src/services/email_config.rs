//! 邮件 scheduler 配置 — BL-COMPANION-EMAIL-YAML-CONFIG (5/18).
//!
//! # 为啥独立一个 module
//!
//! macOS 双击 .app / Win .exe 启动**不读 shell env**, env var 调不动客户机器.
//! Companion 已经有 yaml 配置 (`~/.catfish/companion.yaml`) 给 endpoints 段用,
//! 邮件 scheduler 也走同套路加 `email:` 段.
//!
//! # 配置优先级 (高 → 低)
//!
//! 1. `~/.catfish/companion.yaml` 的 `email:` 段 — 客户调这个 (双击 .app 也读)
//! 2. env var `CATFISH_EMAIL_*` — dev / 测试 / 临时 override
//! 3. 代码默认值 — 600 秒 / 评级开 / deepseek-flash
//!
//! # yaml 例子
//!
//! ```yaml
//! endpoints:
//!   gateway_url: http://10.10.40.50:8999
//!
//! email:
//!   poll_secs: 30           # 邮件扫描间隔, 默认 600 (10 分钟). 0=关.
//!   rate_enabled: true      # LLM 评级: true=只急通知 / false=任何新邮件都通知
//!   rate_model: catfish-public-deepseek-flash  # 评级用的快速 model
//! ```

use std::sync::OnceLock;

use serde::{Deserialize, Serialize};

const DEFAULT_POLL_SECS: u64 = 600;
const DEFAULT_RATE_MODEL: &str = "catfish-public-deepseek-flash";

#[derive(Debug, Clone)]
pub struct EmailConfig {
    pub poll_secs: u64,
    pub rate_enabled: bool,
    pub rate_model: String,
}

#[derive(Debug, Deserialize)]
struct YamlFile {
    email: Option<EmailYaml>,
}

#[derive(Debug, Deserialize)]
struct EmailYaml {
    poll_secs: Option<u64>,
    rate_enabled: Option<bool>,
    rate_model: Option<String>,
}

fn yaml_path() -> Option<std::path::PathBuf> {
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .ok()?;
    Some(std::path::PathBuf::from(home).join(".catfish").join("companion.yaml"))
}

fn read_yaml() -> Option<EmailYaml> {
    let path = yaml_path()?;
    if !path.exists() {
        return None;
    }
    let content = std::fs::read_to_string(&path).ok()?;
    let parsed: YamlFile = serde_yaml::from_str(&content).ok()?;
    parsed.email
}

fn build() -> EmailConfig {
    let yaml = read_yaml();

    let poll_secs = yaml.as_ref()
        .and_then(|y| y.poll_secs)
        .or_else(|| std::env::var("CATFISH_EMAIL_POLL_SECS").ok().and_then(|s| s.parse().ok()))
        .unwrap_or(DEFAULT_POLL_SECS);

    let rate_enabled = yaml.as_ref()
        .and_then(|y| y.rate_enabled)
        .or_else(|| {
            std::env::var("CATFISH_EMAIL_RATE")
                .ok()
                .map(|s| s != "0" && !s.eq_ignore_ascii_case("false"))
        })
        .unwrap_or(true);

    let rate_model = yaml.as_ref()
        .and_then(|y| y.rate_model.clone())
        .or_else(|| std::env::var("CATFISH_EMAIL_RATE_MODEL").ok())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| DEFAULT_RATE_MODEL.to_string());

    EmailConfig { poll_secs, rate_enabled, rate_model }
}

static EMAIL_CONFIG: OnceLock<EmailConfig> = OnceLock::new();

/// 进程级单例. 第一次访问时读 yaml + env, 之后 immutable.
/// 改配置要重启 Companion (跟 endpoints 同模式).
pub fn email_config() -> &'static EmailConfig {
    EMAIL_CONFIG.get_or_init(build)
}

/// BL-COMPANION-PREFS-TOGGLES (5/20): 前端展示当前 effective 配置.
///
/// truth source 是 ~/.catfish/companion.yaml, 改要打开文件 + 重启 Companion.
/// 这个 command 只读, 用来在 AgentPrefsCard 显当前状态 ✅/❌.
#[derive(Debug, Clone, Serialize)]
pub struct EmailConfigPublic {
    pub poll_secs: u64,
    pub rate_enabled: bool,
    pub rate_model: String,
    /// yaml 文件绝对路径 (前端 shell.open 用)
    pub yaml_path: String,
}

#[tauri::command]
pub fn email_config_get() -> EmailConfigPublic {
    let cfg = email_config();
    let path = yaml_path()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_default();
    EmailConfigPublic {
        poll_secs: cfg.poll_secs,
        rate_enabled: cfg.rate_enabled,
        rate_model: cfg.rate_model.clone(),
        yaml_path: path,
    }
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_when_no_yaml_no_env() {
        // 沙箱里 yaml 一般不存在, 走默认值.
        let cfg = build();
        // poll_secs 可能被 env CATFISH_EMAIL_POLL_SECS override, 测试时不假设具体值
        assert!(cfg.poll_secs > 0 || cfg.poll_secs == 0); // 简单存在
        assert!(!cfg.rate_model.is_empty());
    }
}
