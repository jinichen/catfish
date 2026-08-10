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

/// hermes-cli 的 client_secret (BL-P26-DEMO-SECRET).
///
/// 跟 identity-server `config/clients.yaml` 里 hermes-cli 的 `client_secret_hash`
/// 对应. POC 期硬编码 — 这个明文本来就随 Companion 二进制装到每台员工机器,
/// 写进 `~/.hermes/.env` 不构成额外泄露(且 .env 会 chmod 600).
///
/// 生产要换: 两侧一起换 —— identity 侧重算 hash, 这里改明文并重发客户端.
const HERMES_CLI_SECRET: &str = "hermes-dev-secret-2026-please-change";

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
    // P3.5.80 (7/28): identity 可能是自签 HTTPS · Client::new() 不认自签证书.
    let client = crate::util::http_client::central_client(std::time::Duration::from_secs(30))
        .map_err(|e| anyhow::anyhow!(e))?;
    let token_url = format!("{}/token", identity_url.trim_end_matches('/'));
    let resp = client
        .post(&token_url)
        .form(&[
            ("grant_type", "client_credentials"),
            ("client_id", "hermes-cli"),
            // BL-P26-DEMO-SECRET: 用 · identity-server clients.yaml 里 · hermes-cli
            // demo secret. POC 期用 · 达华现场 IT 可换成生产 secret + sync 到 identity.
            // 生产强化: 走 · macOS Keychain / 员工首次 SSO 派生 · POC 简化.
            ("client_secret", HERMES_CLI_SECRET),
            // P3.5.82 (7/29): **不要显式传 scope**。
            //
            // identity 的语义是"requested 为空 → 返 client.allowed_scopes 全集"
            // (clients.py: filter_scopes)。而这里原来写死 `chat.completions`,
            // 于是拿到的 token 只有这一个 scope —— 聊天能用, 但 hermes 侧的
            // 后台任务 (distill / summarize / memory_enforce) 需要
            // `background.tasks`, 全部被网关拒:
            //     "X-Catfish-Internal 请求被拒 (缺 background.tasks scope)"
            //
            // 后果是**记忆链路装好了也不工作**, 而聊天本身正常 —— 现场只会看到
            // "能聊天但不长记性", 根本联想不到是 scope 少了一个。
            //
            // 交给服务端定: clients.yaml 的 allowed_scopes 本来就是权限边界,
            // 由它决定给什么。客户端写死子集, 等于把权限决策复制了一份到客户端,
            // 服务端加了新 scope 客户端还是拿不到。
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

    // ── P3.5.81 (7/29): 把 hermes 侧**自动续期**要的两个键也写进去 ──────
    //
    // hermes 里的 catfish plugin 有一条独立的续期路径
    // (`hermes_token_renewal.get_fresh_service_token`): token 剩 < 5 天时它自己
    // 调 identity 换新的, 让员工 30 天后不会突然全部 401.
    //
    // 但它读的是 env, 而这两个键**从来没有人写过**, 于是它有两处独立失效:
    //   1. `CATFISH_HERMES_CLIENT_SECRET` / `CLIENT_SECRET` 都没有
    //      → 它 warn 一句 "secret 没配" 然后返回旧 token, 续期路径等于不存在;
    //   2. `CATFISH_IDENTITY_URL` 没有 → 它 fallback 到写死的
    //      `http://localhost:8998`, 那是**员工自己的电脑**, 什么都没有.
    // 任一处都足以让续期永远失败, 而且是 fail-silent —— 只在 agent.log 里
    // warn, 界面无感. 7/28 达华联调实测: 该续时两条 warning, 一次都没续成.
    //
    // 后果不在当天, 在 30 天后 service token 到期: 所有走 gateway 的功能
    // (advisory / 审计 / 配额 / P7 转发) 一起 401, 而那时现场没人.
    //
    // 这里写死这两个键是合理的: 本函数刚刚用同一个 identity_url + 同一个
    // secret 成功换到了 token —— 已经证明这组值是对的, 直接落盘给续期路径复用.
    text = replace_or_append_env_line(
        &text,
        "CATFISH_IDENTITY_URL",
        identity_url.trim_end_matches('/'),
    );
    text = replace_or_append_env_line(&text, "CATFISH_HERMES_CLIENT_SECRET", HERMES_CLI_SECRET);

    fs::write(&env_path, text).context("写 .env")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(&env_path, fs::Permissions::from_mode(0o600));
    }

    log::info!(
        "[hermes-jwt-sync-service] ✓ ~/.hermes/.env OPENAI_API_KEY + HERMES_SERVICE_TOKEN 更新 \
         (30 天 service · len={}) + 续期用的 CATFISH_IDENTITY_URL={} / CLIENT_SECRET 已写入",
        tok.access_token.len(),
        identity_url.trim_end_matches('/')
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
    let new = ensure_model_api_mode(&new);
    fs::write(&config_path, new)
        .with_context(|| format!("写 {}", config_path.display()))?;
    log::info!("[hermes-jwt-sync] ✓ config.yaml model.api_key / api_mode 更新");
    Ok(())
}

/// 钉死 `model.api_mode: chat_completions` (8/8).
///
/// ── 病 ────────────────────────────────────────────────────────────────
///
/// hermes v0.20 升上去之后, 早安页的 advisor 全挂, 网关日志里是
///
/// ```text
///     HTTP 404: {"detail":"Not Found"}
///     base_url=http://127.0.0.1:8999/v1
/// ```
///
/// hermes 自己的请求转储写得很清楚: `request.url = .../v1/responses`。
/// 它改用 **OpenAI Responses API** 了, 而我们的网关只有
/// `/v1/chat/completions` 和 `/v1/embeddings` —— 没这条路由, FastAPI 就返
/// 默认 404。
///
/// ── 为什么 v0.19 好好的 ───────────────────────────────────────────────
///
/// `hermes_cli/runtime_provider.py` 同一个分支, 两版差在这里:
///
/// ```text
///     // v0.19
///     detected = _detect_api_mode_for_url(base_url)
///     if detected: api_mode = detected
///     // 认不出来 → 保持 chat_completions
///
///     // v0.20
///     api_mode = _fallback_api_mode(provider, base_url, effective_model)
/// ```
///
/// 而新增的 `_fallback_api_mode` 在 URL 认不出来时, **改成让 provider 自己
/// 声明的 transport 说了算**。我们 config 里写的是 `provider: openai-api`,
/// 它在 `providers.py` 的 overlay 里声明 `transport="codex_responses"` ——
/// 于是指向 127.0.0.1 的网关也被判定成"要走 Responses API"。
///
/// 上游这么改有它的道理 (它 docstring 里说, 是为了修 `openai-api` 指向
/// `us.api.openai.com` 这类数据驻留域名时每轮工具调用都 400 的问题)。它假设
/// `provider: openai-api` 就是真在打 OpenAI; 而我们拿这个名字当"OpenAI 兼容"
/// 的通用标签用, 指向自己的网关。
///
/// ── 为什么放在这里, 而不是让人手改 config.yaml ────────────────────────
///
/// 因为 **Companion bootstrap 会重写 config.yaml** (8/8 实测 14:57 那次,
/// 文件从 2022 涨到 3931 字节)。手加的行留不住。而这个函数每次同步 JWT 都跑,
/// 也就是说不管谁把 config 冲了, 下一次同步就自愈。
///
/// ── 为什么这条压得住 ─────────────────────────────────────────────────
///
/// `runtime_provider.py` 里优先级是
///
/// ```text
///     elif configured_mode && _provider_supports_explicit_api_mode(provider, configured_provider):
///         api_mode = configured_mode          // ← 我们走这条
///     else:
///         api_mode = _fallback_api_mode(...)
/// ```
///
/// 而 `_provider_supports_explicit_api_mode("openai-api", "openai-api")` 是
/// 「两者相等 → true」。所以不是碰巧生效, 是走的上游支持的显式配置路径。
///
/// 幂等: `replace_model_field` 有就替、没有就插。
fn ensure_model_api_mode(text: &str) -> String {
    replace_model_field(text, "api_mode", "chat_completions")
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
    replace_model_field(text, "api_key", new_value)
}

/// P3.5.81 (7/29 达华交付前夜): 把 gateway URL 同步进 hermes 的**同样这 3 个文件**.
///
/// ── 这是在补什么 ────────────────────────────────────────────────────
///
/// `sync_all()` 早就知道"服务器一变, hermes 有 3 个文件要跟着改", 并且逐个
/// 改了它们的**凭证**(api_key / token). 但**地址**只写了一处 —— `.env` 的
/// `CATFISH_GATEWAY_URL`(给 catfish plugin 读的), 而 hermes 真正用来发 LLM
/// 请求的三处地址一处没动:
///
///   `.env` 的 `OPENAI_BASE_URL` · `config.yaml` 的 `model.base_url` ·
///   `auth.json` 的 `credential_pool[].base_url`
///
/// 于是换服务器后, hermes 拿着**新签的 token** 去调**旧地址**. 7/28 实证:
///   - `.env` 里 `CATFISH_GATEWAY_URL` 已是新 IP、`OPENAI_BASE_URL` 还是
///     localhost —— 同一个文件里两个地址一新一旧, 现场 grep 新 IP 有命中
///     就以为改好了;
///   - 而 OpenAI SDK 读的恰恰是 `OPENAI_BASE_URL`, 且它**压过 config.yaml**,
///     所以单改 config.yaml 完全无效, 表象是 `Connection error` 反复重试.
///
/// 一句话: 凭证同步了 3 处, 地址只同步了 1 处, 而且是**没人读的那一处**.
///
/// ── 幂等 / fail 语义 ────────────────────────────────────────────────
///
/// 每个文件独立 try, 单个失败 warn 不阻塞其余(跟 `sync_all` 一致 —— 面板
/// 保存不该因为 hermes 没装就整体失败). hermes 目录不存在直接 skip.
///
/// `url_base` 传 gateway 根地址(如 `http://10.0.0.5:8999`), 内部补 `/v1`.
pub fn sync_base_url(url_base: &str) -> Result<()> {
    let base = url_base.trim().trim_end_matches('/');
    if base.is_empty() {
        log::warn!("[hermes-url-sync] URL 空 · 跳过 sync");
        return Ok(());
    }
    let openai_base = format!("{base}/v1");

    let home_str = crate::util::paths::home_env().context("拿 HOME 挂")?;
    let hermes = PathBuf::from(&home_str).join(".hermes");
    if !hermes.exists() {
        log::debug!(
            "[hermes-url-sync] {} 不存在 (hermes 未装) · 全 skip",
            hermes.display()
        );
        return Ok(());
    }

    // 1. ~/.hermes/.env OPENAI_BASE_URL — OpenAI SDK 真正读的那个, 优先级最高
    let env_path = hermes.join(".env");
    if env_path.exists() {
        match fs::read_to_string(&env_path) {
            Ok(old) => {
                let new_text = replace_or_append_env_line(&old, "OPENAI_BASE_URL", &openai_base);
                if let Err(e) = fs::write(&env_path, new_text) {
                    log::warn!("[hermes-url-sync] 写 .env 挂 (不阻塞): {e:#}");
                } else {
                    log::info!("[hermes-url-sync] ✓ .env OPENAI_BASE_URL → {openai_base}");
                }
            }
            Err(e) => log::warn!("[hermes-url-sync] 读 .env 挂 (不阻塞): {e:#}"),
        }
    } else {
        log::debug!("[hermes-url-sync] .env 不存在 · skip");
    }

    // 2. ~/.hermes/config.yaml model.base_url
    let cfg_path = hermes.join("config.yaml");
    if cfg_path.exists() {
        match fs::read_to_string(&cfg_path) {
            Ok(old) => {
                let new_text = replace_model_field(&old, "base_url", &openai_base);
                if let Err(e) = fs::write(&cfg_path, new_text) {
                    log::warn!("[hermes-url-sync] 写 config.yaml 挂 (不阻塞): {e:#}");
                } else {
                    log::info!("[hermes-url-sync] ✓ config.yaml model.base_url → {openai_base}");
                }
            }
            Err(e) => log::warn!("[hermes-url-sync] 读 config.yaml 挂 (不阻塞): {e:#}"),
        }
    } else {
        log::debug!("[hermes-url-sync] config.yaml 不存在 · skip");
    }

    // 3. ~/.hermes/auth.json credential_pool[].base_url
    if let Err(e) = sync_auth_json_base_url(&hermes, &openai_base) {
        log::warn!("[hermes-url-sync] auth.json base_url 挂 (不阻塞): {e:#}");
    }

    Ok(())
}

/// 把 auth.json 里所有 `base_url` 字段改成新地址.
///
/// 递归遍历而不是写死 `credential_pool.openai-api[0].base_url` —— hermes 的
/// credential pool 结构随版本变过, 写死路径下次升级就静默失效(而"静默失效"
/// 正是这一整类 bug 的病根). 只认字段名, 结构怎么变都跟得上.
///
/// 只改**指向我们自己 gateway** 的那些(靠 `:8999` 判定): auth.json 里可能
/// 还有员工自己配的第三方 provider(OpenAI 官方 / Azure), 不能一起改掉.
fn sync_auth_json_base_url(hermes: &PathBuf, openai_base: &str) -> Result<()> {
    let auth_path = hermes.join("auth.json");
    if !auth_path.exists() {
        log::debug!("[hermes-url-sync] auth.json 不存在 · skip");
        return Ok(());
    }
    let text = fs::read_to_string(&auth_path)
        .with_context(|| format!("读 {}", auth_path.display()))?;
    let mut v: Value = serde_json::from_str(&text)
        .with_context(|| format!("parse {} 失败", auth_path.display()))?;

    let n = rewrite_base_urls(&mut v, openai_base);
    if n == 0 {
        log::debug!("[hermes-url-sync] auth.json 里没有指向 gateway 的 base_url · 不动");
        return Ok(());
    }
    let out = serde_json::to_string_pretty(&v).context("序列化 auth.json")?;
    fs::write(&auth_path, out).with_context(|| format!("写 {}", auth_path.display()))?;
    log::info!("[hermes-url-sync] ✓ auth.json {n} 处 base_url → {openai_base}");
    Ok(())
}

/// 递归改写 JSON 里的 `base_url`. 返回改了几处. 纯函数, 好测.
fn rewrite_base_urls(v: &mut Value, new_base: &str) -> usize {
    let mut n = 0;
    match v {
        Value::Object(map) => {
            for (k, val) in map.iter_mut() {
                if k == "base_url" {
                    if let Some(s) = val.as_str() {
                        // 只认我们自己的 gateway 端口, 别动员工配的第三方 provider
                        if s.contains(":8999") {
                            *val = Value::String(new_base.to_string());
                            n += 1;
                            continue;
                        }
                    }
                }
                n += rewrite_base_urls(val, new_base);
            }
        }
        Value::Array(arr) => {
            for val in arr.iter_mut() {
                n += rewrite_base_urls(val, new_base);
            }
        }
        _ => {}
    }
    n
}

/// 替换/插入顶层 `model:` 段下的某个字段. `replace_model_api_key` 与
/// `replace_model_base_url` 共用 —— 两者的差别只有字段名.
///
/// P3.5.81 (7/29): 原来只有 api_key 版本, base_url 那半边根本不存在, 见
/// `sync_base_url` 的注释.
fn replace_model_field(text: &str, field: &str, new_value: &str) -> String {
    let mut lines: Vec<String> = text.lines().map(|s| s.to_string()).collect();
    let mut in_model = false;
    let mut model_start_idx: Option<usize> = None;
    let mut last_model_line_idx: Option<usize> = None;
    let mut replaced = false;
    let field_prefix = format!("{field}:");

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
            if stripped.starts_with(&field_prefix) {
                let indent: String = line.chars().take_while(|c| c.is_whitespace()).collect();
                *line = format!("{indent}{field}: {new_value}");
                replaced = true;
                break;
            }
        }
    }

    if !replaced {
        let new_line = format!("  {field}: {new_value}");
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

/// P3.5.81 (7/29): URL 同步的回归测试.
///
/// 每条对应 7/28 达华联调当晚真实撞过的一次故障 —— 挂了就说明那个坑回来了.
// 测试拆到隔壁两个文件 —— 见它们的文件头。`#[path]` 让模块仍然挂在本模块下,
// 所以那边的 `super::` 照旧能摸到这里的私有函数。
#[cfg(test)]
#[path = "hermes_jwt_sync_tests_base_url.rs"]
mod tests_base_url_sync;

#[cfg(test)]
#[path = "hermes_jwt_sync_tests.rs"]
mod tests;
