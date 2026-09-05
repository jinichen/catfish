//! 将默认 Hermes 凭证与网关地址同步到 Companion 注册的命名 Profile。
//!
//! Hermes `profile create --clone-from` 只复制创建时的配置；后续 JWT、服务令牌和
//! 服务器地址变化不会自动传播。这里把命名 Profile 纳入既有同步周期，避免后台
//! Bot 在运行一段时间后单独出现 401 或仍请求旧网关。

use anyhow::{Context, Result};
use serde_yaml::Value;
use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

use super::hermes_jwt_sync::{
    replace_model_field, replace_or_append_env_line, sync_auth_json,
    sync_auth_json_base_url, sync_config_yaml,
};

fn configured_profile_ids() -> BTreeSet<String> {
    let Some(home) = crate::util::paths::home_env().ok().map(PathBuf::from) else {
        return BTreeSet::new();
    };
    let Ok(raw) = fs::read_to_string(home.join(".catfish/companion.yaml")) else {
        return BTreeSet::new();
    };
    let Ok(root) = serde_yaml::from_str::<Value>(&raw) else {
        return BTreeSet::new();
    };
    root.get("expert_bots")
        .and_then(|value| value.get("registrations"))
        .and_then(Value::as_mapping)
        .map(|items| {
            items
                .keys()
                .filter_map(Value::as_str)
                .filter(|id| is_safe_profile_id(id))
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

fn is_safe_profile_id(id: &str) -> bool {
    !id.is_empty()
        && id != "default"
        && id.len() <= 64
        && !id.contains(['/', '\\'])
        && !id.contains("..")
}

fn env_value<'a>(raw: &'a str, wanted: &str) -> Option<&'a str> {
    raw.lines().find_map(|line| {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            return None;
        }
        let (key, value) = line.split_once('=')?;
        (key.trim() == wanted).then(|| value.trim().trim_matches(['\'', '"']))
    })
}

fn registered_profile_dirs(hermes: &Path) -> Vec<PathBuf> {
    let profiles_root = hermes.join("profiles");
    let mut ids = configured_profile_ids();
    if let Ok(entries) = fs::read_dir(&profiles_root) {
        for entry in entries.filter_map(Result::ok) {
            let path = entry.path();
            let id = entry.file_name().to_string_lossy().to_string();
            if path.join(".catfish-managed-profile").is_file() && is_safe_profile_id(&id) {
                ids.insert(id);
            }
        }
    }
    ids.into_iter()
        .map(|id| profiles_root.join(id))
        .filter(|path| path.is_dir())
        .collect()
}

pub fn sync_access_token(hermes: &Path, jwt: &str) {
    for profile in registered_profile_dirs(hermes) {
        if let Err(error) = sync_config_yaml(&profile, jwt) {
            log::warn!(
                "[hermes-profile-sync] {} access token 配置同步失败: {error:#}",
                profile.display()
            );
        }
        if let Err(error) = sync_auth_json(&profile) {
            log::warn!(
                "[hermes-profile-sync] {} auth 状态重置失败: {error:#}",
                profile.display()
            );
        }
    }
}

pub fn sync_api_server_key(hermes: &Path, key: &str) {
    for profile in registered_profile_dirs(hermes) {
        let env_path = profile.join(".env");
        let old = fs::read_to_string(&env_path).unwrap_or_default();
        let updated = replace_or_append_env_line(
            &replace_or_append_env_line(&old, "API_SERVER_KEY", key),
            "API_SERVER_ENABLED",
            "true",
        );
        if let Err(error) = fs::write(&env_path, updated) {
            log::warn!(
                "[hermes-profile-sync] 写 {} API server key 失败: {error}",
                env_path.display()
            );
        }
    }
}

/// 新建或注册 Profile 时，在 gateway 重启前复制默认 Profile 的运行时连接参数。
pub fn sync_profile_from_default(hermes: &Path, profile: &Path) {
    let root_env = fs::read_to_string(hermes.join(".env")).unwrap_or_default();
    let env_path = profile.join(".env");
    let mut updated = fs::read_to_string(&env_path).unwrap_or_default();
    for key in [
        "API_SERVER_KEY",
        "API_SERVER_ENABLED",
        "OPENAI_API_KEY",
        "HERMES_SERVICE_TOKEN",
        "OPENAI_BASE_URL",
        "CATFISH_GATEWAY_URL",
        "CATFISH_IDENTITY_URL",
        "CATFISH_HERMES_CLIENT_SECRET",
    ] {
        if let Some(value) = env_value(&root_env, key) {
            updated = replace_or_append_env_line(&updated, key, value);
        }
    }
    if let Err(error) = fs::write(&env_path, updated) {
        log::warn!("[hermes-profile-sync] 写 {} 失败: {error}", env_path.display());
    }

    let root_config = fs::read_to_string(hermes.join("config.yaml")).unwrap_or_default();
    let parsed = serde_yaml::from_str::<Value>(&root_config).unwrap_or(Value::Null);
    let profile_config_path = profile.join("config.yaml");
    if let Ok(mut profile_config) = fs::read_to_string(&profile_config_path) {
        for field in ["api_key", "base_url", "api_mode"] {
            if let Some(value) = parsed
                .get("model")
                .and_then(|model| model.get(field))
                .and_then(Value::as_str)
            {
                profile_config = replace_model_field(&profile_config, field, value);
            }
        }
        if let Err(error) = fs::write(&profile_config_path, profile_config) {
            log::warn!(
                "[hermes-profile-sync] 写 {} 失败: {error}",
                profile_config_path.display()
            );
        }
    }
    let _ = sync_auth_json(profile);
    if let Some(base_url) = env_value(&root_env, "OPENAI_BASE_URL") {
        let _ = sync_auth_json_base_url(profile, base_url);
    }
}

pub fn sync_service_env(
    hermes: &Path,
    token: &str,
    identity_url: &str,
    client_secret: &str,
) {
    for profile in registered_profile_dirs(hermes) {
        let env_path = profile.join(".env");
        let old = fs::read_to_string(&env_path).unwrap_or_default();
        let mut updated = old;
        for (key, value) in [
            ("OPENAI_API_KEY", token),
            ("HERMES_SERVICE_TOKEN", token),
            ("CATFISH_IDENTITY_URL", identity_url.trim_end_matches('/')),
            ("CATFISH_HERMES_CLIENT_SECRET", client_secret),
        ] {
            updated = replace_or_append_env_line(&updated, key, value);
        }
        if let Err(error) = fs::write(&env_path, updated) {
            log::warn!(
                "[hermes-profile-sync] 写 {} 失败: {error}",
                env_path.display()
            );
        }
        let _ = sync_auth_json(&profile);
    }
}

pub fn sync_base_url(hermes: &Path, url_base: &str) {
    let base = url_base.trim().trim_end_matches('/');
    if base.is_empty() {
        return;
    }
    let openai_base = format!("{base}/v1");
    for profile in registered_profile_dirs(hermes) {
        let env_path = profile.join(".env");
        let old_env = fs::read_to_string(&env_path).unwrap_or_default();
        let updated_env = replace_or_append_env_line(
            &replace_or_append_env_line(&old_env, "OPENAI_BASE_URL", &openai_base),
            "CATFISH_GATEWAY_URL",
            base,
        );
        if let Err(error) = fs::write(&env_path, updated_env) {
            log::warn!("[hermes-profile-sync] 写 {} 失败: {error}", env_path.display());
        }

        let config_path = profile.join("config.yaml");
        if config_path.is_file() {
            match fs::read_to_string(&config_path)
                .with_context(|| format!("读 {}", config_path.display()))
            {
                Ok(old) => {
                    let updated = replace_model_field(&old, "base_url", &openai_base);
                    if let Err(error) = fs::write(&config_path, updated) {
                        log::warn!(
                            "[hermes-profile-sync] 写 {} 失败: {error}",
                            config_path.display()
                        );
                    }
                }
                Err(error) => log::warn!("[hermes-profile-sync] {error:#}"),
            }
        }
        if let Err(error) = sync_auth_json_base_url(&profile, &openai_base) {
            log::warn!(
                "[hermes-profile-sync] {} auth 地址同步失败: {error:#}",
                profile.display()
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn profile_id_guard_rejects_traversal() {
        assert!(is_safe_profile_id("finance-reviewer"));
        assert!(!is_safe_profile_id("../escape"));
        assert!(!is_safe_profile_id("a/b"));
        assert!(!is_safe_profile_id("default"));
    }

    #[test]
    fn new_profile_inherits_current_runtime_credentials_and_address() {
        let dir = tempdir().unwrap();
        let profile = dir.path().join("profiles/finance");
        fs::create_dir_all(&profile).unwrap();
        fs::write(
            dir.path().join(".env"),
            "API_SERVER_KEY=server-key\nOPENAI_API_KEY=service-token\nOPENAI_BASE_URL=http://10.0.0.8:8999/v1\nCATFISH_GATEWAY_URL=http://10.0.0.8:8999\n",
        )
        .unwrap();
        fs::write(
            dir.path().join("config.yaml"),
            "model:\n  api_key: employee-token\n  base_url: http://10.0.0.8:8999/v1\n  api_mode: chat_completions\n",
        )
        .unwrap();
        fs::write(profile.join(".env"), "API_SERVER_KEY=old\n").unwrap();
        fs::write(profile.join("config.yaml"), "model:\n  api_key: old\n").unwrap();

        sync_profile_from_default(dir.path(), &profile);

        let env = fs::read_to_string(profile.join(".env")).unwrap();
        assert!(env.contains("API_SERVER_KEY=server-key"));
        assert!(env.contains("OPENAI_API_KEY=service-token"));
        assert!(env.contains("OPENAI_BASE_URL=http://10.0.0.8:8999/v1"));
        let config = fs::read_to_string(profile.join("config.yaml")).unwrap();
        assert!(config.contains("api_key: employee-token"));
        assert!(config.contains("base_url: http://10.0.0.8:8999/v1"));
        assert!(config.contains("api_mode: chat_completions"));
    }
}
