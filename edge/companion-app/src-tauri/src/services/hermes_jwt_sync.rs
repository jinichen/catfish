//! BL-HERMES-JWT-SYNC (7/19 鸿波 Task #15): 员工场景 JWT 自动同步 hermes 3 处.
//!
//! # 为啥要这 module
//!
//! 达华 POC 现场 · 7/18 撞 · WeChat 显英文 "Provider authentication failed".
//! 深追 · hermes 8642 daemon 用**过期 JWT** 调 gateway → 401 → 兜底串英文.
//!
//! JWT 藏 3 处:
//!   1. `~/.hermes/.env` `OPENAI_API_KEY=<JWT>`
//!      hermes credential_pool `source: "env:OPENAI_API_KEY"` 从这拿 (auth.json 记).
//!   2. `~/.hermes/config.yaml` `model.api_key: <JWT>`
//!      hermes model 段读 · 备用 · 但主用是 env.
//!   3. `~/.hermes/auth.json` `credential_pool.openai-api[*].last_status`
//!      老 JWT 挂到 401 · pool 标 `exhausted` · **哪怕换新 JWT 也不再重试** ·
//!      直到 reset. 军规必修.
//!
//! # 触发时机 (3 个 caller)
//!
//! 1. `write_server_config` (面板改 IP 保存) — 面板一改 IP · 顺手同步 3 处
//! 2. `oauth::try_refresh_session` (silent refresh 完) — 每次 refresh · 3 处 sync
//! 3. `lib.rs` setup (启动检查 + 每 25 min 定时) — 员工不点面板/不 refresh 也稳
//!
//! # 决策
//!
//! - **不阻塞**: 3 caller 都 log::warn 后 swallow · 让面板/refresh/启动仍成功
//! - **接受任意 JWT 字符串**: caller 决定用 access_token / id_token / service token
//!   (走 B 路 · 用 access_token · sub=真员工 · quota 正确归账)
//! - **chmod 600 .env**: 跟 server_config.rs 保持一致 · 防泄露
//! - **JSON round-trip auth.json**: serde_json 保结构 · 只 patch 目标字段
//! - **naive line-based sed for config.yaml**: 保 comment · 跟
//!   server_config.rs `replace_or_insert_yaml_field` 同风格.

use anyhow::{Context, Result};
use serde_json::Value;
use std::fs;
use std::path::PathBuf;

/// sync JWT 到 hermes · caller 传 access_token (真员工身份 · quota 正确归属).
///
/// BL-P26-DUAL-TOKEN-SPLIT (7/19 Task #26 鸿波 catch "Companion 关 1h Chat 401"):
///   老逻辑 access_token 覆盖 ~/.hermes/.env OPENAI_API_KEY (TTL 1h · Companion
///   关闭无 refresh 就过期 · hermes daemon 拿 stale 调 gateway 401). 修:
///   sync_all 只写 config.yaml + auth.json · **不再碰 env**. env 由独立
///   sync_service_token_to_env 负责 · 用 client_credentials 拿 30 天 service token.
///
/// 幂等: 反复调无副作用. hermes 未装 (config.yaml 不存在) → 全 skip · Ok(()).
///
/// 2 处任一失败 · log::warn 并**继续下一处** · 最后返 Ok (不阻塞 caller).
pub fn sync_all(jwt: &str) -> Result<()> {
    if jwt.trim().is_empty() {
        log::warn!("[hermes-jwt-sync] JWT 空 · 跳过 sync");
        return Ok(());
    }

    let home_str = crate::util::paths::home_env().context("拿 HOME 挂")?;
    let hermes = PathBuf::from(&home_str).join(".hermes");
    if !hermes.exists() {
        log::debug!("[hermes-jwt-sync] {} 不存在 (hermes 未装) · 全 skip", hermes.display());
        return Ok(());
    }

    // 1. ~/.hermes/config.yaml model.api_key (access_token · Companion Chat 走 gateway 用)
    if let Err(e) = sync_config_yaml(&hermes, jwt) {
        log::warn!("[hermes-jwt-sync] config.yaml 写挂 (不阻塞): {e:#}");
    }

    // 2. ~/.hermes/auth.json openai-api reset healthy · 让 hermes credential_pool 重试
    if let Err(e) = sync_auth_json(&hermes) {
        log::warn!("[hermes-jwt-sync] auth.json reset 挂 (不阻塞): {e:#}");
    }

    Ok(())
}

/// BL-P26-SERVICE-TOKEN-TO-ENV (7/19 Task #26): 拿 30 天 service token 塞
/// ~/.hermes/.env OPENAI_API_KEY · hermes credential_pool 从这拿 · 稳 30 天.
///
/// 用 client_credentials grant · sub=client:hermes-cli · aud=catfish-gateway.
/// TTL 2592000 秒 = 30 天 (identity-server clients.yaml hermes-cli 配的).
///
/// 幂等: 反复调 · 每次拿新 token 覆盖. 拿失败 (identity 挂 / 网络) → warn + Ok
/// (不阻塞) · env 里旧 token 仍有效直到过期.
///
/// **员工归属**: service token sub=client:hermes-cli · 不是真员工. WeChat +
/// Companion 直连 gateway 走 · config.yaml 的 access_token (真员工). hermes
/// daemon 用 env 的 service token 内部调 (P25 patch / catfish plugin) 走
/// service 身份. **RBAC 审计** 走 X-Catfish-User header · 由 hermes/gateway
/// 端注入 · 不看 JWT sub.
pub async fn sync_service_token_to_env(identity_url: &str) -> Result<()> {
    let home_str = crate::util::paths::home_env().context("拿 HOME")?;
    let hermes = PathBuf::from(&home_str).join(".hermes");
    if !hermes.exists() {
        log::debug!("[hermes-jwt-sync-service] {} 不存在 · skip", hermes.display());
        return Ok(());
    }
    let env_path = hermes.join(".env");
    if !env_path.exists() {
        log::debug!("[hermes-jwt-sync-service] {} 不存在 · skip", env_path.display());
        return Ok(());
    }

    // 拿 30 天 service token
    let client = reqwest::Client::new();
    let token_url = format!("{}/token", identity_url.trim_end_matches('/'));
    let resp = client
        .post(&token_url)
        .form(&[
            ("grant_type", "client_credentials"),
            ("client_id", "hermes-cli"),
            // BL-P26-DEMO-SECRET: 用 · identity-server clients.yaml 里 · hermes-cli
            // demo secret. POC 期用 · 达华现场 IT 可换成生产 secret + sync 到 identity.
            // 生产强化: 走 · macOS Keychain / 员工首次 SSO 派生 · POC 简化.
            ("client_secret", "hermes-dev-secret-2026-please-change"),
            ("scope", "chat.completions"),
        ])
        .send()
        .await
        .context("调 identity /token client_credentials")?;

    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        anyhow::bail!(
            "/token client_credentials 返 {status}: {} (识 identity URL 对不对)",
            body.chars().take(200).collect::<String>()
        );
    }

    #[derive(serde::Deserialize)]
    struct TokResp {
        access_token: String,
    }
    let tok: TokResp = resp.json().await.context("解析 /token json")?;

    // 塞 ~/.hermes/.env · 双 key: OPENAI_API_KEY (credential_pool) +
    // HERMES_SERVICE_TOKEN (P7 proxy · hermes plugin catfish-xcatfish-user 内
    // 转发 Companion → gateway 用).
    //
    // BL-P26-DUAL-ENV-KEYS (7/19 二次 catch "HERMES_SERVICE_TOKEN env 没配"):
    //   P7 patch (hermes/plugins/catfish-xcatfish-user/plugin.py) 内部 proxy
    //   从 HERMES_SERVICE_TOKEN env 拿 · 若没 · 透传 client Bearer · 撞 401.
    //   老版本只写 OPENAI_API_KEY · 漏了 P7 那条链路. 修 · 双写.
    let mut text = fs::read_to_string(&env_path).context("读 .env")?;
    for key in ["OPENAI_API_KEY", "HERMES_SERVICE_TOKEN"] {
        text = replace_or_append_env_line(&text, key, &tok.access_token);
    }
    fs::write(&env_path, text).context("写 .env")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(&env_path, fs::Permissions::from_mode(0o600));
    }

    log::info!(
        "[hermes-jwt-sync-service] ✓ ~/.hermes/.env OPENAI_API_KEY + HERMES_SERVICE_TOKEN 更新 (30 天 service · len={})",
        tok.access_token.len()
    );

    // 顺便 · reset auth.json (让 hermes credential_pool 重试)
    let _ = sync_auth_json(&hermes);

    Ok(())
}

// ─── 内部 · 分处写 ──────────────────────────────

// BL-P26-REMOVED: sync_env(access_token) 移除 · access_token 1h TTL 覆盖 env 的 30 天
// service token 导致 Companion 关 1h 后 hermes 挂 401. env 现在由
// sync_service_token_to_env 独立管 · 用 client_credentials 拿 30 天 service token.

fn sync_config_yaml(hermes: &PathBuf, jwt: &str) -> Result<()> {
    let config_path = hermes.join("config.yaml");
    if !config_path.exists() {
        log::debug!("[hermes-jwt-sync] {} 不存在 · skip", config_path.display());
        return Ok(());
    }
    let text = fs::read_to_string(&config_path)
        .with_context(|| format!("读 {}", config_path.display()))?;
    let new = replace_model_api_key(&text, jwt);
    fs::write(&config_path, new)
        .with_context(|| format!("写 {}", config_path.display()))?;
    log::info!("[hermes-jwt-sync] ✓ config.yaml model.api_key 更新");
    Ok(())
}

fn sync_auth_json(hermes: &PathBuf) -> Result<()> {
    let auth_path = hermes.join("auth.json");
    if !auth_path.exists() {
        log::debug!("[hermes-jwt-sync] {} 不存在 · skip", auth_path.display());
        return Ok(());
    }
    let text = fs::read_to_string(&auth_path)
        .with_context(|| format!("读 {}", auth_path.display()))?;
    let mut v: Value = serde_json::from_str(&text)
        .with_context(|| format!("parse {} 失败", auth_path.display()))?;

    let mut reset_count = 0;
    if let Some(pool) = v
        .get_mut("credential_pool")
        .and_then(|p| p.get_mut("openai-api"))
        .and_then(|p| p.as_array_mut())
    {
        for cred in pool {
            if let Some(o) = cred.as_object_mut() {
                o.insert("last_status".into(), Value::String("healthy".into()));
                o.insert("last_error_code".into(), Value::Null);
                o.insert("last_error_message".into(), Value::Null);
                o.insert("last_error_reset_at".into(), Value::Null);
                reset_count += 1;
            }
        }
    }
    fs::write(
        &auth_path,
        serde_json::to_string_pretty(&v).context("serialize auth.json 挂")?,
    )
    .with_context(|| format!("写 {}", auth_path.display()))?;
    log::info!(
        "[hermes-jwt-sync] ✓ auth.json {} 个 openai-api cred reset to healthy",
        reset_count
    );
    Ok(())
}

// ─── helpers · dotenv + yaml line-based edit ──────────────────────────────

/// dotenv line-based replace/append. 跟 server_config.rs::replace_or_append_env_line 同款.
///
/// (不 dedupe 到 util module: 循环依赖风险 + 两处逻辑一致, 复用不难维护.)
fn replace_or_append_env_line(text: &str, key: &str, value: &str) -> String {
    let mut lines: Vec<String> = text.lines().map(|s| s.to_string()).collect();
    let mut replaced = false;
    let prefix = format!("{key}=");
    for line in lines.iter_mut() {
        if line.starts_with(&prefix) {
            *line = format!("{key}={value}");
            replaced = true;
            break;
        }
    }
    if !replaced {
        if !text.is_empty() && !text.ends_with('\n') {
            lines.push(String::new());
        }
        lines.push(format!("{key}={value}"));
    }
    let mut out = lines.join("\n");
    if !out.ends_with('\n') {
        out.push('\n');
    }
    out
}

/// yaml `model:` 段 · 找 `  api_key:` · 整行替换. 保 indent + 其它字段 + comment.
///
/// 找不到 `model:` 段 → 追加整段.
/// 找到 `model:` 但无 `api_key:` → 段末追加 `  api_key: <jwt>`.
fn replace_model_api_key(text: &str, new_value: &str) -> String {
    let mut lines: Vec<String> = text.lines().map(|s| s.to_string()).collect();
    let mut in_model = false;
    let mut model_start_idx: Option<usize> = None;
    let mut last_model_line_idx: Option<usize> = None;
    let mut replaced = false;

    for (i, line) in lines.iter_mut().enumerate() {
        let t = line.trim_end();
        if t == "model:" || t.starts_with("model:") && !t.starts_with("model::") {
            // top-level model: 段头
            if !line.starts_with(char::is_whitespace) {
                in_model = true;
                model_start_idx = Some(i);
                continue;
            }
        }
        if in_model && !line.starts_with(char::is_whitespace) && t.contains(':') && !t.is_empty() {
            in_model = false;
        }
        if in_model {
            last_model_line_idx = Some(i);
            let stripped = t.trim_start();
            if stripped.starts_with("api_key:") {
                let indent: String = line.chars().take_while(|c| c.is_whitespace()).collect();
                *line = format!("{indent}api_key: {new_value}");
                replaced = true;
                break;
            }
        }
    }

    if !replaced {
        let new_line = format!("  api_key: {new_value}");
        if let Some(insert_after) = last_model_line_idx.or(model_start_idx) {
            lines.insert(insert_after + 1, new_line);
        } else {
            lines.push("model:".into());
            lines.push(new_line);
        }
    }

    let mut out = lines.join("\n");
    if !out.ends_with('\n') {
        out.push('\n');
    }
    out
}

// ─── tests ──────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn replace_env_existing() {
        let input = "OTHER=1\nOPENAI_API_KEY=old_jwt\nMORE=2\n";
        let out = replace_or_append_env_line(input, "OPENAI_API_KEY", "new_jwt");
        assert!(out.contains("OPENAI_API_KEY=new_jwt"));
        assert!(!out.contains("old_jwt"));
        assert!(out.contains("OTHER=1"));
        assert!(out.contains("MORE=2"));
    }

    #[test]
    fn replace_env_append_when_absent() {
        let input = "OTHER=1\n";
        let out = replace_or_append_env_line(input, "OPENAI_API_KEY", "new_jwt");
        assert!(out.contains("OTHER=1"));
        assert!(out.ends_with("OPENAI_API_KEY=new_jwt\n"));
    }

    #[test]
    fn replace_yaml_api_key_existing() {
        let input = "model:\n  name: catfish-auto\n  api_key: old_jwt\n  base_url: http://127.0.0.1:8999/v1\n";
        let out = replace_model_api_key(input, "new_jwt");
        assert!(out.contains("api_key: new_jwt"));
        assert!(!out.contains("old_jwt"));
        assert!(out.contains("name: catfish-auto"));
        assert!(out.contains("base_url:"));
    }

    #[test]
    fn replace_yaml_api_key_append_when_absent() {
        let input = "model:\n  name: catfish-auto\n  base_url: http://127.0.0.1:8999/v1\n";
        let out = replace_model_api_key(input, "new_jwt");
        assert!(out.contains("api_key: new_jwt"));
        assert!(out.contains("name: catfish-auto"));
    }

    #[test]
    fn replace_yaml_preserves_other_top_sections() {
        let input = "browser:\n  cdp_url: ws://x\nmodel:\n  api_key: old\nplugins:\n  enabled:\n  - foo\n";
        let out = replace_model_api_key(input, "new");
        assert!(out.contains("api_key: new"));
        assert!(out.contains("browser:"));
        assert!(out.contains("plugins:"));
        assert!(out.contains("cdp_url: ws://x"));
    }
}
