use serde::Deserialize;
use serde_yaml::{Mapping, Value};
use std::fs;
use std::path::{Path, PathBuf};

use super::expert_bots_types::{
    is_supported_scenario, is_valid_profile_name, BotRegistration, ExpertBotsConfig,
    GatewayOwnership, EXPERT_BOTS_SCHEMA_VERSION,
};

#[derive(Debug, Default, Deserialize)]
#[serde(default)]
struct LegacyExpertBotsConfig {
    enabled: bool,
    advisor_profile: String,
    managed_multiplex: bool,
    managed_allowlist: bool,
}

pub fn companion_yaml_path() -> Result<PathBuf, String> {
    let home = crate::util::paths::home_env().map_err(|e| format!("HOME 未设: {e}"))?;
    Ok(PathBuf::from(home).join(".catfish").join("companion.yaml"))
}

pub fn read_optional(path: &Path) -> Result<String, String> {
    match fs::read_to_string(path) {
        Ok(raw) => Ok(raw),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(String::new()),
        Err(e) => Err(format!("读 {} 失败: {e}", path.display())),
    }
}

pub fn atomic_write(path: &Path, content: &str) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("创建 {} 失败: {e}", parent.display()))?;
    }
    let tmp = path.with_extension("catfish-tmp");
    fs::write(&tmp, content).map_err(|e| format!("写 {} 失败: {e}", tmp.display()))?;
    fs::rename(&tmp, path).map_err(|e| format!("替换 {} 失败: {e}", path.display()))
}

pub fn parse_yaml_root(raw: &str, label: &str) -> Result<Value, String> {
    if raw.trim().is_empty() {
        return Ok(Value::Mapping(Mapping::new()));
    }
    let root: Value = serde_yaml::from_str(raw).map_err(|e| format!("解析 {label} 失败: {e}"))?;
    if root.is_mapping() {
        Ok(root)
    } else {
        Err(format!("{label} 顶层必须是 YAML mapping"))
    }
}

fn normalize_config(mut cfg: ExpertBotsConfig) -> Result<ExpertBotsConfig, String> {
    cfg.schema_version = EXPERT_BOTS_SCHEMA_VERSION;
    for (id, registration) in &mut cfg.registrations {
        if !is_valid_profile_name(id) {
            return Err(format!("expert_bots profile id 不合法: {id}"));
        }
        registration.model_policy.validate()?;
    }
    for (scenario, id) in &cfg.bindings {
        if !is_supported_scenario(scenario) {
            return Err(format!("expert_bots 不支持场景: {scenario}"));
        }
        if !cfg.registrations.contains_key(id) {
            return Err(format!("场景 {scenario} 绑定了未注册 Profile: {id}"));
        }
    }
    cfg.gateway_ownership.allowlist_entries.sort();
    cfg.gateway_ownership.allowlist_entries.dedup();
    Ok(cfg)
}

fn migrate_legacy(section: &Value) -> Result<ExpertBotsConfig, String> {
    let legacy: LegacyExpertBotsConfig = serde_yaml::from_value(section.clone())
        .map_err(|e| format!("解析 legacy expert_bots 失败: {e}"))?;
    let id = if legacy.advisor_profile.trim().is_empty() {
        super::expert_bots_types::DEFAULT_ADVISOR_PROFILE.to_string()
    } else {
        legacy.advisor_profile.trim().to_string()
    };
    if !is_valid_profile_name(&id) {
        return Err(format!("legacy advisor_profile 不合法: {id}"));
    }
    let mut cfg = ExpertBotsConfig {
        enabled: legacy.enabled,
        ..ExpertBotsConfig::default()
    };
    cfg.registrations.insert(id.clone(), BotRegistration::default());
    cfg.bindings.insert("briefing.advisor".to_string(), id.clone());
    cfg.gateway_ownership = GatewayOwnership {
        enabled_multiplex: legacy.managed_multiplex,
        allowlist_entries: if legacy.managed_allowlist { vec![id] } else { vec![] },
    };
    normalize_config(cfg)
}

pub fn parse_companion_config(raw: &str) -> Result<ExpertBotsConfig, String> {
    let root = parse_yaml_root(raw, "companion.yaml")?;
    let Some(section) = root.get("expert_bots") else {
        return Ok(ExpertBotsConfig::default());
    };
    if section.get("schema_version").is_none() {
        return migrate_legacy(section);
    }
    let cfg: ExpertBotsConfig = serde_yaml::from_value(section.clone())
        .map_err(|e| format!("解析 companion.yaml expert_bots 失败: {e}"))?;
    normalize_config(cfg)
}

pub fn merge_companion_config(raw: &str, cfg: &ExpertBotsConfig) -> Result<String, String> {
    let mut root = parse_yaml_root(raw, "companion.yaml")?;
    let mapping = root
        .as_mapping_mut()
        .ok_or_else(|| "companion.yaml 顶层必须是 YAML mapping".to_string())?;
    mapping.insert(
        Value::String("expert_bots".to_string()),
        serde_yaml::to_value(cfg).map_err(|e| format!("序列化 expert_bots 失败: {e}"))?,
    );
    serde_yaml::to_string(&root).map_err(|e| format!("序列化 companion.yaml 失败: {e}"))
}

pub fn load_config(path: &Path) -> Result<ExpertBotsConfig, String> {
    parse_companion_config(&read_optional(path)?)
}

pub fn save_config(path: &Path, cfg: &ExpertBotsConfig) -> Result<(), String> {
    let normalized = normalize_config(cfg.clone())?;
    let raw = read_optional(path)?;
    atomic_write(path, &merge_companion_config(&raw, &normalized)?)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_section_is_disabled_v2() {
        let cfg = parse_companion_config("advisor:\n  cache_max_age_minutes: 240\n").unwrap();
        assert!(!cfg.enabled);
        assert_eq!(cfg.schema_version, 2);
        assert!(cfg.registrations.is_empty());
    }

    #[test]
    fn legacy_single_bot_migrates_without_losing_root_keys() {
        let raw = "advisor:\n  cache_max_age_minutes: 240\nexpert_bots:\n  enabled: true\n  advisor_profile: finance\n  managed_multiplex: true\n  managed_allowlist: true\n";
        let cfg = parse_companion_config(raw).unwrap();
        assert!(cfg.enabled);
        assert!(cfg.registrations.contains_key("finance"));
        assert_eq!(cfg.bindings["briefing.advisor"], "finance");
        assert_eq!(cfg.gateway_ownership.allowlist_entries, vec!["finance"]);

        let merged = merge_companion_config(raw, &cfg).unwrap();
        let root: Value = serde_yaml::from_str(&merged).unwrap();
        assert_eq!(root["advisor"]["cache_max_age_minutes"].as_u64(), Some(240));
        assert_eq!(root["expert_bots"]["schema_version"].as_u64(), Some(2));
    }

    #[test]
    fn invalid_binding_fails_closed() {
        let raw = "expert_bots:\n  schema_version: 2\n  enabled: true\n  bindings:\n    briefing.advisor: missing\n";
        assert!(parse_companion_config(raw).unwrap_err().contains("未注册"));
    }
}
