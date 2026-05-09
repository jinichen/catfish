//! OAuth 2.0 / OIDC Authorization Code flow (Companion 端).
//!
//! # 流程
//!
//! ```text
//! Companion 启动
//!   ↓ Keychain 找 access_token
//!   ├─ 有 + 没过期  → 用之
//!   └─ 没有 / 过期 → 走 OAuth flow:
//!        ↓ 起临时 HTTP server 监听 127.0.0.1:<random_port>
//!        ↓ 浏览器开 https://idp/authorize?...&redirect_uri=http://127.0.0.1:<port>/cb
//!        ↓ 员工登录, IdP redirect 回 cb URL with ?code=xxx
//!        ↓ HTTP server 收到 code, 关掉
//!        ↓ POST /token 换 id_token + access_token
//!        ↓ 存 Keychain
//!        ↓ Companion 用 access_token 调 gateway
//! ```
//!
//! # 跟 catfish-identity / 客户 SSO 配合
//!
//! 配置全部从 env 读 (跟 gateway 一致):
//!   - CATFISH_OIDC_ISSUER       (例 http://127.0.0.1:8998 或 https://sso.client.com)
//!   - CATFISH_OIDC_CLIENT_ID    (例 'catfish-companion')
//!   - CATFISH_OIDC_AUDIENCE     (默认 = client_id)
//!   - CATFISH_OIDC_SCOPE        (默认 'openid email profile')
//!   - CATFISH_OIDC_CALLBACK_PORT (可选, 默认随机选一个空闲端口)
//!
//! # dev_token 兜底
//!
//! env CATFISH_DEV_TOKEN 设了 → 启动时直接用, 跳过 OAuth.
//! Companion UI 显 warning banner "你在用 dev token, 不是真 SSO".
//! 决策 6 (docs/AUTH-DESIGN.md § 13).
//!
//! # Phase 1C 简化
//!
//! - 不做 PKCE (Phase 2 加, 让 catfish-identity 也支持先)
//! - 不做 refresh_token rotation (Phase 1C-2 加, 现在过期员工重新登录即可)
//! - 不做 silent renew
//! - state 用 32 字节 random, 防 CSRF (基础)

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use anyhow::{anyhow, bail, Context, Result};
use base64::Engine as _;
use rand::RngCore;
use serde::{Deserialize, Serialize};
use tokio::net::TcpListener;
use tokio::sync::oneshot;

// BL-FIX32 (5/9): token 存储从 macOS Keychain 改文件 (~/.catfish/oauth/<name>),
// keyring crate 不再用. 老 KEYRING_SERVICE 删. 下面三个仍叫 KEYRING_USERNAME_*
// 是因为 save/load/delete 函数签名 + callers 不动, 现在它们是**文件名**,
// 不是 keychain entry name. 语义改了, 命名沿用减改动.
const KEYRING_USERNAME_ACCESS: &str = "access_token";
const KEYRING_USERNAME_ID: &str = "id_token";
const KEYRING_USERNAME_USER_INFO: &str = "user_info";

const DEFAULT_SCOPE: &str = "openid email profile";

/// Login 完成后的用户信息 (从 ID Token claims 提).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuthSession {
    /// User.sub = email (决策 3)
    pub email: String,
    pub name: String,
    pub department: String,
    pub tier: String,
    /// 'oidc' 或 'dev_token'. UI 用这个决定要不要显 warning banner.
    pub auth_method: String,
    /// access_token (gateway 用), unix ts
    pub expires_at: i64,
}

/// 从 env 读 OIDC 配置.
#[derive(Debug, Clone)]
pub struct OidcConfig {
    pub issuer: String,
    pub client_id: String,
    /// gateway 用 audience 验 JWT, Companion 端不用 (id_token 我们不验签).
    /// 但保留字段, Phase 2 加 Companion 端 id_token 验签时用.
    #[allow(dead_code)]
    pub audience: String,
    pub scope: String,
}

/// ~/.catfish/companion.yaml 文件 schema (oidc 段子集).
#[derive(Debug, Deserialize)]
struct CompanionYamlFile {
    oidc: Option<CompanionYamlOidc>,
}

#[derive(Debug, Deserialize)]
struct CompanionYamlOidc {
    issuer: String,
    client_id: Option<String>,
    audience: Option<String>,
    scope: Option<String>,
}

impl OidcConfig {
    /// 加载 OIDC 配置. 优先级 (踩过坑 2026-04-29 鸿波):
    ///
    ///   1. ~/.catfish/companion.yaml 的 oidc 段 — **生产路径**, 员工友好
    ///   2. CATFISH_OIDC_* env — **dev / 测试路径**, terminal 启动用
    ///   3. 都没 → 自动生成默认 yaml + 返默认配置 (catfish-identity 本地)
    ///
    /// 为啥不只用 env: macOS .app 通过 LaunchServices 启动**不继承 terminal env**,
    /// 员工装完 .app 没人帮他 launchctl setenv. yaml 文件路径稳定, 装/部署友好.
    pub fn load() -> Result<Self> {
        // 1. yaml 文件
        if let Some(cfg) = Self::from_yaml_file()? {
            log::info!("OIDC 配置来自 ~/.catfish/companion.yaml");
            return Ok(cfg);
        }
        // 2. env 兜底 (dev / 测试)
        if std::env::var("CATFISH_OIDC_ISSUER").is_ok() {
            log::info!("OIDC 配置来自 env (生产应该用 ~/.catfish/companion.yaml)");
            return Self::from_env();
        }
        // 3. 都没: 自动生成默认 yaml, 配 catfish-identity 本地. 员工后续按需改.
        log::warn!(
            "OIDC 配置不存在, 自动生成默认 ~/.catfish/companion.yaml \
             (指向本地 catfish-identity:8998). 客户用别的 SSO 改这个文件即可."
        );
        Self::ensure_default_yaml()?;
        Self::from_yaml_file()?
            .ok_or_else(|| anyhow!("自动生成默认 yaml 后仍读不到, 内部 bug"))
    }

    pub fn from_env() -> Result<Self> {
        let issuer = std::env::var("CATFISH_OIDC_ISSUER")
            .map_err(|_| anyhow!("CATFISH_OIDC_ISSUER 没设"))?;
        let client_id = std::env::var("CATFISH_OIDC_CLIENT_ID")
            .unwrap_or_else(|_| "catfish-companion".into());
        let audience = std::env::var("CATFISH_OIDC_AUDIENCE")
            .unwrap_or_else(|_| client_id.clone());
        let scope = std::env::var("CATFISH_OIDC_SCOPE")
            .unwrap_or_else(|_| DEFAULT_SCOPE.into());
        Ok(Self {
            issuer: issuer.trim_end_matches('/').to_string(),
            client_id,
            audience,
            scope,
        })
    }

    fn yaml_path() -> Result<std::path::PathBuf> {
        let home = std::env::var("HOME")
            .or_else(|_| std::env::var("USERPROFILE"))
            .map_err(|_| anyhow!("找不到 HOME 环境变量"))?;
        Ok(std::path::PathBuf::from(home)
            .join(".catfish")
            .join("companion.yaml"))
    }

    fn from_yaml_file() -> Result<Option<Self>> {
        let path = Self::yaml_path()?;
        if !path.exists() {
            return Ok(None);
        }
        let content = std::fs::read_to_string(&path)
            .with_context(|| format!("读 {} 失败", path.display()))?;
        let parsed: CompanionYamlFile = serde_yaml::from_str(&content)
            .with_context(|| format!("解析 {} YAML 失败", path.display()))?;
        let oidc = match parsed.oidc {
            Some(o) => o,
            None => return Ok(None),  // 文件存在但没 oidc 段, fallback env
        };
        let client_id = oidc.client_id.unwrap_or_else(|| "catfish-companion".into());
        Ok(Some(Self {
            issuer: oidc.issuer.trim_end_matches('/').to_string(),
            audience: oidc.audience.unwrap_or_else(|| client_id.clone()),
            scope: oidc.scope.unwrap_or_else(|| DEFAULT_SCOPE.into()),
            client_id,
        }))
    }

    /// 第一次启动 (没 yaml 也没 env) 时, 自动生成默认配置文件指向本地
    /// catfish-identity. 客户用别的 SSO 时手动改这个 yaml.
    fn ensure_default_yaml() -> Result<()> {
        let path = Self::yaml_path()?;
        if path.exists() {
            return Ok(());
        }
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let content = r#"# Catfish Companion 配置文件
# 路径: ~/.catfish/companion.yaml
# 自动生成于第一次启动 Companion 时, 客户按需修改.
#
# 改完重启 Companion 即可生效. 员工不需要敲 launchctl setenv 这种命令.

oidc:
  # SSO 端点 (改这里). 三种典型场景:
  #   - 本机 demo:        http://127.0.0.1:8998
  #   - catfish-identity 中央:  http://10.10.40.50:8998
  #   - 企业 SSO (Azure AD/Okta/飞书): IT 给的 issuer URL
  issuer: http://127.0.0.1:8998

  # OIDC client 标识. catfish-identity 用默认就行, 企业 SSO 改成 IT 给的 GUID/App ID.
  client_id: catfish-companion

  # JWT audience claim — 必须跟 IdP 签 token 时填的 aud 一致, 否则 aud mismatch 401.
  # BL-WIN9.2 (5/8): catfish-identity 默认 audience = client_id, 所以这里也跟着填
  # 'catfish-companion' (而不是历史默认的 'test'). 之前默认 'test' 是早期 demo 残留,
  # 跟 catfish-identity 实际签的对不上, 改这条是修隐性 bug.
  audience: catfish-companion

  # OAuth scope. 决定 IdP 返哪些 claim. OIDC 标准三件套, 几乎不需要改.
  #   openid:  标识这是 OIDC 流 (必须)
  #   email:   拿员工邮箱 (catfish 用来识别员工)
  #   profile: 拿员工姓名 / 头像
  scope: openid email profile

# BL-WIN9 / DEPLOY1 (5/8): 网关地址配置 — 客户网关装中央服务器时改这里.
# 优先级: yaml > env (CATFISH_GATEWAY_HOST/PORT) > default 127.0.0.1:8999.
#
# 单机部署 (开发 / demo): 网关跟 Companion 同机, 注释掉这段, 走 default.
# 集中部署 (生产): 取消下面注释, 改 gateway_url 指你网关服务器.
# endpoints:
#   gateway_url: http://10.10.40.50:8999       # 客户网关地址
#   chrome_debug_url: http://127.0.0.1:9222    # Chrome 一般还在本机, 不动

# audit:
#   max_log_size_mb: 100
"#;
        std::fs::write(&path, content)
            .with_context(|| format!("写默认 yaml 到 {} 失败", path.display()))?;
        // 权限 0600 (含敏感配置时安全, 现在也提前打)
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
        }
        log::info!("已生成默认 ~/.catfish/companion.yaml, 客户按需修改 oidc.issuer");
        Ok(())
    }
}

/// 检查 dev_token env (兜底 / 救急 / 演示用 — 决策 6).
pub fn dev_token_from_env() -> Option<String> {
    std::env::var("CATFISH_DEV_TOKEN").ok().filter(|t| !t.is_empty())
}

/// 走完整 OAuth Authorization Code flow, 返 access_token + id_token.
///
/// 步骤:
///   1. 生成 state (CSRF 防御)
///   2. 起临时 HTTP server 监听随机端口
///   3. 浏览器开 authorize URL
///   4. 等 callback 拿 code
///   5. POST /token 换 token
///   6. 存 Keychain
///   7. 返 AuthSession
pub async fn run_login_flow(cfg: &OidcConfig) -> Result<AuthSession> {
    // 1. 生成 state
    let state = random_token(32);

    // 2. 起 HTTP server 监听 127.0.0.1:<random_port>
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .context("无法监听 127.0.0.1 临时端口")?;
    let bound_port = listener.local_addr()?.port();
    let redirect_uri = format!("http://127.0.0.1:{bound_port}/cb");
    log::info!("OAuth callback 监听: {redirect_uri}");

    // (state, code) 通过 oneshot channel 传出来
    let (tx, rx) = oneshot::channel::<CallbackResult>();
    let expected_state = state.clone();
    tokio::spawn(callback_server(listener, expected_state, tx));

    // 3. 打开浏览器
    let auth_url = build_authorize_url(cfg, &redirect_uri, &state);
    log::info!("打开浏览器走 OAuth: {auth_url}");
    open_in_browser(&auth_url)?;

    // 4. 等 callback (60 秒超时)
    let cb_result = tokio::time::timeout(Duration::from_secs(60), rx)
        .await
        .map_err(|_| anyhow!("OAuth callback 60 秒超时, 员工没完成登录"))?
        .map_err(|_| anyhow!("OAuth callback channel 关闭, 内部错误"))?;
    let code = cb_result.code;

    // 5. POST /token 换 token
    let token_resp = exchange_code_for_tokens(cfg, &code, &redirect_uri).await?;

    // 6. 解 id_token claims (仅提 user info, 不验签 — gateway 才验签)
    //    Companion 信任 catfish-identity 因为是同机, 真生产场景 (远程 SSO)
    //    这里也应该验签, Phase 2 加.
    let claims = decode_id_token_claims_unverified(&token_resp.id_token)?;

    let now = chrono::Utc::now().timestamp();
    let expires_at = now + token_resp.expires_in.unwrap_or(3600);

    let session = AuthSession {
        email: claims.sub,
        name: claims.name.unwrap_or_default(),
        department: claims.department.unwrap_or_default(),
        tier: claims.tier.unwrap_or_else(|| "employee".into()),
        auth_method: "oidc".into(),
        expires_at,
    };

    // 7. 存 Keychain (access_token + id_token + session info)
    save_to_keyring(KEYRING_USERNAME_ACCESS, &token_resp.access_token)?;
    save_to_keyring(KEYRING_USERNAME_ID, &token_resp.id_token)?;
    save_to_keyring(
        KEYRING_USERNAME_USER_INFO,
        &serde_json::to_string(&session)?,
    )?;

    log::info!("OAuth login OK: user={} dept={}", session.email, session.department);
    Ok(session)
}

/// Companion 启动时调. 优先级:
///   1. Keychain 有未过期 access_token → 用之 (登录员工的真身份)
///   2. CATFISH_DEV_TOKEN 设了 → 用 dev_token (没登录时的开发兜底)
///   3. 都没有 → 返 None, UI 弹登录
///
/// BL-FIX28 (5/9): 优先级翻转. 老序列让 dev_token 比 keychain 优先, OIDC
/// 登录的员工 (chenhongbo@ffcs.cn) Companion 还是用 dev_token 调 gateway,
/// gateway 解码 → 'dev-user@catfish.dev', quota / audit / chat 全挂虚构 user.
/// 跟 me.ts BL-FIX26 的优先级对齐: 真 token 优先, env 兜底.
pub fn try_load_session() -> Option<AuthSession> {
    // 1. Keychain 找真登录态 (OIDC)
    if let Ok(Some(raw)) = load_from_keyring(KEYRING_USERNAME_USER_INFO) {
        if let Ok(session) = serde_json::from_str::<AuthSession>(&raw) {
            let now = chrono::Utc::now().timestamp();
            if session.expires_at > now {
                return Some(session);
            }
            log::info!("Keychain access_token 过期, fallback 到 dev_token / 弹登录");
        }
    }
    // 2. dev_token 兜底 (没登录 / token 过期)
    if dev_token_from_env().is_some() {
        return Some(AuthSession {
            email: "dev-user".into(),
            name: "Dev User".into(),
            department: "engineering".into(),
            tier: "employee".into(),
            auth_method: "dev_token".into(),
            expires_at: chrono::Utc::now().timestamp() + 86400 * 365,
        });
    }
    // 3. 都没 → UI 弹登录
    None
}

/// 拿 token 给 gateway 调用. **返 id_token 不是 access_token**.
///
/// BL-FIX31 (5/9 鸿波诊断): catfish gateway OIDC validator 显式拒绝
/// token_use=access 的真 access_token (auth/oidc.py:159 安全设计 — access_token
/// 一般不该被当 id_token 用). catfish 这套把 id_token 当 gateway API auth,
/// 所以 keychain 里要返 id_token. Tauri 命令名 auth_get_access_token 是沿用
/// OAuth 习惯命名, 真实语义是 "gateway 收的那个 token".
///
/// BL-FIX28 (5/9): 优先 Keychain, dev_token 兜底. 跟 try_load_session 同序.
/// BL-FIX26 (me.ts) 翻转优先级前提是这个函数返真 OIDC token, 而不是 access_token.
pub fn current_access_token() -> Option<String> {
    // 1. Keychain id_token (OIDC 真登录的, gateway 接受)
    if let Ok(Some(tok)) = load_from_keyring(KEYRING_USERNAME_ID) {
        if !tok.is_empty() {
            return Some(tok);
        }
    }
    // 2. dev_token 兜底 (.env CATFISH_DEV_TOKEN, 仅 dev 模式)
    dev_token_from_env()
}

/// BL-D3 Phase 3.1 (5/9): 拿当前员工 sub (email) — tool-bridge spawn 时注入,
/// 用作 secret-broker rev key + audit. dev_token 时返 'dev-user@catfish.dev'.
pub fn current_user_sub() -> Option<String> {
    try_load_session().map(|s| s.email)
}

/// 登出: 清 Keychain. dev_token 模式下不动 env (那是员工 explicit 设的).
pub fn logout() -> Result<()> {
    let _ = delete_from_keyring(KEYRING_USERNAME_ACCESS);
    let _ = delete_from_keyring(KEYRING_USERNAME_ID);
    let _ = delete_from_keyring(KEYRING_USERNAME_USER_INFO);
    Ok(())
}

// ============================================================
// 内部 helpers
// ============================================================

#[derive(Debug)]
struct CallbackResult {
    code: String,
}

/// 临时 HTTP server, 收 IdP 重定向回来的 code.
async fn callback_server(
    listener: TcpListener,
    expected_state: String,
    tx: oneshot::Sender<CallbackResult>,
) {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let mut tx_holder = Some(tx);

    loop {
        let (mut socket, _) = match listener.accept().await {
            Ok(v) => v,
            Err(e) => {
                log::warn!("callback accept 失败: {e}");
                return;
            }
        };

        let mut buf = [0u8; 4096];
        let n = socket.read(&mut buf).await.unwrap_or(0);
        let request = String::from_utf8_lossy(&buf[..n]);

        // 第一行: GET /cb?code=xxx&state=yyy HTTP/1.1
        let first_line = request.lines().next().unwrap_or("");
        let path_query = first_line.split_whitespace().nth(1).unwrap_or("");

        let (code, state) = parse_callback_query(path_query);

        let body: &str = if state != expected_state {
            "<h1>❌ state 不匹配</h1><p>可能 CSRF 攻击, 拒绝</p>"
        } else if code.is_empty() {
            "<h1>❌ 没收到 code</h1>"
        } else {
            "<h1>✅ 登录成功</h1><p>关闭这个标签页, 回 Catfish Companion</p>"
        };

        let response = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\n\r\n{}",
            body.len(),
            body,
        );
        let _ = socket.write_all(response.as_bytes()).await;
        let _ = socket.shutdown().await;

        if state == expected_state && !code.is_empty() {
            if let Some(tx) = tx_holder.take() {
                let _ = tx.send(CallbackResult { code });
            }
            return; // 完事, 关 server
        }
        // state 不对或没 code 继续等 (理论不该发生)
    }
}

fn parse_callback_query(path_query: &str) -> (String, String) {
    let q = path_query.split_once('?').map(|(_, q)| q).unwrap_or("");
    let mut code = String::new();
    let mut state = String::new();
    for pair in q.split('&') {
        if let Some((k, v)) = pair.split_once('=') {
            // url decode
            let v = url::form_urlencoded::parse(format!("x={v}").as_bytes())
                .next()
                .map(|(_, vv)| vv.into_owned())
                .unwrap_or_default();
            match k {
                "code" => code = v,
                "state" => state = v,
                _ => {}
            }
        }
    }
    (code, state)
}

fn build_authorize_url(cfg: &OidcConfig, redirect_uri: &str, state: &str) -> String {
    let mut params = HashMap::new();
    params.insert("response_type", "code");
    params.insert("client_id", cfg.client_id.as_str());
    params.insert("redirect_uri", redirect_uri);
    params.insert("scope", cfg.scope.as_str());
    params.insert("state", state);
    let q: String = params
        .iter()
        .map(|(k, v)| format!("{}={}", k, urlencode(v)))
        .collect::<Vec<_>>()
        .join("&");
    format!("{}/authorize?{}", cfg.issuer, q)
}

fn urlencode(s: &str) -> String {
    url::form_urlencoded::byte_serialize(s.as_bytes()).collect()
}

#[derive(Deserialize)]
struct TokenResponse {
    access_token: String,
    id_token: String,
    expires_in: Option<i64>,
}

async fn exchange_code_for_tokens(
    cfg: &OidcConfig,
    code: &str,
    redirect_uri: &str,
) -> Result<TokenResponse> {
    let token_url = format!("{}/token", cfg.issuer);
    let client = reqwest::Client::new();
    let resp = client
        .post(&token_url)
        .form(&[
            ("grant_type", "authorization_code"),
            ("code", code),
            ("redirect_uri", redirect_uri),
            ("client_id", cfg.client_id.as_str()),
        ])
        .send()
        .await
        .context("调 /token 失败")?;
    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        bail!("/token 返回 {status}: {body}");
    }
    let token_resp: TokenResponse = resp.json().await.context("解析 /token 响应失败")?;
    Ok(token_resp)
}

#[derive(Deserialize)]
struct IdTokenClaims {
    sub: String,
    name: Option<String>,
    department: Option<String>,
    tier: Option<String>,
}

/// 解 id_token middle segment (base64 JSON), 不验签.
/// gateway 才负责验签, Companion 这里只是为了显 user info.
fn decode_id_token_claims_unverified(id_token: &str) -> Result<IdTokenClaims> {
    let mid = id_token
        .split('.')
        .nth(1)
        .ok_or_else(|| anyhow!("id_token 格式错"))?;
    let padded = format!(
        "{}{}",
        mid,
        "=".repeat((4 - mid.len() % 4) % 4)
    );
    let bytes = base64::engine::general_purpose::URL_SAFE
        .decode(padded.as_bytes())
        .context("id_token base64 解码失败")?;
    serde_json::from_slice(&bytes).context("id_token claims JSON 解析失败")
}

fn random_token(n: usize) -> String {
    let mut buf = vec![0u8; n];
    rand::thread_rng().fill_bytes(&mut buf);
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(&buf)
}

fn open_in_browser(url: &str) -> Result<()> {
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open").arg(url).status()?;
    }
    #[cfg(target_os = "linux")]
    {
        std::process::Command::new("xdg-open").arg(url).status()?;
    }
    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("cmd")
            .args(["/C", "start", url])
            .status()?;
    }
    Ok(())
}

// ============================================================
// Token storage — 文件系统 (BL-FIX32, 5/9)
// ============================================================
//
// 历史: 老实现用 macOS Keychain (keyring crate). 在 unsigned dev binary
// (cargo run target/debug, 没 codesign) 跑时, set_password 报 success 但实
// 际不写 login keychain — silent no-op. 用户从来不会看到那个"允许访问 Keychain"
// 的系统弹窗 (真写入 OAuth login OK 但 security CLI 一行都查不到 entry).
//
// 用户登录走完: log 打 'OAuth login OK: user=chenhongbo@ffcs.cn', 但 chat
// 调 invoke('auth_get_access_token') 永远返 None, 全链路 fallback dev_token,
// gateway 401. 中招过五六次都没看出是 keyring silent fail.
//
// 现在: 直接写 ~/.catfish/oauth/{access_token,id_token,user_info} 三个文件,
// chmod 600. dev 流程立即可用. 真 release build (cargo tauri build --release
// + Apple Developer ID sign) 后再切回 keyring (那时弹窗 + 真写入都正常).
//
// 函数名仍叫 _to_keyring/from_keyring/_keyring 不改, callers 全不动.

fn _oauth_storage_dir() -> Result<std::path::PathBuf> {
    let home = std::env::var("HOME").context("$HOME 未设置")?;
    let dir = std::path::PathBuf::from(home).join(".catfish").join("oauth");
    std::fs::create_dir_all(&dir).context("建 ~/.catfish/oauth 目录失败")?;
    // 目录权限 0700, 防别的用户读
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700));
    }
    Ok(dir)
}

fn save_to_keyring(username: &str, value: &str) -> Result<()> {
    let path = _oauth_storage_dir()?.join(username);
    std::fs::write(&path, value)
        .with_context(|| format!("写 token 文件失败: {}", path.display()))?;
    // 单文件权限 0600
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))
            .with_context(|| format!("chmod 600 失败: {}", path.display()))?;
    }
    log::debug!("oauth token saved: {} ({} bytes)", username, value.len());
    Ok(())
}

fn load_from_keyring(username: &str) -> Result<Option<String>> {
    let path = _oauth_storage_dir()?.join(username);
    if !path.exists() {
        return Ok(None);
    }
    let s = std::fs::read_to_string(&path)
        .with_context(|| format!("读 token 文件失败: {}", path.display()))?;
    Ok(Some(s))
}

fn delete_from_keyring(username: &str) -> Result<()> {
    let path = _oauth_storage_dir()?.join(username);
    if path.exists() {
        std::fs::remove_file(&path)
            .with_context(|| format!("删 token 文件失败: {}", path.display()))?;
    }
    Ok(())
}

// Phase 2 给 caller 可见的 Arc 包装, 避免 lifetime 问题. 现在 OidcConfig 是
// 调用时实例化, 不需要 share. Phase 2 加 background refresh / silent renew
// 时启用此 type alias.
#[allow(dead_code)]
pub type SharedConfig = Arc<OidcConfig>;
