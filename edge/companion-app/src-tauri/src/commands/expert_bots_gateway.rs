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

// ── hermes v0.22: 一个网关托管全部 profile, 关某个专家 Bot = 停放它 ─────────────────────
//
// v0.22 起 `gateway.multiplex_profiles: false` 作废 (照样多路复用), `multiplex_profile_allowlist`
// 被配置迁移删掉 —— 网关托管 profiles/ 下所有 profile, 唯一的"别托管这个"是 profile 目录里的
// `gateway.parked` 标记 (`hermes -p X gateway stop` 打的就是它, hermes_cli/profiles.py
// profile_is_parked)。所以 v0.22 上:
//   · Companion 注册过、且当前要用的 → 删标记
//   · Companion 注册过、但停用 / 没绑场景 / 总开关关 → 放标记
//   · 员工自己建、Companion 没注册的 → 不碰
//   · 主配置只在要用时写 multiplex_profiles: true (显式 true 网关不再二次判断), 不写 false / 白名单

/// v0.22 停放标记文件名 (hermes_cli/profiles.py parked_marker_path)。
pub const PARKED_MARKER: &str = "gateway.parked";

/// 按注册表放 / 删停放标记。返回是否有变化 (要重启网关)。
pub fn reconcile_parked_markers(
    profiles_root: &std::path::Path,
    cfg: &ExpertBotsConfig,
) -> Result<bool, String> {
    let desired = desired_profiles(cfg);
    let mut changed = false;
    for id in cfg.registrations.keys() {
        let dir = profiles_root.join(id);
        if !dir.is_dir() {
            continue;
        }
        let marker = dir.join(PARKED_MARKER);
        let want_parked = !desired.contains(id);
        if want_parked && !marker.exists() {
            std::fs::write(&marker, "catfish-companion\n")
                .map_err(|e| format!("停放专家 Bot {id} 失败: {e}"))?;
            changed = true;
        } else if !want_parked && marker.exists() {
            std::fs::remove_file(&marker).map_err(|e| format!("启用专家 Bot {id} 失败: {e}"))?;
            changed = true;
        }
    }
    Ok(changed)
}

/// 注册过的 profile 当前是否停放 —— 网关重启失败回滚用。
pub fn parked_snapshot(profiles_root: &std::path::Path, cfg: &ExpertBotsConfig) -> Vec<(std::path::PathBuf, bool)> {
    cfg.registrations
        .keys()
        .map(|id| profiles_root.join(id).join(PARKED_MARKER))
        .map(|marker| {
            let parked = marker.exists();
            (marker, parked)
        })
        .collect()
}

pub fn restore_parked(snapshot: &[(std::path::PathBuf, bool)]) {
    for (marker, parked) in snapshot {
        if *parked && !marker.exists() && marker.parent().is_some_and(|d| d.is_dir()) {
            let _ = std::fs::write(marker, "catfish-companion\n");
        } else if !*parked && marker.exists() {
            let _ = std::fs::remove_file(marker);
        }
    }
}

/// v0.22: 只在有专家 Bot 要用时写显式 `multiplex_profiles: true`; 别的都不动。
pub fn reconcile_gateway_config_v022(raw: &str, cfg: &ExpertBotsConfig) -> Result<(String, bool), String> {
    let mut root = parse_yaml_root(raw, "Hermes config.yaml")?;
    let before = serde_yaml::to_string(&root).map_err(|e| e.to_string())?;
    if !desired_profiles(cfg).is_empty() && !multiplex_enabled(&root) {
        set_gateway_value(&mut root, "multiplex_profiles", Value::Bool(true))?;
    }
    let after = serde_yaml::to_string(&root).map_err(|e| format!("序列化 Hermes config.yaml 失败: {e}"))?;
    Ok((after.clone(), before != after))
}

/// v0.22: profile 目录在、没被停放 → 网关会托管它。
pub fn profile_is_served_v022(profiles_root: &std::path::Path, id: &str) -> bool {
    let dir = profiles_root.join(id);
    dir.is_dir() && !dir.join(PARKED_MARKER).exists()
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
    fn v022_parks_only_companion_bots_that_are_not_in_use() {
        let tmp = tempfile::tempdir().unwrap();
        for id in ["in-use", "disabled", "unbound", "employee-own"] {
            std::fs::create_dir_all(tmp.path().join(id)).unwrap();
        }
        // 之前停放过, 现在要用 → 标记要删
        std::fs::write(tmp.path().join("in-use").join(PARKED_MARKER), "x").unwrap();
        let mut cfg = ExpertBotsConfig { enabled: true, ..ExpertBotsConfig::default() };
        cfg.registrations.insert("in-use".into(), BotRegistration::default());
        cfg.registrations.insert("disabled".into(), BotRegistration { enabled: false, ..BotRegistration::default() });
        cfg.registrations.insert("unbound".into(), BotRegistration::default());
        cfg.bindings.insert("briefing.advisor".into(), "in-use".into());
        cfg.bindings.insert("other".into(), "disabled".into());

        assert!(reconcile_parked_markers(tmp.path(), &cfg).unwrap());
        assert!(profile_is_served_v022(tmp.path(), "in-use"));
        assert!(!profile_is_served_v022(tmp.path(), "disabled"));
        assert!(!profile_is_served_v022(tmp.path(), "unbound"));
        assert!(profile_is_served_v022(tmp.path(), "employee-own"), "Companion 没注册的不碰");
        assert!(!reconcile_parked_markers(tmp.path(), &cfg).unwrap(), "第二次 no-op");

        // 总开关关 → 注册过的全停放
        cfg.enabled = false;
        assert!(reconcile_parked_markers(tmp.path(), &cfg).unwrap());
        assert!(!profile_is_served_v022(tmp.path(), "in-use"));
    }

    #[test]
    fn v022_gateway_config_only_turns_multiplex_on_never_off_or_allowlist() {
        let mut cfg = ExpertBotsConfig { enabled: true, ..ExpertBotsConfig::default() };
        cfg.registrations.insert("bot".into(), BotRegistration::default());
        cfg.bindings.insert("briefing.advisor".into(), "bot".into());
        let (out, changed) = reconcile_gateway_config_v022("model:\n  default: x\n", &cfg).unwrap();
        assert!(changed);
        let root: Value = serde_yaml::from_str(&out).unwrap();
        assert!(multiplex_enabled(&root));
        assert!(configured_allowlist(&root).is_none());
        cfg.enabled = false;
        let (out2, changed2) = reconcile_gateway_config_v022(&out, &cfg).unwrap();
        assert!(!changed2, "不写 false (v0.22 作废)");
        assert_eq!(out2, out);
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
