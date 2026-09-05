//! Companion 对 Hermes Profile 的管理与场景路由。
//!
//! Hermes 仍是 Profile runtime 的唯一事实源；这里仅保存注册、模型策略、场景绑定，
//! 并以可回滚事务维护 gateway multiplex allowlist。

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use crate::services::catfish_paths::hermes_home;

use super::expert_bots_config::{
    atomic_write, companion_yaml_path, load_config, parse_yaml_root, read_optional, save_config,
};
use super::expert_bots_gateway::{profile_is_served, reconcile_gateway_config};
use super::expert_bots_profiles::{
    create_profile, delete_managed_profile, list_profiles, profile_dir, run_hermes,
    update_profile_metadata, update_profile_soul, DEFAULT_EXPERT_SOUL,
};
pub use super::expert_bots_types::{
    AvailableProfileSummary, BotRegistration, ExpertBotCreateInput, ExpertBotRoute,
    ExpertBotSummary, ExpertBotUpdateInput, ExpertBotsConfig, ExpertBotsSnapshot,
    ExpertBotsStatus, ExpertScenarioSummary, ModelPolicy, ModelPolicyMode,
    DEFAULT_ADVISOR_PROFILE, SUPPORTED_SCENARIOS,
};
use super::expert_bots_types::{is_supported_scenario, is_valid_profile_name};

static CONFIG_WRITE_LOCK: Mutex<()> = Mutex::new(());

fn get_home() -> Result<PathBuf, String> {
    hermes_home().ok_or_else(|| "无法定位 Hermes home".to_string())
}

fn gateway_config_path(home: &Path) -> PathBuf {
    home.join("config.yaml")
}

fn build_snapshot(home: &Path, cfg: &ExpertBotsConfig) -> Result<ExpertBotsSnapshot, String> {
    let profiles = list_profiles(home)?;
    let by_id: BTreeMap<_, _> = profiles.iter().map(|item| (item.id.as_str(), item)).collect();
    let gateway_raw = read_optional(&gateway_config_path(home))?;
    let gateway = parse_yaml_root(&gateway_raw, "Hermes config.yaml")?;

    let bots = cfg
        .registrations
        .iter()
        .map(|(id, registration)| {
            let profile = by_id.get(id.as_str()).copied();
            let served = profile_is_served(&gateway, id);
            let (ready, reason) = if !cfg.enabled {
                (false, "专家 Bot 总开关未开启".to_string())
            } else if !registration.enabled {
                (false, "该专家 Bot 已停用".to_string())
            } else if profile.is_none() {
                (false, "Hermes Profile 不存在".to_string())
            } else if !profile.is_some_and(|item| item.has_api_server_key) {
                (false, "Profile 缺少可用 API_SERVER_KEY".to_string())
            } else if !served {
                (false, "Hermes gateway 尚未挂载该 Profile".to_string())
            } else {
                (true, "已就绪".to_string())
            };
            let bound_scenarios = cfg
                .bindings
                .iter()
                .filter_map(|(scenario, profile_id)| (profile_id == id).then(|| scenario.clone()))
                .collect();
            ExpertBotSummary {
                id: id.clone(),
                display_name: profile
                    .map(|item| item.display_name.clone())
                    .unwrap_or_else(|| id.clone()),
                description: profile
                    .map(|item| item.description.clone())
                    .unwrap_or_default(),
                managed_by_companion: profile.is_some_and(|item| item.managed_by_companion),
                enabled: registration.enabled,
                ready,
                reason,
                model_policy: registration.model_policy.clone(),
                configured_model: profile.and_then(|item| item.configured_model.clone()),
                provider: profile.and_then(|item| item.provider.clone()),
                skill_count: profile.map(|item| item.skill_count).unwrap_or(0),
                bound_scenarios,
            }
        })
        .collect();

    let available_profiles = profiles
        .iter()
        .map(|profile| AvailableProfileSummary {
            id: profile.id.clone(),
            display_name: profile.display_name.clone(),
            description: profile.description.clone(),
            registered: cfg.registrations.contains_key(&profile.id),
            managed_by_companion: profile.managed_by_companion,
        })
        .collect();
    let scenarios = SUPPORTED_SCENARIOS
        .iter()
        .map(|(id, label)| ExpertScenarioSummary {
            id: (*id).to_string(),
            label: (*label).to_string(),
            profile_id: cfg.bindings.get(*id).cloned(),
        })
        .collect();

    Ok(ExpertBotsSnapshot {
        enabled: cfg.enabled,
        bots,
        available_profiles,
        scenarios,
    })
}

fn persist_and_reconcile(
    companion_path: &Path,
    home: &Path,
    old_cfg: &ExpertBotsConfig,
    mut new_cfg: ExpertBotsConfig,
) -> Result<ExpertBotsConfig, String> {
    let hermes_path = gateway_config_path(home);
    let old_hermes = read_optional(&hermes_path)?;
    let (new_hermes, gateway_changed) = reconcile_gateway_config(&old_hermes, &mut new_cfg)?;
    if gateway_changed {
        atomic_write(&hermes_path, &new_hermes)?;
    }
    if let Err(error) = save_config(companion_path, &new_cfg) {
        if gateway_changed {
            let _ = atomic_write(&hermes_path, &old_hermes);
        }
        return Err(error);
    }
    if gateway_changed {
        if let Err(error) = run_hermes(home, &["gateway", "restart"]) {
            let _ = atomic_write(&hermes_path, &old_hermes);
            let _ = save_config(companion_path, old_cfg);
            let _ = run_hermes(home, &["gateway", "restart"]);
            return Err(format!("Hermes gateway 重启失败，配置已回滚: {error}"));
        }
    }
    Ok(new_cfg)
}

fn with_config_transaction<F, T>(mutate: F) -> Result<T, String>
where
    F: FnOnce(&Path, &Path, &ExpertBotsConfig) -> Result<(ExpertBotsConfig, T), String>,
{
    let _guard = CONFIG_WRITE_LOCK
        .lock()
        .map_err(|_| "expert_bots 配置锁已损坏".to_string())?;
    let companion_path = companion_yaml_path()?;
    let home = get_home()?;
    let old_cfg = load_config(&companion_path)?;
    let (new_cfg, result) = mutate(&companion_path, &home, &old_cfg)?;
    persist_and_reconcile(&companion_path, &home, &old_cfg, new_cfg)?;
    Ok(result)
}

fn ensure_default_advisor(home: &Path, cfg: &mut ExpertBotsConfig) -> Result<(), String> {
    let id = DEFAULT_ADVISOR_PROFILE;
    let dir = profile_dir(home, id)?;
    if !dir.exists() {
        let dir = create_profile(
            home,
            id,
            "default",
            "分析早安数据并给小鲶提供有依据的工作建议",
        )?;
        update_profile_metadata(&dir, Some("早安工作参谋"), None)?;
        update_profile_soul(&dir, DEFAULT_EXPERT_SOUL)?;
        crate::services::hermes_profile_sync::sync_profile_from_default(home, &dir);
    }
    cfg.registrations.entry(id.to_string()).or_default();
    cfg.bindings
        .entry("briefing.advisor".to_string())
        .or_insert_with(|| id.to_string());
    Ok(())
}

fn list_blocking() -> Result<ExpertBotsSnapshot, String> {
    let path = companion_yaml_path()?;
    let cfg = load_config(&path)?;
    build_snapshot(&get_home()?, &cfg)
}

#[tauri::command]
pub async fn expert_bots_list() -> Result<ExpertBotsSnapshot, String> {
    tokio::task::spawn_blocking(list_blocking)
        .await
        .map_err(|e| format!("读取专家 Bot 异常: {e}"))?
}

#[tauri::command]
pub async fn expert_bots_status() -> Result<ExpertBotsStatus, String> {
    let snapshot = expert_bots_list().await?;
    let advisor_id = snapshot
        .scenarios
        .iter()
        .find(|item| item.id == "briefing.advisor")
        .and_then(|item| item.profile_id.clone())
        .unwrap_or_else(|| DEFAULT_ADVISOR_PROFILE.to_string());
    let advisor = snapshot.bots.iter().find(|item| item.id == advisor_id);
    Ok(ExpertBotsStatus {
        enabled: snapshot.enabled,
        ready: advisor.is_some_and(|item| item.ready),
        advisor_profile: advisor_id,
        reason: advisor
            .map(|item| item.reason.clone())
            .unwrap_or_else(|| "早安尚未绑定专家 Bot".to_string()),
    })
}

#[tauri::command]
pub async fn expert_bots_set_enabled(enabled: bool) -> Result<ExpertBotsStatus, String> {
    tokio::task::spawn_blocking(move || {
        with_config_transaction(|_, home, old_cfg| {
            let mut cfg = old_cfg.clone();
            let needs_default_advisor = cfg.registrations.is_empty()
                || cfg.bindings.get("briefing.advisor").is_some_and(|id| id == DEFAULT_ADVISOR_PROFILE);
            if enabled && needs_default_advisor {
                ensure_default_advisor(home, &mut cfg)?;
            }
            cfg.enabled = enabled;
            Ok((cfg, ()))
        })?;
        let cfg = load_config(&companion_yaml_path()?)?;
        let snapshot = build_snapshot(&get_home()?, &cfg)?;
        let advisor_id = cfg
            .bindings
            .get("briefing.advisor")
            .cloned()
            .unwrap_or_else(|| DEFAULT_ADVISOR_PROFILE.to_string());
        let advisor = snapshot.bots.iter().find(|item| item.id == advisor_id);
        Ok(ExpertBotsStatus {
            enabled: cfg.enabled,
            ready: advisor.is_some_and(|item| item.ready),
            advisor_profile: advisor_id,
            reason: advisor
                .map(|item| item.reason.clone())
                .unwrap_or_else(|| "早安尚未绑定专家 Bot".to_string()),
        })
    })
    .await
    .map_err(|e| format!("配置专家 Bot 异常: {e}"))?
}

#[tauri::command]
pub async fn expert_bot_create(input: ExpertBotCreateInput) -> Result<ExpertBotsSnapshot, String> {
    tokio::task::spawn_blocking(move || {
        let id = input.id.trim().to_string();
        if !is_valid_profile_name(&id) {
            return Err(format!("Profile id 不合法: {id}"));
        }
        let mut policy = input.model_policy.clone();
        policy.validate()?;
        with_config_transaction(|_, home, old_cfg| {
            if old_cfg.registrations.contains_key(&id) {
                return Err(format!("专家 Bot {id} 已注册"));
            }
            let dir = create_profile(
                home,
                &id,
                input.clone_from.as_deref().unwrap_or("default"),
                input.description.trim(),
            )?;
            update_profile_metadata(
                &dir,
                Some(input.display_name.trim()),
                Some(input.description.trim()),
            )?;
            update_profile_soul(
                &dir,
                if input.soul.trim().is_empty() {
                    DEFAULT_EXPERT_SOUL
                } else {
                    &input.soul
                },
            )?;
            crate::services::hermes_profile_sync::sync_profile_from_default(home, &dir);
            let mut cfg = old_cfg.clone();
            cfg.registrations.insert(
                id.clone(),
                BotRegistration {
                    enabled: true,
                    model_policy: policy.clone(),
                },
            );
            Ok((cfg, ()))
        })?;
        list_blocking()
    })
    .await
    .map_err(|e| format!("创建专家 Bot 异常: {e}"))?
}

#[tauri::command]
pub async fn expert_bot_update(input: ExpertBotUpdateInput) -> Result<ExpertBotsSnapshot, String> {
    tokio::task::spawn_blocking(move || {
        with_config_transaction(|_, home, old_cfg| {
            let mut cfg = old_cfg.clone();
            let registration = cfg
                .registrations
                .get_mut(&input.id)
                .ok_or_else(|| format!("专家 Bot {} 未注册", input.id))?;
            let dir = profile_dir(home, &input.id)?;
            let managed = dir
                .join(super::expert_bots_types::MANAGED_PROFILE_MARKER)
                .is_file();
            if input.display_name.is_some() || input.description.is_some() || input.soul.is_some() {
                if !managed {
                    return Err("外部 Hermes Profile 只能配置启用状态、模型策略和场景绑定".to_string());
                }
                update_profile_metadata(
                    &dir,
                    input.display_name.as_deref(),
                    input.description.as_deref(),
                )?;
                if let Some(soul) = input.soul.as_deref() {
                    update_profile_soul(&dir, soul)?;
                }
            }
            if let Some(enabled) = input.enabled {
                registration.enabled = enabled;
            }
            if let Some(mut policy) = input.model_policy.clone() {
                policy.validate()?;
                registration.model_policy = policy;
            }
            Ok((cfg, ()))
        })?;
        list_blocking()
    })
    .await
    .map_err(|e| format!("更新专家 Bot 异常: {e}"))?
}

#[tauri::command]
pub async fn expert_bot_register_existing(profile_id: String) -> Result<ExpertBotsSnapshot, String> {
    tokio::task::spawn_blocking(move || {
        let profile_id = profile_id.trim().to_string();
        with_config_transaction(|_, home, old_cfg| {
            if !profile_dir(home, &profile_id)?.is_dir() {
                return Err(format!("Hermes Profile {profile_id} 不存在"));
            }
            crate::services::hermes_profile_sync::sync_profile_from_default(
                home,
                &profile_dir(home, &profile_id)?,
            );
            let mut cfg = old_cfg.clone();
            cfg.registrations.entry(profile_id.clone()).or_default();
            Ok((cfg, ()))
        })?;
        list_blocking()
    })
    .await
    .map_err(|e| format!("注册 Hermes Profile 异常: {e}"))?
}

#[tauri::command]
pub async fn expert_bot_unregister(profile_id: String) -> Result<ExpertBotsSnapshot, String> {
    tokio::task::spawn_blocking(move || {
        with_config_transaction(|_, _, old_cfg| {
            let mut cfg = old_cfg.clone();
            cfg.registrations.remove(&profile_id);
            cfg.bindings.retain(|_, id| id != &profile_id);
            Ok((cfg, ()))
        })?;
        list_blocking()
    })
    .await
    .map_err(|e| format!("取消注册专家 Bot 异常: {e}"))?
}

#[tauri::command]
pub async fn expert_bot_delete(profile_id: String) -> Result<ExpertBotsSnapshot, String> {
    tokio::task::spawn_blocking(move || {
        let companion_path = companion_yaml_path()?;
        let home = get_home()?;
        let old_cfg = load_config(&companion_path)?;
        with_config_transaction(|_, _, current| {
            let mut cfg = current.clone();
            cfg.registrations.remove(&profile_id);
            cfg.bindings.retain(|_, id| id != &profile_id);
            Ok((cfg, ()))
        })?;
        if let Err(error) = delete_managed_profile(&home, &profile_id) {
            let current = load_config(&companion_path)?;
            let _ = persist_and_reconcile(&companion_path, &home, &current, old_cfg);
            return Err(error);
        }
        list_blocking()
    })
    .await
    .map_err(|e| format!("删除专家 Bot 异常: {e}"))?
}

#[tauri::command]
pub async fn expert_bot_bind(
    scenario: String,
    profile_id: Option<String>,
) -> Result<ExpertBotsSnapshot, String> {
    tokio::task::spawn_blocking(move || {
        if !is_supported_scenario(&scenario) {
            return Err(format!("不支持的场景: {scenario}"));
        }
        with_config_transaction(|_, _, old_cfg| {
            let mut cfg = old_cfg.clone();
            if let Some(id) = profile_id.as_deref().map(str::trim).filter(|id| !id.is_empty()) {
                if !cfg.registrations.contains_key(id) {
                    return Err(format!("场景只能绑定已注册的专家 Bot: {id}"));
                }
                cfg.bindings.insert(scenario.clone(), id.to_string());
            } else {
                cfg.bindings.remove(&scenario);
            }
            Ok((cfg, ()))
        })?;
        list_blocking()
    })
    .await
    .map_err(|e| format!("绑定专家 Bot 场景异常: {e}"))?
}

#[tauri::command]
pub async fn expert_bot_soul_get(profile_id: String) -> Result<String, String> {
    tokio::task::spawn_blocking(move || {
        let path = profile_dir(&get_home()?, &profile_id)?.join("SOUL.md");
        if !path.is_file() {
            return Err(format!("Profile {profile_id} 没有 SOUL.md"));
        }
        read_optional(&path)
    })
    .await
    .map_err(|e| format!("读取 Bot 人设异常: {e}"))?
}

#[tauri::command]
pub async fn expert_bot_route(
    scenario: String,
    picker_model: String,
) -> Result<ExpertBotRoute, String> {
    tokio::task::spawn_blocking(move || {
        if !is_supported_scenario(&scenario) {
            return Err(format!("不支持的场景: {scenario}"));
        }
        let cfg = load_config(&companion_yaml_path()?)?;
        let Some(profile_id) = cfg.bindings.get(&scenario).cloned() else {
            return Ok(ExpertBotRoute {
                enabled: false,
                ready: false,
                profile_id: None,
                model: picker_model,
                reason: "该场景未绑定专家 Bot".to_string(),
            });
        };
        let snapshot = build_snapshot(&get_home()?, &cfg)?;
        let bot = snapshot.bots.iter().find(|item| item.id == profile_id);
        let ready = bot.is_some_and(|item| item.ready);
        let model = cfg
            .registrations
            .get(&profile_id)
            .map(|item| item.model_policy.resolve_model(&picker_model))
            .unwrap_or_else(|| picker_model.clone());
        Ok(ExpertBotRoute {
            enabled: cfg.enabled,
            ready,
            profile_id: Some(profile_id),
            model,
            reason: bot
                .map(|item| item.reason.clone())
                .unwrap_or_else(|| "专家 Bot 未注册".to_string()),
        })
    })
    .await
    .map_err(|e| format!("解析专家 Bot 路由异常: {e}"))?
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn snapshot_reports_bindings_and_external_profiles() {
        let dir = tempdir().unwrap();
        let profile = dir.path().join("profiles/finance");
        std::fs::create_dir_all(profile.join("skills/review")).unwrap();
        std::fs::write(profile.join("SOUL.md"), "finance").unwrap();
        std::fs::write(
            profile.join("profile.yaml"),
            "display_name: 财务专家\ndescription: 预算复核\n",
        )
        .unwrap();
        std::fs::write(profile.join(".env"), "API_SERVER_KEY=0123456789abcdef\n").unwrap();
        std::fs::write(
            dir.path().join("config.yaml"),
            "gateway:\n  multiplex_profiles: true\n  multiplex_profile_allowlist: [finance]\n",
        )
        .unwrap();
        let mut cfg = ExpertBotsConfig {
            enabled: true,
            ..ExpertBotsConfig::default()
        };
        cfg.registrations.insert("finance".into(), BotRegistration::default());
        cfg.bindings.insert("email.draft".into(), "finance".into());

        let snapshot = build_snapshot(dir.path(), &cfg).unwrap();
        assert!(snapshot.bots[0].ready);
        assert_eq!(snapshot.bots[0].bound_scenarios, vec!["email.draft"]);
        assert!(!snapshot.bots[0].managed_by_companion);
    }

    #[test]
    fn fixed_model_policy_overrides_picker() {
        let policy = ModelPolicy {
            mode: ModelPolicyMode::Fixed,
            model_id: Some("private-model".into()),
        };
        assert_eq!(policy.resolve_model("picker-model"), "private-model");
    }
}
