use serde_yaml::{Mapping, Value};
use std::collections::BTreeSet;

use super::expert_bots_config::parse_yaml_root;
use super::expert_bots_types::ExpertBotsConfig;

fn gateway_setting<'a>(root: &'a Value, key: &str) -> Option<&'a Value> {
    root.get(key)
        .or_else(|| root.get("gateway").and_then(|gateway| gateway.get(key)))
}

pub fn configured_allowlist(root: &Value) -> Option<Vec<String>> {
    gateway_setting(root, "multiplex_profile_allowlist")?.as_sequence().map(|items| {
        items
            .iter()
            .filter_map(|value| value.as_str().map(str::to_string))
            .collect()
    })
}

pub fn multiplex_enabled(root: &Value) -> bool {
    gateway_setting(root, "multiplex_profiles")
        .and_then(Value::as_bool)
        .unwrap_or(false)
}

fn set_gateway_value(root: &mut Value, key: &str, value: Value) -> Result<(), String> {
    let mapping = root
        .as_mapping_mut()
        .ok_or_else(|| "Hermes config.yaml 顶层必须是 YAML mapping".to_string())?;
    let top_level_key = Value::String(key.to_string());
    if mapping.contains_key(&top_level_key) {
        mapping.insert(top_level_key, value);
        return Ok(());
    }
    let gateway_key = Value::String("gateway".to_string());
    if !mapping.contains_key(&gateway_key) {
        mapping.insert(gateway_key.clone(), Value::Mapping(Mapping::new()));
    }
    let gateway = mapping
        .get_mut(&gateway_key)
        .and_then(Value::as_mapping_mut)
        .ok_or_else(|| "Hermes config.yaml gateway 必须是 mapping".to_string())?;
    gateway.insert(Value::String(key.to_string()), value);
    Ok(())
}

pub fn desired_profiles(cfg: &ExpertBotsConfig) -> BTreeSet<String> {
    if !cfg.enabled {
        return BTreeSet::new();
    }
    cfg.bindings
        .values()
        .filter(|id| cfg.registrations.get(*id).map(|item| item.enabled).unwrap_or(false))
        .cloned()
        .collect()
}

pub fn reconcile_gateway_config(raw: &str, cfg: &mut ExpertBotsConfig) -> Result<(String, bool), String> {
    let mut root = parse_yaml_root(raw, "Hermes config.yaml")?;
    let before = serde_yaml::to_string(&root).map_err(|e| e.to_string())?;
    let desired = desired_profiles(cfg);
    let was_enabled = multiplex_enabled(&root);
    let existing_allowlist = configured_allowlist(&root);
    let should_manage_allowlist = existing_allowlist.is_some() || (!was_enabled && !desired.is_empty());
    let mut allowlist: BTreeSet<String> = existing_allowlist.unwrap_or_default().into_iter().collect();
    let owned_before: BTreeSet<String> = cfg.gateway_ownership.allowlist_entries.iter().cloned().collect();

    for old in &owned_before {
        if !desired.contains(old) {
            allowlist.remove(old);
        }
    }
    if should_manage_allowlist {
        for id in &desired {
            if allowlist.insert(id.clone()) {
                cfg.gateway_ownership.allowlist_entries.push(id.clone());
            }
        }
    }
    cfg.gateway_ownership.allowlist_entries.retain(|id| desired.contains(id));
    cfg.gateway_ownership.allowlist_entries.sort();
    cfg.gateway_ownership.allowlist_entries.dedup();

    if !desired.is_empty() && !was_enabled {
        set_gateway_value(&mut root, "multiplex_profiles", Value::Bool(true))?;
        cfg.gateway_ownership.enabled_multiplex = true;
    } else if desired.is_empty()
        && cfg.gateway_ownership.enabled_multiplex
        && allowlist.is_empty()
    {
        set_gateway_value(&mut root, "multiplex_profiles", Value::Bool(false))?;
        cfg.gateway_ownership.enabled_multiplex = false;
    }
    if should_manage_allowlist {
        set_gateway_value(
            &mut root,
            "multiplex_profile_allowlist",
            serde_yaml::to_value(allowlist.into_iter().collect::<Vec<_>>()).map_err(|e| e.to_string())?,
        )?;
    }
    let after = serde_yaml::to_string(&root).map_err(|e| format!("序列化 Hermes config.yaml 失败: {e}"))?;
    Ok((after.clone(), before != after))
}

pub fn profile_is_served(root: &Value, id: &str) -> bool {
    if !multiplex_enabled(root) {
        return false;
    }
    configured_allowlist(root)
        .map(|items| items.iter().any(|item| item == id))
        .unwrap_or(true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::commands::expert_bots_types::BotRegistration;

    #[test]
    fn reconciliation_preserves_external_entries_and_removes_only_owned() {
        let raw = "gateway:\n  multiplex_profiles: true\n  multiplex_profile_allowlist:\n    - finance\n    - old-bot\n";
        let mut cfg = ExpertBotsConfig { enabled: true, ..ExpertBotsConfig::default() };
        cfg.registrations.insert("new-bot".into(), BotRegistration::default());
        cfg.bindings.insert("briefing.advisor".into(), "new-bot".into());
        cfg.gateway_ownership.allowlist_entries.push("old-bot".into());
        let (updated, changed) = reconcile_gateway_config(raw, &mut cfg).unwrap();
        let root: Value = serde_yaml::from_str(&updated).unwrap();
        assert!(changed);
        assert_eq!(configured_allowlist(&root).unwrap(), vec!["finance", "new-bot"]);
        assert_eq!(cfg.gateway_ownership.allowlist_entries, vec!["new-bot"]);
    }

    #[test]
    fn no_binding_means_no_profile_is_served_by_companion() {
        let mut cfg = ExpertBotsConfig { enabled: true, ..ExpertBotsConfig::default() };
        cfg.registrations.insert("bot".into(), BotRegistration::default());
        assert!(desired_profiles(&cfg).is_empty());
    }

    #[test]
    fn existing_unrestricted_multiplex_stays_unrestricted() {
        let raw = "gateway:\n  multiplex_profiles: true\n";
        let mut cfg = ExpertBotsConfig { enabled: true, ..ExpertBotsConfig::default() };
        cfg.registrations.insert("bot".into(), BotRegistration::default());
        cfg.bindings.insert("briefing.advisor".into(), "bot".into());
        let (updated, _) = reconcile_gateway_config(raw, &mut cfg).unwrap();
        let root: Value = serde_yaml::from_str(&updated).unwrap();
        assert!(configured_allowlist(&root).is_none());
        assert!(cfg.gateway_ownership.allowlist_entries.is_empty());
    }
}
