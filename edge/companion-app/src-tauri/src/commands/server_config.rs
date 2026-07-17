//! P28 (6/5 鸿波) — server config read/write 让员工 Companion UI 改 gateway URL/token,
//! 不用 vim ~/.catfish/*.yaml.
//!
//! 写 2 个文件 (跟 P23-P26 一致):
//!   - ~/.catfish/companion.yaml         (endpoints.gateway_url)
//!   - ~/.catfish/memory_plugin.yaml     (gateway.url + gateway.token, chmod 600)
//!
//! 读: 优先 yaml, env, default. write 时尽量保留 yaml comment (用 serde_yaml round-trip 会丢
//! 注释 — 这里走 regex 替换关键 line 法, 保留头部说明).
//!
//! caller 改完得手动重启 Companion + hermes (yaml 进程只读一次, 不 hot-reload).

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServerConfig {
    /// gateway base URL (e.g. http://127.0.0.1:8999 或 http://10.10.40.50:8999)
    pub gateway_url: String,
    /// catfish-memory plugin → gateway 内部 token (sensitive)
    pub gateway_token: String,
    /// 当前 token 是否从 env 拿 (yaml 空时回退到 env)
    pub token_source: String, // "yaml" | "env" | "none"
    /// P29 (6/5): identity-server URL (OIDC issuer, `catfish login` 走这).
    /// 读自 ~/.catfish/companion.yaml oidc.issuer 或 env CATFISH_OIDC_ISSUER.
    pub identity_url: String,
    // P3.4.1 (6/13 hb): secret_broker_url 字段砍 — 中央服务删,
    // OAuth token 改 Companion 本机存 (~/.catfish/mcp/oauth-tokens/).
}

fn catfish_home() -> Result<PathBuf, String> {
    let home = crate::util::paths::home_env()
        .or_else(|_| std::env::var("USERPROFILE"))
        .map_err(|_| "找不到 HOME".to_string())?;
    Ok(PathBuf::from(home).join(".catfish"))
}

/// Naive YAML field reader — 抓 top-level "section:" 下的 "key: value" 字符串.
/// 不支持 nested deep / multiline. 只用于 gateway.url / gateway.token / endpoints.gateway_url.
fn read_yaml_field(text: &str, section: &str, key: &str) -> Option<String> {
    let mut in_section = false;
    for line in text.lines() {
        let trimmed = line.trim_end();
        if trimmed.starts_with(&format!("{section}:")) {
            in_section = true;
            continue;
        }
        // 下一个 top-level (非空格开头, 含冒号) 退出 section
        if in_section && !line.starts_with(char::is_whitespace) && trimmed.contains(':') && !trimmed.is_empty() {
            in_section = false;
        }
        if in_section {
            let stripped = trimmed.trim();
            if let Some(rest) = stripped.strip_prefix(&format!("{key}:")) {
                let val = rest.trim().trim_matches('"').trim_matches('\'');
                return Some(val.to_string());
            }
        }
    }
    None
}

/// 替换 yaml 真 "key: value" 行. 不存在的话直接追加到 section 末.
/// 保留所有 comment + 其它字段.
fn replace_or_insert_yaml_field(
    text: &str,
    section: &str,
    key: &str,
    new_value: &str,
) -> String {
    let mut lines: Vec<String> = text.lines().map(|s| s.to_string()).collect();
    let mut in_section = false;
    let mut section_start_idx: Option<usize> = None;
    let mut last_section_line_idx: Option<usize> = None;
    let mut replaced = false;

    for (i, line) in lines.iter_mut().enumerate() {
        let trimmed = line.trim_end();
        if trimmed.starts_with(&format!("{section}:")) {
            in_section = true;
            section_start_idx = Some(i);
            continue;
        }
        // 下一个 top-level 退出 section
        if in_section && !line.starts_with(char::is_whitespace) && trimmed.contains(':') && !trimmed.is_empty() {
            in_section = false;
        }
        if in_section {
            last_section_line_idx = Some(i);
            let stripped = trimmed.trim_start();
            if stripped.starts_with(&format!("{key}:")) {
                // 保留前缀空格 indent
                let indent: String = line.chars().take_while(|c| c.is_whitespace()).collect();
                // 含特殊字符或空 → 双引号
                let quoted = if new_value.is_empty() || new_value.contains([' ', ':', '#']) {
                    format!("\"{new_value}\"")
                } else {
                    new_value.to_string()
                };
                *line = format!("{indent}{key}: {quoted}");
                replaced = true;
                break;
            }
        }
    }

    if !replaced {
        let quoted = if new_value.is_empty() || new_value.contains([' ', ':', '#']) {
            format!("\"{new_value}\"")
        } else {
            new_value.to_string()
        };
        let new_line = format!("  {key}: {quoted}");
        if let Some(insert_after) = last_section_line_idx.or(section_start_idx) {
            lines.insert(insert_after + 1, new_line);
        } else {
            // 整 section 不存在 — 追加在末尾
            lines.push(format!("{section}:"));
            lines.push(new_line);
        }
    }

    lines.join("\n") + "\n"
}

#[tauri::command]
pub fn read_server_config() -> Result<ServerConfig, String> {
    let home = catfish_home()?;
    let companion = home.join("companion.yaml");
    let memplugin = home.join("memory_plugin.yaml");

    // 1. gateway URL: companion.yaml endpoints.gateway_url 优先
    let mut url = String::new();
    if let Ok(text) = fs::read_to_string(&companion) {
        if let Some(v) = read_yaml_field(&text, "endpoints", "gateway_url") {
            url = v;
        }
    }
    if url.is_empty() {
        if let Ok(text) = fs::read_to_string(&memplugin) {
            if let Some(v) = read_yaml_field(&text, "gateway", "url") {
                url = v;
            }
        }
    }
    if url.is_empty() {
        url = "http://127.0.0.1:8999".to_string();
    }

    // 2. token: env > memory_plugin.yaml
    let mut token = String::new();
    let mut source = "none";
    if let Ok(env_v) = std::env::var("CATFISH_INTERNAL_DEV_TOKEN") {
        if !env_v.is_empty() {
            token = env_v;
            source = "env";
        }
    }
    if token.is_empty() {
        if let Ok(text) = fs::read_to_string(&memplugin) {
            if let Some(v) = read_yaml_field(&text, "gateway", "token") {
                if !v.is_empty() {
                    token = v;
                    source = "yaml";
                }
            }
        }
    }

    // P29: identity-server URL (OIDC issuer)
    let mut identity_url = String::new();
    if let Ok(text) = fs::read_to_string(&companion) {
        if let Some(v) = read_yaml_field(&text, "oidc", "issuer") {
            identity_url = v;
        }
    }
    if identity_url.is_empty() {
        identity_url = std::env::var("CATFISH_OIDC_ISSUER").unwrap_or_default();
    }
    if identity_url.is_empty() {
        identity_url = "http://127.0.0.1:8998".to_string();
    }

    // P3.4.1 (6/13 hb): secret_broker_url 砍, 服务删了不需要展示给员工

    Ok(ServerConfig {
        gateway_url: url,
        gateway_token: token,
        token_source: source.to_string(),
        identity_url,
    })
}

#[tauri::command(rename_all = "camelCase")]
pub fn write_server_config(
    gateway_url: String,
    gateway_token: String,
    identity_url: Option<String>,
    // P3.4.1 (6/13 hb): secret_broker_url 参数留, 但忽略 — 给前端旧 build 兼容,
    // 老 ServerConfigCard 调时仍传值, 服务端不再写入 yaml. 下次 UI clean
    // 时可彻底删.
    secret_broker_url: Option<String>,
) -> Result<(), String> {
    let _ = secret_broker_url; // 显式忽略
    let home = catfish_home()?;
    fs::create_dir_all(&home).map_err(|e| format!("建 {home:?} 失败: {e}"))?;
    let companion = home.join("companion.yaml");
    let memplugin = home.join("memory_plugin.yaml");

    // 校验
    let trimmed_url = gateway_url.trim();
    if !trimmed_url.starts_with("http://") && !trimmed_url.starts_with("https://") {
        return Err(format!(
            "gateway_url 必须 http:// 或 https:// 开头: {trimmed_url}"
        ));
    }
    let url_clean = trimmed_url.trim_end_matches('/');

    // 1. companion.yaml: endpoints.gateway_url + (可选) oidc.issuer + endpoints.secret_broker_url
    let mut companion_text = fs::read_to_string(&companion).unwrap_or_else(|_| {
        "endpoints:\n  gateway_url: http://127.0.0.1:8999\n".to_string()
    });
    companion_text = replace_or_insert_yaml_field(
        &companion_text,
        "endpoints",
        "gateway_url",
        url_clean,
    );

    // P29: identity_url → oidc.issuer (oauth.rs 读这)
    if let Some(id_url) = identity_url.as_ref() {
        let trimmed = id_url.trim().trim_end_matches('/');
        if !trimmed.is_empty() {
            if !trimmed.starts_with("http://") && !trimmed.starts_with("https://") {
                return Err(format!("identity_url 必须 http:// 或 https:// 开头: {trimmed}"));
            }
            companion_text = replace_or_insert_yaml_field(
                &companion_text,
                "oidc",
                "issuer",
                trimmed,
            );
        }
    }

    // P3.4.1 (6/13 hb): secret_broker_url 不再写 yaml, secret-broker 服务删了

    fs::write(&companion, companion_text)
        .map_err(|e| format!("写 {companion:?} 失败: {e}"))?;

    // 2. memory_plugin.yaml: gateway.url + gateway.token (chmod 600)
    let memplugin_text = fs::read_to_string(&memplugin).unwrap_or_else(|_| {
        "gateway:\n  url: http://127.0.0.1:8999\n  token: \"\"\n".to_string()
    });
    let with_url = replace_or_insert_yaml_field(
        &memplugin_text,
        "gateway",
        "url",
        url_clean,
    );
    let with_token = replace_or_insert_yaml_field(
        &with_url,
        "gateway",
        "token",
        gateway_token.trim(),
    );
    fs::write(&memplugin, with_token)
        .map_err(|e| format!("写 {memplugin:?} 失败: {e}"))?;

    // chmod 600 防 token 泄露 (macOS/Linux only)
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let perms = fs::Permissions::from_mode(0o600);
        let _ = fs::set_permissions(&memplugin, perms);
    }

    Ok(())
}
