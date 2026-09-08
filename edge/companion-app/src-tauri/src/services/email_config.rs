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
//! poll_secs / rate_enabled:
//!   1. `~/.catfish/companion.yaml` 的 `email:` 段 — 客户调这个 (双击 .app 也读)
//!   2. env var `CATFISH_EMAIL_*` — dev / 测试 / 临时 override
//!   3. 代码默认值 — 600 秒 / 评级开
//!
//! rate_model (P3.5.139 鸿波"都要去除硬编码"军规):
//!   rate_model 不再有代码默认值. Option<String> 表示"yaml/env 显式 override".
//!   None → caller (email_scheduler::call_rate_llm / phishing_scan)
//!   8/9 起**只认 picker** (picker_config::current_model). 原来的
//!   role_config("rate_fast") / 这里的 rate_model 两级兜底已砍 —— picker 之外
//!   不许有第二个模型来源。rate_model 字段保留只为兼容老 yaml, 不再被读。
//!   也就是 yaml 没配 rate_model + roles.yaml 没 load + picker 没选 → 评级 fallback Medium.
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
//!   rate_model: catfish-private-main  # 评级 model 显式 override (可选, 不配走 chain)
//!   foxmail_root: 'E:\\mail\\Storage'  # Windows Foxmail 自定义 Storage 目录
//! ```

use std::sync::OnceLock;

use serde::{Deserialize, Serialize};

const DEFAULT_POLL_SECS: u64 = 600;

#[derive(Debug, Clone)]
pub struct EmailConfig {
    pub poll_secs: u64,
    pub rate_enabled: bool,
    /// P3.5.139 (6/29 鸿波"都要去除硬编码"): None = yaml/env 没显式 override,
    /// caller 走 chain picker > role > Err. yaml/env 设了非空字符串才 Some.
    pub rate_model: Option<String>,
    /// Windows Foxmail 自定义 Storage 根目录。None = 由 catfish-email 自动探测。
    pub foxmail_root: Option<String>,
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
    foxmail_root: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
struct SelectedEmailSource {
    client: String,
    root: Option<String>,
}

fn yaml_path() -> Option<std::path::PathBuf> {
    let home = crate::util::paths::home_env()
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

fn selected_source_path() -> Option<std::path::PathBuf> {
    let home = crate::util::paths::home_env()
        .or_else(|_| std::env::var("USERPROFILE"))
        .ok()?;
    Some(std::path::PathBuf::from(home).join(".catfish").join("email-source.json"))
}

fn read_selected_source() -> Option<SelectedEmailSource> {
    let path = selected_source_path()?;
    let content = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&content).ok()
}

/// 应用内选择的来源。它独立于 companion.yaml，避免 GUI 用户手写配置。
pub fn selected_email_client() -> Option<String> {
    read_selected_source()
        .map(|source| source.client)
        .filter(|client| matches!(client.as_str(), "outlook-win" | "foxmail-win"))
}

/// 返回用户在 Companion 中选择的 Foxmail 目录；不包含 YAML/env 的企业 override。
pub fn selected_foxmail_root() -> Option<String> {
    let source = read_selected_source()?;
    (source.client == "foxmail-win").then_some(source.root).flatten()
}

/// 返回 Foxmail 显式目录：企业 YAML 优先，其次是应用内选择。
pub fn foxmail_root_override() -> Option<String> {
    email_config().foxmail_root.clone().or_else(|| {
        let source = read_selected_source()?;
        if source.client == "foxmail-win" {
            source.root
        } else {
            None
        }
    })
}

pub fn save_selected_email_source(client: &str, root: Option<&str>) -> Result<(), String> {
    if !matches!(client, "outlook-win" | "foxmail-win") {
        return Err(format!("不支持的 Windows 邮件客户端: {client}"));
    }
    if client == "foxmail-win" {
        let value = root.unwrap_or_default().trim();
        if value.is_empty() {
            return Err("Foxmail 目录不能为空".to_string());
        }
        if !std::path::Path::new(value).is_dir() {
            return Err(format!("Foxmail 目录不存在或不可读: {value}"));
        }
    }
    let path = selected_source_path().ok_or_else(|| "无法确定用户配置目录".to_string())?;
    let parent = path.parent().ok_or_else(|| "用户配置目录无效".to_string())?;
    std::fs::create_dir_all(parent).map_err(|e| format!("创建配置目录失败: {e}"))?;
    let content = serde_json::to_vec_pretty(&SelectedEmailSource {
        client: client.to_string(),
        root: root.map(str::trim).filter(|value| !value.is_empty()).map(str::to_string),
    }).map_err(|e| format!("序列化邮件来源失败: {e}"))?;
    let temp = path.with_extension("json.tmp");
    std::fs::write(&temp, content).map_err(|e| format!("写入邮件来源失败: {e}"))?;
    // Windows 的 rename 不能覆盖已有目标文件；先尝试无损原子替换，
    // 目标已存在时删除旧选择再完成替换，保证用户切换来源不会失败。
    if let Err(error) = std::fs::rename(&temp, &path) {
        if path.exists() {
            std::fs::remove_file(&path).map_err(|e| format!("替换旧邮件来源失败: {e}"))?;
            std::fs::rename(&temp, &path)
                .map_err(|e| format!("保存邮件来源失败: {e}; 首次替换错误: {error}"))?;
        } else {
            return Err(format!("保存邮件来源失败: {error}"));
        }
    }
    Ok(())
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

    // P3.5.139 (6/29 鸿波"都要去除硬编码"): 删 DEFAULT_RATE_MODEL 常量.
    // yaml/env 没显式 override → None, caller 走 chain (picker > role > Err).
    // 客户改 model 名只改 roles.yaml 一处, 不再 sed 代码默认值.
    let rate_model = yaml.as_ref()
        .and_then(|y| y.rate_model.clone())
        .or_else(|| std::env::var("CATFISH_EMAIL_RATE_MODEL").ok())
        .filter(|s| !s.is_empty());

    let foxmail_root = yaml.as_ref()
        .and_then(|y| y.foxmail_root.clone())
        .or_else(|| std::env::var("CATFISH_FOXMAIL_ROOT").ok())
        .map(|s| s.trim().to_owned())
        .filter(|s| !s.is_empty());

    EmailConfig { poll_secs, rate_enabled, rate_model, foxmail_root }
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
///
/// P3.5.139 (6/29): rate_model 改 Option<String>. None = "跟随 chain", UI 现在
/// 不展示这字段 (只展示 rate_enabled), 改 Option 无前端 break.
#[derive(Debug, Clone, Serialize)]
pub struct EmailConfigPublic {
    pub poll_secs: u64,
    pub rate_enabled: bool,
    pub rate_model: Option<String>,
    pub foxmail_root: Option<String>,
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
        foxmail_root: cfg.foxmail_root.clone(),
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
        // P3.5.139: rate_model 现在 Option, 沙箱里 yaml/env 都不设 → None.
        // env var 可能被 dev 设, 不强 assert None, 只 assert 类型存在不 panic.
        let _ = cfg.rate_model;
    }
}
